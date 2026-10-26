"""Фоновый запуск, автозапуск Windows и защита от двойного экземпляра.

Пользователь взаимодействует с программой только через мастер настройки
(`bot.py setup`): вбивает данные, мастер сам прописывает бота в автозапуск
и запускает его в фоне (pythonw, без окон). После перезагрузки Windows бот
стартует сам, ничего открывать не нужно.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

AUTOSTART_FILENAME = "dota-match-bot.vbs"

_VBS_TEMPLATE = (
    "' Dota Match Bot — автозапуск при входе в Windows.\n"
    "' Создан мастером настройки; удалить можно вручную из папки Автозагрузка.\n"
    'Set shell = CreateObject("WScript.Shell")\n'
    'shell.CurrentDirectory = "{root}"\n'
    'shell.Run """{pythonw}"" bot.py run", 0, False\n'
    "Set shell = Nothing\n"
)


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("Переменная APPDATA не найдена — автозапуск доступен только на Windows")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def autostart_path() -> Path:
    return startup_dir() / AUTOSTART_FILENAME


def install_autostart(project_root: Path) -> Path:
    """Прописывает бота в автозапуск Windows (идемпотентно). Только Windows."""
    if sys.platform != "win32":
        raise RuntimeError("Автозапуск поддерживается только на Windows")
    project_root = Path(project_root).resolve()
    pythonw = project_root / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        raise RuntimeError(f"Не найден {pythonw} — сначала выполни install.bat")
    path = autostart_path()
    content = _VBS_TEMPLATE.format(root=str(project_root), pythonw=str(pythonw))
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return path
    path.write_text(content, encoding="utf-8")
    log.info("Автозапуск записан: %s", path)
    return path


def remove_autostart() -> bool:
    path = autostart_path()
    if path.exists():
        path.unlink()
        log.info("Автозапуск удалён: %s", path)
        return True
    return False


def start_background(project_root: Path) -> None:
    """Запускает бота фоном (pythonw, без консоли) и не ждёт завершения."""
    if sys.platform != "win32":
        raise RuntimeError("Фоновый запуск поддерживается только на Windows")
    project_root = Path(project_root).resolve()
    pythonw = project_root / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        raise RuntimeError(f"Не найден {pythonw} — сначала выполни install.bat")
    subprocess.Popen(
        [str(pythonw), "bot.py", "run"],
        cwd=str(project_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    log.info("Фоновый процесс запущен: %s bot.py run", pythonw)


def stop_running_instance(project_root: Path) -> int | None:
    """Останавливает фоновый экземпляр бота, если он запущен.

    Нужен мастеру настройки: одновременный опрос getUpdates ботом и мастером
    даёт 409 Conflict от Telegram. Возвращает pid остановленного процесса.
    """
    lock_path = Path(project_root) / "run.lock"
    try:
        pid = int(lock_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return None
    if "python" not in out:
        return None
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        capture_output=True, timeout=15,
    )
    log.info("Остановлен фоновый экземпляр бота (pid %s) на время настройки", pid)
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass
    return pid


class SingleInstance:
    """Блокировка второго экземпляра бота через msvcrt.locking.

    Файл-лок живёт рядом с проектом; ОС сама снимает блокировку, если
    процесс убит. Удобно: повторный `bot.py run` (из автозапуска или руками)
    просто завершается, не плодя параллельные детекторы.
    """

    def __init__(self, lock_path: Path):
        self._lock_path = Path(lock_path)
        self._file = None
        self.acquired = False

    def acquire(self) -> bool:
        if sys.platform != "win32":
            # Вне Windows — простая проверка pid-файла.
            try:
                pid = int(self._lock_path.read_text().strip())
                os.kill(pid, 0)
                return False
            except (OSError, ValueError):
                pass
            self._lock_path.write_text(str(os.getpid()))
            self.acquired = True
            return True
        import msvcrt

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._lock_path, "a+b")
        try:
            msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self._file.close()
            self._file = None
            return False
        self._file.seek(0)
        self._file.truncate()
        self._file.write(str(os.getpid()).encode("ascii"))
        self._file.flush()
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.acquired:
            return
        if self._file is not None:
            try:
                self._file.seek(0)
                msvcrt_import = __import__("msvcrt")
                msvcrt_import.locking(self._file.fileno(), msvcrt_import.LK_UNLCK, 1)
                self._file.close()
            except OSError:
                pass
            self._file = None
        else:
            try:
                self._lock_path.unlink(missing_ok=True)
            except OSError:
                pass
        self.acquired = False
