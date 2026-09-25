$ErrorActionPreference = 'SilentlyContinue'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
Stop-ScheduledTask -TaskName $MP_TaskName
Start-Sleep -Seconds 1
Write-Host 'Server gestoppt. Autostart bleibt eingerichtet.' -ForegroundColor Yellow
