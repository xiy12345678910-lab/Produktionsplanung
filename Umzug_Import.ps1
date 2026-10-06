# Maschinenplanung - Serverumzug, Schritt 2 (NEUER Server, als Administrator).
#
#   .\Umzug_Import.ps1 -Paket D:\Transfer\Umzug_ALT_V12.19.0_2026-10-06_120000.zip
#
# Voraussetzung: Auf dem neuen Server ist Setup_Windows.ps1 gelaufen (gleiche oder neuere Version als im Paket).
# Ablauf: Paket pruefen (Manifest, SHA256, Version) -> Datenbank + Firmenconfig nach backups\ -> Restore_Datenbank.ps1 -Ja -MitConfig.
# LAN_CONFIG.json und LAN_ADRESSEN.txt bleiben die des neuen Servers (neue IP). Den Stand vor dem Import sichert Restore als *_vor_restore.
param([Parameter(Mandatory = $true)][string]$Paket)
$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
Assert-MPAdmin 'Umzug_Import.ps1'
Test-MPSupportedOS | Out-Null
$Version = Get-MPPackageVersion $Base
$Paket = (Resolve-Path -LiteralPath $Paket).Path

$tmp = Join-Path $Base ('umzug_tmp_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Write-Host '1/3 Paket pruefen ...'
    Expand-Archive -LiteralPath $Paket -DestinationPath $tmp -Force
    $mf = Join-Path $tmp 'manifest.json'
    if (-not (Test-Path -LiteralPath $mf)) { throw 'manifest.json fehlt - das ist kein Umzugspaket.' }
    $m = Get-Content -LiteralPath $mf -Raw -Encoding UTF8 | ConvertFrom-Json
    $dbName = [string]$m.database
    if ($dbName -notmatch '^maschinenplanung_[0-9_-]+\.sqlite3$') { throw 'Ungueltiger Datenbankname im Manifest.' }
    $dbFile = Join-Path $tmp $dbName
    if (-not (Test-Path -LiteralPath $dbFile)) { throw "Datenbank fehlt im Paket: $dbName" }
    if ((Get-FileHash -LiteralPath $dbFile -Algorithm SHA256).Hash -ne [string]$m.databaseSha256) { throw 'Pruefsumme der Datenbank stimmt nicht - Paket beschaedigt.' }
    if ([version]([string]$m.version) -gt [version]$Version) { throw "Das Paket stammt von V$($m.version), dieser Server hat nur V$Version. Erst auf V$($m.version) oder neuer aktualisieren (UPDATE_LIVE.ps1)." }
    Write-Host "    Paket von $($m.computer), V$($m.version), $($m.created) - in Ordnung."

    Write-Host '2/3 Dateien bereitstellen ...'
    $backups = Join-Path $Base 'backups'
    New-Item -ItemType Directory -Path $backups -Force | Out-Null
    Copy-Item -LiteralPath $dbFile -Destination (Join-Path $backups $dbName) -Force
    $withConfig = $false
    if ([string]$m.config) {
        if ([string]$m.config -notmatch '^firma_[0-9_-]+\.zip$') { throw 'Ungueltiger Config-Name im Manifest.' }
        $cfgFile = Join-Path $tmp ([string]$m.config)
        if (Test-Path -LiteralPath $cfgFile) { Copy-Item -LiteralPath $cfgFile -Destination (Join-Path $backups ([string]$m.config)) -Force; $withConfig = $true }
    }

    Write-Host '3/3 Wiederherstellen ...'
    $restore = Join-Path $Base 'Restore_Datenbank.ps1'
    if ($withConfig) { & $restore -Datei (Join-Path $backups $dbName) -MitConfig -Ja } else { & $restore -Datei (Join-Path $backups $dbName) -Ja }
} finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host ''
Write-Host 'UMZUG ABGESCHLOSSEN. Benutzer oeffnen die NEUE Adresse (siehe Server_Status.ps1); alle Browser mit Strg+F5 neu laden.' -ForegroundColor Green
Write-Host 'Danach den alten Server dauerhaft stillegen (Deinstallieren.ps1), damit nicht zwei Staende entstehen.' -ForegroundColor Yellow
