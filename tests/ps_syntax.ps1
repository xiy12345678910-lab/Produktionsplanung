# Syntaxpruefung aller PowerShell-Skripte (ohne Ausfuehrung). Aufruf: pwsh -File tests/ps_syntax.ps1
$root = Split-Path -Parent $PSScriptRoot
$bad = 0
foreach ($f in Get-ChildItem -LiteralPath $root -Recurse -Filter '*.ps1' | Where-Object { $_.FullName -notmatch '[\\/]\.git[\\/]' }) {
    $tokens = $null; $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count) {
        $bad++
        foreach ($e in $errors) { Write-Host "FAIL $($f.Name):$($e.Extent.StartLineNumber) $($e.Message)" }
    } else { Write-Host "PASS $($f.Name)" }
}
# V12.14.0: config\ darf nie in $MP_AppFiles / $MP_ObsoleteFiles stehen (Updates fassen sie nicht an).
$common = Get-Content -LiteralPath (Join-Path $root 'MP_Common.ps1') -Raw
foreach ($var in 'MP_AppFiles', 'MP_ObsoleteFiles') {
    $m = [regex]::Match($common, '\$' + $var + '\s*=\s*@\((.*?)\r?\n\)', 'Singleline')
    if (-not $m.Success -or $m.Groups[1].Value -match "'config[\\/]?[^']*'") { $bad++; Write-Host "FAIL $var enthaelt config oder fehlt" } else { Write-Host "PASS $var ohne config" }
}
$n = (Get-ChildItem -LiteralPath $root -Recurse -Filter '*.ps1').Count
Write-Host "`n$($n - $bad)/$n bestanden"
exit [int]($bad -gt 0)
