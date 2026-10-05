# Maschinenplanung - Update direkt von GitHub (fuer den normalen Windows-Benutzer, z. B. boensch).
# Laedt das Paket in den eigenen Download-Ordner, entsperrt die Dateien und startet
# UPDATE_LIVE.ps1 in einem Administrator-Fenster (UAC-Abfrage). Daten bleiben erhalten.
#
# Aufruf (PowerShell, KEIN Administrator noetig):
#   powershell -ExecutionPolicy Bypass -File .\Update_von_GitHub.ps1
#   powershell -ExecutionPolicy Bypass -File .\Update_von_GitHub.ps1 -Branch main
#   powershell -ExecutionPolicy Bypass -File .\Update_von_GitHub.ps1 -NurHerunterladen
param(
    [string]$Branch = 'claude/new-session-mpx5ch',
    [string]$Repo = 'xiy12345678910-lab/Produktionsplanung',
    [string]$Ziel = (Join-Path $env:USERPROFILE 'Downloads\Maschinenplanung_Update'),
    [switch]$NurHerunterladen
)
$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 nutzt sonst ggf. TLS 1.0 - GitHub verlangt TLS 1.2
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'   # Fortschrittsbalken bremst Invoke-WebRequest stark

$work = Join-Path $Ziel (Get-Date -Format 'yyyy-MM-dd_HHmmss')
New-Item -ItemType Directory -Path $work -Force | Out-Null
$zip = Join-Path $work 'paket.zip'
$url = "https://codeload.github.com/$Repo/zip/refs/heads/$Branch"

Write-Host "Benutzer : $env:USERDOMAIN\$env:USERNAME"
Write-Host "Quelle   : $Repo  (Branch $Branch)"
Write-Host "Ziel     : $work"
try {
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
} catch {
    throw "Download fehlgeschlagen ($url): $($_.Exception.Message). Internet/Proxy pruefen oder Branch-Namen kontrollieren."
}
Expand-Archive -LiteralPath $zip -DestinationPath $work -Force
Remove-Item -LiteralPath $zip -Force

$pkg = Get-ChildItem -LiteralPath $work -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'UPDATE_LIVE.ps1') } | Select-Object -First 1
if (-not $pkg) { throw 'Im heruntergeladenen Paket fehlt UPDATE_LIVE.ps1.' }
# Aus dem Internet geladene Skripte sind gesperrt (Zone.Identifier) - entsperren
Get-ChildItem -LiteralPath $pkg.FullName -Recurse -File | Unblock-File

$m = Select-String -LiteralPath (Join-Path $pkg.FullName 'server.py') -Pattern '^APP_VERSION = "([^"]+)"' | Select-Object -First 1
$ver = if ($m) { $m.Matches[0].Groups[1].Value } else { '?' }
Write-Host ""
Write-Host "Paket V$ver bereit: $($pkg.FullName)" -ForegroundColor Green

if ($NurHerunterladen) {
    Write-Host "Installation spaeter: PowerShell als Administrator, dann"
    Write-Host "  Set-Location '$($pkg.FullName)'; Set-ExecutionPolicy -Scope Process Bypass; .\UPDATE_LIVE.ps1"
    return
}

$cmd = "Set-Location -LiteralPath '$($pkg.FullName)'; try { & '.\UPDATE_LIVE.ps1' } catch { Write-Host `$_ -ForegroundColor Red }; Write-Host ''; Read-Host 'Fertig - Enter schliesst dieses Fenster'"
try {
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $cmd)
} catch {
    throw "Administrator-Fenster wurde nicht gestartet (UAC abgelehnt?): $($_.Exception.Message)"
}
Write-Host "UPDATE_LIVE.ps1 laeuft im Administrator-Fenster. Danach: .\CHECK_LAN_SICHERHEIT.ps1 und alle Browser mit Strg+F5 neu laden."
