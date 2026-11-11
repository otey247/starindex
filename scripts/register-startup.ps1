<#
.SYNOPSIS
  Runs StarIndex in the background whenever you sign in to Windows, so scheduled synchronisation,
  indexing and backups happen without opening it by hand. Uses Windows Task Scheduler only.

.DESCRIPTION
      powershell -ExecutionPolicy Bypass -File scripts\register-startup.ps1            # register
      powershell -ExecutionPolicy Bypass -File scripts\register-startup.ps1 -Remove    # unregister

  The task runs as the current user (no elevation) and starts scripts\start.ps1 -NoBrowser hidden.
  Schedules themselves (how often to sync, back up, clean up) are edited in StarIndex under Sync.
#>
param(
    [switch]$Remove,
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$TaskName = "StarIndex"
$Root = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed the StarIndex startup task."
    exit 0
}

$script = Join-Path $Root "scripts\start.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -NoBrowser -Port $Port" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "StarIndex local search for starred repositories" -Force | Out-Null
Write-Host "StarIndex will now start in the background when you sign in (http://127.0.0.1:$Port)."
Write-Host "Start it right now with:  Start-ScheduledTask -TaskName $TaskName"
