#Requires -Version 5.1
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$packaging = Split-Path -Parent $PSScriptRoot
$errorsFound = @()
foreach ($file in Get-ChildItem -LiteralPath $packaging -Recurse -File -Filter '*.ps1') {
    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
    foreach ($error in $errors) { $errorsFound += "$($file.Name):$($error.Extent.StartLineNumber): $($error.Message)" }
    if ($file.Directory.Name -eq 'offline' -or $file.Name -eq 'install_full_bundle.ps1') {
        foreach ($node in $ast.FindAll({ param($a) $a -is [Management.Automation.Language.CommandAst] }, $true)) {
            if ($node.GetCommandName() -match '^(?i:Invoke-WebRequest|Invoke-RestMethod|Start-BitsTransfer|curl|wget|winget|choco|Invoke-Expression|iex)$') { $errorsFound += "$($file.Name): network/dynamic command in deployment path" }
        }
        foreach ($node in $ast.FindAll({ param($a) $a -is [Management.Automation.Language.StringConstantExpressionAst] }, $true)) {
            if ($node.Value -match '^(?i:https?://|ftp://|\\\\)') { $errorsFound += "$($file.Name): remote resource literal in deployment path" }
        }
    }
}
if ($errorsFound.Count) { throw ($errorsFound -join "`n") }
. (Join-Path $packaging 'offline\Common.ps1')
. (Join-Path $packaging 'offline\Components.ps1')
$script:Deploy = @{ DryRun = $true; RebootRequired = $false; PayloadRoot = $packaging; LogRoot = $packaging; Journal = '' }
function Assert-Equal { param($Actual, $Expected); if ($Actual -cne $Expected) { throw "Assertion failed: [$Actual] != [$Expected]" } }
Assert-Equal (ConvertTo-NativeArguments @('a', 'two words', '')) 'a "two words" ""'
Assert-Equal (ConvertTo-NativeArguments @('D:\Power Test\')) '"D:\Power Test\\"'
Assert-Equal (ConvertTo-NativeArguments @('say"hi')) '"say\"hi"'
Assert-Equal (Test-VersionRange '2.0' ([pscustomobject]@{ minimumVersion = '1.0' })) $true
Assert-Equal (Test-VersionRange '0.5' ([pscustomobject]@{ minimumVersion = '1.0' })) $false
foreach ($value in @('../escape', 'C:\escape', 'sub\..\escape', 'a:stream', 'NUL')) {
    $rejected = $false
    try { [void](Get-PayloadPath $packaging $value) } catch { $rejected = $true }
    if (-not $rejected) { throw "Unsafe payload path accepted: $value" }
}
# DryRun must not launch even a nonexistent executable or create a journal.
Assert-Equal (Invoke-DeployProcess 'dry-test' (Join-Path $packaging 'definitely-missing.exe') @('arg')) 0
Assert-Equal $script:Deploy.RebootRequired $false
# Mock only installer/OS boundaries: successful PnP exit still needs verification.
function Invoke-DeployProcess { return 259 }
function Test-DriverStaged { return $false }
$script:Deploy.DryRun = $false
function Write-DeployEvent { param($Component, $State, $Detail) }
$rejected = $false
try { Install-PnpComponent ([pscustomobject]@{ id = 'pnp-test'; inf = @('test.inf') }) } catch { $rejected = $true }
if (-not $rejected) { throw 'PnP 259 accepted without Driver Store verification' }
function Test-DriverStaged { return $true }
Install-PnpComponent ([pscustomobject]@{ id = 'pnp-test'; inf = @('test.inf') })
Write-Host 'PowerShell parser and boundary-mocked logic tests passed. This is not Windows installation/hardware acceptance.'
