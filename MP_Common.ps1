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

# Alle Programmdateien des Pakets. Nur diese werden kopiert/gesichert.
$MP_AppFiles = @(
    'server.py', 'release_gates.py', 'index.html',
    'Backup_Datenbank.py', 'Backup_Datenbank.ps1',
    'MP_Common.ps1', 'Setup_Windows.ps1', 'INSTALLIEREN_ALS_ADMIN.ps1', 'UPDATE_LIVE.ps1',
    'Run_Server_LAN.ps1', 'Start_Server.ps1', 'Stop_Server.ps1', 'Neustart_Server.ps1',
    'Server_Status.ps1', 'CHECK_LAN_SICHERHEIT.ps1', 'Deinstallieren.ps1',
    'README_Windows.txt', 'BENUTZER_KURZANLEITUNG.txt', 'FEHLERCODES.txt', 'RELEASE_NOTES.txt'
)

# Veraltete Dateien frueherer Versionen, die im Live-Ordner nicht liegen bleiben duerfen
# (u. a. alte Updater, die V11-Code auf eine V12-Datenbank zurueckspielen koennten).
$MP_ObsoleteFiles = @(
    'UPDATE_LIVE_AUF_V11_2_3.ps1', 'UPDATE_LIVE_AUF_V11_2_4.ps1', 'UPDATE_LIVE_AUF_V11_3_1.ps1',
    'STOPP_UND_ADMINSPERRE_ENTFERNEN.ps1', 'Autostart_entfernen.ps1',
    'FEHLERCODES_V11_3_1.txt', 'RELEASE_NOTES_V11_3_1.txt', 'BENUTZER_KURZANLEITUNG.zip'
)

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
    # Der Server laeuft als SYSTEM. Liegt Python in einem Benutzerprofil, kann dieser
    # Benutzer Python-Dateien veraendern, die dann als SYSTEM ausgefuehrt werden.
    $full = [IO.Path]::GetFullPath($PythonExe)
    return -not ($full -like "$env:SystemDrive\Users\*")
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
    foreach ($sub in @('data', 'backups', 'update_backups')) {
        Set-MPFolderAcl (Join-Path $Base $sub) $false
    }
}

function Test-MPFolderAclSafe([string]$Path) {
    # Liefert $null wenn sicher, sonst eine Beschreibung des Problems.
    $acl = Get-Acl -LiteralPath $Path
    $trusted = @('S-1-5-18', 'S-1-5-32-544', 'S-1-3-0')
    $writeRights = [Security.AccessControl.FileSystemRights]'Write,Modify,FullControl,CreateFiles,AppendData,WriteData,ChangePermissions,TakeOwnership,Delete'
    foreach ($ace in $acl.Access) {
        if ($ace.AccessControlType -ne 'Allow') { continue }
        try { $sid = $ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch { $sid = [string]$ace.IdentityReference }
        if ($trusted -contains $sid) { continue }
        if (($ace.FileSystemRights -band $writeRights) -ne 0) {
            return "$($ace.IdentityReference) hat Schreibrechte ($($ace.FileSystemRights))"
        }
    }
    try { $owner = (New-Object Security.Principal.NTAccount($acl.Owner)).Translate([Security.Principal.SecurityIdentifier]).Value } catch { $owner = '' }
    if ($owner -and $owner -notin @('S-1-5-18', 'S-1-5-32-544')) { return "Besitzer ist $($acl.Owner)" }
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

function Get-MPHealth([string]$Base, [int]$TimeoutSec = 3) {
    $cfg = Join-Path $Base 'LAN_CONFIG.json'
    if (-not (Test-Path $cfg)) { return $null }
    $c = Get-Content $cfg -Raw | ConvertFrom-Json
    try {
        $r = Invoke-RestMethod -Uri "http://$($c.lan_ip):$($c.port)/api/health" -TimeoutSec $TimeoutSec
        return [PSCustomObject]@{ Config = $c; Health = $r }
    } catch { return [PSCustomObject]@{ Config = $c; Health = $null } }
}

function Wait-MPHealth([string]$Base, [string]$ExpectedVersion, [int]$Seconds = 30) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        Start-Sleep -Seconds 1
        $h = Get-MPHealth $Base 2
        if ($h -and $h.Health -and $h.Health.ok -and [string]$h.Health.version -eq $ExpectedVersion) { return $h }
    }
    return $null
}

function Assert-MPPrivatePaths([object]$Config) {
    foreach ($path in @('/server.py', '/data/maschinenplanung.sqlite3', '/backups/test.sqlite3', '/update_backups/test.txt', '/LAN_CONFIG.json')) {
        $exposed = $false
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://$($Config.lan_ip):$($Config.port)$path" -TimeoutSec 3
            if ([int]$resp.StatusCode -lt 400) { $exposed = $true }
        } catch {
            $status = $null
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            if ($status -and $status -ne 404) { throw "Unerwarteter HTTP-Status $status bei $path" }
        }
        if ($exposed) { throw "Sicherheitsfehler: $path ist per HTTP erreichbar." }
    }
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

function Invoke-MPPreflight([string]$NewSource, [string]$PythonExe, [string]$DbCopySource, [object]$Config, [string]$ExpectedVersion) {
    # Startet die NEUE Version mit einer KOPIE der Datenbank auf einem Ersatzport.
    # Das Live-System wird dabei nicht beruehrt.
    $dir = Join-Path $env:TEMP ('mp_preflight_' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Path (Join-Path $dir 'data') -Force | Out-Null
    foreach ($name in $MP_AppFiles) { Copy-Item -LiteralPath (Join-Path $NewSource $name) -Destination $dir -Force }
    Copy-Item -LiteralPath $DbCopySource -Destination (Join-Path $dir 'data\maschinenplanung.sqlite3') -Force
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
                $r = Invoke-RestMethod -Uri "http://$($Config.lan_ip):$port/api/health" -TimeoutSec 2
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

