from __future__ import annotations

import logging
import sys
import time
from enum import Enum

log = logging.getLogger(__name__)

TH32CS_SNAPPROCESS = 0x00000002
SW_RESTORE = 9
VK_RETURN = 0x0D
KEYEVENTF_KEYUP = 0x0002
FOCUS_SETTLE_SECONDS = 0.15


class InputResult(str, Enum):
    ACCEPTED = "accepted"
    UNSUPPORTED = "unsupported"
    PROCESS_NOT_FOUND = "process_not_found"
    WINDOW_NOT_FOUND = "window_not_found"
    NOT_FOREGROUND = "not_foreground"
    ERROR = "error"


def _load_win32():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    user32.keybd_event.restype = None

    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    return ctypes, wintypes, user32, kernel32, PROCESSENTRY32W


class Win32InputController:
    """Локальное управление окном Dota 2 через Win32."""

    def __init__(self):
        self._api = _load_win32()

    @property
    def available(self) -> bool:
        return self._api is not None

    def foreground_pid(self) -> int | None:
        if self._api is None:
            return None
        ctypes, wintypes, user32, _, _ = self._api
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) or None

    def find_pid(self, process_name: str) -> int | None:
        if self._api is None:
            return None
        ctypes, wintypes, _, kernel32, PROCESSENTRY32W = self._api
        target = process_name.strip().lower()
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot:
            return None
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        try:
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return None
            while True:
                if entry.szExeFile.strip().lower() == target:
                    return int(entry.th32ProcessID)
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    return None
        finally:
            kernel32.CloseHandle(snapshot)

    def find_main_window(self, pid: int) -> int | None:
        if self._api is None:
            return None
        ctypes, wintypes, user32, _, _ = self._api
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def visit(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            window_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if int(window_pid.value) != pid:
                return True
            if user32.GetWindowTextLengthW(hwnd) <= 0:
                return True
            found.append(int(hwnd))
            return False

        user32.EnumWindows(visit, 0)
        return found[0] if found else None

    def focus(self, hwnd: int) -> bool:
        if self._api is None:
            return False
        _, wintypes, user32, kernel32, _ = self._api
        user32.ShowWindow(hwnd, SW_RESTORE)
        foreground = user32.GetForegroundWindow()
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        current_thread = kernel32.GetCurrentThreadId()
        attached = False
        if foreground_thread and foreground_thread != current_thread:
            attached = bool(
                user32.AttachThreadInput(current_thread, foreground_thread, wintypes.BOOL(True))
            )
        try:
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, foreground_thread, wintypes.BOOL(False))
        time.sleep(FOCUS_SETTLE_SECONDS)
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)

    def send_enter(self) -> None:
        if self._api is None:
            raise RuntimeError("Win32 недоступен")
        _, _, user32, _, _ = self._api
        user32.keybd_event(VK_RETURN, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)


_controller: Win32InputController | None = None


def is_process_running(process_name: str) -> bool | None:
    """Проверяет, запущен ли процесс. None — платформа без поддержки Win32."""
    global _controller
    if sys.platform != "win32":
        return None
    if _controller is None:
        _controller = Win32InputController()
    if not _controller.available:
        return None
    try:
        return _controller.find_pid(process_name) is not None
    except Exception:
        log.debug("is_process_running: ошибка перечисления процессов", exc_info=True)
        return None


def accept_match(
    process_name: str = "dota2.exe",
    focus_window: bool = True,
    controller: Win32InputController | None = None,
) -> InputResult:
    """Подтверждает найденный матч, отправляя Enter в окно процесса.

    Enter отправляется только если процесс (и его окно) уже в фокусе.
    """
    ctrl = controller or Win32InputController()
    if not ctrl.available:
        return InputResult.UNSUPPORTED
    try:
        pid = ctrl.find_pid(process_name)
        if pid is None:
            return InputResult.PROCESS_NOT_FOUND
        if ctrl.foreground_pid() == pid:
            ctrl.send_enter()
            return InputResult.ACCEPTED
        hwnd = ctrl.find_main_window(pid)
        if hwnd is None:
            return InputResult.WINDOW_NOT_FOUND
        if not focus_window or not ctrl.focus(hwnd):
            return InputResult.NOT_FOREGROUND
        if ctrl.foreground_pid() != pid:
            return InputResult.NOT_FOREGROUND
        ctrl.send_enter()
        return InputResult.ACCEPTED
    except Exception:
        log.exception("Ошибка локального управления Dota 2")
        return InputResult.ERROR
