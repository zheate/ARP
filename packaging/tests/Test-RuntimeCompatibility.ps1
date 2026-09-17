#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\offline\Common.ps1')
. (Join-Path $PSScriptRoot '..\offline\Components.ps1')
$script:Deploy = @{ DryRun = $false; PayloadRoot = $PSScriptRoot; LogRoot = $PSScriptRoot }
$script:installedVersion = ''
$script:installCalls = 0
$script:finishVersion = '23.5'
function Test-RuntimeProbe { param($Probe); return Test-VersionRange $script:installedVersion $Probe }
function Write-DeployEvent { param($Component, $State, $Detail) }
function Invoke-DeployProcess {
    param($Component, $FilePath, $Arguments, $SuccessCodes, $RebootCodes, $TimeoutSeconds, $WorkingDirectory)
    if ($Component -ne 'ni-runtime' -or $Arguments -notcontains '-FeedPath') { throw 'Wrong component/command dispatched' }
    $script:installCalls++
    $script:installedVersion = $script:finishVersion
    return 0
}
$component = [pscustomobject]@{
    id = 'ni-runtime'; kind = 'vendor'; launcher = 'powershell64'; path = 'fixture.ps1'
    arguments = @('-FeedPath', '{PayloadRoot}/feed', '-LogRoot', '{LogRoot}')
    successExitCodes = @(0); rebootExitCodes = @(3010); timeoutSeconds = 1800
    detect = @([pscustomobject]@{ kind = 'file-version'; minimumVersion = '23.5' })
}
# Existing installed components do not create a blanket rejection. Versioned
# postconditions, not uninstall-entry counts, decide reuse versus installation.
foreach ($version in @('23.5', '24.0')) {
    $script:installedVersion = $version
    $script:installCalls = 0
    Install-RuntimeComponent $component
    if ($script:installCalls -ne 0) { throw 'A sufficient existing runtime was reinstalled/downgraded' }
}
foreach ($version in @('', '9.7')) {
    $script:installedVersion = $version
    $script:installCalls = 0
    Install-RuntimeComponent $component
    if ($script:installCalls -ne 1) { throw 'Missing/old runtime did not invoke its reviewed installer' }
}
$script:installedVersion = '9.7'
$script:finishVersion = '9.7'
$rejected = $false
try { Install-RuntimeComponent $component } catch { $rejected = $true }
if (-not $rejected) { throw 'Successful installer exit incorrectly waived failed runtime postconditions' }
$component.detect[0] | Add-Member -NotePropertyName maximumVersion -NotePropertyValue '24.0'
$script:installedVersion = '25.0'
$script:installCalls = 0
$rejected = $false
try { Install-RuntimeComponent $component } catch { $rejected = $true }
if (-not $rejected -or $script:installCalls -ne 0) { throw 'Out-of-review version was silently downgraded' }
Write-Host 'Runtime reuse, old/missing runtime installation and postcondition checks passed (mock installers; no NI changes).'
