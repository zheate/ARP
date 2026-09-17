#Requires -Version 5.1
<# Candidate for the x64 Redistributable in StarLab 3.80 (COM 10.4.0.0).
Commands come from the bundled COM manual revision 13 and VC prerequisite.
The deployment lock must remain unapproved until disconnected target tests pass.
The caller verifies the entire payload and owns timeout/indeterminate state.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][string]$LogRoot,
    [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not [Environment]::Is64BitProcess) { throw 'Use native x64 Windows PowerShell' }
$source = [IO.Path]::GetFullPath($SourceRoot)
$target = Join-Path ([Environment]::GetFolderPath('CommonProgramFiles')) 'Power Test\Ophir\10.4.0.0'
foreach ($localPath in @($source, $target, [IO.Path]::GetFullPath($LogRoot))) {
    if ($localPath -notmatch '^[A-Za-z]:\\') { throw 'Ophir source, destination and logs must be on local drives' }
}
$runtime = Join-Path $source 'runtime-x64'
$vcRedist = Join-Path $source 'vc_redist.x64.exe'
$dll = Join-Path $runtime 'OphirLMMeasurement.dll'
if (-not (Test-Path -LiteralPath $LogRoot -PathType Container)) { throw 'The caller must prepare the log directory' }
foreach ($path in @($dll, $vcRedist, (Join-Path $runtime 'swapInf.exe'), (Join-Path $runtime 'ophdrivers\ophdev.inf'), (Join-Path $runtime 'ophdrivers\pulsar.inf'))) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing reviewed Ophir asset: $path" }
}
if ((Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash -ne 'BF6F3BFE7D70D4B0216B523010D25B6EE84E9C4F76AB3F70E35D8999D68E4E00') {
    throw 'This adapter requires the inspected StarLab 3.80 x64 COM library'
}
function ConvertTo-OphirArguments {
    param([string[]]$Items)
    return (($Items | ForEach-Object {
        if ($_ -eq '' -or $_ -match '[\s"]') { '"' + ($_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"' } else { $_ }
    }) -join ' ')
}
function Invoke-OphirCommand {
    param([string]$Step, [string]$Executable, [string[]]$Items, [string]$Directory)
    $arguments = ConvertTo-OphirArguments $Items
    Write-Host "$Step : $Executable $arguments"
    $start = @{
        FilePath = $Executable; ArgumentList = $arguments; WorkingDirectory = $Directory
        Wait = $true; PassThru = $true; WindowStyle = 'Hidden'
        RedirectStandardOutput = (Join-Path $LogRoot "ophir-$Step-stdout.log")
        RedirectStandardError = (Join-Path $LogRoot "ophir-$Step-stderr.log")
    }
    $process = Start-Process @start
    try { return [int]$process.ExitCode } finally { $process.Dispose() }
}
function Test-OphirDriverStaged {
    param([string]$Inf)
    $expected = (Get-FileHash -LiteralPath $Inf -Algorithm SHA256).Hash
    foreach ($driver in @(Get-WindowsDriver -Online -All -ErrorAction Stop)) {
        if ((Split-Path $driver.OriginalFileName -Leaf) -ieq (Split-Path $Inf -Leaf) -and (Test-Path -LiteralPath $driver.OriginalFileName -PathType Leaf)) {
            if ((Get-FileHash -LiteralPath $driver.OriginalFileName -Algorithm SHA256).Hash -eq $expected) { return $true }
        }
    }
    return $false
}
$plan = [ordered]@{
    mode = 'candidate-plan-only'; installed = $false; source = $source; destination = $target
    prerequisite = @{ executable = $vcRedist; arguments = @('/q', '/norestart'); minimumVersion = '14.22.27821.0' }
    registration = @{ executable = (Join-Path $env:SystemRoot 'System32\regsvr32.exe'); arguments = @('/s', (Join-Path $target 'OphirLMMeasurement.dll')) }
    drivers = @{ executable = (Join-Path $target 'swapInf.exe'); arguments = @('StarlabDrivers', '--silent') }
    requiresOphirUsbDisconnected = $true
    acceptancePending = @('Windows 18363 offline installation', 'legacy COM coexistence', 'restart suppression', 'USB device acceptance')
}
if ($PlanOnly) {
    $plan | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $LogRoot 'ophir-install-plan.json') -Encoding UTF8
    Write-Host 'PLAN ONLY: no components copied, registered or installed.'
    exit 0
}
# Manufacturer requires disconnection before silent driver installation.
# This reads Windows PnP presence only; it never opens an instrument or sends commands.
if (@(Get-PnpDevice -PresentOnly -ErrorAction Stop | Where-Object { $_.InstanceId -match '^USB\\VID_0BD3&' }).Count) {
    throw 'Disconnect Ophir USB devices before installing their runtime and drivers'
}
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator privileges required' }
$expectedServer = Join-Path $target 'OphirLMMeasurement.dll'
$keepNewerServer = $false
$existingClass = Get-Item -LiteralPath 'HKLM:\SOFTWARE\Classes\OphirLMMeasurement.CoLMMeasurement\CLSID' -ErrorAction SilentlyContinue
if ($existingClass) {
    $existingClsid = [guid]::Empty
    if ([guid]::TryParse([string]$existingClass.GetValue(''), [ref]$existingClsid)) {
        $existingKey = Get-Item -LiteralPath "HKLM:\SOFTWARE\Classes\CLSID\$($existingClsid.ToString('B'))\InprocServer32" -ErrorAction SilentlyContinue
        if ($existingKey) {
            $existingPath = ([string]$existingKey.GetValue('')).Trim('"')
            if ($existingPath -and $existingPath -notmatch '^[A-Za-z]:\\') { throw 'Existing Ophir COM server is not on a local drive' }
            if ($existingPath -and (Test-Path -LiteralPath $existingPath -PathType Leaf)) {
                $versionInfo = (Get-Item -LiteralPath $existingPath).VersionInfo
                $existingVersion = [version]('{0}.{1}.{2}.{3}' -f $versionInfo.FileMajorPart, $versionInfo.FileMinorPart, $versionInfo.FileBuildPart, $versionInfo.FilePrivatePart)
                if ($existingVersion -gt [version]'10.4.0.0') { $keepNewerServer = $true; $expectedServer = [IO.Path]::GetFullPath($existingPath) }
            }
        }
    }
}
$reboot = $false
$vc = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
$vcVersion = $null
$vcReady = $vc -and $vc.PSObject.Properties['Installed'] -and $vc.Installed -eq 1 -and $vc.PSObject.Properties['Version'] -and [version]::TryParse(([string]$vc.Version).TrimStart('v'), [ref]$vcVersion) -and $vcVersion -ge [version]'14.22.27821.0'
if (-not $vcReady) {
    $code = Invoke-OphirCommand 'vc-runtime' $vcRedist @('/q', '/norestart') $source
    if ($code -eq 3010) { $reboot = $true } elseif ($code -ne 0) { throw "VC++ runtime failed: $code" }
    $vc = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction Stop
    if ($vc.Installed -ne 1 -or -not [version]::TryParse(([string]$vc.Version).TrimStart('v'), [ref]$vcVersion) -or $vcVersion -lt [version]'14.22.27821.0') { throw 'VC++ runtime postcondition failed' }
}
# Use a machine-wide, versioned directory, independent of the application path.
# Refuse modified existing files rather than overwriting a possibly loaded DLL.
$files = @(Get-ChildItem -LiteralPath $runtime -File -Recurse)
foreach ($file in $files) {
    $relative = $file.FullName.Substring($runtime.Length).TrimStart('\')
    $destination = Join-Path $target $relative
    if (Test-Path -LiteralPath $destination) {
        if ((Get-FileHash -LiteralPath $destination).Hash -ne (Get-FileHash -LiteralPath $file.FullName).Hash) { throw "Conflicting existing Ophir file: $destination" }
    }
}
foreach ($file in $files) {
    $relative = $file.FullName.Substring($runtime.Length).TrimStart('\')
    $destination = Join-Path $target $relative
    if (-not (Test-Path -LiteralPath $destination)) {
        [void](New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force)
        Copy-Item -LiteralPath $file.FullName -Destination $destination
    }
    if ((Get-FileHash -LiteralPath $destination).Hash -ne (Get-FileHash -LiteralPath $file.FullName).Hash) { throw "Ophir copy verification failed: $destination" }
}
if (-not $keepNewerServer) {
    $code = Invoke-OphirCommand 'register' $plan.registration.executable $plan.registration.arguments $target
    if ($code -ne 0) { throw "Ophir COM registration failed: $code" }
}
$code = Invoke-OphirCommand 'drivers' $plan.drivers.executable $plan.drivers.arguments $target
if ($code -ne 0) { throw "Ophir driver installer failed: $code" }
foreach ($name in @('ophdev.inf', 'pulsar.inf')) {
    if (-not (Test-OphirDriverStaged (Join-Path $target "ophdrivers\$name"))) { throw "Ophir driver was not found in Driver Store: $name" }
}
# Check the registered server, not just a copied library. Outer COM probe also
# checks version and activation before marking the component successful.
$clsid = (Get-Item -LiteralPath 'HKLM:\SOFTWARE\Classes\OphirLMMeasurement.CoLMMeasurement\CLSID').GetValue('')
$server = (Get-Item -LiteralPath "HKLM:\SOFTWARE\Classes\CLSID\$clsid\InprocServer32").GetValue('')
if ([IO.Path]::GetFullPath(([string]$server).Trim('"')) -ine $expectedServer) { throw 'Ophir COM points to a different runtime' }
$instance = $null
try { $instance = New-Object -ComObject OphirLMMeasurement.CoLMMeasurement -ErrorAction Stop }
finally { if ($instance) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($instance) } }
Write-Host 'Ophir COM and driver postconditions passed. Hardware acceptance is separate.'
if ($reboot) { exit 3010 }
exit 0
