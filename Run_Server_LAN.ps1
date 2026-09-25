# Wird vom Scheduled Task "Maschinenplanung Server" als SYSTEM gestartet.
# Ermittelt das physische LAN, setzt die Firewallregeln und startet server.py LAN-only.
# Alle Ausgaben (auch Fehler) landen in logs\server_<Datum>.log.
param(
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Base 'logs'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$Log = Join-Path $LogDir ('server_' + (Get-Date -Format 'yyyy-MM-dd') + '.log')
function Write-Log([string]$Text) {
    Add-Content -LiteralPath $Log -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Text) -Encoding UTF8
}
# Alte Logs nach 30 Tagen entfernen
Get-ChildItem -LiteralPath $LogDir -Filter 'server_*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force -ErrorAction SilentlyContinue

try {
    . (Join-Path $Base 'MP_Common.ps1')
    Write-Log "=== Start Run_Server_LAN (Python: $PythonExe, Port $Port) ==="

    $lan = Get-MPLanInfo
    if (-not $lan) {
        throw 'Kein aktives physisches IPv4-LAN mit Netzwerkprofil Privat oder Domaene gefunden. Server wird aus Sicherheitsgruenden NICHT gestartet.'
    }
    $Subnet = (& $PythonExe -I -c "import ipaddress; print(ipaddress.ip_network('$($lan.IP)/$($lan.PrefixLength)', strict=False))").Trim()
    if (-not $Subnet) { throw 'LAN-Subnetz konnte nicht ermittelt werden.' }

    ($MP_FirewallRules + $MP_LegacyFirewallRules) | ForEach-Object {
        Remove-NetFirewallRule -DisplayName $_ -ErrorAction SilentlyContinue | Out-Null
    }
    New-NetFirewallRule -DisplayName $MP_FwAllow -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
        -LocalAddress $lan.IP -RemoteAddress $Subnet -Profile Private, Domain | Out-Null
    New-NetFirewallRule -DisplayName $MP_FwBlockPublic -Direction Inbound -Action Block -Protocol TCP -LocalPort $Port `
        -Profile Public | Out-Null
    try {
        New-NetFirewallRule -DisplayName $MP_FwBlockInternet -Direction Inbound -Action Block -Protocol TCP -LocalPort $Port `
            -LocalAddress $lan.IP -RemoteAddress Internet -Profile Private, Domain | Out-Null
    } catch {
        Write-Log 'WARNUNG: Firewall-Internet-Sperre konnte nicht angelegt werden; LAN-Allow + Subnetzpruefung bleiben aktiv.'
    }

    $config = [ordered]@{
        lan_ip                = $lan.IP
        prefix_length         = $lan.PrefixLength
        subnet                = $Subnet
        port                  = $Port
        interface             = $lan.InterfaceAlias
        interface_description = $lan.InterfaceDescription
        profile               = $lan.Profile
        updated_at            = (Get-Date).ToString('o')
    }
    $config | ConvertTo-Json | Set-Content -Path "$Base\LAN_CONFIG.json" -Encoding UTF8
    @(
        "PC-Name: http://$env:COMPUTERNAME`:$Port",
        "LAN-IP:  http://$($lan.IP)`:$Port",
        "Subnetz: $Subnet",
        "Adapter: $($lan.InterfaceAlias) / $($lan.InterfaceDescription)",
        '',
        'Zugriff ist ausschliesslich aus diesem lokalen Subnetz erlaubt.'
    ) | Set-Content -Path "$Base\LAN_ADRESSEN.txt" -Encoding UTF8

    Write-Log "LAN-only Bind: $($lan.IP):$Port / Subnetz $Subnet / Adapter $($lan.InterfaceAlias)"
} catch {
    Write-Log ("FEHLER vor dem Serverstart: " + $_.Exception.Message)
    exit 1
}

# Python-Ausgaben (inkl. Fehler/Traceback) zeilenweise ins Log.
# -I: isolierter Modus; -u: ungepuffert; -X utf8: Umlaute unabhaengig von der Windows-Codepage.
# Stderr-Zeilen duerfen hier keinen PowerShell-Abbruch ausloesen -> Continue.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
& $PythonExe -X utf8 -u -I "$Base\server.py" --host $lan.IP --port $Port --allowed-subnet $Subnet 2>&1 |
    ForEach-Object { Write-Log ([string]$_) }
$code = $LASTEXITCODE
Write-Log "=== server.py beendet (Exitcode $code) ==="
exit $code
