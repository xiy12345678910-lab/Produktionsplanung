# Maschinenplanung - Datenbank aus einer Sicherung wiederherstellen (als Administrator).
#
#   .\Restore_Datenbank.ps1                 Auswahl aus den letzten 10 Sicherungen (Enter = neueste)
#   .\Restore_Datenbank.ps1 -Datei <pfad>   bestimmte Sicherung
#   .\Restore_Datenbank.ps1 -MitConfig      zusaetzlich config\ aus dem passenden firma_*.zip (aktuelle Datei vorher als .bak)
#
# Ablauf: Sicherung pruefen -> Server stoppen (mit Nachfassen) -> aktuellen Stand sichern (*_vor_restore)
#         -> zurueckspielen (-wal/-shm entfernt, integrity_check) -> Server starten -> Health-Check.
# Startet der Server danach nicht, wird der Stand vor dem Restore automatisch zurueckgespielt.
param([string]$Datei, [switch]$MitConfig)
$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
Assert-MPAdmin 'Restore_Datenbank.ps1'
$PythonExe = Get-MPPython
$Version = Get-MPPackageVersion $Base
$Backups = Join-Path $Base 'backups'

if (-not $Datei) {
    $list = @(Get-ChildItem -LiteralPath $Backups -File -Filter 'maschinenplanung_*.sqlite3' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 10)
    if (-not $list.Count) { throw "Keine Sicherung in $Backups gefunden." }
    for ($i = 0; $i -lt $list.Count; $i++) {
        Write-Host ("[{0}] {1:dd.MM.yyyy HH:mm}  {2}" -f ($i + 1), $list[$i].LastWriteTime, $list[$i].Name)
    }
    $pick = Read-Host 'Nummer (Enter = 1)'
    $n = if ($pick) { [int]$pick } else { 1 }
    if ($n -lt 1 -or $n -gt $list.Count) { throw 'Ungueltige Auswahl.' }
    $Datei = $list[$n - 1].FullName
}
$Datei = (Resolve-Path -LiteralPath $Datei).Path
Write-Host "Sicherung: $Datei" -ForegroundColor Cyan
if ((Read-Host 'Live-Daten werden ersetzt. Fortfahren? (j/N)') -notmatch '^[jJyY]') { Write-Host 'Abgebrochen.'; return }

$pre = $null
$start = Get-Date
try {
    Write-Host '1/4 Server stoppen ...'
    Stop-MPServer $Base
    Wait-MPTaskIdle $MP_TaskName 30 | Out-Null

    Write-Host '2/4 Zurueckspielen (aktueller Stand wird vorher gesichert) ...'
    # stderr von Python darf unter 'Stop' keinen Abbruch ausloesen (Windows PowerShell 5.1).
    $ErrorActionPreference = 'Continue'
    $out = & $PythonExe -I (Join-Path $Base 'Backup_Datenbank.py') --restore $Datei 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    $out | ForEach-Object { Write-Host "    $_" }
    if ($code -ne 0) { throw 'Wiederherstellung fehlgeschlagen - Live-Datenbank unveraendert.' }
    if ($MitConfig) {
        $zip = Join-Path (Split-Path -Parent $Datei) (([IO.Path]::GetFileName($Datei) -replace '^maschinenplanung_', 'firma_' -replace '\.sqlite3$', '.zip'))
        if (-not (Test-Path -LiteralPath $zip)) { throw "Config-Sicherung fehlt: $zip (Datenbank ist bereits zurueckgespielt)." }
        $ErrorActionPreference = 'Continue'
        $outC = & $PythonExe -I (Join-Path $Base 'Backup_Datenbank.py') --restore-config $zip 2>&1
        $codeC = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        $outC | ForEach-Object { Write-Host "    $_" }
        if ($codeC -ne 0) { throw 'Config-Wiederherstellung fehlgeschlagen.' }
    }
    $pre = Get-ChildItem -LiteralPath $Backups -File -Filter 'maschinenplanung_*_vor_restore.sqlite3' |
        Where-Object { $_.LastWriteTime -ge $start.AddSeconds(-2) } | Sort-Object LastWriteTime -Descending | Select-Object -First 1

    Write-Host '3/4 Server starten ...'
    Start-ScheduledTask -TaskName $MP_TaskName
    Write-Host '4/4 Health-Check (bis 90 s) ...'
    $h = Wait-MPHealth $Base $Version 90
    if (-not $h) { throw 'Server startet mit der zurueckgespielten Datenbank nicht.' }
    Write-Host "WIEDERHERGESTELLT - Server laeuft (V$($h.Health.version)). Alle Browser mit Strg+F5 neu laden." -ForegroundColor Green
    if ($pre) { Write-Host "Stand vor dem Restore: $($pre.FullName)" }
}
catch {
    $err = $_
    Write-Host "FEHLER: $($err.Exception.Message)" -ForegroundColor Red
    if ($pre) {
        Write-Host 'Stand vor dem Restore wird zurueckgespielt ...' -ForegroundColor Yellow
        try { Stop-MPServer $Base } catch { }
        & $PythonExe -I (Join-Path $Base 'Backup_Datenbank.py') --restore $pre.FullName | Out-Null
    }
    Start-ScheduledTask -TaskName $MP_TaskName -ErrorAction SilentlyContinue
    throw $err
}
