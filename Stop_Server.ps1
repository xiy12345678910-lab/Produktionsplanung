$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
# V12.10.2: Task stoppen UND haengengebliebenen server.py-Prozess beenden; Port muss danach frei sein.
Stop-MPServer $Base
Write-Host 'Server gestoppt. Autostart bleibt eingerichtet.' -ForegroundColor Yellow
