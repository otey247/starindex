<#
.SYNOPSIS
  Installs StarIndex on Windows: Python environment, dependencies and the web interface.

.DESCRIPTION
  Run from the repository root in PowerShell:

      powershell -ExecutionPolicy Bypass -File scripts\install.ps1

  Requirements: Python 3.11 or newer (https://www.python.org/downloads/ or `winget install Python.Python.3.12`).
  Node.js 20+ is only needed when the web interface has not been built yet (frontend\dist is missing).
  Application data is kept in %LOCALAPPDATA%\StarIndex, never in this folder.

.PARAMETER Gpu
  Also install ONNX Runtime with CUDA support so embeddings and reranking use an NVIDIA GPU.

.PARAMETER SentenceTransformers
  Also install the optional Sentence Transformers embedding backend (large download, includes PyTorch).
#>
param(
    [switch]$Gpu,
    [switch]$SentenceTransformers
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Find-Python {
    foreach ($candidate in @(@("py", "-3"), @("python"), @("python3"))) {
        $exe = $candidate[0]
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            $args = @()
            if ($candidate.Length -gt 1) { $args = $candidate[1..($candidate.Length - 1)] }
            $version = & $exe @args -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $version) {
                $parts = $version.Trim().Split(".")
                if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) {
                    return ,@($exe) + $args
                }
            }
        }
    }
    return $null
}

Write-Host "StarIndex installer" -ForegroundColor Cyan

$python = Find-Python
if (-not $python) {
    Write-Host "Python 3.11 or newer was not found. Install it with:  winget install Python.Python.3.12" -ForegroundColor Red
    exit 1
}
Write-Host ("Using Python: " + ($python -join " "))

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment in .venv ..."
    $pyExe = $python[0]
    $pyArgs = @()
    if ($python.Length -gt 1) { $pyArgs = $python[1..($python.Length - 1)] }
    & $pyExe @pyArgs -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
}

Write-Host "Installing Python dependencies (this downloads about 300 MB the first time) ..."
& $venvPython -m pip install --upgrade pip --quiet
$extras = @()
if ($Gpu) { $extras += "gpu" }
if ($SentenceTransformers) { $extras += "sentence-transformers" }
$target = "."
if ($extras.Count -gt 0) { $target = ".[" + ($extras -join ",") + "]" }
& $venvPython -m pip install -e $target --quiet
if ($LASTEXITCODE -ne 0) { throw "Installing Python dependencies failed." }

$dist = Join-Path $Root "frontend\dist\index.html"
if (-not (Test-Path $dist)) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host "Building the web interface ..."
        Push-Location (Join-Path $Root "frontend")
        try {
            npm ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
            npm run build
            if ($LASTEXITCODE -ne 0) { throw "Building the web interface failed." }
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "The web interface is not built and Node.js was not found." -ForegroundColor Yellow
        Write-Host "Install Node.js 20+ (winget install OpenJS.NodeJS.LTS) and run this script again." -ForegroundColor Yellow
        exit 1
    }
}

Write-Host ""
Write-Host "Checking the installation ..."
& $venvPython -m starindex.cli doctor
Write-Host ""
Write-Host "Installed. Start StarIndex with:" -ForegroundColor Green
Write-Host "    powershell -ExecutionPolicy Bypass -File scripts\start.ps1"
Write-Host "or double-click Start-StarIndex.cmd"
