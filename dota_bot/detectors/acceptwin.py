from __future__ import annotations

import logging
import os
import sys
import time
from typing import Iterable

from ..models import EventType, MatchEvent
from .base import BaseDetector

log = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

ACCEPT_TEXTS = (
    "принять",
    "accept",
    "принять матч",
    "accept match",
)


def _load_win32():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

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


class DotaProcessFinder:
    def __init__(self, process_names: Iterable[str]):
        self._api = _load_win32()
        self._targets = {name.strip().lower() for name in process_names if name.strip()}

    @property
    def available(self) -> bool:
        return self._api is not None

    def find_pid(self) -> int | None:
        if self._api is None:
            return None
        ctypes, wintypes, user32, kernel32 = self._api
        result: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)
        def enum_proc(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value or pid.value in result:
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
                    result.append(int(pid.value))
            finally:
                kernel32.CloseHandle(handle)
            return True

        user32.EnumWindows(enum_proc, 0)
        return result[0] if result else None


class AcceptUiDetector(BaseDetector):
    name = "acceptui"

    def __init__(
        self,
        sink,
        process_names: Iterable[str] = ("dota2.exe",),
        poll_interval_ms: int = 250,
        cooldown_seconds: float = 90.0,
    ):
        super().__init__(sink)
        self._finder = DotaProcessFinder(process_names)
        self._poll_interval = max(poll_interval_ms, 150) / 1000
        self._cooldown_seconds = cooldown_seconds
        self._last_emit = 0.0
        self._seen_accept = False
        self._pid: int | None = None

    def _find_accept_button(self) -> bool:
        try:
            import uiautomation as auto  # type: ignore
        except Exception as exc:
            log.error("acceptui: требуется пакет uiautomation: %s", exc)
            raise

        try:
            window = auto.WindowControl(searchDepth=1, ClassName="SDL_app")
        except Exception:
            window = None

        if window is None or not window.Exists(0, 0):
            try:
                if self._pid:
                    window = auto.WindowControl(searchDepth=1, ProcessId=self._pid)
                else:
                    window = auto.GetForegroundControl()
                    if window:
                        window = window.GetTopLevelControl()
            except Exception:
                window = None

        if window is None or not getattr(window, "Exists", lambda *_: False)(0, 0):
            return False

        def walk(control, depth: int = 0) -> bool:
            if depth > 14:
                return False
            try:
                name = (getattr(control, "Name", "") or "").strip().lower()
                if name in ACCEPT_TEXTS:
                    ctrl_type = getattr(control, "ControlTypeName", "") or ""
                    if "Button" in ctrl_type or name.startswith("принять") or name.startswith("accept"):
                        try:
                            if control.IsEnabled and control.IsVisible:
                                return True
                        except Exception:
                            return True
                for child in control.GetChildren():
                    if walk(child, depth + 1):
                        return True
            except Exception:
                return False
            return False

        try:
            return walk(window)
        except Exception:
            return False

    def run(self) -> None:
        if not self._finder.available:
            log.error("acceptui работает только на Windows")
            return
        try:
            import uiautomation as auto  # type: ignore
            auto.SetGlobalSearchTimeout(1)
        except Exception as exc:
            log.error("acceptui: установи uiautomation (pip install uiautomation): %s", exc)
            return
        log.info("Ожидаю появление кнопки «Принять» в окне Dota 2")
        while not self.stopping:
            try:
                pid = self._finder.find_pid()
                if pid:
                    self._pid = pid
                has_accept = self._find_accept_button() if self._pid or True else False
                now = time.time()
                if has_accept and not self._seen_accept and now - self._last_emit > self._cooldown_seconds:
                    self._last_emit = now
                    log.info("Найдена кнопка «Принять» — матч найден")
                    self.emit(
                        MatchEvent(
                            type=EventType.MATCH_FOUND,
                            source=self.name,
                            detected_at=now,
                        )
                    )
                self._seen_accept = has_accept
                if not has_accept:
                    pass
            except Exception:
                log.debug("Ошибка acceptui", exc_info=True)
            time.sleep(self._poll_interval)
