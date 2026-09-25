# Manuelles Online-Backup (laeuft auch automatisch zweimal taeglich als Task "Maschinenplanung Backup").
$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
$PythonExe = Get-MPPython
& $PythonExe -I "$Base\Backup_Datenbank.py"
if ($LASTEXITCODE -eq 2) { Write-Warning 'Lokales Backup OK, Zweitkopie (BACKUP_ZIEL.txt) fehlgeschlagen.'; exit 2 }
if ($LASTEXITCODE -ne 0) { throw 'Backup fehlgeschlagen.' }
Write-Host 'Backup abgeschlossen und geprueft.' -ForegroundColor Green
