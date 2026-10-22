from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

from ..models import EventType, MatchEvent
from .base import BaseDetector

log = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _load_win32():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long * 4)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return ctypes, wintypes, user32, kernel32


class DotaWindowFinder:
    def __init__(self, process_names: list[str]):
        self._api = _load_win32()
        self._targets = {name.strip().lower() for name in process_names if name.strip()}

    @property
    def available(self) -> bool:
        return self._api is not None

    def find(self):
        if self._api is None:
            return None
        ctypes, wintypes, user32, kernel32 = self._api
        result = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)
        def enum_proc(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return True
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if not handle:
                return True
            try:
                size = wintypes.DWORD(1024)
                buffer = ctypes.create_unicode_buffer(size.value)
                if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    return True
                name = os.path.basename(buffer.value).lower()
                if name in self._targets:
                    result.append(hwnd)
            finally:
                kernel32.CloseHandle(handle)
            return True

        user32.EnumWindows(enum_proc, 0)
        if not result:
            return None
        return result[0]


class AcceptScreenDetector(BaseDetector):
    name = "acceptui"

    def __init__(self, sink, process_names: list[str], poll_interval_ms: int = 500, cooldown_seconds: float = 60.0):
        super().__init__(sink)
        self._finder = DotaWindowFinder(process_names)
        self._poll_interval = max(poll_interval_ms, 250) / 1000
        self._cooldown_seconds = cooldown_seconds
        self._last_emit = 0.0
        self._was_accept = False

    def run(self) -> None:
        if self._finder.available is False:
            log.error("acceptui детектор работает только на Windows")
            return
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
            import pyautogui  # type: ignore
        except Exception as exc:
            log.error("acceptui: не хватает зависимостей (pyautogui, opencv-python, numpy, pillow): %s", exc)
            return
        log.info("Ожидаю экран принятия матча")
        while not self.stopping:
            try:
                hwnd = self._finder.find()
                if not hwnd:
                    self._was_accept = False
                    time.sleep(self._poll_interval)
                    continue
                ctypes, wintypes, user32, kernel32 = _load_win32()
                rect = (ctypes.c_long * 4)()
                if not user32.GetWindowRect(hwnd, rect):
                    self._was_accept = False
                    time.sleep(self._poll_interval)
                    continue
                x1, y1, x2, y2 = rect[0], rect[1], rect[2], rect[3]
                w = max(0, x2 - x1)
                h = max(0, y2 - y1)
                if w < 800 or h < 600:
                    self._was_accept = False
                    time.sleep(self._poll_interval)
                    continue
                screenshot = pyautogui.screenshot(region=(x1, y1, w, h))
                img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
                lower_green = np.array([35, 80, 80])
                upper_green = np.array([90, 255, 255])
                mask = cv2.inRange(hsv, lower_green, upper_green)
                kernel = np.ones((5, 5), np.uint8)
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
                cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                accept_found = False
                for c in cnts:
                    area = cv2.contourArea(c)
                    if area < 1500:
                        continue
                    x, y, bw, bh = cv2.boundingRect(c)
                    if bh > 80 and bw > 120 and bw < 400 and bh < 160:
                        roi = img[y : y + bh, x : x + bw]
                        if roi.size == 0:
                            continue
                        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                        edges = cv2.Canny(gray, 50, 150)
                        if edges.sum() > 500:
                            accept_found = True
                            break
                now = time.time()
                if accept_found and not self._was_accept and now - self._last_emit > self._cooldown_seconds:
                    self._last_emit = now
                    log.info("Обнаружена кнопка «Принять» в Dota 2")
                    self.emit(
                        MatchEvent(
                            type=EventType.MATCH_FOUND,
                            source="acceptui",
                            detected_at=now,
                        )
                    )
                self._was_accept = accept_found
            except Exception:
                log.debug("Ошибка acceptui детектора", exc_info=True)
            time.sleep(self._poll_interval)
