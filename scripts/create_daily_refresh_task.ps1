param(
  [string]$TaskName = "TradeHistoryDailyRefresh",
  [string]$Time = "19:00"
)

$repo = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$repo\scripts\run_daily_refresh.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description "Daily refresh of trade history statements/prices/fx" -Force

