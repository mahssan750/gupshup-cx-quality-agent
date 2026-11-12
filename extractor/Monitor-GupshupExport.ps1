param(
    [Parameter(Mandatory = $true)]
    [string]$ExportPath,
    [switch]$Watch,
    [ValidateRange(2, 3600)]
    [int]$IntervalSeconds = 10
)

$ErrorActionPreference = 'Stop'
$resolvedExportPath = [System.IO.Path]::GetFullPath($ExportPath)
$selectionPath = Join-Path $resolvedExportPath 'selection.json'
$manifestPath = Join-Path $resolvedExportPath 'manifest.jsonl'
$logPath = Join-Path $resolvedExportPath 'background.log'
$errorLogPath = Join-Path $resolvedExportPath 'background-errors.log'
$zipPath = Join-Path $resolvedExportPath 'zips'

function Get-ExportStatus {
    $selection = if (Test-Path -LiteralPath $selectionPath) {
        Get-Content -Raw -LiteralPath $selectionPath | ConvertFrom-Json
    } else {
        $null
    }

    $zipFiles = if (Test-Path -LiteralPath $zipPath) {
        @(Get-ChildItem -LiteralPath $zipPath -Filter '*.zip' -File)
    } else {
        @()
    }

    $failedIds = [System.Collections.Generic.HashSet[string]]::new()
    if (Test-Path -LiteralPath $manifestPath) {
        foreach ($line in Get-Content -LiteralPath $manifestPath) {
            if (-not $line.Trim()) { continue }
            try {
                $row = $line | ConvertFrom-Json
                if ($row.state -eq 'failed') { [void]$failedIds.Add([string]$row.id) }
                if ($row.state -eq 'downloaded') { [void]$failedIds.Remove([string]$row.id) }
            } catch {
                # Ignore an incomplete final line while the downloader is writing.
            }
        }
    }

    $processes = @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" | Where-Object {
        $_.CommandLine -like '*gupshup-transcript-downloader.mjs*' -and
        $_.CommandLine -like "*$resolvedExportPath*"
    })

    $target = if ($null -ne $selection) { [int]$selection.queued } else { 0 }
    $completed = $zipFiles.Count
    $percent = if ($target -gt 0) { [math]::Round(($completed / $target) * 100, 2) } else { 0 }

    [pscustomobject]@{
        Time = Get-Date
        Running = $processes.Count -gt 0
        ProcessIds = ($processes.ProcessId -join ', ')
        Completed = $completed
        Target = $target
        Percent = $percent
        Remaining = [math]::Max(0, $target - $completed)
        CurrentFailures = $failedIds.Count
        DownloadedMB = [math]::Round((($zipFiles | Measure-Object Length -Sum).Sum / 1MB), 2)
        LastLog = if (Test-Path -LiteralPath $logPath) {
            (Get-Content -LiteralPath $logPath -Tail 1)
        } else {
            'Waiting for background.log'
        }
        LastError = if (Test-Path -LiteralPath $errorLogPath) {
            (Get-Content -LiteralPath $errorLogPath -Tail 1)
        } else {
            ''
        }
    }
}

do {
    Clear-Host
    $status = Get-ExportStatus
    $status | Format-List

    if (-not $Watch) { break }
    if ($status.Target -gt 0 -and $status.Completed -ge $status.Target) {
        Write-Host 'Export complete.' -ForegroundColor Green
        break
    }
    if (-not $status.Running) {
        Write-Warning 'Downloader is not running. See the guide for the resume command.'
        break
    }
    Start-Sleep -Seconds $IntervalSeconds
} while ($true)

