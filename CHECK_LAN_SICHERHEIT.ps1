# Maschinenplanung - Sicherheits- und Betriebscheck (als Administrator ausfuehren)
$ErrorActionPreference = 'SilentlyContinue'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
$ok = $true
$Expected = Get-MPPackageVersion $Base
Write-Host "=== Sicherheitscheck Maschinenplanung V$Expected ===" -ForegroundColor Cyan

function Pass([string]$m) { Write-Host "PASS: $m" -ForegroundColor Green }
function Fail([string]$m) { Write-Host "FAIL: $m" -ForegroundColor Red; $script:ok = $false }
function Warn([string]$m) { Write-Host "WARN: $m" -ForegroundColor Yellow }
function CidrToWindowsMask([string]$cidr) {
    if ($cidr -notmatch '^([^/]+)/([0-9]{1,2})$') { return $null }
    $prefix = [int]$Matches[2]; $octets = @()
    for ($i = 0; $i -lt 4; $i++) {
        $bits = $prefix - ($i * 8)
        if ($bits -ge 8) { $octets += 255 } elseif ($bits -le 0) { $octets += 0 } else { $octets += [int](256 - [math]::Pow(2, 8 - $bits)) }
    }
    return "$($Matches[1])/$($octets -join '.')"
}
function HasProfile($rule, [string]$name) { return ([string]$rule.Profile -split ',\s*') -contains $name }

$configPath = Join-Path $Base 'LAN_CONFIG.json'
if (-not (Test-Path $configPath)) { Fail 'LAN_CONFIG.json fehlt.'; Write-Host 'ERGEBNIS: NICHT FREIGEGEBEN' -ForegroundColor Red; exit 1 }
$c = Get-Content $configPath -Raw | ConvertFrom-Json

# --- Netzwerk ---------------------------------------------------------------------------
$currentProfile = Get-NetConnectionProfile -InterfaceAlias $c.interface
if ($currentProfile -and $currentProfile.NetworkCategory -in @('Private', 'DomainAuthenticated')) { Pass "Netzprofil $($currentProfile.NetworkCategory)" }
else { Fail "Aktuelles Netzprofil ist nicht Privat/Domaene (Interface: $($c.interface))." }

$listen = @(Get-NetTCPConnection -State Listen -LocalPort $c.port)
if (@($listen | Where-Object { $_.LocalAddress -eq $c.lan_ip }).Count -gt 0 -and @($listen | Where-Object { $_.LocalAddress -ne $c.lan_ip }).Count -eq 0) { Pass "Server bindet ausschliesslich $($c.lan_ip):$($c.port)" }
else { Fail "Listener ist nicht strikt auf $($c.lan_ip):$($c.port) begrenzt." }

$allow = Get-NetFirewallRule -DisplayName $MP_FwAllow
if (-not $allow) { Fail 'LAN-Allow-Regel fehlt.' }
else {
    $af = $allow | Get-NetFirewallAddressFilter; $pf = $allow | Get-NetFirewallPortFilter
    $maskForm = CidrToWindowsMask ([string]$c.subnet); $remote = @($af.RemoteAddress)
    $remoteOk = ($remote -contains [string]$c.subnet) -or ($maskForm -and ($remote -contains $maskForm))
    if ($remoteOk -and (@($af.LocalAddress) -contains [string]$c.lan_ip) -and ([string]$pf.LocalPort -eq [string]$c.port) -and $allow.Enabled -eq 'True' -and $allow.Action -eq 'Allow' -and ((HasProfile $allow 'Domain') -or (HasProfile $allow 'Private'))) {
        Pass "Firewall erlaubt nur $($c.subnet) -> $($c.lan_ip):$($c.port)"
    } else { Fail "LAN-Allow-Regel ist nicht eng genug. Local=$($af.LocalAddress -join ',') Remote=$($af.RemoteAddress -join ',')" }
}
foreach ($legacy in $MP_LegacyFirewallRules) { if (Get-NetFirewallRule -DisplayName $legacy) { Fail "Alte Firewallregel existiert noch: $legacy" } }
$pub = Get-NetFirewallRule -DisplayName $MP_FwBlockPublic
if ($pub -and $pub.Enabled -eq 'True' -and $pub.Action -eq 'Block' -and (HasProfile $pub 'Public')) { Pass 'Public-Profil explizit blockiert.' } else { Fail 'Public-Blockregel fehlt oder ist falsch.' }
$inet = Get-NetFirewallRule -DisplayName $MP_FwBlockInternet
if ($inet -and $inet.Enabled -eq 'True' -and $inet.Action -eq 'Block') { Pass 'Internet-Zugriff explizit blockiert.' } else { Warn 'Internet-Blockregel fehlt (LAN-Allow + Subnetzpruefung bleiben aktiv).' }

$adapter = Get-NetAdapter -Name $c.interface
$hardwareOk = $adapter -and $adapter.Status -eq 'Up' -and ((-not ($adapter.PSObject.Properties.Name -contains 'HardwareInterface')) -or [bool]$adapter.HardwareInterface) -and ("$($adapter.Name) $($adapter.InterfaceDescription)" -notmatch '(?i)Tailscale|WireGuard|VPN|Hyper-V|vEthernet|VirtualBox|VMware|WSL|Docker|Loopback|ZeroTier|Hamachi')
if ($hardwareOk) { Pass "Physischer LAN-Adapter: $($adapter.Name)" } else { Fail 'Konfigurierter Adapter ist nicht als physischer LAN-Adapter verifiziert.' }

# --- Server / Version -------------------------------------------------------------------
$h = Get-MPHealth $Base
if ($h -and $h.Health -and $h.Health.ok -and [string]$h.Health.version -eq $Expected) { Pass "Server-Health V$($h.Health.version)" }
elseif ($h -and $h.Health) { Fail "Server laeuft V$($h.Health.version), Paket im Ordner ist V$Expected." }
else { Fail 'Server ist ueber die konfigurierte LAN-IP nicht erreichbar.' }
try { Assert-MPPrivatePaths $c; Pass 'Programm-, Daten- und Backupdateien sind nicht per HTTP abrufbar.' } catch { Fail $_.Exception.Message }

# --- Windows: Task, Ordnerrechte, Python ------------------------------------------------
$task = Get-ScheduledTask -TaskName $MP_TaskName
if (-not $task) { Fail "Scheduled Task '$MP_TaskName' fehlt." }
else {
    $action = @($task.Actions)[0]
    $secureRoot = [IO.Path]::GetFullPath($MP_InstallBase).TrimEnd('\')
    try { $wdFull = [IO.Path]::GetFullPath([string]$action.WorkingDirectory).TrimEnd('\') } catch { $wdFull = '' }
    if ($wdFull -ieq $secureRoot) { Pass "SYSTEM-Task laeuft aus $wdFull" } else { Fail "SYSTEM-Task laeuft nicht aus $secureRoot" }
    if ([string]$action.Arguments -match '(?i)-PythonExe\s+"([^"]+)"') {
        if (Test-MPPythonLocationSafe $Matches[1]) { Pass "Python-Pfad ausserhalb von Benutzerprofilen: $($Matches[1])" }
        else { Fail "Python liegt in einem Benutzerprofil ($($Matches[1])) und wird als SYSTEM ausgefuehrt." }
    }
}
foreach ($legacyTask in $MP_LegacyTaskNames) { if (Get-ScheduledTask -TaskName $legacyTask) { Fail "Alter Task existiert noch: $legacyTask" } }
$aclIssues = @()
foreach ($p in @($MP_InstallBase) + @(Get-ChildItem -LiteralPath $MP_InstallBase -Recurse -Force | Select-Object -ExpandProperty FullName)) {
    $issue = Test-MPFolderAclSafe $p
    if ($issue) { $aclIssues += "$p : $issue" }
}
if ($aclIssues.Count -eq 0) { Pass 'Live-Ordner: nur SYSTEM/Administratoren duerfen schreiben.' }
else { Fail "Live-Ordner ist fuer Nicht-Admins beschreibbar ($($aclIssues.Count) Stellen), z. B. $($aclIssues[0])" }
foreach ($name in $MP_ObsoleteFiles) { if (Test-Path (Join-Path $MP_InstallBase $name)) { Fail "Veraltete Datei im Live-Ordner: $name" } }

# --- Backups ----------------------------------------------------------------------------
if (Get-ScheduledTask -TaskName $MP_BackupTaskName) { Pass "Backup-Task '$MP_BackupTaskName' eingerichtet." } else { Fail 'Automatischer Backup-Task fehlt.' }
$last = Get-ChildItem (Join-Path $MP_InstallBase 'backups') -Filter 'maschinenplanung_*.sqlite3' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($last -and $last.LastWriteTime -gt (Get-Date).AddHours(-26)) { Pass "Letztes Backup: $($last.Name)" }
elseif ($last) { Fail "Letztes Backup ist aelter als 26 h: $($last.Name)" } else { Fail 'Kein Backup vorhanden.' }
if (-not (Test-Path (Join-Path $MP_InstallBase 'BACKUP_ZIEL.txt'))) { Warn 'Kein Zweitziel fuer Backups (BACKUP_ZIEL.txt). Backups liegen nur auf diesem PC.' }

if ($ok) { Write-Host 'ERGEBNIS: PASS' -ForegroundColor Green; exit 0 }
else { Write-Host 'ERGEBNIS: NICHT FREIGEGEBEN' -ForegroundColor Red; exit 1 }
