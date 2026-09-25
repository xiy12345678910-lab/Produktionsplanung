# Startet Setup_Windows.ps1 mit Administratorrechten (Erstinstallation).
$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
if (-not (Test-MPAdmin)) {
    Write-Host 'Administratorrechte erforderlich. PowerShell wird erhoeht neu geoeffnet.' -ForegroundColor Yellow
    $exe = (Get-Process -Id $PID).Path
    Start-Process -FilePath $exe -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    exit
}
& "$Base\Setup_Windows.ps1"
