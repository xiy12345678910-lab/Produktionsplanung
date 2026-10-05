# V12.17.2: Tests fuer die ACL-Bitpruefung (Test-MPRightsWritable) und Klartext der Task-Ergebnisse.
# Rein: laeuft auch unter Linux (Funktionen werden per AST aus MP_Common.ps1 geladen).
# Unter Windows zusaetzlich echter ACL-Test mit icacls (Users:RX = sicher, Users:Modify = unsicher).
$root = Split-Path -Parent $PSScriptRoot
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'MP_Common.ps1'), [ref]$null, [ref]$null)
foreach ($n in 'Test-MPRightsWritable', 'Get-MPTaskResultText', 'Test-MPFolderAclSafe') {
    $fn = $ast.Find({ param($a) $a -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $a.Name -eq $n }, $true)
    . ([scriptblock]::Create($fn.Extent.Text))
}
$bad = 0; $total = 0
function Check([bool]$ok, [string]$label) {
    $script:total++
    if ($ok) { Write-Host "PASS $label" } else { $script:bad++; Write-Host "FAIL $label" }
}
$R = [Security.AccessControl.FileSystemRights]
$GR = [int64]0x80000000 - 0x100000000   # GENERIC_READ als Int32 (negativ)
$GE = [int64]0x20000000
$cases = @(
    @('ReadAndExecute+Synchronize', [int64]($R::ReadAndExecute -bor $R::Synchronize), $false),
    @('ReadAndExecute', [int64]$R::ReadAndExecute, $false),
    @('Read', [int64]$R::Read, $false),
    @('GENERIC_READ|GENERIC_EXECUTE', ($GR -bor $GE), $false),
    @('Synchronize', [int64]$R::Synchronize, $false),
    @('Modify', [int64]$R::Modify, $true),
    @('Write', [int64]$R::Write, $true),
    @('GENERIC_WRITE', [int64]0x40000000, $true),
    @('GENERIC_ALL', [int64]0x10000000, $true),
    @('FullControl', [int64]$R::FullControl, $true),
    @('AppendData', [int64]$R::AppendData, $true),
    @('WriteData', [int64]$R::WriteData, $true),
    @('Delete', [int64]$R::Delete, $true),
    @('ChangePermissions', [int64]$R::ChangePermissions, $true),
    @('TakeOwnership', [int64]$R::TakeOwnership, $true),
    @('WriteAttributes', [int64]$R::WriteAttributes, $true),
    @('DeleteSubdirectoriesAndFiles', [int64]$R::DeleteSubdirectoriesAndFiles, $true)
)
foreach ($c in $cases) { Check ((Test-MPRightsWritable $c[1]) -eq $c[2]) "Rechte $($c[0]) -> $($c[2])" }
Check ((Get-MPTaskResultText 267009) -eq 'laeuft') 'Task-Ergebnis 267009 = laeuft'
Check ((Get-MPTaskResultText 0) -eq 'OK') 'Task-Ergebnis 0 = OK'
Check ((Get-MPTaskResultText 1) -match 'Ergebnis 1') 'Task-Ergebnis unbekannt bleibt Zahl'

if ($IsWindows -or $env:OS -eq 'Windows_NT') {
    $d = Join-Path ([IO.Path]::GetTempPath()) ('mpacl_' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $d | Out-Null
    try {
        & icacls $d /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
        Check ($null -eq (Test-MPFolderAclSafe $d $false)) 'echte ACL: Users:RX -> sicher'
        & icacls $d /grant '*S-1-5-32-545:(OI)(CI)M' | Out-Null
        Check ($null -ne (Test-MPFolderAclSafe $d $false)) 'echte ACL: Users:Modify -> unsicher'
        & icacls $d /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-11:(OI)(CI)W' | Out-Null
        Check ($null -ne (Test-MPFolderAclSafe $d $false)) 'echte ACL: Authentifizierte Benutzer Write -> unsicher'
    } finally {
        & icacls $d /reset | Out-Null
        Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Write-Host "`n$($total - $bad)/$total bestanden"
exit [int]($bad -gt 0)
