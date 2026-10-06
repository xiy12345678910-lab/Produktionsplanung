# Maschinenplanung - Serverumzug, Schritt 1 (ALTER Server, als Administrator).
#
#   .\Umzug_Export.ps1                      erzeugt Umzug_<Rechner>_V<Version>_<Zeit>.zip in umzug\
#   .\Umzug_Export.ps1 -Ziel D:\Transfer    anderer Ordner (z. B. USB-Stick / Netzlaufwerk)
#   .\Umzug_Export.ps1 -ServerLaufenLassen  Server nicht stoppen (Aenderungen danach gehen NICHT mit um)
#
# Ablauf: Server stoppen -> geprueftes Datenbank-Backup + Firmenconfig -> ein ZIP mit manifest.json.
# Die Live-Daten bleiben unveraendert. Auf dem NEUEN Server: Setup_Windows.ps1, dann Umzug_Import.ps1.
param([string]$Ziel, [switch]$ServerLaufenLassen)
$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
Assert-MPAdmin 'Umzug_Export.ps1'
$PythonExe = Get-MPPython
$Version = Get-MPPackageVersion $Base
if (-not $Ziel) { $Ziel = Join-Path $Base 'umzug' }
New-Item -ItemType Directory -Path $Ziel -Force | Out-Null

if (-not $ServerLaufenLassen) {
    Write-Host '1/3 Server stoppen (damit nach dem Export keine Aenderungen verloren gehen) ...'
    Stop-MPServer $Base
    Wait-MPTaskIdle $MP_TaskName 30 | Out-Null
} else {
    Write-Host '1/3 Server laeuft weiter (Hinweis: spaetere Aenderungen sind nicht im Paket).' -ForegroundColor Yellow
}

Write-Host '2/3 Datenbank und Firmenconfig sichern ...'
$start = Get-Date
$ErrorActionPreference = 'Continue'
$out = & $PythonExe -I (Join-Path $Base 'Backup_Datenbank.py') 2>&1
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
$out | ForEach-Object { Write-Host "    $_" }
if ($code -ne 0 -and $code -ne 2) { throw 'Backup fehlgeschlagen - kein Umzugspaket erzeugt.' }
$db = Get-ChildItem -LiteralPath (Join-Path $Base 'backups') -File -Filter 'maschinenplanung_*.sqlite3' |
    Where-Object { $_.Name -notmatch '_vor_restore' -and $_.LastWriteTime -ge $start.AddSeconds(-2) } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $db) { throw 'Frisches Backup nicht gefunden.' }
$firma = Join-Path $db.DirectoryName (($db.Name -replace '^maschinenplanung_', 'firma_') -replace '\.sqlite3$', '.zip')

Write-Host '3/3 Umzugspaket schreiben ...'
$os = Get-MPOSInfo
$stage = Join-Path $Ziel ('_stage_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    Copy-Item -LiteralPath $db.FullName -Destination $stage
    $hasConfig = Test-Path -LiteralPath $firma
    if ($hasConfig) { Copy-Item -LiteralPath $firma -Destination $stage }
    $hash = (Get-FileHash -LiteralPath $db.FullName -Algorithm SHA256).Hash
    $manifest = [ordered]@{
        format = 1; version = $Version; created = (Get-Date).ToString('s'); computer = $env:COMPUTERNAME
        os = $os.Caption; osBuild = $os.Build; database = $db.Name; databaseSha256 = $hash
        config = $(if ($hasConfig) { Split-Path -Leaf $firma } else { '' })
    }
    ($manifest | ConvertTo-Json) | Set-Content -LiteralPath (Join-Path $stage 'manifest.json') -Encoding UTF8
    $zip = Join-Path $Ziel ("Umzug_{0}_V{1}_{2}.zip" -f $env:COMPUTERNAME, $Version, (Get-Date -Format 'yyyy-MM-dd_HHmmss'))
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host ''
Write-Host "UMZUGSPAKET: $zip" -ForegroundColor Green
Write-Host 'Naechste Schritte: Paket auf den neuen Server kopieren, dort Setup_Windows.ps1 und danach'
Write-Host "  .\Umzug_Import.ps1 -Paket <Pfad zur ZIP-Datei>"
if (-not $ServerLaufenLassen) { Write-Host 'Der alte Server bleibt gestoppt. Bei Bedarf: Start_Server.ps1' -ForegroundColor Yellow }
