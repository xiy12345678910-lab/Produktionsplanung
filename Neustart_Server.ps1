$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
Stop-ScheduledTask -TaskName $MP_TaskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Start-ScheduledTask -TaskName $MP_TaskName
$h = Wait-MPHealth $Base (Get-MPPackageVersion $Base) 20
if ($h) { Write-Host "PASS - V$($h.Health.version) neu gestartet: http://$($h.Config.lan_ip):$($h.Config.port)" -ForegroundColor Green }
else { Write-Host 'FEHLER - Server antwortet nach Neustart nicht.' -ForegroundColor Red; exit 1 }
