# #84: Admin-Button "Update installieren" Ende-zu-Ende unter Windows (windows-latest, Administrator, Windows PowerShell 5.1).
# Weg wie beim Kunden: Browser-API /api/updates/install -> Hintergrundprozess (app_updates.launch_worker) -> run_job
# -> Download/Pruefung des Release-Pakets -> UPDATE_LIVE.ps1 aus dem Paket -> Neustart ueber den SYSTEM-Task.
# Aufruf:  powershell -File tests/ci_windows_update_button.ps1 -Scheme https|http
#
# Ablauf:
#  1. Aktueller Stand mit kuenstlich niedrigerer Version (z. B. 12.27.99) per Setup_Windows.ps1 installieren.
#  2. Eine Aenderung anlegen (Benutzer + Mitarbeiter im Datenstand), per API wie der Browser.
#  3. Test-Release des aktuellen Commits bauen (tools/build_release.py) und ueber eine lokale Nachbildung der
#     GitHub-Release-API (tests/update_button_lib.py serve, nur 127.0.0.1) bereitstellen.
#  4. Server einmal mit MP_UPDATE_TEST_API neu starten (nur Umgebungsvariable dieses Prozesses, siehe
#     app_updates.api_root), als Admin anmelden, /api/updates findet die Version, POST /api/updates/install.
#  5. Warten bis der Worker fertig ist; pruefen: Health = neue Version, Daten erhalten, Audit, Protokoll
#     updates\install-<id>.log mit Erfolg, Status/Lock/Wartungssperre, Server wieder als SYSTEM-Task.
#
# Abweichungen von der Produktion (nur Testkopien, wie tests/ci_windows_deploy.ps1):
#  - Get-MPLanInfo liefert die IP des Runners (kein "physisches Privat-Netz" auf Runnern). Diese Aenderung steckt
#    auch im Test-Release, weil UPDATE_LIVE.ps1 und Run_Server_LAN.ps1 aus dem Paket laufen.
#  - Setup_Windows.ps1 bekommt Benutzer/Passwort statt Read-Host.
#  - Updatequelle ist die lokale Nachbildung statt api.github.com (MP_UPDATE_TEST_API nur fuer diesen einen
#    Serverstart; nach dem Update laeuft der Server wieder normal als SYSTEM-Task ohne diese Variable).
# Exitcode 0 nur, wenn alle Pruefungen bestanden sind, sonst 1.
param(
    [ValidateSet('https', 'http')][string]$Scheme = 'https'
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Work = Join-Path ($(if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP })) 'mpbutton'
Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $Work | Out-Null
$AdminPw = 'Admin-Passwort-CI-1'
$LibPy = Join-Path $Repo 'tests\update_button_lib.py'
$Fail = 0
function Check([bool]$ok, [string]$label) {
    if ($ok) { Write-Host "PASS $label" } else { $script:Fail++; Write-Host "FAIL $label" -ForegroundColor Red }
}
function Utf8Bom([string]$path, [string]$text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($true))) }
function Q([string]$s) { return '"' + $s + '"' }
function Patch-Lan([string]$folder, [string]$ip) {
    $f = Join-Path $folder 'MP_Common.ps1'
    $t = [IO.File]::ReadAllText($f)
    $marker = 'function Get-MPLanInfo {'
    if (-not $t.Contains($marker)) { throw "Get-MPLanInfo nicht gefunden in $f" }
    $inject = "$marker`n    return [PSCustomObject]@{ IP = '$ip'; PrefixLength = 24; InterfaceIndex = 1; Profile = 'Private'; InterfaceAlias = 'CI'; InterfaceDescription = 'CI-Runner' }`n"
    Utf8Bom $f ($t.Replace($marker, $inject))
}
function Patch-Setup([string]$folder) {
    $f = Join-Path $folder 'Setup_Windows.ps1'
    $s = [IO.File]::ReadAllText($f)
    $s = $s.Replace("Read-Host 'Trotzdem fortfahren? (j/N)'", "'j'").Replace("Read-Host 'Erster Admin-Benutzername'", "'admin'")
    $s = $s.Replace("Read-Host 'Admin-Passwort (mind. 8 Zeichen)' -AsSecureString", '(ConvertTo-SecureString $env:MP_CI_PW -AsPlainText -Force)')
    if ($s.Contains('Read-Host')) { throw "Setup_Windows.ps1 in $folder enthaelt unbekannte Abfragen (Read-Host)." }
    Utf8Bom $f $s
    return $f
}
function Run-PS([string]$script, [string[]]$extra, [string]$log) {
    # Wie in der Praxis: Windows PowerShell 5.1, als Administrator
    $args2 = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script) + $extra
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & powershell.exe @args2 *>&1 | Tee-Object -FilePath $log | ForEach-Object { Write-Host $_ }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return $code
}
function Lib([string[]]$LibArgs) {
    # Python-Baustein (PASS/FAIL-Zeilen); Rueckgabe $true nur bei Exitcode 0. Stderr bricht hier nie ab.
    # -X utf8: Quelltexte und Ausgaben unabhaengig von der Windows-Codepage (wie Run_Server_LAN.ps1).
    # Argumente als ein Array (nicht ueber $args): "--url" usw. kommen so in 5.1 und 7 unveraendert bei Python an.
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $script:PyExe -X utf8 $script:LibPy @LibArgs | Out-Host
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return ($code -eq 0)
}
function Health([string]$url) { try { return [string](Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 5).version } catch { return $null } }
function Show-Tail([string]$path, [int]$lines = 80) {
    if ($path -and (Test-Path -LiteralPath $path)) { Write-Host "=== $path"; Get-Content -LiteralPath $path -Tail $lines | ForEach-Object { Write-Host $_ } }
}

$PyExe = (Get-Command python.exe -ErrorAction Stop).Source
$mock = $null
$jobId = $null
$Base = Join-Path $env:ProgramData 'Maschinenplanung'
try {
    $ip = (& $PyExe -c "import sys; sys.path.insert(0, r'$Repo\tests'); import deploy_lib as L; print(L.find_lan_ip())").Trim()
    if (-not $ip -or $ip -eq 'None') { throw 'Keine LAN-IPv4-Adresse des Runners gefunden.' }
    . (Join-Path $Repo 'MP_Common.ps1')
    $newVer = Get-MPPackageVersion $Repo
    Write-Host "Runner-IP $ip, Installationsordner $Base, aktueller Stand V$newVer, Schema $Scheme" -ForegroundColor Cyan
    Check (-not (Test-Path -LiteralPath (Join-Path $Base 'data\maschinenplanung.sqlite3'))) 'Runner ohne bestehende Installation'

    # ---------------- 1. Aktuellen Stand mit kuenstlich niedrigerer Version installieren ----------------
    $old = Join-Path $Work 'old'
    New-Item -ItemType Directory -Path $old | Out-Null
    foreach ($name in $MP_AppFiles) { Copy-MPAppFile $Repo $old $name }
    Patch-Lan $old $ip
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $oldVer = [string](& $PyExe -X utf8 $LibPy lower-version --dir $old | Select-Object -Last 1)
    $code = $LASTEXITCODE; $ErrorActionPreference = $prev
    $oldVer = $oldVer.Trim()
    if ($code -ne 0 -or -not $oldVer -or [version]$oldVer -ge [version]$newVer) { throw "Version konnte nicht abgesenkt werden ($oldVer)." }
    Check ((Get-MPPackageVersion $old) -eq $oldVer) "Installationsstand: aktueller Code mit Version V$oldVer"
    $setup = Patch-Setup $old
    $env:MP_CI_PW = $AdminPw
    $setupArgs = @()
    if ($Scheme -eq 'http') { $setupArgs = @('-OhneHttps') }
    $code = Run-PS $setup $setupArgs (Join-Path $Work 'setup.log')
    Remove-Item Env:MP_CI_PW -ErrorAction SilentlyContinue
    Check ($code -eq 0) "Setup_Windows.ps1 $($setupArgs -join ' ') (Exitcode $code)"
    if ($code -ne 0) { throw 'Installation fehlgeschlagen.' }
    $actualScheme = Get-MPScheme $Base
    Check ($actualScheme -eq $Scheme) "Installation nutzt $actualScheme (erwartet $Scheme)"
    $Url = "${actualScheme}://${ip}:$MP_Port"
    $caArgs = @()
    if ($actualScheme -eq 'https') { $caArgs = @('--cafile', (Join-Path $Base 'Firmen-CA.crt')) }
    Check ((Health $Url) -eq $oldVer) "Server antwortet mit V$oldVer ($Url)"
    # Updatequelle (konto/repository) ohne Releases: Der neue Server prueft nach dem Update (ohne Testquelle) bei
    # GitHub und findet dort sicher nichts; ein echtes neueres Release koennte sonst auf dem Runner installiert werden.
    # Die Nachbildung beantwortet jedes konto/repository.
    $cfgPath = Join-Path $Base 'LAN_CONFIG.json'
    $lan = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
    $lan | Add-Member -NotePropertyName UpdateRepo -NotePropertyValue 'mp-ci-test/kein-release' -Force
    Utf8Bom $cfgPath ($lan | ConvertTo-Json)

    # ---------------- 2. Aenderung vor dem Update ----------------
    Check (Lib (@('seed', '--url', $Url, '--admin-password', $AdminPw) + $caArgs)) 'Aenderung vor dem Update angelegt (Benutzer + Datenstand)'

    # ---------------- 3. Test-Release des aktuellen Commits + GitHub-Nachbildung ----------------
    $commit = ([string](& git -C $Repo rev-parse HEAD)).Trim()
    $plain = Join-Path $Work 'release_plain'
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $PyExe -X utf8 (Join-Path $Repo 'tools\build_release.py') --output $plain | Out-Host
    $code = $LASTEXITCODE; $ErrorActionPreference = $prev
    Check ($code -eq 0 -and (Test-Path -LiteralPath (Join-Path $plain "produktionsplanung-v$newVer.zip"))) "tools/build_release.py baut V$newVer aus dem Repository (Exitcode $code)"
    # Installierbares Testrelease: derselbe Build-Code, Quelle = $MP_AppFiles mit Runner-LAN (siehe Kopf).
    $pkgSrc = Join-Path $Work 'pkgsrc'
    New-Item -ItemType Directory -Path $pkgSrc | Out-Null
    foreach ($name in $MP_AppFiles) { Copy-MPAppFile $Repo $pkgSrc $name }
    Patch-Lan $pkgSrc $ip
    $release = Join-Path $Work 'release'
    Check (Lib @('build', '--root', $pkgSrc, '--output', $release, '--commit', $commit)) "Testrelease V$newVer (Commit $($commit.Substring(0, 12))) gebaut und geprueft"
    $portFile = Join-Path $Work 'mock.port'
    $mockLog = Join-Path $Work 'mock_requests.jsonl'
    $mockArgs = @('-X', 'utf8', (Q $LibPy), 'serve', '--dir', (Q $release), '--port-file', (Q $portFile), '--log', (Q $mockLog))
    $mock = Start-Process -FilePath $PyExe -ArgumentList $mockArgs -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Work 'mock_out.log') -RedirectStandardError (Join-Path $Work 'mock_err.log')
    for ($i = 0; $i -lt 60 -and -not (Test-Path -LiteralPath $portFile) -and -not $mock.HasExited; $i++) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Path -LiteralPath $portFile)) { Show-Tail (Join-Path $Work 'mock_err.log'); throw 'GitHub-Nachbildung startet nicht.' }
    $mockPort = [int]([IO.File]::ReadAllText($portFile).Trim())
    Check ($mockPort -gt 0) "GitHub-Nachbildung auf http://127.0.0.1:$mockPort/repos/"

    # ---------------- 4. Server mit Testquelle neu starten, Button ausloesen ----------------
    $task = Get-ScheduledTask -TaskName $MP_TaskName
    $taskPy = $null
    if ([string]@($task.Actions)[0].Arguments -match '(?i)-PythonExe\s+"([^"]+)"') { $taskPy = $Matches[1] }
    if (-not $taskPy) { throw 'Python des Servertasks nicht ermittelt.' }
    Stop-MPServer $Base
    # Nur dieser Prozessbaum erbt die Variable; der Worker erbt sie vom Server (launch_worker).
    $env:MP_UPDATE_TEST_API = "http://127.0.0.1:$mockPort/repos/"
    $srvArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Q (Join-Path $Base 'Run_Server_LAN.ps1')), '-PythonExe', (Q $taskPy), '-Port', [string]$MP_Port)
    $manual = Start-Process -FilePath (Get-Command powershell.exe).Source -ArgumentList $srvArgs -PassThru -WindowStyle Hidden
    Remove-Item Env:MP_UPDATE_TEST_API -ErrorAction SilentlyContinue
    Check ([bool](Wait-MPHealth $Base $oldVer 60)) "Server V$oldVer mit Testquelle gestartet"
    $jobFile = Join-Path $Work 'job.json'
    $ok = Lib (@('trigger', '--url', $Url, '--admin-password', $AdminPw, '--expect-version', $newVer, '--out', $jobFile) + $caArgs)
    Check $ok "Admin: /api/updates findet V$newVer, POST /api/updates/install startet den Auftrag"
    if (-not $ok) { throw 'Update ueber den Button nicht gestartet.' }
    $jobId = [string](Get-Content -LiteralPath $jobFile -Raw | ConvertFrom-Json).jobId
    if ($jobId -notmatch '^[0-9a-f]{24}$') { throw "Ungueltige Auftrags-ID '$jobId'." }

    # ---------------- 5. Warten und pruefen ----------------
    $ok = Lib (@('wait', '--url', $Url, '--admin-password', $AdminPw, '--expect-version', $newVer, '--job-id', $jobId, '--timeout', '1200') + $caArgs)
    Check $ok 'Hintergrundprozess fertig: Auftrag complete'
    # UPDATE_LIVE.ps1 meldet 'complete' kurz bevor run_job seinen Audit-Eintrag schreibt und das Lock entfernt.
    $lockFile = Join-Path $Base 'updates\update.lock'
    for ($i = 0; $i -lt 60 -and (Test-Path -LiteralPath $lockFile); $i++) { Start-Sleep -Seconds 1 }
    $installLog = Join-Path $Base "updates\install-$jobId.log"
    Check (Test-Path -LiteralPath $installLog) "Protokoll updates\install-$jobId.log vorhanden"
    $logText = ''
    if (Test-Path -LiteralPath $installLog) { $logText = [string](Get-Content -LiteralPath $installLog -Raw) }
    Check ($logText -match ('UPDATE ERFOLGREICH - V' + [regex]::Escape($newVer))) "Protokoll meldet 'UPDATE ERFOLGREICH - V$newVer'"
    Check ($logText -notmatch 'UPDATE FEHLGESCHLAGEN') 'Protokoll ohne Fehlschlag'
    Check ((Health $Url) -eq $newVer) "/api/health meldet V$newVer"
    Check ((Get-MPPackageVersion $Base) -eq $newVer) "Programmdateien im Live-Ordner sind V$newVer"
    $status = Get-Content -LiteralPath (Join-Path $Base 'updates\status.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Check ($status.jobId -eq $jobId -and $status.stage -eq 'complete' -and -not $status.rolledBack) "updates\status.json: Auftrag $jobId complete, kein Rollback"
    Check (-not (Test-Path -LiteralPath $lockFile)) 'updates\update.lock entfernt (Worker beendet)'
    Check (-not (Test-Path -LiteralPath (Join-Path $Base 'updates\installing'))) 'Wartungssperre updates\installing entfernt'
    Check (@(Get-ChildItem -LiteralPath (Join-Path $Base 'update_backups') -Directory -Filter "pre_V$($newVer)_*" -ErrorAction SilentlyContinue).Count -ge 1) "Rollback-Stand update_backups\pre_V$($newVer)_* angelegt"
    Check ($manual.HasExited) 'manuell gestarteter Server (mit Testquelle) wurde vom Updater beendet'
    Check ((Get-ScheduledTask -TaskName $MP_TaskName).State -eq 'Running') 'Servertask laeuft'
    $owner = $null
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort $MP_Port -ErrorAction SilentlyContinue) | Select-Object -First 1
    if ($listener) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
        if ($proc) { $owner = [string](Invoke-CimMethod -InputObject $proc -MethodName GetOwner).User }
    }
    Check ($owner -eq 'SYSTEM') "neuer Server laeuft als SYSTEM (Task, ohne Testquelle; Besitzer '$owner')"
    Check (Lib (@('verify', '--url', $Url, '--admin-password', $AdminPw, '--expect-version', $newVer, '--job-id', $jobId, '--mock-log', $mockLog) + $caArgs)) 'Daten, Audit und Updatestatus nach dem Update'
} catch {
    $script:Fail++
    Write-Host "FAIL Abbruch: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ($_.ScriptStackTrace)
} finally {
    Remove-Item Env:MP_UPDATE_TEST_API -ErrorAction SilentlyContinue
    if ($mock -and -not $mock.HasExited) { Stop-Process -Id $mock.Id -Force -ErrorAction SilentlyContinue }
    if ($Fail -gt 0) {
        if ($jobId) { Show-Tail (Join-Path $Base "updates\install-$jobId.log") 120 }
        Show-Tail (Join-Path $Base 'updates\status.json') 20
        Get-ChildItem -LiteralPath (Join-Path $Base 'logs') -Filter 'server_*.log' -ErrorAction SilentlyContinue | ForEach-Object { Show-Tail $_.FullName 60 }
        Show-Tail (Join-Path $Work 'mock_err.log') 40
    }
}

# Ausdruecklich setzen: der Exitcode eines frueheren (auch absichtlich fehlschlagenden) Aufrufs darf nie durchschlagen (#83).
if ($Fail -gt 0) { Write-Host "$Fail Pruefung(en) fehlgeschlagen" -ForegroundColor Red; exit 1 }
Write-Host 'Alle Pruefungen bestanden' -ForegroundColor Green
exit 0
