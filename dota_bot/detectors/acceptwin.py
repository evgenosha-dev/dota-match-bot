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

# Зелёная кнопка «Принять» в HSV (Dota 2: насыщенный зелёный).
GREEN_HSV_LOWER = (35, 80, 80)
GREEN_HSV_UPPER = (90, 255, 255)
# Доля от ширины окна: кнопка принятия занимает примерно 12–35% ширины
# и 4–14% высоты. Пороги масштабируются под разрешение.
BUTTON_MIN_W_FRAC = 0.06
BUTTON_MAX_W_FRAC = 0.45
BUTTON_MIN_H_FRAC = 0.04
BUTTON_MAX_H_FRAC = 0.20


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
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long * 4)]
    user32.GetWindowRect.restype = wintypes.BOOL

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
    """Находит видимое окно процесса Dota 2 и отдаёт его экранный прямоугольник."""

    def __init__(self, process_names: Iterable[str]):
        self._api = _load_win32()
        self._targets = {name.strip().lower() for name in process_names if name.strip()}

    @property
    def available(self) -> bool:
        return self._api is not None

    def find_rect(self) -> tuple[int, int, int, int] | None:
        """Возвращает (left, top, width, height) окна Dota 2 или None."""
        if self._api is None:
            return None
        ctypes, wintypes, user32, kernel32 = self._api
        result: list[tuple[int, int, int, int]] = []

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
                    rect = (ctypes.c_long * 4)()
                    if user32.GetWindowRect(hwnd, rect):
                        w = int(rect[2]) - int(rect[0])
                        h = int(rect[3]) - int(rect[1])
                        if w > 0 and h > 0:
                            result.append((int(rect[0]), int(rect[1]), w, h))
            finally:
                kernel32.CloseHandle(handle)
            return True

        user32.EnumWindows(enum_proc, 0)
        if not result:
            return None
        # Берём самое большое окно Dota (на случай нескольких процессов).
        return max(result, key=lambda r: r[2] * r[3])


class AcceptConfirmationStateMachine:
    """Подтверждает сигнал кнопки «Принять» серией стабильных опросов.

    Одиночный ложный срабатывание анализа скриншота не должно отправлять
    уведомление: кнопка должна оставаться на экране `confirmations` опросов
    подряд. Повторное подтверждение в пределах cooldown подавляется.
    """

    def __init__(self, confirmations: int = 3, cooldown_seconds: float = 90.0):
        self._confirmations = max(1, confirmations)
        self._cooldown_seconds = cooldown_seconds
        self._positive_count = 0
        self._last_emit_at: float | None = None

    def observe(self, has_accept: bool, now: float) -> bool:
        if not has_accept:
            self._positive_count = 0
            return False
        self._positive_count += 1
        if self._positive_count < self._confirmations:
            return False
        if (
            self._last_emit_at is not None
            and now - self._last_emit_at < self._cooldown_seconds
        ):
            return False
        self._last_emit_at = now
        return True

    def reset(self) -> None:
        self._positive_count = 0


class AcceptUiDetector(BaseDetector):
    """Детектирует зелёную кнопку «Принять» на скриншоте окна Dota 2.

    Dota 2 (SDL) не отдаёт свой интерфейс через UI Automation, поэтому
    детекция идёт по изображению: ищется крупный зелёный прямоугольник
    пропорций кнопки принятия.
    """

    name = "accept_button"

    def __init__(
        self,
        sink,
        process_names: Iterable[str] = ("dota2.exe",),
        poll_interval_ms: int = 250,
        cooldown_seconds: float = 90.0,
        required_confirmations: int = 3,
    ):
        super().__init__(sink)
        self._finder = DotaWindowFinder(process_names)
        self._poll_interval = max(poll_interval_ms, 150) / 1000
        self._confirmation = AcceptConfirmationStateMachine(
            confirmations=required_confirmations,
            cooldown_seconds=cooldown_seconds,
        )

    def _detect_accept_button(self, rect: tuple[int, int, int, int]) -> bool:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
            import pyautogui  # type: ignore
        except Exception as exc:
            log.error("accept_button: не хватает зависимостей (pyautogui, opencv-python, numpy, pillow): %s", exc)
            raise

        x, y, w, h = rect
        if w < 800 or h < 600:
            return False
        screenshot = pyautogui.screenshot(region=(x, y, w, h))
        img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        return has_accept_button(img)

    def run(self) -> None:
        if not self._finder.available:
            log.error("accept_button работает только на Windows")
            return
        log.info("Ожидаю появление кнопки «Принять» в окне Dota 2")
        while not self.stopping:
            try:
                rect = self._finder.find_rect()
                has_accept = bool(rect) and self._detect_accept_button(rect)
                now = time.time()
                if self._confirmation.observe(has_accept, now):
                    log.info("Обнаружена кнопка «Принять» — матч найден")
                    self.emit(
                        MatchEvent(
                            type=EventType.MATCH_FOUND,
                            source=self.name,
                            detected_at=now,
                        )
                    )
            except Exception:
                log.debug("Ошибка accept_button", exc_info=True)
            time.sleep(self._poll_interval)


def has_accept_button(img_bgr) -> bool:
    """True, если на изображении окна Dota 2 есть зелёная кнопка «Принять».

    Чистая функция над массивом OpenCV (BGR) — без скриншота, чтобы её
    можно было тестировать на синтетических изображениях.
    """
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    h, w = img_bgr.shape[:2]
    if w < 800 or h < 600:
        return False
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array(GREEN_HSV_LOWER, dtype=np.uint8),
        np.array(GREEN_HSV_UPPER, dtype=np.uint8),
    )
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_w, max_w = w * BUTTON_MIN_W_FRAC, w * BUTTON_MAX_W_FRAC
    min_h, max_h = h * BUTTON_MIN_H_FRAC, h * BUTTON_MAX_H_FRAC
    for c in cnts:
        if cv2.contourArea(c) < 1500:
            continue
        bx, by, bw, bh = cv2.boundingRect(c)
        if not (min_w <= bw <= max_w and min_h <= bh <= max_h):
            continue
        # Берём ROI с отступом: у однотонно залитой кнопки граница совпадает
        # с краем bbox, и Canny без отступа не видит градиента.
        pad = max(4, int(0.08 * bw))
        px1, py1 = max(0, bx - pad), max(0, by - pad)
        px2, py2 = min(w, bx + bw + pad), min(h, by + bh + pad)
        roi = img_bgr[py1:py2, px1:px2]
        if roi.size == 0:
            continue
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        if edges.sum() > 500:
            return True
    return False
