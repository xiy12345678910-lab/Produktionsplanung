$ErrorActionPreference = 'SilentlyContinue'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
$expected = Get-MPPackageVersion $Base
Write-Host "=== Maschinenplanung V$expected - Status ===" -ForegroundColor Cyan
foreach ($name in @($MP_TaskName, $MP_BackupTaskName)) {
    $task = Get-ScheduledTask -TaskName $name
    if ($task) {
        $info = $task | Get-ScheduledTaskInfo
        # 0 = ok, 2 = lokales Backup ok / Zweitkopie fehlgeschlagen, 267009/267011 = laeuft / noch nie gelaufen
        $res = [int64]$info.LastTaskResult
        $color = if ($res -in @(0, 267009, 267011)) { 'Gray' } elseif ($res -eq 2) { 'Yellow' } else { 'Red' }
        Write-Host "$name : $($task.State) | letzter Lauf $($info.LastRunTime) ($(Get-MPTaskResultText $res))" -ForegroundColor $color
        if ($name -eq $MP_BackupTaskName -and $res -notin @(0, 267009, 267011)) {
            Write-Host "  WARNUNG: letztes automatisches Backup fehlgeschlagen$(if ($res -eq 2) { ' (nur Zweitkopie)' })." -ForegroundColor $color
        }
    } else { Write-Host "$name : NICHT INSTALLIERT" -ForegroundColor Red }
}
$h = Get-MPHealth $Base
if ($h) {
    Write-Host "Bind-IP: $($h.Config.lan_ip) | Subnetz: $($h.Config.subnet) | Netzprofil: $($h.Config.profile)"
    if ($h.Health -and $h.Health.ok) {
        $color = if ([string]$h.Health.version -eq $expected) { 'Green' } else { 'Yellow' }
        Write-Host "Server: laeuft V$($h.Health.version) (Paket V$expected)" -ForegroundColor $color
    } else { Write-Host 'Server: NICHT ERREICHBAR' -ForegroundColor Red }
} else { Write-Host 'LAN_CONFIG.json fehlt oder ist gerade nicht lesbar.' -ForegroundColor Red }
$last = Get-ChildItem (Join-Path $Base 'backups') -Filter 'maschinenplanung_*.sqlite3' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($last) {
    $age = (Get-Date) - $last.LastWriteTime
    $color = if ($age.TotalHours -gt 26) { 'Red' } else { 'Gray' }
    Write-Host "Letztes Backup: $($last.Name) ($($last.LastWriteTime))$(if ($age.TotalHours -gt 26) { ' - AELTER ALS 26 H' })" -ForegroundColor $color
} else { Write-Host 'Letztes Backup: KEINES' -ForegroundColor Red }
Get-NetFirewallRule -DisplayName 'Maschinenplanung*' | ForEach-Object {
    $pf = $_ | Get-NetFirewallPortFilter; $af = $_ | Get-NetFirewallAddressFilter
    Write-Host "Firewall: $($_.DisplayName) | $($_.Action) | Profile=$($_.Profile) | Local=$($af.LocalAddress -join ',') | Remote=$($af.RemoteAddress -join ',') | Port=$($pf.LocalPort)"
}
Write-Host '--- Letzte Zeilen Server-Log ---' -ForegroundColor Cyan
Write-Host (Get-MPLogTail $Base 15)
