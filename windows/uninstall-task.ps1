<# Stops TailDeck and removes the logon task. #>
param([string]$TaskName = "TailDeck")
$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName $TaskName
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
# TailDeck cleans up its own helper processes (websockify, ttyd) the next time it starts;
# stop them now as well so no ports stay busy.
$root = Split-Path -Parent $PSScriptRoot
Get-CimInstance Win32_Process |
  Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine -like "*$root*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Write-Host "Task '$TaskName' removed."
