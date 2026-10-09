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
                if ($cfgRepo -match '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { $Repo = $cfgRepo; break }
            }
        } catch { }
    }
}
# Windows PowerShell 5.1 nutzt sonst ggf. TLS 1.0 - GitHub verlangt TLS 1.2
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'   # Fortschrittsbalken bremst Invoke-WebRequest stark

if ($Repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw "Ungueltiger Repo-Name: $Repo" }
if ($Sha256) { $Sha256 = $Sha256.Trim().ToLowerInvariant(); if ($Sha256 -notmatch '^[0-9a-f]{64}$') { throw 'SHA256 muss 64 Hex-Zeichen haben.' } }

function Invoke-MPGitHubApi([string]$Path) {
    $headers = @{ 'User-Agent' = 'Maschinenplanung-Update'; 'Accept' = 'application/vnd.github+json' }
    return Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/$Path" -Headers $headers -UseBasicParsing
}

# --- 1. Quelle auf einen festen Commit aufloesen ----------------------------------------------
if ($Commit) {
    $sha = $Commit.Trim().ToLowerInvariant()
    if ($sha -notmatch '^[0-9a-f]{40}$') { throw '-Commit braucht den vollstaendigen 40-stelligen Hash.' }
    $label = "Commit $sha"
} elseif ($Branch) {
    if (-not $UnsicherBranch) { throw 'Branch-Updates sind ungeprueft. Nur mit -UnsicherBranch (Test) - fuer Live ein Release verwenden.' }
    $sha = [string](Invoke-MPGitHubApi ("commits/" + [uri]::EscapeDataString($Branch))).sha
    $label = "Branch $Branch (UNGEPRUEFT)"
} else {
    if (-not $Tag) {
        try { $Tag = [string](Invoke-MPGitHubApi 'releases/latest').tag_name }
        catch { throw 'Kein veroeffentlichtes Release gefunden. Release nur ueber GitHub Actions "Release package" (Entwurf) anlegen und veroeffentlichen, siehe docs/RELEASE_REGELN.md.' }
    }
    try { $sha = [string](Invoke-MPGitHubApi ("commits/" + [uri]::EscapeDataString($Tag))).sha }
    catch { throw "Tag $Tag nicht gefunden." }
    # Releases nur aus main: der Tag-Commit muss in main enthalten sein (main gleich oder weiter).
    $cmp = Invoke-MPGitHubApi ("compare/main..." + $sha)
    if ([string]$cmp.status -notin @('identical', 'behind')) { throw "Abbruch: $Tag zeigt auf einen Commit ausserhalb von main ($sha)." }
    $label = "Release $Tag"
}
if ($sha -notmatch '^[0-9a-f]{40}$') { throw "Commit konnte nicht ermittelt werden ($label)." }

# --- 2. Download + Pruefung (laeuft im Benutzer- ODER im Administratorkontext) ------------------
# Bewusst als Text: wird 1:1 im Administrator-Fenster ausgefuehrt (nur gepruefte Werte eingesetzt).
$core = @'
function Get-MPUpdatePackage([string]$Repo, [string]$Sha, [string]$Sha256, [string]$Dir, [bool]$Protect) {
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
    $url = "https://codeload.github.com/$Repo/zip/$Sha"
    try { Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing }
    catch { throw "Download fehlgeschlagen ($url): $($_.Exception.Message)" }
    $hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "SHA256   : $hash"
    if ($Sha256 -and $hash -ne $Sha256) { Remove-Item -LiteralPath $zip -Force; throw "SHA256 stimmt nicht (erwartet $Sha256). Abbruch, nichts installiert." }
    Expand-Archive -LiteralPath $zip -DestinationPath $Dir -Force
    Remove-Item -LiteralPath $zip -Force
    # GitHub legt den Inhalt in <repo>-<commit>/ ab: genau dieser Commit muss es sein.
    $pkg = Get-ChildItem -LiteralPath $Dir -Directory | Where-Object { $_.Name -like "*-$Sha" -and (Test-Path (Join-Path $_.FullName 'UPDATE_LIVE.ps1')) } | Select-Object -First 1
    if (-not $pkg) { throw "Paket passt nicht zu Commit $Sha oder UPDATE_LIVE.ps1 fehlt." }
    if ($IsWindows -ne $false) { Get-ChildItem -LiteralPath $pkg.FullName -Recurse -File | Unblock-File }
    return $pkg.FullName
}
'@

Write-Host "Benutzer : $env:USERDOMAIN\$env:USERNAME"
Write-Host "Quelle   : $Repo  ($label)"
Write-Host "Commit   : $sha" -ForegroundColor Cyan

if ($NurHerunterladen) {
    . ([scriptblock]::Create($core))
    $work = Join-Path $Ziel (Get-Date -Format 'yyyy-MM-dd_HHmmss')
    $pkg = Get-MPUpdatePackage -Repo $Repo -Sha $sha -Sha256 $Sha256 -Dir $work -Protect $false
    Write-Host "Paket bereit: $pkg" -ForegroundColor Green
    Write-Host 'Installieren: Update ohne -NurHerunterladen starten (prueft und entpackt erneut im geschuetzten Ordner).'
    return
}

# --- 3. Administrator-Fenster: Download/Pruefung im geschuetzten Ordner, dann UPDATE_LIVE.ps1 ----
$shaArg = if ($Sha256) { $Sha256 } else { '' }
$elevated = $core + @"

`$ErrorActionPreference = 'Stop'
try {
    `$dir = Join-Path `$env:ProgramData ('Maschinenplanung_Update\' + (Get-Date -Format 'yyyy-MM-dd_HHmmss'))
    `$pkg = Get-MPUpdatePackage -Repo '$Repo' -Sha '$sha' -Sha256 '$shaArg' -Dir `$dir -Protect `$true
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
