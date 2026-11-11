<#
.SYNOPSIS
  Development mode: API with the worker on port 8765 and the Vite dev server (hot reload) on port 5173.

.DESCRIPTION
      powershell -ExecutionPolicy Bypass -File scripts\dev.ps1

  Opens two windows. The Vite server proxies /api to the backend. Use a separate data directory so
  development never touches your real library:  -DataDir .\.dev-data
#>
param(
    [string]$DataDir = ".dev-data"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw "Run scripts\install.ps1 first." }
$data = Join-Path $Root $DataDir

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "`$env:STARINDEX_DATA_DIR='$data'; `$env:STARINDEX_LOG_LEVEL='DEBUG'; Set-Location '$Root'; & '$venvPython' -m starindex.cli serve"
)
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$(Join-Path $Root "frontend")'; if (-not (Test-Path node_modules)) { npm ci }; npm run dev"
)
Write-Host "Backend:  http://127.0.0.1:8765   (data: $data)"
Write-Host "Frontend: http://127.0.0.1:5173   (hot reload)"
