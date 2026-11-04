# One-click start for Windows. Run: powershell -ExecutionPolicy Bypass -File start.ps1
param([switch]$NoSeed)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = Get-Command py, python -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $python) { throw 'Python 3.11+ is required' }

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host 'Creating virtual environment...'
    & $python.Source -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install -q -r backend\requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
}

Set-Location backend
& ..\.venv\Scripts\python.exe -m app.cli init-env
if ($NoSeed) { & ..\.venv\Scripts\python.exe -m app.cli migrate } else { & ..\.venv\Scripts\python.exe -m app.cli seed }

if (-not (Test-Path 'static\index.html')) {
    Write-Warning 'Frontend build not found. Run: cd frontend; npm install; npm run build'
}
Write-Host 'Open http://127.0.0.1:8000  (demo password: Demo@2026)'
& ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
