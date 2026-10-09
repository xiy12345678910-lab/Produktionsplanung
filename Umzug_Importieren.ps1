# Maschinenplanung - Umzug auf ein neues Geraet, Teil 2 (NEUES Geraet, als Administrator).
# Aus dem entpackten Programmpaket (gleiche oder neuere Version als auf dem alten Geraet) starten:
#
#   .\Umzug_Importieren.ps1 -Datei E:\Umzug_<Firma>_<Datum>.zip [-Sha256 <hash>]
#   .\Umzug_Importieren.ps1 -Datei <zip> -Ueberschreiben     Ziel hat schon Daten: aktueller Stand wird vorher gesichert
#
# Ablauf: Paket pruefen (Manifest, SHA256, Version) -> ohne Installation: Programm aus diesem Paket installieren
#         (kein Setup_Windows.ps1 noetig, kein neuer Admin) / mit Installation: Server stoppen -> Datenbank und
#         config\ (Firmenprofil, Lizenz, tls\ mit Firmen-CA) einspielen -> Ordnerrechte -> Firmen-CA vertrauen ->
#         Tasks -> Start -> Health-Check -> neue Adresse anzeigen.
# Das Serverzertifikat wird beim Start fuer die neue IP automatisch erneuert; die Firmen-CA bleibt gleich, die
# Arbeitsplaetze brauchen keinen neuen CA-Import. Die Lizenz gilt fuer die tenantId in firma.json (nicht fuer die Hardware).
param(
    [Parameter(Mandatory = $true)][string]$Datei,
    [string]$Sha256,
    [switch]$Ueberschreiben
)
$ErrorActionPreference = 'Stop'
$SourceBase = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SourceBase 'MP_Common.ps1')
Assert-MPAdmin 'Umzug_Importieren.ps1'

$Base = $MP_InstallBase
if (-not (Test-Path -LiteralPath $Datei -PathType Leaf)) { throw "MP-UMZ-002: Umzugsdatei nicht gefunden: $Datei" }
$Datei = (Resolve-Path -LiteralPath $Datei).Path
$PythonExe = Get-MPPython
if (-not (Test-MPPythonLocationSafe $PythonExe)) {
    Write-Warning "Python ($PythonExe) ist fuer normale Benutzer beschreibbar. Der Server laeuft als SYSTEM - bitte Python 'fuer alle Benutzer' (C:\Program Files) installieren."
}

function Invoke-MPUmzugPy([string]$Folder, [string[]]$PyArgs) {
    # Ruft Umzug.py auf; Rueckgabe: Ergebnisobjekt oder Ausnahme mit der Meldung von Umzug.py.
    $resultFile = Join-Path $env:TEMP ('mp_umzug_' + [guid]::NewGuid().ToString('N') + '.json')
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = & $PythonExe -X utf8 -I (Join-Path $Folder 'Umzug.py') @PyArgs --ergebnis $resultFile 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    $text = @($out | ForEach-Object { [string]$_ } | Where-Object { $_ -and $_ -ne 'System.Management.Automation.RemoteException' })
    $text | ForEach-Object { Write-Host "    $_" }
    if ($code -ne 0 -or -not (Test-Path -LiteralPath $resultFile)) {
        $msg = ($text | Where-Object { $_ -match 'MP-UMZ-\d{3}' } | Select-Object -First 1)
        if (-not $msg) { $msg = 'MP-UMZ-009: Umzug.py fehlgeschlagen (Meldung oben).' }
        throw ($msg -replace '^FEHLER\s+', '')
    }
    $r = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
    Remove-Item -LiteralPath $resultFile -Force -ErrorAction SilentlyContinue
    return $r
}

# --- 1. Paket pruefen (schreibt nichts) ------------------------------------------------------------
Write-Host '1/7 Umzugspaket pruefen ...'
$verifyArgs = @('--verify', '--datei', $Datei)
if ($Sha256) { $verifyArgs += @('--sha256', $Sha256) }
$pkg = Invoke-MPUmzugPy $SourceBase $verifyArgs
Write-Host "    Paket von V$($pkg.appVersion), Rechner $($pkg.quelle), erstellt $($pkg.erstellt), Revision $($pkg.dbRevision)" -ForegroundColor Green

$installed = Test-Path -LiteralPath (Join-Path $Base 'server.py')
$CodeSource = $SourceBase
if ($installed) {
    # Bestehende Installation: ihr Programm wird verwendet (Update spaeter wie gewohnt mit UPDATE_LIVE.ps1).
    $CodeSource = $Base
    $sameFolder = [IO.Path]::GetFullPath($SourceBase).TrimEnd('\') -ieq [IO.Path]::GetFullPath($Base).TrimEnd('\')
    if (-not $sameFolder -and (Get-MPPackageVersion $SourceBase) -ne (Get-MPPackageVersion $Base)) {
        Write-Warning "Auf diesem Geraet ist bereits V$(Get-MPPackageVersion $Base) installiert; diese wird verwendet (Paketordner wird nicht installiert)."
    }
}
$Version = Get-MPPackageVersion $CodeSource
if (-not $Version) { throw "MP-UMZ-001: server.py mit APP_VERSION fehlt in $CodeSource." }
if ([version]$Version -lt [version]([string]$pkg.appVersion)) {
    throw "MP-UMZ-004: Dieses Programm ist V$Version, das Umzugspaket stammt von V$($pkg.appVersion). Programmpaket V$($pkg.appVersion) oder neuer verwenden. Nichts wurde veraendert."
}
if ((Test-Path -LiteralPath (Join-Path $Base 'data\maschinenplanung.sqlite3')) -and -not $Ueberschreiben) {
    throw "MP-UMZ-005: Auf diesem Geraet gibt es bereits Daten ($Base\data). Nur mit -Ueberschreiben (aktueller Stand wird vorher gesichert). Nichts wurde veraendert."
}
if (Test-Path -LiteralPath (Join-Path $Base 'updates\installing')) {
    if ([IO.File]::ReadAllText((Join-Path $Base 'updates\installing')) -notmatch '^UMZUG') { throw 'MP-UMZ-007: Auf diesem Geraet laeuft gerade ein Update. Umzug erst danach.' }
}
foreach ($name in $MP_AppFiles) {
    if (-not (Test-Path -LiteralPath (Get-MPAppPath $CodeSource $name))) { throw "MP-UMZ-001: Programm unvollstaendig: $name fehlt in $CodeSource." }
}
$lan = Get-MPLanInfo
if (-not $lan) { throw 'MP-UMZ-001: Kein aktives physisches LAN mit Netzwerkprofil Privat oder Domaene gefunden (wie Setup_Windows.ps1). Nichts wurde veraendert.' }
Write-Host "    LAN: $($lan.InterfaceAlias) / $($lan.IP) / $($lan.Profile)" -ForegroundColor Cyan

# --- 2. Programm bereitstellen bzw. Server stoppen ------------------------------------------------------
if ($installed) {
    Write-Host '2/7 Server stoppen ...'
    Stop-MPServer $Base
    Wait-MPTaskIdle $MP_TaskName 30 | Out-Null
} else {
    Write-Host "2/7 Programm V$Version nach $Base installieren ..."
    Install-MPTzdata $PythonExe $SourceBase
    New-Item -ItemType Directory -Path $Base -Force | Out-Null
    foreach ($name in $MP_AppFiles) { Copy-MPAppFile $SourceBase $Base $name }
}
# Ordner vor dem Einspielen sperren: Datenbank und private Schluessel entstehen nie mit Benutzer-Leserecht.
Protect-MPInstall $Base
if ($pkg.tls -or $pkg.ca) { Set-MPFolderAcl (Get-MPTlsDir $Base) $false }

# --- 3. Daten und config\ einspielen ---------------------------------------------------------------
Write-Host '3/7 Datenbank und config\ einspielen ...'
$importArgs = @('--import', '--datei', $Datei, '--base', $Base)
if ($Sha256) { $importArgs += @('--sha256', $Sha256) }
if ($Ueberschreiben) { $importArgs += '--ueberschreiben' }
try {
    $r = Invoke-MPUmzugPy $Base $importArgs
} catch {
    if ($installed) { Start-ScheduledTask -TaskName $MP_TaskName -ErrorAction SilentlyContinue }
    throw
}
if ($r.vorherDb) { Write-Host "    Bisheriger Stand gesichert: $($r.vorherDb)" -ForegroundColor Green }

# --- 4. Rechte und Firmen-CA ----------------------------------------------------------------------------
Write-Host '4/7 Ordnerrechte und Firmen-CA ...'
Protect-MPInstall $Base
if (Test-Path -LiteralPath (Join-Path (Get-MPTlsDir $Base) 'firmen-ca.crt')) {
    try {
        $ca = Publish-MPCa $Base
        Write-Host "    Firmen-CA vertraut (Fingerabdruck $($ca.Thumbprint)) - dieselbe wie auf dem alten Geraet." -ForegroundColor Green
    } catch {
        # Der Health-Check unten zeigt, ob HTTPS trotzdem funktioniert; Abhilfe wie bei HTTPS_Einrichten.ps1.
        Write-Warning "$($_.Exception.Message) - Firmen-CA.crt bei Bedarf von Hand als Stammzertifikat importieren (README_Windows.txt 2c)."
    }
} else {
    Remove-Item -LiteralPath (Join-Path $Base 'Firmen-CA.crt') -Force -ErrorAction SilentlyContinue
}

# --- 5. Tasks und Start ------------------------------------------------------------------------------------
Write-Host '5/7 Tasks registrieren und Server starten ...'
Remove-MPLegacyTasks
Register-MPServerTask $Base $PythonExe
Register-MPBackupTask $Base $PythonExe
Start-ScheduledTask -TaskName $MP_TaskName

# --- 6. Health-Check ----------------------------------------------------------------------------------------
Write-Host '6/7 Health-/Versionscheck (bis 90 s) ...'
$health = Wait-MPHealth $Base $Version 90
if (-not $health) {
    Write-Host '--- letzte Zeilen aus logs\ ---' -ForegroundColor Yellow
    Write-Host (Get-MPLogTail $Base 40)
    $hint = 'Das Umzugspaket ist unveraendert und kann erneut eingespielt werden.'
    if ($r.vorherDb) { $hint = "Vorheriger Stand: .\Restore_Datenbank.ps1 -Datei `"$($r.vorherDb)`" -MitConfig" }
    throw "MP-UMZ-008: Server V$Version antwortet nach dem Import nicht (Log oben). $hint"
}
Assert-MPPrivatePaths $health.Config $health.Url

# --- 7. Einstellungen aus dem Paket ---------------------------------------------------------------------------
Write-Host '7/7 Einstellungen uebernehmen ...'
if ($r.updateRepo) {
    $cfgFile = Join-Path $Base 'LAN_CONFIG.json'
    $cfg = Get-Content -LiteralPath $cfgFile -Raw | ConvertFrom-Json
    if (-not $cfg.UpdateRepo) {
        $cfg | Add-Member -NotePropertyName UpdateRepo -NotePropertyValue ([string]$r.updateRepo) -Force
        [IO.File]::WriteAllText($cfgFile, ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    }
    Write-Host "    UpdateRepo: $($r.updateRepo)"
}
if ($r.backupZielPfad -and -not $r.backupZielErreichbar) {
    Write-Warning "Zweitziel aus BACKUP_ZIEL.txt ist von hier nicht erreichbar: $($r.backupZielPfad). Pfad pruefen; das Computerkonto ($env:COMPUTERNAME`$) braucht dort Schreibrechte."
}

Write-Host ''
Write-Host "UMZUG ABGESCHLOSSEN - Maschinenplanung V$Version laeuft auf diesem Geraet" -ForegroundColor Green
Write-Host "Neue Adresse fuer Benutzer: $($health.Url)" -ForegroundColor Cyan
Write-Host "                       bzw. $((Get-MPScheme $Base))://$($env:COMPUTERNAME):$($health.Config.port)"
Write-Host "Datenstand (Revision): $($r.dbRevision) - Firma: $($r.firma)"
if ($r.lizenz) { Write-Host 'Lizenz: mit umgezogen (gilt fuer die Firma, nicht fuer das Geraet).' }
if ($health.Url -like 'https:*') {
    Write-Host 'Arbeitsplaetze: KEIN neuer Zertifikatsimport noetig (gleiche Firmen-CA). Nur Lesezeichen auf die neue Adresse aendern.'
}
Write-Host 'Danach:' -ForegroundColor Yellow
Write-Host '  - Altes Geraet: .\Deinstallieren.ps1 (stoppt den Server dort; seine Daten bleiben als Reserve liegen).'
Write-Host '  - Umzugsdatei loeschen (enthaelt alle Daten und private Schluessel).'
Write-Host "  - Pruefen mit: $Base\CHECK_LAN_SICHERHEIT.ps1"
