$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
$task = Get-ScheduledTask -TaskName $MP_TaskName -ErrorAction Stop
if ($task.State -ne 'Running') { Start-ScheduledTask -TaskName $MP_TaskName }
$h = Wait-MPHealth $Base (Get-MPPackageVersion $Base) 15
if ($h) { Write-Host "PASS - Server V$($h.Health.version) laeuft auf http://$($h.Config.lan_ip):$($h.Config.port)" -ForegroundColor Green }
else { Write-Host 'WARNUNG - Task gestartet, Health-Check antwortet (noch) nicht.' -ForegroundColor Yellow }
