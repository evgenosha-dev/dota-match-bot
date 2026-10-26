[CmdletBinding()]
param(
    [switch]$NoSetup
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

Write-Host ''
Write-Host 'Dota Match Bot installer' -ForegroundColor Cyan
Write-Host ''

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $python) {
    Write-Host 'Python 3.10+ was not found. Install it from https://www.python.org/downloads/' -ForegroundColor Red
    exit 1
}

$venvDir = Join-Path $PSScriptRoot '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host 'Creating virtual environment .venv...'
    & $python.Source -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        throw "python -m venv failed with exit code $LASTEXITCODE"
    }
}

Write-Host 'Installing dependencies...'
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed with exit code $LASTEXITCODE"
}
& $venvPython -m pip install --requirement requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "pip install failed with exit code $LASTEXITCODE"
}

if ($NoSetup) {
    Write-Host ''
    Write-Host 'Installation finished. Run setup with:' -ForegroundColor Green
    Write-Host '  .\.venv\Scripts\python.exe bot.py setup'
    exit 0
}

Write-Host ''
Write-Host 'Starting Telegram pairing wizard...' -ForegroundColor Cyan
& $venvPython bot.py setup
exit $LASTEXITCODE
