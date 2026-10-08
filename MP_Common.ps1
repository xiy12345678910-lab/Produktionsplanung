# Maschinenplanung - gemeinsame Einstellungen und Hilfsfunktionen fuer alle Windows-Skripte.
# Wird per Dot-Sourcing geladen:  . "$PSScriptRoot\MP_Common.ps1"

$MP_InstallBase    = Join-Path $env:ProgramData 'Maschinenplanung'
$MP_Port           = 8765
$MP_TaskName       = 'Maschinenplanung Server'
$MP_BackupTaskName = 'Maschinenplanung Backup'
# Fruehere Tasknamen; werden bei Setup/Update entfernt.
$MP_LegacyTaskNames = @('Maschinenplanung V11 Server')

$MP_FwAllow         = "Maschinenplanung LAN Allow $MP_Port"
$MP_FwBlockPublic   = "Maschinenplanung LAN Block Public $MP_Port"
$MP_FwBlockInternet = "Maschinenplanung LAN Block Internet $MP_Port"
$MP_FirewallRules   = @($MP_FwAllow, $MP_FwBlockPublic, $MP_FwBlockInternet)
$MP_LegacyFirewallRules = @(
    "Maschinenplanung V11 TCP $MP_Port",
    "Maschinenplanung V11 LAN Allow $MP_Port",
    "Maschinenplanung V11 LAN Block Public $MP_Port",
    "Maschinenplanung V11 LAN Block Internet $MP_Port"
)

# V12.14.0: Firmenkonfiguration (config\firma.json, Logo, Lizenz) liegt NEBEN dem Programm, nicht in $MP_AppFiles.
# Updates, Setup und $MP_ObsoleteFiles duerfen config\ nie ueberschreiben oder loeschen.
$MP_ConfigDir = 'config'

# Alle Programmdateien des Pakets. Nur diese werden kopiert/gesichert.
$MP_AppFiles = @(
    'server.py', 'mp_license.py', 'release_gates.py', 'app_updates.py', 'index.html',
    'Backup_Datenbank.py', 'Backup_Datenbank.ps1',
    'MP_Common.ps1', 'Setup_Windows.ps1', 'INSTALLIEREN_ALS_ADMIN.ps1', 'UPDATE_LIVE.ps1',
    'Run_Server_LAN.ps1', 'Start_Server.ps1', 'Stop_Server.ps1', 'Neustart_Server.ps1',
    'Server_Status.ps1', 'CHECK_LAN_SICHERHEIT.ps1', 'Deinstallieren.ps1',
    'README_Windows.txt', 'BENUTZER_KURZANLEITUNG.txt', 'FEHLERCODES.txt', 'RELEASE_NOTES.txt',
    'Update_von_GitHub.ps1', 'Restore_Datenbank.ps1', 'Firma_Einrichten.ps1', 'HTTPS_Einrichten.ps1', 'requirements.txt',
    'vorlage_werbetechnik.json', 'vorlage_metall_cnc.json', 'vorlage_leer.json', 'vorlage_demo.json'
)

# Veraltete Dateien frueherer Versionen, die im Live-Ordner nicht liegen bleiben duerfen
# (u. a. alte Updater, die V11-Code auf eine V12-Datenbank zurueckspielen koennten).
$MP_ObsoleteFiles = @(
    'UPDATE_LIVE_AUF_V11_2_3.ps1', 'UPDATE_LIVE_AUF_V11_2_4.ps1', 'UPDATE_LIVE_AUF_V11_3_1.ps1',
    'STOPP_UND_ADMINSPERRE_ENTFERNEN.ps1', 'Autostart_entfernen.ps1',
    'FEHLERCODES_V11_3_1.txt', 'RELEASE_NOTES_V11_3_1.txt', 'BENUTZER_KURZANLEITUNG.zip'
)

# V12.27.0 (#73 Phase 1 Vorbereitung): Programmdateien duerfen hoechstens EINE Ordnerebene tief liegen
# (z. B. 'core/config.py', immer mit '/'). Gleiche Regel wie app_updates.member_name.
function Test-MPAppName([string]$Name) {
    # Kein '..', kein Laufwerk, kein '\', keine leeren Teile, keine Punkt-Teile, keine Geraetenamen, max. eine Ebene.
    if ($Name -cnotmatch '\A[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?\z') { return $false }
    # Reservierte Ordner (Konfiguration, Daten, Sicherungen) sind keine Programmordner.
    if ($Name.Contains('/') -and (@('config', 'data', 'backups', 'update_backups', 'updates', '__pycache__') -contains $Name.Split('/')[0])) { return $false }
    foreach ($part in $Name.Split('/')) {
        if ($part.StartsWith('.') -or $part.EndsWith('.') -or $part -match '^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$') { return $false }
    }
    return $true
}

function Get-MPAppPath([string]$Root, [string]$Name) {
    if (-not (Test-MPAppName $Name)) { throw "MP-UPD-006: Ungueltiger Programmdateiname: $Name" }
    return (Join-Path $Root ($Name.Replace('/', [string][IO.Path]::DirectorySeparatorChar)))
}

function Copy-MPAppFile([string]$Source, [string]$Target, [string]$Name) {
    # Kopiert eine Programmdatei und legt den Unterordner bei Bedarf an. Gleiche Quelle/Ziel: nichts tun.
    $src = Get-MPAppPath $Source $Name
    $dst = Get-MPAppPath $Target $Name
    if ([IO.Path]::GetFullPath($src) -ieq [IO.Path]::GetFullPath($dst)) { return }
    $parent = Split-Path -Parent $dst
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Copy-Item -LiteralPath $src -Destination $dst -Force
}

function Get-MPAppFileList([string]$Folder) {
    # Liest $MP_AppFiles aus einem (z. B. alten installierten) MP_Common.ps1 ohne es auszufuehren.
    $f = Join-Path $Folder 'MP_Common.ps1'
    if (-not (Test-Path -LiteralPath $f)) { return @() }
    $m = [regex]::Match([IO.File]::ReadAllText($f), '\$MP_AppFiles\s*=\s*@\((.*?)\r?\n\)', 'Singleline')
    if (-not $m.Success) { return @() }
    return @([regex]::Matches($m.Groups[1].Value, "'([^']+)'") | ForEach-Object { $_.Groups[1].Value })
}

function Test-MPAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-MPAdmin([string]$Script) {
    if (-not (Test-MPAdmin)) { throw "$Script muss als Administrator gestartet werden." }
}

function Get-MPPackageVersion([string]$Folder) {
    $py = Join-Path $Folder 'server.py'
    if (-not (Test-Path $py)) { return $null }
    $m = Select-String -LiteralPath $py -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

function Get-MPPython {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) { $exe = (& py -3 -c "import sys; print(sys.executable)").Trim() }
    else {
        $python = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $python) { throw 'Python 3 wurde nicht gefunden. Bitte Python 3 (fuer alle Benutzer) installieren.' }
        $exe = $python.Source
    }
    if (-not (Test-Path $exe)) { throw "Python wurde nicht gefunden: $exe" }
    return $exe
}

function Test-MPPythonLocationSafe([string]$PythonExe) {
    # Der Server laeuft als SYSTEM. Darf ein normaler Benutzer Python-Dateien veraendern
    # (Benutzerprofil oder z. B. C:\Python311 mit Schreibrecht fuer "Authentifizierte Benutzer"),
    # wird dessen Code als SYSTEM ausgefuehrt.
    $full = [IO.Path]::GetFullPath($PythonExe)
    if ($full -like "$env:SystemDrive\Users\*") { return $false }
    # V12.10.2: Rechte des Python-Ordners und der Standardbibliothek pruefen, nicht nur den Pfad.
    $dir = Split-Path -Parent $full
    foreach ($p in @($dir, (Join-Path $dir 'Lib'))) {
        # Besitzer ist bei Installation unter Program Files oft das installierende Admin-Konto -> nur Schreibrechte werten.
        if ((Test-Path -LiteralPath $p) -and (Test-MPFolderAclSafe $p $false)) { return $false }
    }
    return $true
}

function Install-MPTzdata([string]$PythonExe, [string]$Folder) {
    # Version und SHA256 fest in requirements.txt (keine ungeprueften Pakete als Administrator).
    & $PythonExe -m pip install --disable-pip-version-check --quiet --require-hashes -r (Join-Path $Folder 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Write-Warning 'tzdata nicht installiert; Server nutzt die Windows-Zeitzone.' }
}

function Get-MPLanInfo {
    $routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        Where-Object { $_.NextHop -and $_.NextHop -ne '0.0.0.0' } |
        Sort-Object RouteMetric, InterfaceMetric
    foreach ($r in $routes) {
        $adapter = Get-NetAdapter -InterfaceIndex $r.InterfaceIndex -ErrorAction SilentlyContinue
        if (-not $adapter -or $adapter.Status -ne 'Up') { continue }
        # Nur echte Hardwareadapter; virtuelle/VPN-Adapter bestimmen nie die Server-Bindung.
        if ($adapter.PSObject.Properties.Name -contains 'HardwareInterface') {
            if (-not [bool]$adapter.HardwareInterface) { continue }
        }
        $adapterText = "$($adapter.Name) $($adapter.InterfaceDescription)"
        if ($adapterText -match '(?i)Tailscale|WireGuard|VPN|Hyper-V|vEthernet|VirtualBox|VMware|WSL|Docker|Loopback|ZeroTier|Hamachi') { continue }
        $profile = Get-NetConnectionProfile -InterfaceIndex $r.InterfaceIndex -ErrorAction SilentlyContinue
        if (-not $profile -or $profile.NetworkCategory -notin @('Private', 'DomainAuthenticated')) { continue }
        $ip = Get-NetIPAddress -InterfaceIndex $r.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
            Select-Object -First 1
        if ($ip) {
            return [PSCustomObject]@{
                IP                   = $ip.IPAddress
                PrefixLength         = [int]$ip.PrefixLength
                InterfaceIndex       = [int]$r.InterfaceIndex
                Profile              = [string]$profile.NetworkCategory
                InterfaceAlias       = [string]$adapter.Name
                InterfaceDescription = [string]$adapter.InterfaceDescription
            }
        }
    }
    return $null
}

function Set-MPFolderAcl([string]$Path, [bool]$UsersCanRead) {
    # Gesperrter Live-Ordner: nur SYSTEM und Administratoren duerfen schreiben.
    # Frueher geerbte/explizite Rechte (auch "Benutzer duerfen Dateien anlegen" aus
    # C:\ProgramData) werden entfernt; Besitzer wird die Administratorengruppe.
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    $none = [Security.AccessControl.PropagationFlags]::None
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $id = New-Object Security.Principal.SecurityIdentifier($sid)
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($id, 'FullControl', $inherit, $none, 'Allow')))
    }
    if ($UsersCanRead) {
        $users = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-545')
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($users, 'ReadAndExecute', $inherit, $none, 'Allow')))
    }
    $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
    Set-Acl -LiteralPath $Path -AclObject $acl
    & icacls.exe $Path /setowner '*S-1-5-32-544' /T /C /Q | Out-Null
    if (Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue) {
        & icacls.exe "$Path\*" /reset /T /C /Q | Out-Null
    }
}

function Protect-MPInstall([string]$Base) {
    Set-MPFolderAcl $Base $true
    foreach ($sub in @('data', 'backups', 'update_backups', 'updates')) {
        Set-MPFolderAcl (Join-Path $Base $sub) $false
    }
    # V12.21.0: private HTTPS-Schluessel nur fuer SYSTEM und Administratoren (config\ selbst bleibt lesbar).
    $tls = Join-Path (Join-Path $Base $MP_ConfigDir) 'tls'
    if (Test-Path -LiteralPath $tls) { Set-MPFolderAcl $tls $false }
}

function Get-MPTaskResultText([int64]$Result) {
    # V12.17.2: Klartext fuer LastTaskResult (267009 = 0x41301 "Task laeuft gerade" wirkte wie ein Fehler).
    switch ($Result) {
        0        { return 'OK' }
        2        { return 'Backup lokal OK, Zweitkopie fehlgeschlagen' }
        267008   { return 'bereit' }
        267009   { return 'laeuft' }
        267010   { return 'deaktiviert' }
        267011   { return 'noch nie gelaufen' }
        267014   { return 'wurde beendet' }
        default  { return ('Ergebnis {0} (0x{1:X})' -f $Result, $Result) }
    }
}

function Test-MPRightsWritable([int64]$Rights) {
    # V12.17.2: Nur echte Schreib-Bits zaehlen. FullControl/Modify enthalten auch Lese-Bits
    # (ReadData, ReadAttributes, ReadPermissions, Synchronize ...), die allein kein Schreibrecht sind.
    # WriteData 0x2, AppendData 0x4, WriteExtendedAttributes 0x10, DeleteSubdirectoriesAndFiles 0x40,
    # WriteAttributes 0x100, Delete 0x10000, ChangePermissions 0x40000, TakeOwnership 0x80000
    # plus rohe generische Rechte (bei inherit-only ACEs als Zahl): GENERIC_WRITE 0x40000000, GENERIC_ALL 0x10000000.
    # GENERIC_READ (0x80000000) und GENERIC_EXECUTE (0x20000000) zaehlen nicht.
    [int64]$mask = 0x2 + 0x4 + 0x10 + 0x40 + 0x100 + 0x10000 + 0x40000 + 0x80000 + 0x40000000 + 0x10000000
    return (($Rights -band $mask) -ne 0)
}

function Test-MPFolderAclSafe([string]$Path, [bool]$CheckOwner = $true) {
    # Liefert $null wenn sicher, sonst eine Beschreibung des Problems.
    $acl = Get-Acl -LiteralPath $Path
    # SYSTEM, Administratoren, ERSTELLER-BESITZER, TrustedInstaller (Windows-Installer unter C:\Program Files)
    $trusted = @('S-1-5-18', 'S-1-5-32-544', 'S-1-3-0', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    foreach ($ace in $acl.Access) {
        if ($ace.AccessControlType -ne 'Allow') { continue }
        try { $sid = $ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch { $sid = [string]$ace.IdentityReference }
        if ($trusted -contains $sid) { continue }
        if (Test-MPRightsWritable ([int64]$ace.FileSystemRights)) {
            return "$($ace.IdentityReference) hat Schreibrechte ($($ace.FileSystemRights))"
        }
    }
    try { $owner = (New-Object Security.Principal.NTAccount($acl.Owner)).Translate([Security.Principal.SecurityIdentifier]).Value } catch { $owner = '' }
    if ($CheckOwner -and $owner -and $owner -notin @('S-1-5-18', 'S-1-5-32-544')) { return "Besitzer ist $($acl.Owner)" }
    return $null
}

function Remove-MPLegacyTasks {
    foreach ($name in $MP_LegacyTaskNames) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
        }
    }
}

function Register-MPServerTask([string]$Base, [string]$PythonExe) {
    $psExe = (Get-Command powershell.exe -ErrorAction Stop).Source
    $taskArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$Base\Run_Server_LAN.ps1`" -PythonExe `"$PythonExe`" -Port $MP_Port"
    $action = New-ScheduledTaskAction -Execute $psExe -Argument $taskArgs -WorkingDirectory $Base
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $MP_TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

function Register-MPBackupTask([string]$Base, [string]$PythonExe) {
    # Zweimal taeglich ein Online-Backup (SQLite Backup API, WAL-sicher) inkl. Integritaetspruefung.
    $action = New-ScheduledTaskAction -Execute $PythonExe -Argument "-I `"$Base\Backup_Datenbank.py`"" -WorkingDirectory $Base
    $triggers = @((New-ScheduledTaskTrigger -Daily -At '12:15'), (New-ScheduledTaskTrigger -Daily -At '22:15'))
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Register-ScheduledTask -TaskName $MP_BackupTaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
}

function Stop-MPServer([string]$Base) {
    foreach ($name in @($MP_TaskName) + $MP_LegacyTaskNames) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $MP_Port -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
        $cmd = [string]$proc.CommandLine
        if ($proc -and $cmd -match '(?i)server\.py' -and $cmd -match '(?i)Maschinenplanung') {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 1
    $left = @(Get-NetTCPConnection -State Listen -LocalPort $MP_Port -ErrorAction SilentlyContinue)
    if ($left.Count -gt 0) { throw "Port $MP_Port ist nach dem Serverstopp noch belegt." }
}

function Read-MPConfig([string]$Base) {
    # LAN_CONFIG.json wird beim Serverstart neu geschrieben. Waehrenddessen kann die Datei kurz
    # gesperrt oder unvollstaendig sein - dann kurz warten statt abzubrechen; $null = (noch) nicht lesbar.
    $cfg = Join-Path $Base 'LAN_CONFIG.json'
    for ($i = 1; $i -le 10; $i++) {
        if (-not (Test-Path -LiteralPath $cfg)) { return $null }
        try {
            $c = Get-Content -LiteralPath $cfg -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
            if ($c -and $c.lan_ip -and $c.port) { return $c }
        } catch { }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

# V12.21.0: HTTPS, sobald config\tls\server.crt und server.key vorliegen (HTTPS_Einrichten.ps1).
# Windows PowerShell 5.1 bietet TLS 1.2 nicht auf jedem System von selbst an.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

function Get-MPScheme([string]$Base) {
    $tls = Join-Path (Join-Path $Base $MP_ConfigDir) 'tls'
    if ((Test-Path -LiteralPath (Join-Path $tls 'server.crt')) -and (Test-Path -LiteralPath (Join-Path $tls 'server.key'))) { return 'https' }
    return 'http'
}

function Get-MPHealth([string]$Base, [int]$TimeoutSec = 3) {
    $c = Read-MPConfig $Base
    if (-not $c) { return $null }
    $url = '{0}://{1}:{2}' -f (Get-MPScheme $Base), $c.lan_ip, $c.port
    try {
        $r = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec $TimeoutSec
        return [PSCustomObject]@{ Config = $c; Health = $r; Url = $url }
    } catch { return [PSCustomObject]@{ Config = $c; Health = $null; Url = $url } }
}

function Wait-MPHealth([string]$Base, [string]$ExpectedVersion, [int]$Seconds = 30) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        Start-Sleep -Seconds 1
        $h = Get-MPHealth $Base 2
        if ($h -and $h.Health -and $h.Health.ok -and [string]$h.Health.version -eq $ExpectedVersion) { return $h }
    }
    return $null
}

function Assert-MPPrivatePaths([object]$Config, [string]$Url = '') {
    if (-not $Url) { $Url = "http://$($Config.lan_ip):$($Config.port)" }
    foreach ($path in @('/server.py', '/data/maschinenplanung.sqlite3', '/backups/test.sqlite3', '/update_backups/test.txt', '/LAN_CONFIG.json')) {
        $exposed = $false
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -Uri "$Url$path" -TimeoutSec 3
            if ([int]$resp.StatusCode -lt 400) { $exposed = $true }
        } catch {
            $status = $null
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            if ($status -and $status -ne 404) { throw "Unerwarteter HTTP-Status $status bei $path" }
        }
        if ($exposed) { throw "Sicherheitsfehler: $path ist per HTTP erreichbar." }
    }
}

function Get-MPTlsDir([string]$Base) {
    return (Join-Path (Join-Path $Base $MP_ConfigDir) 'tls')
}

function Install-MPTls([string]$Base, [string]$PythonExe, [string]$HostIp, [string[]]$Names = @()) {
    # #52: Gemeinsame HTTPS-Einrichtung fuer HTTPS_Einrichten.ps1 und Setup_Windows.ps1 (eine Implementierung).
    # Legt in config\tls Firmen-CA (falls fehlend) und Serverzertifikat an, vertraut der CA in LocalMachine\Root
    # und legt Firmen-CA.crt zum Verteilen in den Live-Ordner. Startet den Server NICHT neu.
    # Fehler: Ausnahme mit MP-TLS-002. Rueckgabe: Fingerabdruck der CA und Pfad von Firmen-CA.crt.
    $tls = Get-MPTlsDir $Base
    # Ordner zuerst sperren, damit die privaten Schluessel nie mit Benutzer-Leserecht entstehen.
    Set-MPFolderAcl $tls $false
    $argList = @('-X', 'utf8', '-I', (Join-Path $Base 'server.py'), '--tls-einrichten', '--host', $HostIp)
    foreach ($n in $Names) { $argList += @('--tls-name', $n) }
    $env:MP_CONFIG_DIR = Join-Path $Base $MP_ConfigDir
    $code = 1
    # server.py schreibt UTF-8 (-X utf8); fuer die weitergereichte Ausgabe kurz UTF-8 lesen (sonst Umlaut-Salat).
    $prevEnc = $null
    try { $prevEnc = [Console]::OutputEncoding; [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false) } catch { $prevEnc = $null }
    try {
        # Out-Host: Ausgaben von server.py gehoeren auf die Konsole, nicht in den Rueckgabewert der Funktion.
        & $PythonExe @argList | Out-Host
        $code = $LASTEXITCODE
    } finally {
        Remove-Item Env:\MP_CONFIG_DIR -ErrorAction SilentlyContinue
        if ($prevEnc) { try { [Console]::OutputEncoding = $prevEnc } catch { } }
    }
    if ($code -ne 0) { throw "MP-TLS-002: HTTPS-Einrichtung fehlgeschlagen (server.py --tls-einrichten, Exitcode $code)." }
    Set-MPFolderAcl $tls $false
    $caFile = Join-Path $tls 'firmen-ca.crt'
    try {
        $ca = New-Object Security.Cryptography.X509Certificates.X509Certificate2($caFile)
        $store = New-Object Security.Cryptography.X509Certificates.X509Store('Root', 'LocalMachine')
        $store.Open('ReadWrite')
        try { $store.Add($ca) } finally { $store.Close() }
        $public = Join-Path $Base 'Firmen-CA.crt'
        Copy-Item -LiteralPath $caFile -Destination $public -Force
    } catch {
        throw "MP-TLS-002: Firmen-CA konnte nicht vertraut/bereitgestellt werden: $($_.Exception.Message)"
    }
    return [PSCustomObject]@{ Thumbprint = [string]$ca.Thumbprint; PublicCa = $public; Folder = $tls }
}

function Remove-MPTls([string]$Base) {
    # #52: Rueckfall auf HTTP NUR fuer eine eben von Setup_Windows.ps1 angelegte HTTPS-Einrichtung:
    # entfernt config\tls, Firmen-CA.crt im Live-Ordner und die eben vertraute CA aus LocalMachine\Root.
    # Liefert $true, wenn config\tls danach nicht mehr existiert.
    $tls = Get-MPTlsDir $Base
    $caFile = Join-Path $tls 'firmen-ca.crt'
    if (Test-Path -LiteralPath $caFile) {
        try {
            $ca = New-Object Security.Cryptography.X509Certificates.X509Certificate2($caFile)
            $store = New-Object Security.Cryptography.X509Certificates.X509Store('Root', 'LocalMachine')
            $store.Open('ReadWrite')
            try {
                foreach ($c in @($store.Certificates.Find('FindByThumbprint', $ca.Thumbprint, $false))) { $store.Remove($c) }
            } finally { $store.Close() }
        } catch { Write-Warning "Firmen-CA konnte nicht aus dem Zertifikatspeicher entfernt werden: $($_.Exception.Message)" }
    }
    Remove-Item -LiteralPath $tls -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $Base 'Firmen-CA.crt') -Force -ErrorAction SilentlyContinue
    return (-not (Test-Path -LiteralPath $tls))
}

function Wait-MPTaskIdle([string]$Name, [int]$Seconds = 30) {
    # Start-ScheduledTask ist wirkungslos, solange eine alte Instanz noch als "Running" gilt.
    for ($i = 0; $i -lt $Seconds; $i++) {
        $t = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        if (-not $t -or $t.State -ne 'Running') { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Get-MPLogTail([string]$Base, [int]$Lines = 40) {
    $dir = Join-Path $Base 'logs'
    $last = Get-ChildItem -LiteralPath $dir -Filter 'server_*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $last) { return '(kein Server-Log vorhanden)' }
    return ((Get-Content -LiteralPath $last.FullName -Tail $Lines -Encoding UTF8) -join [Environment]::NewLine)
}

function Invoke-MPPreflight([string]$NewSource, [string]$PythonExe, [string]$DbCopySource, [object]$Config, [string]$ExpectedVersion, [string]$LiveBase = '') {
    # Startet die NEUE Version mit einer KOPIE der Datenbank auf einem Ersatzport.
    # Das Live-System wird dabei nicht beruehrt.
    $dir = Join-Path $env:TEMP ('mp_preflight_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path (Join-Path $dir 'data') -Force | Out-Null
    foreach ($name in $MP_AppFiles) { Copy-MPAppFile $NewSource $dir $name }
    Copy-Item -LiteralPath $DbCopySource -Destination (Join-Path $dir 'data\maschinenplanung.sqlite3') -Force
    # V12.14.0: neue Version mit der ECHTEN Firmenkonfiguration pruefen (Kopie, Live bleibt unberuehrt).
    if ($LiveBase -and (Test-Path -LiteralPath (Join-Path $LiveBase $MP_ConfigDir))) {
        Copy-Item -LiteralPath (Join-Path $LiveBase $MP_ConfigDir) -Destination (Join-Path $dir $MP_ConfigDir) -Recurse -Force
    }
    $port = 8799
    while ($port -gt 8780 -and @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue).Count -gt 0) { $port-- }
    $out = Join-Path $dir 'preflight_out.log'; $err = Join-Path $dir 'preflight_err.log'
    $argList = @('-X', 'utf8', '-u', '-I', "`"$dir\server.py`"", '--host', [string]$Config.lan_ip, '--port', [string]$port, '--allowed-subnet', [string]$Config.subnet)
    $proc = Start-Process -FilePath $PythonExe -ArgumentList $argList -PassThru -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
    $ok = $false
    try {
        for ($i = 0; $i -lt 45 -and -not $proc.HasExited; $i++) {
            Start-Sleep -Seconds 1
            try {
                $r = Invoke-RestMethod -Uri ("{0}://{1}:{2}/api/health" -f (Get-MPScheme $dir), $Config.lan_ip, $port) -TimeoutSec 2
                if ($r.ok -and [string]$r.version -eq $ExpectedVersion) { $ok = $true; break }
            } catch { }
        }
    } finally {
        if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1 }
    }
    $log = ((@(Get-Content -LiteralPath $out -ErrorAction SilentlyContinue) + @(Get-Content -LiteralPath $err -ErrorAction SilentlyContinue)) -join [Environment]::NewLine)
    Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
    return [PSCustomObject]@{ Ok = $ok; Log = $log; Port = $port }
}

