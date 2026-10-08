# Echtes Windows-Deploy in CI (windows-latest, Administrator): alten Stand mit seinem eigenen Setup_Windows.ps1 installieren,
# Testdaten per API anlegen, mit dem aktuellen UPDATE_LIVE.ps1 aktualisieren, Health + Datenvergleich (tests/deploy_lib.py).
# Aufruf:  ./tests/ci_windows_deploy.ps1 -OldCommit dac0860 -Scenario ci|noci
#
# Nur diese Testumgebung weicht von der Produktion ab (Live-Skripte bleiben unveraendert, es wird nur eine KOPIE gepatcht):
#  - Get-MPLanInfo liefert die IP des Runners (Runner haben kein "physisches Privat-Netz"),
#  - Setup_Windows.ps1 des alten Stands bekommt Benutzer/Passwort statt Read-Host.
# Abschnitt 8 (#52) installiert danach den AKTUELLEN Stand neu: -OhneHttps (HTTP), erneutes Setup auf einer
# bestehenden HTTP-Installation (bleibt HTTP) und eine echte Neuinstallation (HTTPS als Standard).
param(
    [Parameter(Mandatory = $true)][string]$OldCommit,
    [ValidateSet('ci', 'noci')][string]$Scenario = 'ci'
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Work = Join-Path ($(if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP })) 'mpdeploy'
Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $Work | Out-Null
$AdminPw = 'Admin-Passwort-CI-1'
$Fail = 0
function Check([bool]$ok, [string]$label) {
    if ($ok) { Write-Host "PASS $label" } else { $script:Fail++; Write-Host "FAIL $label" -ForegroundColor Red }
}
function Utf8Bom([string]$path, [string]$text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($true))) }
function Patch-Lan([string]$folder, [string]$ip) {
    $f = Join-Path $folder 'MP_Common.ps1'
    $t = [IO.File]::ReadAllText($f)
    $marker = 'function Get-MPLanInfo {'
    if (-not $t.Contains($marker)) { throw "Get-MPLanInfo nicht gefunden in $f" }
    $inject = "$marker`n    return [PSCustomObject]@{ IP = '$ip'; PrefixLength = 24; InterfaceIndex = 1; Profile = 'Private'; InterfaceAlias = 'CI'; InterfaceDescription = 'CI-Runner' }`n"
    Utf8Bom $f ($t.Replace($marker, $inject))
}
function Patch-Setup([string]$folder) {
    # Setup_Windows.ps1 ohne Rueckfragen: Benutzer/Passwort statt Read-Host (nur in dieser Testkopie).
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
function Py { & python (Join-Path $Repo 'tests\deploy_lib.py') @args | Out-Host; if ($LASTEXITCODE -ne 0) { return $false } return $true }
function Health([string]$url) { try { return (Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 5).version } catch { return $null } }
function Hashes([string]$dir) {
    $h = @{}
    if (Test-Path -LiteralPath $dir) {
        Get-ChildItem -LiteralPath $dir -Recurse -File | ForEach-Object { $h[$_.FullName.Substring($dir.Length).TrimStart('\')] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    }
    return $h
}
function Same-Hashes($a, $b) {
    if ($a.Count -ne $b.Count) { return $false }
    foreach ($k in $a.Keys) { if ($a[$k] -ne $b[$k]) { return $false } }
    return $true
}

$ip = (& python -c "import sys; sys.path.insert(0, r'$Repo\tests'); import deploy_lib as L; print(L.find_lan_ip())").Trim()
if (-not $ip -or $ip -eq 'None') { throw 'Keine LAN-IPv4-Adresse des Runners gefunden.' }
$Base = Join-Path $env:ProgramData 'Maschinenplanung'
$Url = "http://${ip}:8765"
Write-Host "Runner-IP $ip, Installationsordner $Base, alter Stand $OldCommit, Szenario $Scenario" -ForegroundColor Cyan

# ---------------- 1. Alten Stand installieren (sein eigenes Setup_Windows.ps1) ----------------
$old = Join-Path $Work 'old'
New-Item -ItemType Directory -Path $old | Out-Null
$zip = Join-Path $Work 'old.zip'
& git -C $Repo archive --format=zip -o $zip $OldCommit
if ($LASTEXITCODE -ne 0) { throw "git archive $OldCommit fehlgeschlagen (fetch-depth: 0 gesetzt?)" }
Expand-Archive -LiteralPath $zip -DestinationPath $old
Patch-Lan $old $ip
$setup = Patch-Setup $old
$env:MP_CI_PW = $AdminPw
$code = Run-PS $setup @() (Join-Path $Work 'setup.log')
Check ($code -eq 0) "alter Stand $OldCommit installiert (Setup_Windows.ps1, Exitcode $code)"
if ($code -ne 0) { exit 1 }
$oldVer = Health $Url
Check ([bool]$oldVer) "alter Server antwortet (V$oldVer)"

# ---------------- 2. Testdaten per API, Zusatzdateien, Backups ----------------
Check (Py seed --url $Url --admin-password $AdminPw --scenario $Scenario --out (Join-Path $Work 'seed.json')) 'Testdaten per API angelegt'
$cfgPath = Join-Path $Base 'LAN_CONFIG.json'
$lan = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
$lan | Add-Member -NotePropertyName UpdateRepo -NotePropertyValue 'konto/repo' -Force
Utf8Bom $cfgPath ($lan | ConvertTo-Json)
$second = Join-Path $Work 'backup_zweitziel'
New-Item -ItemType Directory -Path $second | Out-Null
Utf8Bom (Join-Path $Base 'BACKUP_ZIEL.txt') ($second + "`r`n")
& python (Join-Path $Base 'Backup_Datenbank.py')
Check ($LASTEXITCODE -in 0, 2) 'altes Backup-Skript legt Backups an'
Check (Py fingerprint --url $Url --seed (Join-Path $Work 'seed.json') --out (Join-Path $Work 'fp_old.json')) 'Fingerabdruck vor dem Update'
$bakBefore = Hashes (Join-Path $Base 'backups')
$zielBefore = (Get-FileHash -LiteralPath (Join-Path $Base 'BACKUP_ZIEL.txt')).Hash
$hadCfg = Test-Path -LiteralPath (Join-Path $Base 'config\firma.json')
Check ($bakBefore.Count -gt 0) "backups\ enthaelt $($bakBefore.Count) Dateien"

# ---------------- 3. Neues Paket = $MP_AppFiles des aktuellen Stands ----------------
. (Join-Path $Repo 'MP_Common.ps1')

# ---- 3a. Unterordner-Helfer (#73): Pfad, Kopieren, Dateiliste alter Staende, Backup/Rollback wie UPDATE_LIVE.ps1 ----
$sub = Join-Path $Work 'subtest'
$subSrc = Join-Path $sub 'pkg'; $subLive = Join-Path $sub 'live'; $subRb = Join-Path $sub 'rb'
foreach ($d in @($subSrc, $subLive, $subRb)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
Check ((Get-MPAppPath $sub 'core/x.py') -eq (Join-Path (Join-Path $sub 'core') 'x.py') -and -not (Test-MPAppName 'config/x.py') -and -not (Test-MPAppName 'a/b/c.py') -and -not (Test-MPAppName '..\x.py')) 'Get-MPAppPath/Test-MPAppName: core/x.py ok, config/, zwei Ebenen und .. abgewiesen'
New-Item -ItemType Directory -Path (Join-Path $subSrc 'core') | Out-Null
[IO.File]::WriteAllBytes((Join-Path $subSrc 'core\x.py'), [byte[]](0, 1, 2, 255, 13, 10))
Copy-MPAppFile $subSrc $subLive 'core/x.py'
Check (Test-Path -LiteralPath (Join-Path $subLive 'core\x.py') -PathType Leaf) 'Copy-MPAppFile legt core\ im Ziel an'
$oldCommon = (& git -C $Repo show 'v12.22.0:MP_Common.ps1') -join "`n"
if ($LASTEXITCODE -ne 0 -or -not $oldCommon) { $oldCommon = [IO.File]::ReadAllText((Join-Path $Repo 'MP_Common.ps1')); $oldIsCurrent = $true } else { $oldIsCurrent = $false }
$oldDir = Join-Path $sub 'oldcommon'
New-Item -ItemType Directory -Path $oldDir | Out-Null
[IO.File]::WriteAllText((Join-Path $oldDir 'MP_Common.ps1'), $oldCommon)
$oldList = @(Get-MPAppFileList $oldDir)
Check ($oldList.Count -gt 0 -and $oldList -contains 'server.py' -and (-not $oldIsCurrent -or $oldList.Count -eq @($MP_AppFiles).Count)) "Get-MPAppFileList liest die Dateiliste eines alten MP_Common.ps1 ($($oldList.Count) Dateien, aktuell $(@($MP_AppFiles).Count))"
# Backup/Rollback wie UPDATE_LIVE.ps1: live hat core\x.py (alt) und server.py; das Update ueberschreibt core\x.py,
# fuegt core\neu.py hinzu und legt __pycache__ an; der Rollback muss den alten Stand byte-genau herstellen.
[IO.File]::WriteAllText((Join-Path $subLive 'server.py'), 'alt')
foreach ($name in @($oldList + @('core/x.py', 'core/neu.py') | Where-Object { $_.Contains('/') -and (Test-MPAppName $_) } | Sort-Object -Unique)) {
    if (Test-Path -LiteralPath (Get-MPAppPath $subLive $name) -PathType Leaf) { Copy-MPAppFile $subLive $subRb $name }
}
$hashBefore = (Get-FileHash -LiteralPath (Join-Path $subLive 'core\x.py')).Hash
[IO.File]::WriteAllBytes((Join-Path $subSrc 'core\x.py'), [byte[]](9, 9, 9)); [IO.File]::WriteAllText((Join-Path $subSrc 'core\neu.py'), 'neu')
foreach ($name in @('core/x.py', 'core/neu.py')) { Copy-MPAppFile $subSrc $subLive $name }
Check ((Get-FileHash -LiteralPath (Join-Path $subLive 'core\x.py')).Hash -ne $hashBefore) 'Simulation: Update hat core\x.py geaendert'
# Rollback-Schleife (Auszug aus UPDATE_LIVE.ps1)
Get-ChildItem -LiteralPath $subRb -Directory | Where-Object { $_.Name -ne $MP_ConfigDir } | ForEach-Object {
    $s = $_.Name
    foreach ($file in @(Get-ChildItem -LiteralPath $_.FullName -File)) { Copy-MPAppFile $subRb $subLive "$s/$($file.Name)" }
}
New-Item -ItemType Directory -Path (Join-Path $subLive 'core\__pycache__') | Out-Null
foreach ($name in @('core/x.py', 'core/neu.py')) {
    if (-not (Test-Path -LiteralPath (Get-MPAppPath $subRb $name))) {
        $neu = Get-MPAppPath $subLive $name
        Remove-Item -LiteralPath $neu -Force -ErrorAction SilentlyContinue
        $parent = Split-Path -Parent $neu
        if ($name.Contains('/')) { Remove-Item -LiteralPath (Join-Path $parent '__pycache__') -Recurse -Force -ErrorAction SilentlyContinue }
        if ($name.Contains('/') -and (Test-Path -LiteralPath $parent) -and -not (Get-ChildItem -LiteralPath $parent -Force)) { Remove-Item -LiteralPath $parent -Force -ErrorAction SilentlyContinue }
    }
}
Check ((Get-FileHash -LiteralPath (Join-Path $subLive 'core\x.py')).Hash -eq $hashBefore -and -not (Test-Path -LiteralPath (Join-Path $subLive 'core\neu.py'))) 'Rollback stellt core\x.py byte-genau her und entfernt core\neu.py'
Remove-Item -LiteralPath (Join-Path $subLive 'core') -Recurse -Force
foreach ($name in @('core/neu.py')) { Copy-MPAppFile $subSrc $subLive $name }
$neu = Get-MPAppPath $subLive 'core/neu.py'; Remove-Item -LiteralPath $neu -Force
New-Item -ItemType Directory -Path (Join-Path $subLive 'core\__pycache__') | Out-Null
Remove-Item -LiteralPath (Join-Path (Split-Path -Parent $neu) '__pycache__') -Recurse -Force
if (-not (Get-ChildItem -LiteralPath (Split-Path -Parent $neu) -Force)) { Remove-Item -LiteralPath (Split-Path -Parent $neu) -Force }
Check (-not (Test-Path -LiteralPath (Join-Path $subLive 'core'))) 'Rollback entfernt den neuen Ordner core\ (mit __pycache__), wenn er leer wird'
$new = Join-Path $Work 'new'
New-Item -ItemType Directory -Path $new | Out-Null
foreach ($name in $MP_AppFiles) { Copy-MPAppFile $Repo $new $name }
Patch-Lan $new $ip
$newVer = Get-MPPackageVersion $new
$template = Join-Path $Work 'vorlage.json'
Copy-Item -LiteralPath (Join-Path $Repo 'tools\legacy_employer_seed.json') -Destination $template

if ($Scenario -eq 'noci' -and -not $hadCfg) {
    $code = Run-PS (Join-Path $new 'UPDATE_LIVE.ps1') @() (Join-Path $Work 'update_stop.log')
    $txt = Get-Content -LiteralPath (Join-Path $Work 'update_stop.log') -Raw
    Check ($code -ne 0 -and $txt -match 'MP-CFG-006') 'Bestand ohne Firmenprofil: UPDATE_LIVE bricht mit MP-CFG-006 ab'
    Check ($txt -match 'NICHT veraendert') 'Meldung nennt: Live-System unveraendert und was zu tun ist (Firma_Einrichten.ps1)'
    Check ((Health $Url) -eq $oldVer) "alter Server laeuft unveraendert weiter (V$oldVer)"
    Check (-not (Test-Path (Join-Path $Base 'config\firma.json'))) 'keine neutrale firma.json geschrieben'
    Check (Py verify --url $Url --seed (Join-Path $Work 'seed.json') --fingerprint (Join-Path $Work 'fp_old.json') --no-company) 'Daten nach dem abgebrochenen Update unveraendert'
    $code = Run-PS (Join-Path $new 'Firma_Einrichten.ps1') @('-Vorlage', $template) (Join-Path $Work 'firma.log')
    Check ($code -eq 0 -and (Test-Path (Join-Path $Base 'config\firma.json'))) 'Firma_Einrichten.ps1 -Vorlage legt config\firma.json an'
}

# ---------------- 4. Update ----------------
$code = Run-PS (Join-Path $new 'UPDATE_LIVE.ps1') @() (Join-Path $Work 'update1.log')
Check ($code -eq 0) "UPDATE_LIVE.ps1 erfolgreich (Exitcode $code)"
if ($code -ne 0) { exit 1 }
Check ((Health $Url) -eq $newVer) "Health-Check: V$newVer"
$tplArg = @(); if ($Scenario -eq 'noci' -and -not $hadCfg) { $tplArg = @('--template', $template) }
Check (Py verify --url $Url --seed (Join-Path $Work 'seed.json') --fingerprint (Join-Path $Work 'fp_old.json') @tplArg) 'Daten, Logins und Firmenprofil nach dem Update wie vorher'
$bakAfter = Hashes (Join-Path $Base 'backups')
$lost = @($bakBefore.Keys | Where-Object { $bakAfter[$_] -ne $bakBefore[$_] })
Check ($lost.Count -eq 0) "backups\ unveraendert (Bestand $($bakBefore.Count) Dateien, jetzt $($bakAfter.Count))"
Check ((Get-FileHash -LiteralPath (Join-Path $Base 'BACKUP_ZIEL.txt')).Hash -eq $zielBefore) 'BACKUP_ZIEL.txt byte-gleich'
$lanAfter = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
Check ($lanAfter.lan_ip -eq $lan.lan_ip -and $lanAfter.port -eq $lan.port -and $lanAfter.subnet -eq $lan.subnet -and $lanAfter.UpdateRepo -eq 'konto/repo') 'LAN_CONFIG.json: Adresse, Port, Subnetz und UpdateRepo bleiben (updated_at wird vom Serverstart erneuert)'
Check (Test-Path (Join-Path $Base 'config\firma.json')) 'config\firma.json vorhanden'
Check (-not (Test-Path (Join-Path $Base 'UPDATE_LIVE_AUF_V11_2_3.ps1'))) 'veraltete Dateien entfernt'

# ---------------- 5. Zweites Update (aktueller Stand auf sich selbst) ----------------
Check (Py fingerprint --url $Url --seed (Join-Path $Work 'seed.json') --out (Join-Path $Work 'fp_1.json')) 'Fingerabdruck nach dem ersten Update'
$cfgBefore = Hashes (Join-Path $Base 'config')
$code = Run-PS (Join-Path $new 'UPDATE_LIVE.ps1') @() (Join-Path $Work 'update2.log')
Check ($code -eq 0) "zweites UPDATE_LIVE.ps1 erfolgreich (Exitcode $code)"
Check (Py fingerprint --url $Url --seed (Join-Path $Work 'seed.json') --out (Join-Path $Work 'fp_2.json')) 'Fingerabdruck nach dem zweiten Update'
Check (Py same --a (Join-Path $Work 'fp_1.json') --b (Join-Path $Work 'fp_2.json')) 'zweites Update: Datenbankinhalt identisch'
Check (Same-Hashes $cfgBefore (Hashes (Join-Path $Base 'config'))) "zweites Update: config\ byte-gleich ($($cfgBefore.Count) Dateien)"

# ---------------- 6. Fehlerfall: defektes Paket darf Live nicht anfassen ----------------
$broken = Join-Path $Work 'broken'
Copy-Item -LiteralPath $new -Destination $broken -Recurse
$srv = Join-Path $broken 'server.py'
$st = [IO.File]::ReadAllText($srv)
[IO.File]::WriteAllText($srv, ($st -replace '(?m)^(APP_VERSION\s*=.*)$', "`$1`nraise SystemExit(5)"), (New-Object Text.UTF8Encoding($false)))
$code = Run-PS (Join-Path $broken 'UPDATE_LIVE.ps1') @() (Join-Path $Work 'update_broken.log')
Check ($code -ne 0) 'defektes Paket: UPDATE_LIVE bricht im Vorabtest ab'
Check ((Health $Url) -eq $newVer) 'defektes Paket: Live-Server laeuft unveraendert'
Check (Py fingerprint --url $Url --seed (Join-Path $Work 'seed.json') --out (Join-Path $Work 'fp_3.json')) 'Fingerabdruck nach dem Abbruch'
Check (Py same --a (Join-Path $Work 'fp_2.json') --b (Join-Path $Work 'fp_3.json')) 'defektes Paket: Daten unveraendert'

# ---------------- 7. Generic admin-update installation N -> N+1 -> N+2 ----------------
# Real Windows tasks/restart/backup/rollback; trusted download/hash checks run in test_updates.py.
function Future-Package([int]$Increment, [bool]$BreakMigration = $false) {
    $dir = Join-Path $Work ("future_$Increment" + $(if ($BreakMigration) { '_broken' } else { '' }))
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    foreach ($name in $MP_AppFiles) { Copy-MPAppFile $new $dir $name }
    $v = [version]$newVer
    $next = "$($v.Major).$($v.Minor).$($v.Build + $Increment)"
    foreach ($name in @('server.py', 'index.html')) {
        $f = Join-Path $dir $name
        $content = [IO.File]::ReadAllText($f).Replace($newVer, $next)
        if ($BreakMigration -and $name -eq 'server.py') {
            # Preflight copy passes; the live migration fails after a deliberate DB change.
            $inject = "    init_db()`n    if BASE.name == 'Maschinenplanung':`n        with db_session() as con:`n            con.execute('UPDATE state SET revision=revision+99 WHERE id=1')`n        raise SystemExit(9)"
            $content = $content.Replace('    init_db()', $inject)
        }
        [IO.File]::WriteAllText($f, $content, (New-Object Text.UTF8Encoding($false)))
    }
    return [PSCustomObject]@{ Path = $dir; Version = $next }
}
$jobPath = Join-Path $Base 'updates\status.json'
New-Item -ItemType Directory -Path (Split-Path -Parent $jobPath) -Force | Out-Null
foreach ($increment in @(1, 2)) {
    $future = Future-Package $increment
    Utf8Bom $jobPath (@{ jobId = '0123456789abcdef01234567'; stage = 'preflight'; actor = 'admin'; version = $future.Version } | ConvertTo-Json)
    Check (Py fingerprint --url $Url --seed (Join-Path $Work 'seed.json') --out (Join-Path $Work "before_generic_$increment.json")) 'Fingerabdruck vor generischem Update'
    $code = Run-PS (Join-Path $future.Path 'UPDATE_LIVE.ps1') @('-UpdateJobPath', $jobPath) (Join-Path $Work "generic_$increment.log")
    Check ($code -eq 0 -and (Health $Url) -eq $future.Version) "Generisches Update auf $($future.Version) mit echtem Task-Neustart"
    $jobState = Get-Content -LiteralPath $jobPath -Raw | ConvertFrom-Json
    Check ($jobState.stage -eq 'complete' -and -not (Test-Path (Join-Path $Base 'updates\installing'))) 'Update abgeschlossen, Wartungssperre entfernt'
    Check (Py fingerprint --url $Url --seed (Join-Path $Work 'seed.json') --out (Join-Path $Work "after_generic_$increment.json")) 'Fingerabdruck nach generischem Update'
    Check (Py same --a (Join-Path $Work "before_generic_$increment.json") --b (Join-Path $Work "after_generic_$increment.json")) 'Generisches Update erhaelt Daten und Konfiguration'
}
$beforeFailure = Health $Url
Check (Py fingerprint --url $Url --seed (Join-Path $Work 'seed.json') --out (Join-Path $Work 'before_rollback.json')) 'Fingerabdruck vor Migrationsfehler'
$future = Future-Package 3 $true
Utf8Bom $jobPath (@{ jobId = '0123456789abcdef01234567'; stage = 'preflight'; actor = 'admin'; version = $future.Version } | ConvertTo-Json)
$code = Run-PS (Join-Path $future.Path 'UPDATE_LIVE.ps1') @('-UpdateJobPath', $jobPath) (Join-Path $Work 'generic_rollback.log')
Check ($code -ne 0 -and (Health $Url) -eq $beforeFailure) 'Migrationsfehler stellt vorherige laufende Version wieder her'
$jobState = Get-Content -LiteralPath $jobPath -Raw | ConvertFrom-Json
Check ($jobState.stage -eq 'failed' -and $jobState.rolledBack) 'Rollback-Healthcheck erfolgreich und Fehlerstatus gespeichert'
Check (Py fingerprint --url $Url --seed (Join-Path $Work 'seed.json') --out (Join-Path $Work 'after_rollback.json')) 'Fingerabdruck nach Rollback'
Check (Py same --a (Join-Path $Work 'before_rollback.json') --b (Join-Path $Work 'after_rollback.json')) 'Migrationsfehler verliert keine Daten/Revision/History/Config'

# ---------------- 8. #52: Neuinstallation mit dem aktuellen Setup_Windows.ps1 (HTTPS als Standard) ----------------
# Unabhaengig vom alten Stand, daher nur im Szenario ci (spart Laufzeit). Der Bestand aus 1-7 wird dafuer entfernt.
function Remove-Live([string]$tag) {
    $code = Run-PS (Join-Path $Base 'Deinstallieren.ps1') @() (Join-Path $Work "deinst_$tag.log")
    Check ($code -eq 0) "Deinstallieren.ps1 vor $tag (Exitcode $code)"
}
function Remove-Folder([string]$path) {
    for ($i = 0; $i -lt 10 -and (Test-Path -LiteralPath $path); $i++) {
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $path) { Start-Sleep -Seconds 2 }
    }
    return (-not (Test-Path -LiteralPath $path))
}
if ($Scenario -eq 'ci') {
    $UrlTls = "https://${ip}:8765"
    $tlsDir = Join-Path $Base 'config\tls'
    $newSetup = Patch-Setup $new

    # 8a. Frische Installation mit -OhneHttps: HTTP wie bisher, kein config\tls.
    Remove-Live 'ohne_https'
    Check (Remove-Folder $Base) 'Live-Ordner fuer die Neuinstallation entfernt'
    $code = Run-PS $newSetup @('-OhneHttps') (Join-Path $Work 'setup_ohne_https.log')
    Check ($code -eq 0) "Setup_Windows.ps1 -OhneHttps (Exitcode $code)"
    Check (-not (Test-Path -LiteralPath $tlsDir)) '-OhneHttps: kein config\tls'
    Check ((Health $Url) -eq $newVer) "-OhneHttps: Server antwortet per HTTP (V$newVer)"

    # 8b. Erneutes Setup auf der bestehenden HTTP-Installation (Datenbank fehlt, LAN_CONFIG.json bleibt): kein TLS.
    Remove-Live 'reparatur'
    Get-ChildItem -LiteralPath (Join-Path $Base 'data') -Filter 'maschinenplanung.sqlite3*' -ErrorAction SilentlyContinue | Remove-Item -Force
    $code = Run-PS $newSetup @() (Join-Path $Work 'setup_reparatur.log')
    $txt = Get-Content -LiteralPath (Join-Path $Work 'setup_reparatur.log') -Raw
    Check ($code -eq 0 -and $txt -match 'Bestehende Installation erkannt') "erneutes Setup erkennt die bestehende Installation (Exitcode $code)"
    Check (-not (Test-Path -LiteralPath $tlsDir)) 'erneutes Setup: HTTP bleibt HTTP (kein config\tls)'
    Check ((Health $Url) -eq $newVer) 'erneutes Setup: Server antwortet weiter per HTTP'

    # 8c. Echte Neuinstallation ohne Schalter: HTTPS als Standard.
    Remove-Live 'https'
    Check (Remove-Folder $Base) 'Live-Ordner fuer die HTTPS-Neuinstallation entfernt'
    $code = Run-PS $newSetup @() (Join-Path $Work 'setup_https.log')
    Check ($code -eq 0) "Setup_Windows.ps1 ohne Schalter (Exitcode $code)"
    Check ((Test-Path -LiteralPath (Join-Path $tlsDir 'server.crt')) -and (Test-Path -LiteralPath (Join-Path $tlsDir 'server.key'))) 'Neuinstallation legt config\tls (server.crt/server.key) an'
    $pub = Join-Path $Base 'Firmen-CA.crt'
    $trusted = $false
    if (Test-Path -LiteralPath $pub) {
        $ca = New-Object Security.Cryptography.X509Certificates.X509Certificate2($pub)
        $trusted = [bool](Get-ChildItem -LiteralPath ('Cert:\LocalMachine\Root\' + $ca.Thumbprint) -ErrorAction SilentlyContinue)
    }
    Check $trusted 'Firmen-CA.crt im Live-Ordner und in LocalMachine\Root vertraut'
    # Ohne -SkipCertificateCheck: das Zertifikat muss ueber die eben vertraute Firmen-CA gueltig sein.
    Check ((Health $UrlTls) -eq $newVer) "Neuinstallation: Server antwortet per HTTPS ($UrlTls, V$newVer)"
    Check (-not (Health $Url)) 'Neuinstallation: kein unverschluesseltes HTTP mehr'
}

if ($Fail -gt 0) { Write-Host "$Fail Pruefung(en) fehlgeschlagen" -ForegroundColor Red; exit 1 }
Write-Host 'Alle Pruefungen bestanden' -ForegroundColor Green
