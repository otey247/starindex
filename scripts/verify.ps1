<#
.SYNOPSIS
  Verifies a StarIndex installation on this machine end to end and writes a report.

.DESCRIPTION
      powershell -ExecutionPolicy Bypass -File scripts\verify.ps1              # checks only
      powershell -ExecutionPolicy Bypass -File scripts\verify.ps1 -Sync        # also import the starred list
      powershell -ExecutionPolicy Bypass -File scripts\verify.ps1 -Research    # also run a research request (slow)

  Starts StarIndex in the background when it is not already running, then runs `starindex verify`:
  installation, GitHub connection and synchronisation, search in every mode, each model provider,
  a grounded answer with the default local model, ChatGPT plan status, GPU detection and MCP.
  Connect GitHub and add a model in the app first; the report lists whatever still needs a manual step.
#>
param(
    [int]$Port = 8765,
    [switch]$Sync,
    [switch]$Research
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "StarIndex is not installed yet. Run scripts\install.ps1 first." -ForegroundColor Red
    exit 1
}
$env:PYTHONUTF8 = "1"
$url = "http://127.0.0.1:$Port"

function Test-Running {
    try {
        $response = Invoke-WebRequest -Uri "$url/api/health" -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

$startedHere = $null
if (-not (Test-Running)) {
    Write-Host "Starting StarIndex on port $Port ..."
    $startedHere = Start-Process -FilePath $venvPython -ArgumentList @("-m", "starindex.cli", "serve", "--port", "$Port") -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddSeconds(90)
    while (-not (Test-Running)) {
        if ((Get-Date) -gt $deadline) {
            Write-Host "StarIndex did not start within 90 seconds. Run scripts\start.ps1 to see the error." -ForegroundColor Red
            exit 1
        }
        Start-Sleep -Seconds 2
    }
}

$arguments = @("-m", "starindex.cli", "verify", "--url", $url)
if ($Sync) { $arguments += "--sync" }
if ($Research) { $arguments += "--research" }
Set-Location $Root
& $venvPython @arguments
$code = $LASTEXITCODE

if ($startedHere) {
    Write-Host "StarIndex keeps running in the background (process $($startedHere.Id)). Stop it with scripts\stop.ps1."
}
exit $code
