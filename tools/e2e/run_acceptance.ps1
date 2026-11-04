# Rebuild-free helper: restart a fresh demo instance on port 8100 and run the browser acceptance flow.
param([string]$ShotDir = "$env:TEMP\yihu-shots", [string]$Image = "")
$ErrorActionPreference = 'Continue'
$root = Resolve-Path "$PSScriptRoot\..\.."
Set-Location $root

Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -match 'run_demo_server' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "tools\run_demo_server.py", "--port", "8100", "--fresh" -WindowStyle Hidden `
    -RedirectStandardOutput "$env:TEMP\yihu-demo.out" -RedirectStandardError "$env:TEMP\yihu-demo.err" | Out-Null
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    try {
        if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8100/api/health-check).StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}
if (-not $ready) { throw 'demo server did not start' }

$global = & npm.cmd root -g
$env:NODE_PATH = "$global\@playwright\cli\node_modules"
$env:E2E_IMAGE = $Image
node tools\e2e\acceptance.cjs http://127.0.0.1:8100 $ShotDir
