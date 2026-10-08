# Startet Setup_Windows.ps1 mit Administratorrechten (Erstinstallation).
# #52: HTTPS wird bei Neuinstallation eingerichtet; -OhneHttps wird an Setup_Windows.ps1 durchgereicht.
param([switch]$OhneHttps)
$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
if (-not (Test-MPAdmin)) {
    Write-Host 'Administratorrechte erforderlich. PowerShell wird erhoeht neu geoeffnet.' -ForegroundColor Yellow
    $exe = (Get-Process -Id $PID).Path
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($OhneHttps) { $argList += '-OhneHttps' }
    Start-Process -FilePath $exe -Verb RunAs -ArgumentList $argList
    exit
}
& "$Base\Setup_Windows.ps1" -OhneHttps:$OhneHttps
