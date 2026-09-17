#Requires -Version 5.1
<#
Candidate adapter for the reviewed NI 23.5 media and NIPM 24.0.
Native CLI help and isolated configuration were checked on the build machine.
Disconnected installation/legacy-upgrade/restart acceptance is required before
this adapter may be marked approved in the deployment lock.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$FeedPath,
    [Parameter(Mandatory = $true)][string]$LogRoot,
    [switch]$Simulate
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not [Environment]::Is64BitProcess) { throw 'Use native x64 Windows PowerShell' }
$preservationScript = Join-Path $PSScriptRoot 'NiPreservation.ps1'
if (-not (Test-Path -LiteralPath $preservationScript)) { $preservationScript = Join-Path $PSScriptRoot '..\offline\NiPreservation.ps1' }
. $preservationScript
if (-not $Simulate -and @(Get-ExistingNiSoftware).Count) {
    throw 'Existing NI runtime/applications must be preserved. This candidate cannot change their NI stack; qualify reuse instead. -Simulate is read-only.'
}
$nipkg = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'National Instruments\NI Package Manager\nipkg.exe'
if (-not (Test-Path -LiteralPath $nipkg -PathType Leaf)) { throw 'NI Package Manager must be installed first' }
$info = (Get-Item -LiteralPath $nipkg).VersionInfo
if ($info.FileMajorPart -ne 24 -or $info.FileMinorPart -ne 0) { throw 'This candidate adapter has only been inspected with NIPM 24.0; review other releases first' }
$feed = [IO.Path]::GetFullPath($FeedPath)
if ($feed -notmatch '^[A-Za-z]:\\' -or -not (Test-Path -LiteralPath (Join-Path $feed 'Packages') -PathType Leaf)) { throw 'A complete local NI feed is required' }
if (-not (Test-Path -LiteralPath $LogRoot -PathType Container)) { throw 'The caller must prepare the log directory' }
# --config replaces the caller's feed configuration. Never register/remove
# feeds in the machine or operator configuration to perform this deployment.
$config = Join-Path ([IO.Path]::GetFullPath($LogRoot)) ('ni-local-' + [guid]::NewGuid().ToString('N') + '.ini')
[IO.File]::WriteAllText($config, "[nipkg]`nenable-NI-connectivity=false`n", (New-Object Text.UTF8Encoding($false)))
$log = Join-Path $LogRoot ('ni-cli-' + [guid]::NewGuid().ToString('N') + '.log')
function Invoke-NiCli {
    param([string[]]$CliArguments, [switch]$AllowReboot)
    # Native call is synchronous; preserve the real exit code before logging.
    # The outer orchestrator owns timeout/indeterminate-state handling.
    # Windows PowerShell 5.1 wraps native stderr as ErrorRecord. Let the exit
    # code classify it; a reboot-required diagnostic is not a script failure.
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = & $nipkg @CliArguments "--config=$config" 2>&1
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $savedPreference }
    $output | Out-File -LiteralPath $log -Encoding utf8 -Append
    $output | ForEach-Object { Write-Host $_ }
    if ($code -eq -125071 -and $AllowReboot) { return 3010 }
    if ($code -ne 0) { throw "NI command '$($CliArguments[0])' failed: $code. Log: $log" }
    return 0
}
[void](Invoke-NiCli @('feed-add', '--name=arp-offline-runtime', $feed))
[void](Invoke-NiCli @('feed-list'))
[void](Invoke-NiCli @('update', 'arp-offline-runtime'))
$arguments = @('install', '--accept-eulas', '--yes', '--language=zh-CN', '--install-also-upgrades')
$arguments += @(
    'ni-daqmx-runtime-core:windows_x64=23.5.0.49391-0+f239'
)
# Serial devices use the bundled serial transport when native VISA is absent.
# Do not install NI-VISA merely to provide ASRL access on a legacy machine.
# nipkg.exe's inspected install help has no --prevent-reboot switch. Do not
# substitute NIPackageManager.exe switches. Reboot behavior needs target tests.
# Resolve the upgrade transaction before changing installed NI packages. Never
# opt into downgrades, forced removals or suppression of incompatibility errors.
# Existing NI stacks are guarded above; simulation is not permission to upgrade.
[void](Invoke-NiCli ($arguments + @('--simulate')))
if ($Simulate) {
    Write-Host 'SIMULATION ONLY: installation and legacy-software compatibility remain unverified.'
    exit 0
}
$code = Invoke-NiCli $arguments -AllowReboot
exit $code
