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
    $candidates = @(
        @{ Exe = "python"; Extra = @() },
        @{ Exe = "py"; Extra = @("-3") },
        @{ Exe = "python3"; Extra = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        $extra = $candidate.Extra
        $version = $null
        try {
            $version = & $candidate.Exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch {
            continue
        }
        if ($LASTEXITCODE -ne 0 -or -not $version) { continue }
        $parts = "$version".Trim().Split(".")
        if ($parts.Length -ge 2 -and [int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) {
            return $candidate
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
$pythonExtra = $python.Extra
Write-Host ("Using Python: " + $python.Exe + " " + ($pythonExtra -join " "))
& $python.Exe @pythonExtra --version

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment in .venv ..."
    & $python.Exe @pythonExtra -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
}

Write-Host "Installing Python dependencies (this downloads about 300 MB the first time) ..."
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "Upgrading pip failed." }
$target = "."
if ($SentenceTransformers) { $target = ".[sentence-transformers]" }
& $venvPython -m pip install -e $target
if ($LASTEXITCODE -ne 0) { throw "Installing Python dependencies failed." }

if ($Gpu) {
    # ONNX Runtime ships CPU and CUDA builds as separate packages that must not be installed together.
    Write-Host "Switching ONNX Runtime to the CUDA build ..."
    & $venvPython -m pip uninstall -y onnxruntime --quiet
    & $venvPython -m pip install onnxruntime-gpu --quiet
    if ($LASTEXITCODE -ne 0) { throw "Installing onnxruntime-gpu failed." }
}

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
