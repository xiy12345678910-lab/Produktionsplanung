# Maschinenplanung - Update direkt von GitHub (fuer den normalen Windows-Benutzer, z. B. <Benutzername>).
#
# V12.10.2 - gesicherte Update-Kette:
#   - Quelle ist ein Release (Tag), dessen Commit in "main" liegt - kein beweglicher Branch-Kopf.
#   - Installiert wird genau EIN fester Commit (Hash wird angezeigt), optional mit SHA256-Pruefung des ZIP.
#   - Download, Pruefung und Entpacken passieren erst im Administrator-Fenster in einem Ordner,
#     auf den nur SYSTEM/Administratoren schreiben duerfen (kein Austausch zwischen Download und UAC).
#
# Aufruf (PowerShell, KEIN Administrator noetig):
#   powershell -ExecutionPolicy Bypass -File .\Update_von_GitHub.ps1                  # neuestes Release
#   powershell -ExecutionPolicy Bypass -File .\Update_von_GitHub.ps1 -Tag v12.10.2
#   powershell -ExecutionPolicy Bypass -File .\Update_von_GitHub.ps1 -Commit <40-stelliger Hash> [-Sha256 <hash>]
#   powershell -ExecutionPolicy Bypass -File .\Update_von_GitHub.ps1 -NurHerunterladen
#   powershell -ExecutionPolicy Bypass -File .\Update_von_GitHub.ps1 -Tag v12.26.0 -Rueckstufen   # zurueck zu aelterer Version
#     (nur Programmcode; die Datenbank bleibt. Der INSTALLIERTE UPDATE_LIVE.ps1 installiert das alte Paket. docs/ROLLBACK.md)
#   Nur zum Testen (ungeprueft): -Branch <name> -UnsicherBranch
#
# V12.29.0 (Bruecke GitHub -> Gitea): Quelle "konto/repo" = GitHub; "https://host[:port][/pfad]/konto/repo" = Gitea
# (API api/v1, Paket = Quellarchiv des Commits). Privates Gitea: Lesetoken in config\update\token.txt (nur Administratoren
# lesbar - das Skript dann in einer Administrator-PowerShell starten).
param(
    [string]$Tag,
    [string]$Commit,
    [string]$Sha256,
    [string]$Branch,
    [switch]$UnsicherBranch,
    [string]$Repo,
    [string]$Ziel = (Join-Path $env:USERPROFILE 'Downloads\Maschinenplanung_Update'),
    [switch]$NurHerunterladen,
    [switch]$Rueckstufen
)
$ErrorActionPreference = 'Stop'
# V12.15.0: Quelle aus LAN_CONFIG.json (Feld UpdateRepo, "konto/repo") im Live-Ordner bzw. neben dem Skript.
# Ohne Eintrag gilt unveraendert der bisherige Standard.
if (-not $Repo) {
    $Repo = 'xiy12345678910-lab/Produktionsplanung'
    foreach ($cfgFile in @('C:\ProgramData\Maschinenplanung\LAN_CONFIG.json', (Join-Path $PSScriptRoot 'LAN_CONFIG.json'))) {
        try {
            if (Test-Path -LiteralPath $cfgFile) {
                $cfgRepo = [string](Get-Content -LiteralPath $cfgFile -Raw | ConvertFrom-Json).UpdateRepo
                if ($cfgRepo) { $Repo = $cfgRepo; break }
            }
        } catch { }
    }
}
# Windows PowerShell 5.1 nutzt sonst ggf. TLS 1.0 - GitHub verlangt TLS 1.2
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'   # Fortschrittsbalken bremst Invoke-WebRequest stark

$Repo = $Repo.Trim().TrimEnd('/') -replace '\.git$', '' -replace '^https://github\.com/', ''
$giteaBase = ''
if ($Repo -cmatch '^(https://[A-Za-z0-9.-]+(:[1-9][0-9]{0,4})?(/[A-Za-z0-9_.-]+)*?)/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$') {
    $giteaBase = $Matches[1]; $repoPath = $Matches[4]
} elseif ($Repo -cmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    $repoPath = $Repo
} else { throw "Ungueltige Updatequelle: $Repo (konto/repo oder https://gitea-host/konto/repo)" }
if (@(($Repo -replace '^https://[^/]+', '').Split('/') | Where-Object { $_ -and -not $_.Trim('.') }).Count) { throw "Ungueltige Updatequelle: $Repo" }
$tokenFile = ''
if ($giteaBase) {
    $tokenFile = @('C:\ProgramData\Maschinenplanung\config\update\token.txt', (Join-Path $PSScriptRoot 'config\update\token.txt')) |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ($Sha256) { $Sha256 = $Sha256.Trim().ToLowerInvariant(); if ($Sha256 -notmatch '^[0-9a-f]{64}$') { throw 'SHA256 muss 64 Hex-Zeichen haben.' } }

function Invoke-MPGitHubApi([string]$Path) {
    if ($giteaBase) {
        return Invoke-RestMethod -Uri "$giteaBase/api/v1/repos/$repoPath/$Path" -Headers (Get-MPUpdateHeaders $tokenFile) -UseBasicParsing
    }
    $headers = @{ 'User-Agent' = 'Maschinenplanung-Update'; 'Accept' = 'application/vnd.github+json' }
    return Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/$Path" -Headers $headers -UseBasicParsing
}

# --- 2. Download + Pruefung (laeuft im Benutzer- ODER im Administratorkontext) ------------------
# Bewusst als Text: wird 1:1 im Administrator-Fenster ausgefuehrt (nur gepruefte Werte eingesetzt).
$core = @'
function Get-MPUpdateHeaders([string]$TokenFile) {
    # Gitea-Lesetoken nur aus der Datei, nie ueber die Befehlszeile.
    $h = @{ 'User-Agent' = 'Maschinenplanung-Update' }
    if ($TokenFile -and (Test-Path -LiteralPath $TokenFile)) {
        try { $t = ([string](Get-Content -LiteralPath $TokenFile -Raw)).Trim() }
        catch { throw 'config\update\token.txt ist nicht lesbar - Skript in einer Administrator-PowerShell starten.' }
        if ($t -notmatch '^[A-Za-z0-9_.-]{16,200}$') { throw 'Ungueltiger Token in config\update\token.txt.' }
        $h['Authorization'] = "token $t"
    }
    return $h
}
function Get-MPUpdatePackage([string]$Url, [string]$Sha, [string]$Sha256, [string]$Dir, [bool]$Protect, [string]$TokenFile) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    if ($Protect) {
        # Staging nur fuer SYSTEM + Administratoren; vorab angelegte Links/Ordner anderer Benutzer verwerfen.
        $parent = Split-Path -Parent $Dir
        if (Test-Path -LiteralPath $parent) {
            $item = Get-Item -LiteralPath $parent -Force
            $owner = try { (Get-Acl -LiteralPath $parent).GetOwner([Security.Principal.SecurityIdentifier]).Value } catch { '' }
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                [IO.Directory]::Delete($parent, $false)   # nur den Link entfernen, nie dem Ziel folgen
            } elseif ($owner -notin @('S-1-5-18', 'S-1-5-32-544')) {
                Rename-Item -LiteralPath $parent -NewName ((Split-Path -Leaf $parent) + '_unsicher_' + (Get-Date -Format 'yyyyMMddHHmmss'))
            }
        }
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
            $id = New-Object Security.Principal.SecurityIdentifier($sid)
            $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($id, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
        }
        $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
        Set-Acl -LiteralPath $parent -AclObject $acl
        # Alte Staging-Ordner: die letzten 3 behalten
        Get-ChildItem -LiteralPath $parent -Directory | Sort-Object LastWriteTime -Descending | Select-Object -Skip 3 |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    }
    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    $zip = Join-Path $Dir 'paket.zip'
    try { Invoke-WebRequest -Uri $Url -OutFile $zip -UseBasicParsing -Headers (Get-MPUpdateHeaders $TokenFile) }
    catch { throw "Download fehlgeschlagen ($Url): $($_.Exception.Message)" }
    $hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "SHA256   : $hash"
    if ($Sha256 -and $hash -ne $Sha256) { Remove-Item -LiteralPath $zip -Force; throw "SHA256 stimmt nicht (erwartet $Sha256). Abbruch, nichts installiert." }
    Expand-Archive -LiteralPath $zip -DestinationPath $Dir -Force
    Remove-Item -LiteralPath $zip -Force
    $dirs = @(Get-ChildItem -LiteralPath $Dir -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'UPDATE_LIVE.ps1') })
    if ($Url -like 'https://codeload.github.com/*') {
        # GitHub legt den Inhalt in <repo>-<commit>/ ab: genau dieser Commit muss es sein.
        $pkg = $dirs | Where-Object { $_.Name -like "*-$Sha" } | Select-Object -First 1
    } else {
        # Gitea legt das Archiv von .../archive/<commit>.zip in <repo>/ ab (Commit steht in der URL): genau ein Ordner.
        $pkg = if ($dirs.Count -eq 1) { $dirs[0] }
    }
    if (-not $pkg) { throw "Paket passt nicht zu Commit $Sha oder UPDATE_LIVE.ps1 fehlt." }
    if ($IsWindows -ne $false) { Get-ChildItem -LiteralPath $pkg.FullName -Recurse -File | Unblock-File }
    return $pkg.FullName
}
'@
. ([scriptblock]::Create($core))   # Get-MPUpdateHeaders wird schon fuer die API gebraucht

# --- 1. Quelle auf einen festen Commit aufloesen ----------------------------------------------
if ($Commit) {
    $sha = $Commit.Trim().ToLowerInvariant()
    if ($sha -notmatch '^[0-9a-f]{40}$') { throw '-Commit braucht den vollstaendigen 40-stelligen Hash.' }
    $label = "Commit $sha"
} elseif ($Branch) {
    if (-not $UnsicherBranch) { throw 'Branch-Updates sind ungeprueft. Nur mit -UnsicherBranch (Test) - fuer Live ein Release verwenden.' }
    $sha = if ($giteaBase) { [string](Invoke-MPGitHubApi ("branches/" + [uri]::EscapeDataString($Branch))).commit.id } else { [string](Invoke-MPGitHubApi ("commits/" + [uri]::EscapeDataString($Branch))).sha }
    $label = "Branch $Branch (UNGEPRUEFT)"
} else {
    if (-not $Tag) {
        try { $Tag = [string](Invoke-MPGitHubApi 'releases/latest').tag_name }
        catch { throw 'Kein veroeffentlichtes Release gefunden. Release nur ueber den Workflow "Release package" (Entwurf) anlegen und veroeffentlichen, siehe docs/RELEASE_REGELN.md.' }
    }
    try {
        $sha = if ($giteaBase) { [string](Invoke-MPGitHubApi ("tags/" + [uri]::EscapeDataString($Tag))).commit.sha } else { [string](Invoke-MPGitHubApi ("commits/" + [uri]::EscapeDataString($Tag))).sha }
    } catch { throw "Tag $Tag nicht gefunden." }
    if ($sha -notmatch '^[0-9a-f]{40}$') { throw "Commit zu $Tag konnte nicht ermittelt werden." }
    # Releases nur aus main: der Tag-Commit muss in main enthalten sein (main gleich oder weiter).
    $cmp = Invoke-MPGitHubApi ("compare/main..." + $sha)
    $inMain = if ($giteaBase) { $cmp.total_commits -eq 0 } else { [string]$cmp.status -in @('identical', 'behind') }
    if (-not $inMain) { throw "Abbruch: $Tag zeigt auf einen Commit ausserhalb von main ($sha)." }
    $label = "Release $Tag"
}
if ($sha -notmatch '^[0-9a-f]{40}$') { throw "Commit konnte nicht ermittelt werden ($label)." }


$pkgUrl = if ($giteaBase) { "$giteaBase/api/v1/repos/$repoPath/archive/$sha.zip" } else { "https://codeload.github.com/$Repo/zip/$sha" }

Write-Host "Benutzer : $env:USERDOMAIN\$env:USERNAME"
Write-Host "Quelle   : $Repo  ($label)"
Write-Host "Commit   : $sha" -ForegroundColor Cyan

if ($NurHerunterladen) {
    $work = Join-Path $Ziel (Get-Date -Format 'yyyy-MM-dd_HHmmss')
    $pkg = Get-MPUpdatePackage -Url $pkgUrl -Sha $sha -Sha256 $Sha256 -Dir $work -Protect $false -TokenFile $tokenFile
    Write-Host "Paket bereit: $pkg" -ForegroundColor Green
    Write-Host 'Installieren: Update ohne -NurHerunterladen starten (prueft und entpackt erneut im geschuetzten Ordner).'
    return
}

# --- 3. Administrator-Fenster: Download/Pruefung im geschuetzten Ordner, dann UPDATE_LIVE.ps1 ----
$shaArg = if ($Sha256) { $Sha256 } else { '' }
$tokenArg = ([string]$tokenFile) -replace "'", "''"   # Pfad (nie der Token selbst) im Text des Administrator-Fensters
$elevated = $core + @"

`$ErrorActionPreference = 'Stop'
try {
    `$dir = Join-Path `$env:ProgramData ('Maschinenplanung_Update\' + (Get-Date -Format 'yyyy-MM-dd_HHmmss'))
    `$pkg = Get-MPUpdatePackage -Url '$pkgUrl' -Sha '$sha' -Sha256 '$shaArg' -Dir `$dir -Protect `$true -TokenFile '$tokenArg'
    Write-Host "Paket: `$pkg" -ForegroundColor Green
    Set-Location -LiteralPath `$pkg
    if (`$$([bool]$Rueckstufen)) {
        # Aelteres Paket mit dem Ablauf des INSTALLIERTEN (neueren) Updaters: nur Code, Daten bleiben.
        & (Join-Path `$env:ProgramData 'Maschinenplanung\UPDATE_LIVE.ps1') -Paket `$pkg -Rueckstufen
    } else {
        & '.\UPDATE_LIVE.ps1'
    }
} catch { Write-Host `$_ -ForegroundColor Red }
Write-Host ''
Read-Host 'Fertig - Enter schliesst dieses Fenster'
"@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($elevated))
try {
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded)
} catch {
    throw "Administrator-Fenster wurde nicht gestartet (UAC abgelehnt?): $($_.Exception.Message)"
}
Write-Host "Update laeuft im Administrator-Fenster. Danach: .\CHECK_LAN_SICHERHEIT.ps1 und alle Browser mit Strg+F5 neu laden."
