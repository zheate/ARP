#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PayloadRoot,
    [Parameter(Mandatory = $true)][string]$AppMsiPath,
    [string]$AppInstallDir = $env:ARP_SETUP_INSTALLDIR,
    [Parameter(Mandatory = $true)][string]$ExpectedManifestSha256,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
foreach ($module in @('Common', 'Payload', 'Components', 'NiPreservation', 'Application')) {
    . (Join-Path $PSScriptRoot "offline\$module.ps1")
}
$script:Deploy = @{ DryRun = [bool]$DryRun; RebootRequired = $false; PayloadRoot = $PayloadRoot; LogRoot = ''; Journal = '' }
$mutex = $null
$locked = $false
$exitCode = 1
try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'Run this installer using native x64 Windows PowerShell on Windows x64' }
    $manifest = Read-VerifiedDeployment $PayloadRoot $ExpectedManifestSha256
    Assert-MsiHash $AppMsiPath $manifest
    $build = [int](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').CurrentBuildNumber
    if ($build -lt $manifest.minimumWindowsBuild) { throw "Windows build $build is below the reviewed minimum $($manifest.minimumWindowsBuild)" }
    $directory = Assert-ApplicationPath $AppInstallDir
    # Run before any vendor installation, including NIPM and WebView2.
    # Existing NI software must not be upgraded merely to satisfy our bundle.
    $reuseNi = Assert-NiPreservation $manifest
    $script:Deploy.LogRoot = Join-Path $env:ProgramData 'Power Test\InstallerLogs\dry-run'
    if (-not $DryRun) {
        $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator permission is required' }
        $mutex = New-Object Threading.Mutex($false, 'Global\ARP.PowerTest.OfflineInstaller')
        try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { throw 'Another Power Test deployment is running' }
        Assert-NoUnresolvedInstaller
        Assert-ApplicationPreflight $AppMsiPath $directory $manifest.application
        foreach ($name in @('InstallerLogs', 'InstallerCache')) {
            New-ProtectedDirectory (Join-Path $env:ProgramData "Power Test\$name")
        }
        $run = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N')
        $script:Deploy.LogRoot = Join-Path $env:ProgramData "Power Test\InstallerLogs\$run"
        New-ProtectedDirectory $script:Deploy.LogRoot
        $script:Deploy.Journal = Join-Path $script:Deploy.LogRoot 'deployment.jsonl'
        $cache = Join-Path $env:ProgramData "Power Test\InstallerCache\$run"
        New-ProtectedDirectory $cache
        $script:Deploy.CacheRoot = $cache
        $cachedPayload = Join-Path $cache 'drivers'
        New-ProtectedDirectory $cachedPayload
        # Copy only pinned files, not arbitrary siblings from a directory tree.
        foreach ($file in $manifest.files) {
            $target = Get-PayloadPath $cachedPayload $file.path
            New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
            Copy-Item -LiteralPath (Get-PayloadPath $PayloadRoot $file.path) -Destination $target
        }
        Copy-Item -LiteralPath (Get-PayloadPath $PayloadRoot 'deployment.json') -Destination $cachedPayload
        Copy-Item -LiteralPath $AppMsiPath -Destination (Join-Path $cache 'PowerTest.msi')
        $manifest = Read-VerifiedDeployment $cachedPayload $ExpectedManifestSha256
        $AppMsiPath = Join-Path $cache 'PowerTest.msi'
        Assert-MsiHash $AppMsiPath $manifest
        $script:Deploy.PayloadRoot = $cachedPayload
    }
    Write-DeployEvent 'deployment' 'start' @{ version = $manifest.application.version; windowsBuild = $build; architecture = 'x64'; directory = $directory; manifestHash = $ExpectedManifestSha256; dryRun = [bool]$DryRun }
    foreach ($component in $manifest.components) {
        if ($reuseNi -and $component.id -in @('ni-package-manager', 'ni-runtime')) {
            Write-DeployEvent $component.id 'existing-runtime-reused-no-ni-changes' $null
            continue
        }
        if ($component.kind -eq 'pnp') { Install-PnpComponent $component }
        elseif ($component.kind -in @('webview2', 'vendor')) { Install-RuntimeComponent $component }
        else { throw "Unsupported component kind: $($component.kind)" }
    }
    Install-Application $AppMsiPath $directory $manifest.application
    $exitCode = if ($script:Deploy.RebootRequired) { 3010 } else { 0 }
    Write-DeployEvent 'deployment' $(if ($DryRun) { 'plan-complete-not-installed' } else { 'complete' }) @{ rebootRequired = $script:Deploy.RebootRequired; exitCode = $exitCode }
} catch {
    $message = $_.Exception.Message
    if ($script:Deploy.Journal) { Write-DeployEvent 'deployment' 'failed' @{ error = $message; rebootRequired = $script:Deploy.RebootRequired } }
    [Console]::Error.WriteLine("Power Test deployment failed: $message")
    [Console]::Error.WriteLine("Installer logs: $($script:Deploy.LogRoot)")
    $exitCode = 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
exit $exitCode
