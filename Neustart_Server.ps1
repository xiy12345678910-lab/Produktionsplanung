$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
# V12.29.0: Task und haengengebliebenen server.py beenden, damit der neue Start den Port sicher bekommt.
try { Stop-MPServer $Base } catch { Write-Warning $_.Exception.Message }
[void](Wait-MPTaskIdle $MP_TaskName 30)
Start-ScheduledTask -TaskName $MP_TaskName
$h = Wait-MPHealth $Base (Get-MPPackageVersion $Base) 90
if ($h) { Write-Host "PASS - V$($h.Health.version) neu gestartet: $($h.Url)" -ForegroundColor Green }
else { Write-Host 'FEHLER - Server antwortet nach Neustart nicht.' -ForegroundColor Red; exit 1 }
