# Maschinenplanung - Erstinstallation als Windows LAN-only Server
# Als Administrator ausfuehren (oder INSTALLIEREN_ALS_ADMIN.ps1 doppelklicken).
# Fuer bestehende Installationen stattdessen UPDATE_LIVE.ps1 verwenden.
# #52: Eine NEUinstallation richtet HTTPS ein (wie HTTPS_Einrichten.ps1). Abschalten: -OhneHttps
# Eine bestehende Installation (LAN_CONFIG.json, Servertask oder Backups vorhanden) behaelt ihren Stand (HTTP bleibt HTTP).
param([switch]$OhneHttps)
$ErrorActionPreference = 'Stop'
$SourceBase = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SourceBase 'MP_Common.ps1')
Assert-MPAdmin 'Setup_Windows.ps1'

$Version = Get-MPPackageVersion $SourceBase
$Base = $MP_InstallBase
if (Test-Path (Join-Path $Base 'data\maschinenplanung.sqlite3')) {
    throw "Es existiert bereits eine Installation mit Datenbank in $Base. Bitte UPDATE_LIVE.ps1 verwenden."
}
# #52: Neuinstallation erkennen, BEVOR etwas kopiert oder gestartet wird. Spuren einer bestehenden Installation:
# LAN_CONFIG.json (schreibt jeder Serverstart), ein Servertask (auch alter Name) oder Sicherungen in backups\.
$existingMarks = @()
if (Test-Path -LiteralPath (Join-Path $Base 'LAN_CONFIG.json')) { $existingMarks += 'LAN_CONFIG.json' }
foreach ($taskName in @($MP_TaskName) + $MP_LegacyTaskNames) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { $existingMarks += "Task '$taskName'" }
}
if (Get-ChildItem -LiteralPath (Join-Path $Base 'backups') -Filter '*.sqlite3' -ErrorAction SilentlyContinue | Select-Object -First 1) { $existingMarks += 'backups' }
$IsNewInstall = ($existingMarks.Count -eq 0)
# Vorhandenes config\tls (auch nur eine Firmen-CA) wird nie angefasst - Arbeitsplaetze koennten sie schon importiert haben.
$TlsBefore = (Test-Path -LiteralPath (Get-MPTlsDir $Base))
$TlsCreated = $false
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

# #52: HTTPS als Standard bei Neuinstallation. Bestehende Installationen und -OhneHttps bleiben unveraendert.
if ($TlsBefore) {
    Write-Host 'config\tls ist bereits vorhanden - bleibt unveraendert (HTTPS, sobald server.crt und server.key vorliegen; sonst HTTPS_Einrichten.ps1).' -ForegroundColor Cyan
} elseif ($OhneHttps) {
    Write-Host 'HTTPS nicht eingerichtet (-OhneHttps). Spaeter moeglich mit HTTPS_Einrichten.ps1 (README_Windows.txt 2c).' -ForegroundColor Yellow
} elseif (-not $IsNewInstall) {
    Write-Host "Bestehende Installation erkannt ($($existingMarks -join ', ')): HTTPS wird nicht automatisch eingerichtet." -ForegroundColor Yellow
    Write-Host 'Empfohlen: HTTPS_Einrichten.ps1 (README_Windows.txt 2c).' -ForegroundColor Yellow
} else {
    Write-Host 'HTTPS einrichten (Firmen-CA und Serverzertifikat) ...'
    try {
        $tlsInfo = Install-MPTls $Base $PythonExe ([string]$lan.IP)
        $TlsCreated = $true
        Write-Host "Firmen-CA vertraut (Fingerabdruck $($tlsInfo.Thumbprint)). Zum Verteilen: $($tlsInfo.PublicCa)" -ForegroundColor Green
    } catch {
        $msg = $_.Exception.Message
        if ($msg -notmatch 'MP-TLS-') { $msg = "MP-TLS-002: $msg" }
        [void](Remove-MPTls $Base)
        Write-Warning "$msg"
        Write-Warning 'Rueckfall: Installation laeuft weiter mit HTTP. HTTPS spaeter mit HTTPS_Einrichten.ps1 nachholen (README_Windows.txt 2c, FEHLERCODES.txt).'
    }
}

Remove-MPLegacyTasks
Register-MPServerTask $Base $PythonExe
Register-MPBackupTask $Base $PythonExe
Start-ScheduledTask -TaskName $MP_TaskName

$health = Wait-MPHealth $Base $Version 30
if (-not $health -and $TlsCreated) {
    # #52: Server antwortet nicht per HTTPS - keinen kaputten Server hinterlassen, sondern auf HTTP zurueckfallen.
    Write-Warning 'MP-TLS-003: Server antwortet nach der HTTPS-Einrichtung nicht per HTTPS. Rueckfall auf HTTP.'
    try { Stop-MPServer $Base } catch { Write-Warning $_.Exception.Message }
    [void](Wait-MPTaskIdle $MP_TaskName 30)
    if (-not (Remove-MPTls $Base)) { Write-Warning 'config\tls konnte nicht vollstaendig entfernt werden.' }
    $TlsCreated = $false
    Start-ScheduledTask -TaskName $MP_TaskName
    $health = Wait-MPHealth $Base $Version 30
    if ($health) { Write-Warning 'Server laeuft mit HTTP. HTTPS spaeter mit HTTPS_Einrichten.ps1 nachholen (README_Windows.txt 2c, FEHLERCODES.txt).' }
}
if (-not $health) { throw "Server V$Version antwortet nicht. Bitte Server_Status.ps1 pruefen." }
Assert-MPPrivatePaths $health.Config $health.Url

Write-Host ''
Write-Host "=== FERTIG - Maschinenplanung V$Version (LAN only) ===" -ForegroundColor Green
Write-Host "Installationsordner: $Base (nur Administratoren duerfen aendern)"
Write-Host "Adresse fuer Benutzer: $($health.Url)"
if ($health.Url -like 'https:*') {
    Write-Host "Arbeitsplaetze: $Base\Firmen-CA.crt einmal als vertrauenswuerdige Stammzertifizierungsstelle importieren (README_Windows.txt 2c)."
} else {
    Write-Host 'HTTPS nicht eingerichtet - empfohlen: HTTPS_Einrichten.ps1 (README_Windows.txt 2c).' -ForegroundColor Yellow
}
Write-Host 'Backups: zweimal taeglich automatisch (12:15 / 22:15) nach backups\'
Write-Host 'Empfehlung: Zweitziel in BACKUP_ZIEL.txt eintragen (z. B. Netzlaufwerk).'
Write-Host "Pruefen mit: $Base\CHECK_LAN_SICHERHEIT.ps1"
