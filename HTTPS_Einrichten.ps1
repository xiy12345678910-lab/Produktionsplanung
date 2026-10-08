# Maschinenplanung - HTTPS im LAN einrichten (einmalig, als Administrator im Live-Ordner).
#   cd C:\ProgramData\Maschinenplanung
#   .\HTTPS_Einrichten.ps1                         (Zertifikat fuer LAN-IP und PC-Namen)
#   .\HTTPS_Einrichten.ps1 -Name mp.firma.local    (zusaetzlicher DNS-Name, mehrfach moeglich)
# Legt in config\tls eine eigene Firmen-CA und ein Serverzertifikat an (nur SYSTEM/Administratoren lesbar),
# vertraut der CA auf diesem Server, legt Firmen-CA.crt zum Verteilen in den Live-Ordner und startet den Server neu.
# Erneut ausfuehren erneuert nur das Serverzertifikat; die Firmen-CA bleibt, Arbeitsplaetze muessen nichts neu importieren.
# Neue LAN-IP oder Ablauf in < 30 Tagen erneuert der Server beim Start selbst.
param([string[]]$Name = @())
$ErrorActionPreference = 'Stop'
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Base 'MP_Common.ps1')
Assert-MPAdmin 'HTTPS_Einrichten.ps1'
$c = Read-MPConfig $Base
if (-not $c) { throw 'LAN_CONFIG.json fehlt - Server zuerst einmal starten (Start_Server.ps1).' }
$PythonExe = Get-MPPython
# #52: gleiche Implementierung wie Setup_Windows.ps1 (MP_Common.ps1, Install-MPTls).
$r = Install-MPTls $Base $PythonExe ([string]$c.lan_ip) $Name
$public = $r.PublicCa
Write-Host "Firmen-CA vertraut (Fingerabdruck $($r.Thumbprint)). Zum Verteilen: $public" -ForegroundColor Green

Stop-ScheduledTask -TaskName $MP_TaskName -ErrorAction SilentlyContinue
[void](Wait-MPTaskIdle $MP_TaskName 30)
Start-ScheduledTask -TaskName $MP_TaskName
$h = Wait-MPHealth $Base (Get-MPPackageVersion $Base) 30
if ($h) {
    Write-Host "PASS - Server laeuft jetzt auf $($h.Url)" -ForegroundColor Green
    Write-Host 'Arbeitsplaetze: Firmen-CA.crt einmal als vertrauenswuerdige Stammzertifizierungsstelle importieren (README_Windows.txt 2c).'
} else {
    Write-Host 'FEHLER - Server antwortet per HTTPS nicht. Log: logs\server_<Datum>.log. Zurueck auf HTTP: Ordner config\tls umbenennen und Neustart_Server.ps1.' -ForegroundColor Red
    exit 1
}
