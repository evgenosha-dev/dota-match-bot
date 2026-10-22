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


class ForegroundStateMachine:
    """Логика детекта перехода фокуса окна на процесс Dota 2."""

    def __init__(
        self,
        targets: Iterable[str],
        confirmation_delay_ms: int = 750,
        cooldown_seconds: float = 120.0,
    ):
        self._targets = {name.strip().lower() for name in targets if name.strip()}
        self._confirmation_delay_ms = confirmation_delay_ms
        self._cooldown_seconds = cooldown_seconds
        self._armed = False
        self._previous_was_target = False
        self._pending_since: float | None = None
        self._streak_from_target = False
        self._last_alert_at: float | None = None

    @property
    def is_armed(self) -> bool:
        return self._armed

    @property
    def targets(self) -> frozenset[str]:
        return frozenset(self._targets)

    def observe(self, process_name: str | None, now: float) -> bool:
        """Возвращает True, если зафиксирован переход на процесс Dota 2."""
        name = (process_name or "").strip().lower()
        is_target = name in self._targets

        if not self._armed:
            if not is_target:
                self._armed = True
            self._previous_was_target = is_target
            return False

        if not is_target:
            self._pending_since = None
            self._previous_was_target = False
            return False

        if self._pending_since is None:
            self._pending_since = now
            self._streak_from_target = self._previous_was_target

        if self._streak_from_target:
            return False

        if (now - self._pending_since) * 1000 < self._confirmation_delay_ms:
            return False

        if self._last_alert_at is not None and now - self._last_alert_at < self._cooldown_seconds:
            return False

        self._last_alert_at = now
        self._pending_since = None
        self._previous_was_target = True
        return True


def _load_win32():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
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


class Win32ForegroundReader:
    """Возвращает имя процесса, окно которого сейчас в фокусе."""

    def __init__(self):
        self._api = _load_win32()

    @property
    def available(self) -> bool:
        return self._api is not None

    def read(self) -> str | None:
        if self._api is None:
            return None
        ctypes, wintypes, user32, kernel32 = self._api
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return os.path.basename(buffer.value).lower()
        finally:
            kernel32.CloseHandle(handle)


class ForegroundDetector(BaseDetector):
    """Детектор «матч найден» по выходу окна Dota 2 на передний план."""

    name = "foreground"

    def __init__(
        self,
        sink,
        process_names: Iterable[str],
        poll_interval_ms: int = 250,
        confirmation_delay_ms: int = 750,
        cooldown_seconds: float = 120.0,
    ):
        super().__init__(sink)
        self._machine = ForegroundStateMachine(
            process_names,
            confirmation_delay_ms=confirmation_delay_ms,
            cooldown_seconds=cooldown_seconds,
        )
        self._poll_interval = max(poll_interval_ms, 50) / 1000
        self._reader = Win32ForegroundReader()

    @property
    def machine(self) -> ForegroundStateMachine:
        return self._machine

    def run(self) -> None:
        if not self._reader.available:
            log.error("Детектор foreground работает только на Windows")
            return
        log.info("Слежу за окнами: %s", ", ".join(sorted(self._machine.targets)))
        while not self.stopping:
            try:
                process_name = self._reader.read()
            except Exception:
                log.exception("Не удалось прочитать активное окно")
                process_name = None
            try:
                if self._machine.observe(process_name, time.time()):
                    log.info("Окно Dota 2 вышло на передний план — возможно найден матч")
                    self.emit(
                        MatchEvent(
                            type=EventType.MATCH_FOUND,
                            source=self.name,
                            detected_at=time.time(),
                        )
                    )
            except Exception:
                log.exception("Ошибка в логике детектора foreground")
            time.sleep(self._poll_interval)
