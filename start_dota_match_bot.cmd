@echo off
rem Запускает бота без окна консоли (pythonw). Логи: logs\bot.log рядом с проектом.
rem Если нужен запуск с консолью — выполни: .venv\Scripts\python.exe bot.py run
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" bot.py run
    exit /b 0
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" bot.py run
) else (
    python bot.py run
)
pause
