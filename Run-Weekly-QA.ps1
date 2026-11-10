[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ZipFolder,
    [Parameter(Mandatory = $true)]
    [string]$Roster,
    [Parameter(Mandatory = $true)]
    [datetime]$FromDate,
    [Parameter(Mandatory = $true)]
    [datetime]$ToDate,
    [ValidateRange(0, 1000000)]
    [int]$SampleSize = 0,
    [ValidateRange(0.01, 1000)]
    [decimal]$Budget = 4.00,
    [ValidateRange(0.01, 1000)]
    [decimal]$StopAt = 3.80,
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
if (-not (Test-Path -LiteralPath $Roster -PathType Leaf)) {
    throw "Roster workbook was not found: $Roster"
}
if ($ToDate.Date -lt $FromDate.Date) {
    throw 'ToDate must be on or after FromDate.'
}

$command = if ($SampleSize -gt 0) { 'sample' } else { 'run-week' }
$arguments = @(
    $command,
    '--zip-folder', $ZipFolder,
    '--roster', $Roster,
    '--from-date', $FromDate.ToString('yyyy-MM-dd'),
    '--to-date', $ToDate.ToString('yyyy-MM-dd'),
    '--budget', $Budget.ToString([Globalization.CultureInfo]::InvariantCulture),
    '--stop-at', $StopAt.ToString([Globalization.CultureInfo]::InvariantCulture)
)
if ($SampleSize -gt 0) {
    $arguments += @('--sample-size', $SampleSize.ToString())
}
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
