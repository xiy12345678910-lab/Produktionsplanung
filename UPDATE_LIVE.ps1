# Maschinenplanung - sicherer Live-Updater (versionsunabhaengig)
# Als Administrator aus dem entpackten NEUEN Paketordner starten.
#
# Ablauf: Online-Backup -> Stopp -> finales Backup -> Code sichern -> neue Dateien ->
#         Ordner sperren -> Tasks registrieren -> Start -> Health/Version -> HTTP-Check.
# Bei jedem Fehler: alter Code UND Datenbank aus dem finalen Backup werden zurueckgespielt.
param([string]$UpdateJobPath)
$ErrorActionPreference = 'Stop'
$NewSource = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $NewSource 'MP_Common.ps1')
Assert-MPAdmin 'UPDATE_LIVE.ps1'

$NewVersion = Get-MPPackageVersion $NewSource
if (-not $NewVersion) { throw 'server.py im Updatepaket fehlt oder enthaelt keine APP_VERSION.' }
foreach ($name in $MP_AppFiles) {
    if (-not (Test-Path (Join-Path $NewSource $name))) { throw "Updatepaket unvollstaendig: $name fehlt." }
}
$Stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$TargetBase = $MP_InstallBase

# --- Bestehende Installation ermitteln ------------------------------------------------
$oldTask = $null
foreach ($name in @($MP_TaskName) + $MP_LegacyTaskNames) {
    $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($t) { $oldTask = $t; break }
}
if (-not $oldTask) { throw 'Kein Maschinenplanung-Servertask gefunden. Fuer eine Neuinstallation Setup_Windows.ps1 verwenden.' }
$oldAction = @($oldTask.Actions)[0]
$OldBase = [string]$oldAction.WorkingDirectory
if (-not $OldBase -and [string]$oldAction.Arguments -match '(?i)-File\s+"([^"]*Run_Server_LAN\.ps1)"') { $OldBase = Split-Path -Parent $Matches[1] }
if (-not $OldBase -or -not (Test-Path (Join-Path $OldBase 'data\maschinenplanung.sqlite3'))) { throw "Live-Datenbank nicht gefunden (Ordner: $OldBase)." }
$PythonExe = $null
if ([string]$oldAction.Arguments -match '(?i)-PythonExe\s+"([^"]+)"') { $PythonExe = $Matches[1] }
if (-not $PythonExe -or -not (Test-Path $PythonExe)) { $PythonExe = Get-MPPython }
$migrating = -not ([IO.Path]::GetFullPath($OldBase).TrimEnd('\') -ieq [IO.Path]::GetFullPath($TargetBase).TrimEnd('\'))

# The browser starts a detached worker; this file only accepts its fixed private status path.
if ($UpdateJobPath -and [IO.Path]::GetFullPath($UpdateJobPath) -ine [IO.Path]::GetFullPath((Join-Path $OldBase 'updates\status.json'))) { throw 'MP-UPD-001: Ungueltiger Updateauftrag.' }
function Set-MPUpdateStage([string]$Stage, [string]$ErrorText = '', [bool]$RolledBack = $false) {
    if (-not $UpdateJobPath) { return }
    $job = Get-Content -LiteralPath $UpdateJobPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $job | Add-Member -NotePropertyName stage -NotePropertyValue $Stage -Force
    $job | Add-Member -NotePropertyName error -NotePropertyValue $ErrorText -Force
    $job | Add-Member -NotePropertyName rolledBack -NotePropertyValue $RolledBack -Force
    $tmp = "$UpdateJobPath.ps.tmp"
    [IO.File]::WriteAllText($tmp, ($job | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false)))
    if (Test-Path -LiteralPath $UpdateJobPath) { [IO.File]::Replace($tmp, $UpdateJobPath, [NullString]::Value) } else { [IO.File]::Move($tmp, $UpdateJobPath) }
}

# --- Downgrade-Schutz -----------------------------------------------------------------
$running = Get-MPHealth $OldBase
if ($running -and $running.Health -and $running.Health.version) {
    if ([version]([string]$running.Health.version) -gt [version]$NewVersion) {
        throw "Abbruch: Live laeuft V$($running.Health.version), Paket ist aelter (V$NewVersion). Downgrades zerstoeren neuere Datenstaende."
    }
}

Write-Host "Updatepaket: V$NewVersion ($NewSource)" -ForegroundColor Cyan
Write-Host "Live-Ordner: $OldBase$(if($migrating){" -> $TargetBase"})" -ForegroundColor Cyan
Write-Host "Python:      $PythonExe" -ForegroundColor DarkGray
if (-not (Test-MPPythonLocationSafe $PythonExe)) { Write-Warning 'Python-Ordner ist fuer normale Benutzer beschreibbar. Empfehlung: Python fuer alle Benutzer (C:\Program Files) installieren und Update erneut ausfuehren.' }

$FinalBackup = $null
$RollbackCode = $null
$LiveTouched = $false
$Maintenance = Join-Path $TargetBase 'updates\installing'
$UpdateMutex = New-Object Threading.Mutex($false, 'Global\MaschinenplanungUpdate')
$HaveMutex = $false
try {
    try { $HaveMutex = $UpdateMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $HaveMutex = $true }
    if (-not $HaveMutex) { throw 'MP-UPD-002: Ein anderes Update laeuft bereits.' }
    Set-MPUpdateStage 'backup'
    Write-Host '1/9 Online-Datenbankbackup ...'
    # Use this package's backup code: legacy installations may not yet back up firma.json.
    & $PythonExe -I (Join-Path $NewSource 'Backup_Datenbank.py') --base $OldBase
    if ($LASTEXITCODE -notin @(0, 2)) { throw 'Online-Datenbankbackup fehlgeschlagen.' }

    Write-Host '2/9 Vorabtest: neue Version mit einer Datenkopie starten (Live bleibt unberuehrt) ...'
    $preCfg = Read-MPConfig $OldBase
    Set-MPUpdateStage 'preflight'
    if ($preCfg) {
        $preDb = Get-ChildItem (Join-Path $OldBase 'backups') -File -Filter 'maschinenplanung_*.sqlite3' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $pre = Invoke-MPPreflight $NewSource $PythonExe $preDb.FullName $preCfg $NewVersion $OldBase
        if (-not $pre.Ok) {
            Write-Host '--- Ausgabe des Vorabtests ---' -ForegroundColor Yellow
            Write-Host $pre.Log
            if ($pre.Log -match 'MP-CFG-006') {
                $zielHint = ''
                if ($migrating) { $zielHint = " -Ziel '$OldBase'" }
                throw "MP-CFG-006: Bestand ohne Firmenprofil (config\firma.json fehlt, Name/Logo nicht in den Daten). Live-System wurde NICHT veraendert. Zuerst als Administrator: .\Firma_Einrichten.ps1 -Vorlage <datei>$zielHint (oder -Neutral fuer bewusst neutral), danach UPDATE_LIVE.ps1 erneut starten."
            }
            throw "Vorabtest fehlgeschlagen: V$NewVersion startet mit einer Kopie der Live-Daten nicht. Live-System wurde NICHT veraendert."
        }
        Write-Host "    PASS: V$NewVersion startet mit Datenkopie (Testport $($pre.Port))." -ForegroundColor Green
    } else {
        throw 'MP-UPD-004: LAN_CONFIG.json fehlt oder ist nicht lesbar. Vorabtest ist verpflichtend.'
    }

    Write-Host '3/9 Live-Server stoppen ...'
    New-Item -ItemType Directory -Path (Split-Path -Parent $Maintenance) -Force | Out-Null
    Set-MPFolderAcl (Split-Path -Parent $Maintenance) $false
    [IO.File]::WriteAllText($Maintenance, $NewVersion)
    $LiveTouched = $true
    Set-MPUpdateStage 'backup'
    Stop-MPServer $OldBase

    Write-Host '4/9 Finales Backup nach Stopp ...'
    & $PythonExe -I (Join-Path $NewSource 'Backup_Datenbank.py') --base $OldBase
    if ($LASTEXITCODE -notin @(0, 2)) { throw 'Finales Datenbankbackup fehlgeschlagen.' }
    $FinalBackup = Get-ChildItem (Join-Path $OldBase 'backups') -File -Filter 'maschinenplanung_*.sqlite3' |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $FinalBackup) { throw 'Finales Datenbankbackup wurde nicht gefunden.' }
    Write-Host "    DB-Sicherung: $($FinalBackup.FullName)" -ForegroundColor Green

    if (Test-Path -LiteralPath (Join-Path $OldBase 'config\firma.json')) {
        $cfgBackup = $FinalBackup.FullName.Replace('maschinenplanung_', 'firma_').Replace('.sqlite3', '.zip')
        if (-not (Test-Path -LiteralPath $cfgBackup)) { throw 'MP-UPD-004: Firmenprofil-Backup fehlt.' }
        & $PythonExe -I -c "import zipfile,sys; z=zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None and 'firma.json' in z.namelist()" $cfgBackup
        if ($LASTEXITCODE -ne 0) { throw 'MP-UPD-004: Firmenprofil-Backup ist ungueltig.' }
    }

    Write-Host '5/9 Bisherigen Programmstand sichern ...'
    New-Item -ItemType Directory -Path (Join-Path $TargetBase 'update_backups') -Force | Out-Null
    $RollbackCode = Join-Path $TargetBase "update_backups\pre_V$($NewVersion)_$Stamp"
    New-Item -ItemType Directory -Path $RollbackCode -Force | Out-Null
    Get-ChildItem -LiteralPath $OldBase -File | Copy-Item -Destination $RollbackCode -Force
    if (Test-Path -LiteralPath (Join-Path $OldBase $MP_ConfigDir)) {
        Copy-Item -LiteralPath (Join-Path $OldBase $MP_ConfigDir) -Destination (Join-Path $RollbackCode $MP_ConfigDir) -Recurse -Force
    }
    Copy-Item -LiteralPath $FinalBackup.FullName -Destination (Join-Path $RollbackCode 'maschinenplanung_vor_update.sqlite3') -Force
    Write-Host "    Rollback-Stand: $RollbackCode" -ForegroundColor Green

    Set-MPUpdateStage 'installation'
    Write-Host '6/9 Dateien uebernehmen ...'
    if ($migrating) {
        foreach ($sub in @('data', 'backups')) { New-Item -ItemType Directory -Path (Join-Path $TargetBase $sub) -Force | Out-Null }
        Copy-Item -Path (Join-Path $OldBase 'backups\*') -Destination (Join-Path $TargetBase 'backups') -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath (Join-Path $OldBase $MP_ConfigDir)) {
            Copy-Item -LiteralPath (Join-Path $OldBase $MP_ConfigDir) -Destination $TargetBase -Recurse -Force
        }
        foreach ($f in @('LAN_CONFIG.json', 'LAN_ADRESSEN.txt', 'BACKUP_ZIEL.txt')) {
            if (Test-Path (Join-Path $OldBase $f)) { Copy-Item (Join-Path $OldBase $f) (Join-Path $TargetBase $f) -Force }
        }
        # Nie eine alte -wal/-shm neben einer neu eingespielten Datenbank liegen lassen.
        Remove-Item (Join-Path $TargetBase 'data\maschinenplanung.sqlite3-wal'), (Join-Path $TargetBase 'data\maschinenplanung.sqlite3-shm') -Force -ErrorAction SilentlyContinue
        Copy-Item -LiteralPath $FinalBackup.FullName -Destination (Join-Path $TargetBase 'data\maschinenplanung.sqlite3') -Force
    }
    foreach ($name in $MP_AppFiles) {
        $src = Join-Path $NewSource $name
        $dst = Join-Path $TargetBase $name
        if ([IO.Path]::GetFullPath($src) -ine [IO.Path]::GetFullPath($dst)) { Copy-Item -LiteralPath $src -Destination $dst -Force }
    }
    foreach ($name in $MP_ObsoleteFiles) { Remove-Item -LiteralPath (Join-Path $TargetBase $name) -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath (Join-Path $TargetBase '__pycache__') -Recurse -Force -ErrorAction SilentlyContinue

    Write-Host '7/9 Ordner sperren, Zeitzonendaten, Tasks ...'
    Protect-MPInstall $TargetBase
    Install-MPTzdata $PythonExe $TargetBase
    Remove-MPLegacyTasks
    if (-not (Wait-MPTaskIdle $MP_TaskName 30)) { Write-Warning 'Alte Task-Instanz meldet noch "Running" - Start wird trotzdem versucht.' }
    Register-MPServerTask $TargetBase $PythonExe
    Register-MPBackupTask $TargetBase $PythonExe
    Set-MPUpdateStage 'migration'
    Start-ScheduledTask -TaskName $MP_TaskName
    Set-MPUpdateStage 'restart'

    Set-MPUpdateStage 'healthcheck'
    Write-Host '8/9 Health-/Versionscheck (bis 90 s) ...'
    $health = Wait-MPHealth $TargetBase $NewVersion 90
    if (-not $health) {
        $state = (Get-ScheduledTask -TaskName $MP_TaskName -ErrorAction SilentlyContinue).State
        Write-Host "--- Task-Status: $state · letzte Zeilen aus logs\ ---" -ForegroundColor Yellow
        Write-Host (Get-MPLogTail $TargetBase 40)
        throw "Server V$NewVersion antwortet nicht (Start ueber den Task fehlgeschlagen, siehe Log oben)."
    }
    Write-Host "    PASS: $($health.Url) / V$($health.Health.version)" -ForegroundColor Green

    Write-Host '9/9 Sicherheitscheck private Dateien ...'
    Assert-MPPrivatePaths $health.Config $health.Url
    Write-Host '    PASS: Programm-, Daten- und Backupdateien sind nicht per HTTP abrufbar.' -ForegroundColor Green

    # V12.10.2: nur die letzten 5 Update-Sicherungen behalten (enthalten je eine volle Datenbankkopie).
    Get-ChildItem -LiteralPath (Join-Path $TargetBase 'update_backups') -Directory -Filter 'pre_V*' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 5 |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

    Write-Host ''
    Write-Host "UPDATE ERFOLGREICH - V$NewVersion" -ForegroundColor Green
    Write-Host "Live-Ordner:     $TargetBase"
    Write-Host "Rollback-Stand:  $RollbackCode"
    if ($migrating) { Write-Host "Alter Ordner bleibt unveraendert: $OldBase (vorerst nicht loeschen)" -ForegroundColor Yellow }
    Remove-Item -LiteralPath $Maintenance -Force -ErrorAction SilentlyContinue
    Set-MPUpdateStage 'complete'
    Write-Host 'Alle Browser-Tabs einmal mit Strg+F5 neu laden. Danach CHECK_LAN_SICHERHEIT.ps1 ausfuehren.' -ForegroundColor Cyan
}
catch {
    $err = $_
    Write-Host ''
    Write-Host "UPDATE FEHLGESCHLAGEN: $($err.Exception.Message)" -ForegroundColor Red
    if (-not $HaveMutex) { throw $err }
    if (-not $LiveTouched) {
        Set-MPUpdateStage 'failed' $err.Exception.Message
        Write-Host 'Live-System wurde nicht veraendert; kein Rollback noetig.' -ForegroundColor Yellow
        throw $err
    }
    Set-MPUpdateStage 'rollback' $err.Exception.Message
    Write-Host 'Rollback wird ausgefuehrt ...' -ForegroundColor Yellow
    try { Stop-MPServer $TargetBase } catch { Stop-ScheduledTask -TaskName $MP_TaskName -ErrorAction SilentlyContinue }
    if (-not $migrating -and $RollbackCode -and (Test-Path $RollbackCode)) {
        Get-ChildItem -LiteralPath $RollbackCode -File | Where-Object { $_.Name -ne 'maschinenplanung_vor_update.sqlite3' } |
            Copy-Item -Destination $TargetBase -Force
        if (Test-Path -LiteralPath (Join-Path $RollbackCode $MP_ConfigDir)) {
            # Retain the directory and replace its contents from the verified saved profile.
            Get-ChildItem -LiteralPath (Join-Path $TargetBase $MP_ConfigDir) -Force | Remove-Item -Recurse -Force
            Copy-Item -LiteralPath (Join-Path $RollbackCode $MP_ConfigDir) -Destination $TargetBase -Recurse -Force
        }
        foreach ($name in $MP_AppFiles) {
            if (-not (Test-Path (Join-Path $RollbackCode $name))) { Remove-Item -LiteralPath (Join-Path $TargetBase $name) -Force -ErrorAction SilentlyContinue }
        }
        if ($FinalBackup) {
            # Eine evtl. bereits migrierte Datenbank durch den Stand vor dem Update ersetzen.
            Remove-Item (Join-Path $TargetBase 'data\maschinenplanung.sqlite3-wal'), (Join-Path $TargetBase 'data\maschinenplanung.sqlite3-shm') -Force -ErrorAction SilentlyContinue
            Copy-Item -LiteralPath (Join-Path $RollbackCode 'maschinenplanung_vor_update.sqlite3') -Destination (Join-Path $TargetBase 'data\maschinenplanung.sqlite3') -Force
        }
    }
    $MP_FirewallRules | ForEach-Object { Remove-NetFirewallRule -DisplayName $_ -ErrorAction SilentlyContinue }
    # Urspruenglichen Servertask exakt wiederherstellen.
    if ($oldTask.TaskName -ne $MP_TaskName) { Unregister-ScheduledTask -TaskName $MP_TaskName -Confirm:$false -ErrorAction SilentlyContinue }
    $action = New-ScheduledTaskAction -Execute ([string]$oldAction.Execute) -Argument ([string]$oldAction.Arguments) -WorkingDirectory $OldBase
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $oldTask.TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Wait-MPTaskIdle $oldTask.TaskName 30 | Out-Null
    Start-ScheduledTask -TaskName $oldTask.TaskName -ErrorAction SilentlyContinue
    $oldVersion = Get-MPPackageVersion $OldBase
    $restored = Wait-MPHealth $OldBase $oldVersion 90
    if ($restored) {
        Remove-Item -LiteralPath $Maintenance -Force -ErrorAction SilentlyContinue
        Set-MPUpdateStage 'failed' $err.Exception.Message $true
    } else {
        Set-MPUpdateStage 'failed' ('Rollback-Healthcheck fehlgeschlagen. ' + $err.Exception.Message) $false
        throw 'MP-UPD-005: Rollback-Healthcheck fehlgeschlagen. Wartungssperre bleibt aktiv; IT muss den gesicherten Stand wiederherstellen.'
    }
    Write-Host "Alter Stand wieder gestartet (Task '$($oldTask.TaskName)', Ordner $OldBase)." -ForegroundColor Yellow
    throw $err
}

finally {
    if ($HaveMutex) { $UpdateMutex.ReleaseMutex() }
    $UpdateMutex.Dispose()
}
