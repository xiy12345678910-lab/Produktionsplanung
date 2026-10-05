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
$n = (Get-ChildItem -LiteralPath $root -Recurse -Filter '*.ps1').Count
Write-Host "`n$($n - $bad)/$n bestanden"
exit [int]($bad -gt 0)
