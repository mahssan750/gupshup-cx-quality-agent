[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ZipFolder,
    [switch]$NoAI,
    [switch]$SkipDashboard
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$cli = Join-Path $projectRoot '.venv\Scripts\cxquality.exe'

if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {
    throw "Virtual environment is not installed. Follow the README setup first: $cli"
}
if (-not (Test-Path -LiteralPath $ZipFolder -PathType Container)) {
    throw "ZIP folder was not found: $ZipFolder"
}
$arguments = @(
    'run-week',
    '--zip-folder', $ZipFolder
)
if ($NoAI) {
    $arguments += '--no-ai'
}

Push-Location $projectRoot
try {
    & $cli @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Weekly QA command failed with exit code $LASTEXITCODE."
    }
    if (-not $SkipDashboard) {
        $listener = Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue
        if (-not $listener) {
            Start-Process -FilePath $cli -ArgumentList @('serve', '--port', '8765') -WorkingDirectory $projectRoot -WindowStyle Hidden
        }
        Start-Process 'http://127.0.0.1:8765'
    }
}
finally {
    Pop-Location
}
