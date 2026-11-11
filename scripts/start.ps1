<#
.SYNOPSIS
  Starts StarIndex (API, background worker and web interface) and opens it in the browser.

.DESCRIPTION
      powershell -ExecutionPolicy Bypass -File scripts\start.ps1 [-Port 8765] [-NoBrowser] [-DataDir D:\StarIndexData]

  StarIndex listens on 127.0.0.1 only. Close this window or press Ctrl+C to stop it; background
  work (synchronisation, indexing, embedding) resumes where it stopped the next time you start it.
#>
param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [string]$DataDir = "",
    [string]$QdrantUrl = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "StarIndex is not installed yet. Run:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1" -ForegroundColor Red
    exit 1
}
if ($DataDir) { $env:STARINDEX_DATA_DIR = $DataDir }
if ($QdrantUrl) { $env:STARINDEX_QDRANT_URL = $QdrantUrl }
$env:PYTHONUTF8 = "1"

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "Port $Port is already in use. If StarIndex is already running, open http://127.0.0.1:$Port" -ForegroundColor Yellow
    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
    exit 0
}

$arguments = @("-m", "starindex.cli", "serve", "--port", "$Port")
if (-not $NoBrowser) { $arguments += "--open" }
Set-Location $Root
& $venvPython @arguments
