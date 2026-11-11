<#
.SYNOPSIS
  Stops a StarIndex instance listening on the given port (default 8765).
#>
param([int]$Port = 8765)

$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $connections) {
    Write-Host "Nothing is listening on port $Port."
    exit 0
}
foreach ($processId in ($connections | Select-Object -ExpandProperty OwningProcess -Unique)) {
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -like "python*") {
        Stop-Process -Id $processId
        Write-Host "Stopped StarIndex (process $processId). Unfinished background work resumes on the next start."
    } else {
        Write-Host "Port $Port is used by '$($process.ProcessName)', not StarIndex. Nothing was stopped." -ForegroundColor Yellow
    }
}
