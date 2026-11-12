[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$cli = Join-Path $projectRoot '.venv\Scripts\cxquality.exe'

if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {
    throw "Virtual environment is not installed. See PROJECT-GUIDE.md."
}

$listener = Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue
if (-not $listener) {
    Start-Process -FilePath $cli -ArgumentList @('serve', '--port', '8765') -WorkingDirectory $projectRoot -WindowStyle Hidden
    Start-Sleep -Seconds 2
}
Start-Process 'http://127.0.0.1:8765/runs/new'
