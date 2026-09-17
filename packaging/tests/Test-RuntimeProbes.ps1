#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\offline\Common.ps1')
. (Join-Path $PSScriptRoot '..\offline\Components.ps1')
$script:Deploy = @{ PayloadRoot = $PSScriptRoot }
$script:serverPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$script:activationCalls = 0
$script:activationResult = $true
function Get-MachineComServerPath { param($ProgId); return $script:serverPath }
function Test-ComActivation { param($ProgId); $script:activationCalls++; return $script:activationResult }
$probe = [pscustomobject]@{ kind = 'com'; progId = 'OphirLMMeasurement.CoLMMeasurement'; minimumVersion = '1.0' }
if (-not (Test-RuntimeProbe $probe) -or $script:activationCalls -ne 1) { throw 'Registered server version and COM activation should both pass' }
$script:activationResult = $false
if (Test-RuntimeProbe $probe) { throw 'A versioned server does not prove COM activation' }
$script:activationResult = $true
$probe.minimumVersion = '65535.0'
$script:activationCalls = 0
if ((Test-RuntimeProbe $probe) -or $script:activationCalls -ne 0) { throw 'Old registered server should fail before activation' }
$probe.minimumVersion = '1.0'
$probe | Add-Member -NotePropertyName maximumVersion -NotePropertyValue '1.0'
$rejected = $false
try { Test-RuntimeProbe $probe | Out-Null } catch { $rejected = $true }
if (-not $rejected -or $script:activationCalls -ne 0) { throw 'Unreviewed newer registered server should refuse before activation' }
$probe.PSObject.Properties.Remove('maximumVersion')
$script:serverPath = ''
if ((Test-RuntimeProbe $probe) -or $script:activationCalls -ne 0) { throw 'Missing machine registration must not pass from a copied file' }
$script:serverPath = '\\server\runtime\OphirLMMeasurement.dll'
if ((Test-RuntimeProbe $probe) -or $script:activationCalls -ne 0) { throw 'Remote COM registration must not trigger network loading' }
$script:staged = $false
function Test-DriverStaged {
    param([string]$InfPath)
    if ($InfPath -ne (Join-Path $PSScriptRoot 'ophdrivers\ophdev.inf')) { throw 'Wrong driver reference' }
    return $script:staged
}
$driver = [pscustomobject]@{ kind = 'driver-store'; inf = 'ophdrivers/ophdev.inf' }
if (Test-RuntimeProbe $driver) { throw 'Missing driver must fail a vendor component postcondition' }
$script:staged = $true
if (-not (Test-RuntimeProbe $driver)) { throw 'Verified Driver Store entry should pass' }
$driver.inf = '../escape.inf'
$rejected = $false
try { Test-RuntimeProbe $driver | Out-Null } catch { $rejected = $true }
if (-not $rejected) { throw 'Driver probe escaped the verified payload' }
Write-Host 'Registered COM version/activation and vendor Driver Store probe boundaries passed (mock registration/activation/Driver Store).'
