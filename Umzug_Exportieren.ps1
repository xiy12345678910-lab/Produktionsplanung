# Maschinenplanung - Umzug auf ein neues Geraet, Teil 1 (ALTES Geraet, als Administrator).
# Erstellt EINE Datei Umzug_<Firma>_<Datum>.zip mit Datenbank (frisches, geprueftes Backup), config\ (firma.json,
# Logo, lizenz.key, tls\ inkl. Firmen-CA) und BACKUP_ZIEL.txt; Manifest mit Version und SHA256 jeder Datei.
# Danach ist dieses Geraet schreibgeschuetzt (nur Lesen), damit keine Aenderung verloren geht.
#
#   .\Umzug_Exportieren.ps1                    Datei nach backups\umzug\ (nur Administratoren)
#   .\Umzug_Exportieren.ps1 -Ziel E:\          Datei direkt auf einen USB-Stick o. ae.
#   .\Umzug_Exportieren.ps1 -OhneSperre        Probelauf: Server bleibt beschreibbar (spaetere Aenderungen fehlen im Paket)
#   .\Umzug_Exportieren.ps1 -Entsperren        Umzug abgebrochen: Schreibsperre wieder aufheben
# Nichts wird geloescht oder gestoppt. Weiter auf dem neuen Geraet: Umzug_Importieren.ps1 (README_Windows.txt 9).
param([string]$Ziel, [switch]$OhneSperre, [switch]$Entsperren)
$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
Assert-MPAdmin 'Umzug_Exportieren.ps1'

# Gleiche Sperrdatei wie beim Update: der Server lehnt jede Aenderung ab (MP-UPD-003), Lesen bleibt moeglich.
$Lock = Join-Path $Base 'updates\installing'
function Test-MPUmzugLock { return ((Test-Path -LiteralPath $Lock) -and ([IO.File]::ReadAllText($Lock) -match '^UMZUG')) }

if ($Entsperren) {
    if (-not (Test-Path -LiteralPath $Lock)) { Write-Host 'Keine Schreibsperre vorhanden.' -ForegroundColor Green; return }
    if (-not (Test-MPUmzugLock)) { throw 'MP-UMZ-007: Die Sperre stammt von einem laufenden Update (UPDATE_LIVE.ps1) und wird hier nicht entfernt.' }
    Remove-Item -LiteralPath $Lock -Force
    Write-Host 'Schreibsperre aufgehoben - dieses Geraet ist wieder normal beschreibbar.' -ForegroundColor Green
    Write-Host 'Ein bereits erstelltes Umzugspaket ist damit veraltet: bei Bedarf neu exportieren.' -ForegroundColor Yellow
    return
}

if (-not (Test-Path -LiteralPath (Join-Path $Base 'data\maschinenplanung.sqlite3'))) { throw "MP-UMZ-001: Keine Datenbank in $Base\data. Skript im Live-Ordner starten." }
if ((Test-Path -LiteralPath $Lock) -and -not (Test-MPUmzugLock)) { throw 'MP-UMZ-007: Ein Update laeuft gerade (updates\installing). Umzug erst danach.' }
$PythonExe = Get-MPPython
$Version = Get-MPPackageVersion $Base
if (-not $Ziel) {
    # backups\ ist nur fuer SYSTEM/Administratoren lesbar (das Paket enthaelt private HTTPS-Schluessel und alle Daten).
    $Ziel = Join-Path $Base 'backups\umzug'
    New-Item -ItemType Directory -Path $Ziel -Force | Out-Null
}

$LockedNow = $false
if (-not $OhneSperre -and -not (Test-MPUmzugLock)) {
    Write-Host '1/2 Schreibsperre setzen (Benutzer koennen weiter lesen, aber nichts mehr aendern) ...'
    New-Item -ItemType Directory -Path (Split-Path -Parent $Lock) -Force | Out-Null
    Set-MPFolderAcl (Split-Path -Parent $Lock) $false
    [IO.File]::WriteAllText($Lock, "UMZUG V$Version $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
    $LockedNow = $true
    Start-Sleep -Seconds 3   # laufende Speichervorgaenge abschliessen lassen
} elseif ($OhneSperre) {
    Write-Warning 'Probelauf ohne Schreibsperre: Aenderungen nach diesem Zeitpunkt fehlen im Paket.'
}

Write-Host '2/2 Datenbank sichern und Umzugspaket erstellen ...'
$resultFile = Join-Path $env:TEMP ('mp_umzug_' + [guid]::NewGuid().ToString('N') + '.json')
# stderr von Python darf unter 'Stop' keinen Abbruch ausloesen (Windows PowerShell 5.1).
$ErrorActionPreference = 'Continue'
$out = & $PythonExe -X utf8 -I (Join-Path $Base 'Umzug.py') --export --base $Base --ziel $Ziel --ergebnis $resultFile 2>&1
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
$out | ForEach-Object { Write-Host "    $_" }
if ($code -ne 0 -or -not (Test-Path -LiteralPath $resultFile)) {
    if ($LockedNow) { Remove-Item -LiteralPath $Lock -Force -ErrorAction SilentlyContinue }
    throw 'Umzugspaket konnte nicht erstellt werden (Meldung oben). Dieses Geraet laeuft unveraendert weiter.'
}
$r = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
Remove-Item -LiteralPath $resultFile -Force -ErrorAction SilentlyContinue

$inhalt = @('Datenbank', 'Firmenprofil')
if ($r.lizenz) { $inhalt += 'Lizenz' }
if ($r.tls) { $inhalt += 'HTTPS-Zertifikate inkl. Firmen-CA' }
Write-Host ''
Write-Host 'UMZUGSPAKET ERSTELLT' -ForegroundColor Green
Write-Host "Datei:     $($r.datei)"
Write-Host "SHA256:    $($r.sha256)"
Write-Host "Version:   V$($r.appVersion)   Datenstand (Revision): $($r.dbRevision)"
Write-Host "Inhalt:    $($inhalt -join ', ')"
Write-Host 'ACHTUNG: Die Datei enthaelt alle Daten, die Lizenz und private HTTPS-Schluessel. Nur auf einem sicheren' -ForegroundColor Yellow
Write-Host '         Datentraeger transportieren und nach dem Umzug loeschen.' -ForegroundColor Yellow
if (Test-MPUmzugLock) {
    Write-Host 'Dieses Geraet ist jetzt schreibgeschuetzt (nur Lesen). Umzug abbrechen: .\Umzug_Exportieren.ps1 -Entsperren' -ForegroundColor Cyan
}
Write-Host ''
Write-Host 'NAECHSTE SCHRITTE'
Write-Host '  1. Datei auf das neue Geraet kopieren (USB-Stick oder Netzlaufwerk).'
Write-Host "  2. Neues Geraet: Python 3 fuer alle Benutzer installieren, Programmpaket V$($r.appVersion) oder neuer entpacken."
Write-Host '  3. Dort PowerShell als Administrator im entpackten Paketordner:'
Write-Host "       .\Umzug_Importieren.ps1 -Datei <Pfad>\$(Split-Path -Leaf $r.datei) -Sha256 $($r.sha256)"
Write-Host '  4. Wenn das neue Geraet laeuft: hier (altes Geraet) .\Deinstallieren.ps1 ausfuehren.'
