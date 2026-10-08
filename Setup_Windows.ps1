# Maschinenplanung - Erstinstallation als Windows LAN-only Server
# Als Administrator ausfuehren (oder INSTALLIEREN_ALS_ADMIN.ps1 doppelklicken).
# Fuer bestehende Installationen stattdessen UPDATE_LIVE.ps1 verwenden.
$ErrorActionPreference = 'Stop'
$SourceBase = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SourceBase 'MP_Common.ps1')
Assert-MPAdmin 'Setup_Windows.ps1'

$Version = Get-MPPackageVersion $SourceBase
$Base = $MP_InstallBase
if (Test-Path (Join-Path $Base 'data\maschinenplanung.sqlite3')) {
    throw "Es existiert bereits eine Installation mit Datenbank in $Base. Bitte UPDATE_LIVE.ps1 verwenden."
}
foreach ($name in $MP_AppFiles) {
    if (-not (Test-Path -LiteralPath (Get-MPAppPath $SourceBase $name))) { throw "Paket unvollstaendig: $name fehlt." }
}

$PythonExe = Get-MPPython
if (-not (Test-MPPythonLocationSafe $PythonExe)) {
    Write-Warning "Python ($PythonExe) ist fuer normale Benutzer beschreibbar. Der Server laeuft als SYSTEM - bitte Python 'fuer alle Benutzer' (C:\Program Files) installieren."
    if ((Read-Host 'Trotzdem fortfahren? (j/N)') -notmatch '^[jJ]') { throw 'Abgebrochen.' }
}
Write-Host 'Zeitzonendaten (tzdata) installieren ...'
Install-MPTzdata $PythonExe $SourceBase

Write-Host "Programmdateien nach $Base kopieren ..."
New-Item -ItemType Directory -Path $Base -Force | Out-Null
foreach ($name in $MP_AppFiles) { Copy-MPAppFile $SourceBase $Base $name }
Protect-MPInstall $Base
Set-Location $Base

$lan = Get-MPLanInfo
if (-not $lan) { throw 'Sicherheitsstopp: Kein aktives physisches LAN mit Netzwerkprofil Privat oder Domaene gefunden.' }
Write-Host "LAN erkannt: $($lan.InterfaceAlias) / $($lan.IP) / $($lan.Profile)" -ForegroundColor Cyan

$AdminUser = Read-Host 'Erster Admin-Benutzername'
$Secure = Read-Host 'Admin-Passwort (mind. 8 Zeichen)' -AsSecureString
$BSTR = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
try {
    $Plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($BSTR)
    if ($Plain.Length -lt 8) { throw 'Passwort muss mindestens 8 Zeichen haben.' }
    $env:MP_ADMIN_PASSWORD = $Plain
    & $PythonExe -I "$Base\server.py" --init-admin $AdminUser
    if ($LASTEXITCODE -ne 0) { throw 'Admin konnte nicht eingerichtet werden.' }
} finally {
    $env:MP_ADMIN_PASSWORD = $null
    if ($BSTR -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR) }
    $Plain = $null
}
Protect-MPInstall $Base

Remove-MPLegacyTasks
Register-MPServerTask $Base $PythonExe
Register-MPBackupTask $Base $PythonExe
Start-ScheduledTask -TaskName $MP_TaskName

$health = Wait-MPHealth $Base $Version 30
if (-not $health) { throw "Server V$Version antwortet nicht. Bitte Server_Status.ps1 pruefen." }
Assert-MPPrivatePaths $health.Config $health.Url

Write-Host ''
Write-Host "=== FERTIG - Maschinenplanung V$Version (LAN only) ===" -ForegroundColor Green
Write-Host "Installationsordner: $Base (nur Administratoren duerfen aendern)"
Write-Host "Adresse fuer Benutzer: $($health.Url)"
Write-Host 'Backups: zweimal taeglich automatisch (12:15 / 22:15) nach backups\'
Write-Host 'Empfehlung: Zweitziel in BACKUP_ZIEL.txt eintragen (z. B. Netzlaufwerk).'
Write-Host "Pruefen mit: $Base\CHECK_LAN_SICHERHEIT.ps1"
