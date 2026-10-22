param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt
& $Python -m pip install "pyinstaller>=6.0"

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name dota-match-bot `
    --collect-all telegram `
    bot.py

Write-Host ""
Write-Host "Готово: dist\dota-match-bot.exe"
Write-Host "Первый запуск проведёт мастер настройки и создаст config.json рядом с exe."
