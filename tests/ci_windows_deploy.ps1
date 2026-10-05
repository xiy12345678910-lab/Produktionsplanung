# Echtes Windows-Deploy in CI (windows-latest, Administrator): alten Stand mit seinem eigenen Setup_Windows.ps1 installieren,
# Testdaten per API anlegen, mit dem aktuellen UPDATE_LIVE.ps1 aktualisieren, Health + Datenvergleich (tests/deploy_lib.py).
# Aufruf:  ./tests/ci_windows_deploy.ps1 -OldCommit dac0860 -Scenario ci|noci
#
# Nur diese Testumgebung weicht von der Produktion ab (Live-Skripte bleiben unveraendert, es wird nur eine KOPIE gepatcht):
#  - Get-MPLanInfo liefert die IP des Runners (Runner haben kein "physisches Privat-Netz"),
#  - Setup_Windows.ps1 des alten Stands bekommt Benutzer/Passwort statt Read-Host.
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
$setup = Join-Path $old 'Setup_Windows.ps1'
$t = [IO.File]::ReadAllText($setup)
$n0 = $t.Length
$t = $t.Replace("Read-Host 'Trotzdem fortfahren? (j/N)'", "'j'").Replace("Read-Host 'Erster Admin-Benutzername'", "'admin'")
$t = $t.Replace("Read-Host 'Admin-Passwort (mind. 8 Zeichen)' -AsSecureString", '(ConvertTo-SecureString $env:MP_CI_PW -AsPlainText -Force)')
if ($t.Contains('Read-Host')) { throw 'Setup_Windows.ps1 des alten Stands enthaelt unbekannte Abfragen (Read-Host).' }
Utf8Bom $setup $t
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
$new = Join-Path $Work 'new'
New-Item -ItemType Directory -Path $new | Out-Null
foreach ($name in $MP_AppFiles) { Copy-Item -LiteralPath (Join-Path $Repo $name) -Destination $new }
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

if ($Fail -gt 0) { Write-Host "$Fail Pruefung(en) fehlgeschlagen" -ForegroundColor Red; exit 1 }
Write-Host 'Alle Pruefungen bestanden' -ForegroundColor Green
