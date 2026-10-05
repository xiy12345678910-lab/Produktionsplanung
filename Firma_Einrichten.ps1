# Maschinenplanung - Firmenprofil (config\firma.json) aus einer Vorlage anlegen.
# Noetig, wenn ein Bestand von V12.13 oder aelter ohne Firmenprofil aktualisiert wird (Fehlercode MP-CFG-006).
# Als Administrator aus dem NEUEN Paketordner starten, vor UPDATE_LIVE.ps1:
#   .\Firma_Einrichten.ps1 -Vorlage C:\Pfad\legacy_employer_seed.json     (Name, Farbe, Logo, Begriffe aus der Vorlage)
#   .\Firma_Einrichten.ps1 -Neutral                                         (bewusst ohne Firmennamen/Logo)
# -Ziel: Live-Ordner, falls nicht der Standard (ProgramData\Maschinenplanung). Eine vorhandene firma.json wird nie ueberschrieben.
param(
    [string]$Vorlage,
    [switch]$Neutral,
    [string]$Ziel
)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Here 'MP_Common.ps1')
Assert-MPAdmin 'Firma_Einrichten.ps1'
if (-not $Vorlage -and -not $Neutral) { throw 'Bitte -Vorlage <datei> oder -Neutral angeben.' }
if (-not $Ziel) { $Ziel = $MP_InstallBase }
if ($Vorlage) {
    if (-not (Test-Path -LiteralPath $Vorlage)) { throw "Vorlage nicht gefunden: $Vorlage" }
    $arg = (Resolve-Path -LiteralPath $Vorlage).Path
} else { $arg = 'neutral' }
$PythonExe = Get-MPPython
New-Item -ItemType Directory -Path $Ziel -Force | Out-Null
$env:MP_CONFIG_DIR = Join-Path $Ziel $MP_ConfigDir
try {
    & $PythonExe -X utf8 -I (Join-Path $Here 'server.py') --firma-einrichten $arg
    $code = $LASTEXITCODE
} finally {
    Remove-Item Env:\MP_CONFIG_DIR -ErrorAction SilentlyContinue
}
if ($code -eq 4) { Write-Host 'Es gibt bereits eine firma.json - nichts geaendert.' -ForegroundColor Yellow; exit 0 }
if ($code -ne 0) { throw "Firmenprofil konnte nicht angelegt werden (Exitcode $code)." }
Write-Host "Fertig: $($env:MP_CONFIG_DIR) - jetzt UPDATE_LIVE.ps1 starten." -ForegroundColor Green
