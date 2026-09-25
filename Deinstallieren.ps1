# Stoppt den Server und entfernt Autostart, Backup-Task und Firewallregeln.
# Programmordner, Datenbank, Backups und Benutzerkonten werden NICHT geloescht.
$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
Assert-MPAdmin 'Deinstallieren.ps1'
try { Stop-MPServer $Base } catch { Write-Warning $_.Exception.Message }
foreach ($name in @($MP_TaskName, $MP_BackupTaskName) + $MP_LegacyTaskNames) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
}
($MP_FirewallRules + $MP_LegacyFirewallRules) | ForEach-Object { Remove-NetFirewallRule -DisplayName $_ -ErrorAction SilentlyContinue }
Write-Host 'Server gestoppt; Autostart, Backup-Task und Firewallregeln entfernt.' -ForegroundColor Green
Write-Host "Daten bleiben erhalten: $Base\data und $Base\backups" -ForegroundColor DarkGray
