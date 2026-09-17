#Requires -Version 5.1
<# Read-only target qualification. Does not install, scan instruments or change settings. #>
[CmdletBinding()]
param([string]$OutputPath)
$ErrorActionPreference = 'Stop'
# Resolve script context after parameter binding. Some invocation contexts do
# not populate PSScriptRoot while evaluating parameter defaults.
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $outputDirectory = $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($outputDirectory) -and $PSCommandPath) {
        $outputDirectory = Split-Path -Parent $PSCommandPath
    }
    if ([string]::IsNullOrWhiteSpace($outputDirectory) -and $MyInvocation.MyCommand.Path) {
        $outputDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    if ([string]::IsNullOrWhiteSpace($outputDirectory)) {
        $outputDirectory = (Get-Location).Path
    }
    $OutputPath = Join-Path $outputDirectory 'offline-target-readiness.json'
}
$issues = New-Object 'Collections.Generic.List[string]'
function Read-NativeExportNames {
    param([string]$Path)
    # Parse the PE file as bytes. Never load/execute a vendor DLL or open hardware.
    $bytes = [IO.File]::ReadAllBytes($Path)
    function Read-U16([long]$Offset) {
        if ($Offset -lt 0 -or $Offset + 2 -gt $bytes.LongLength) { throw 'Invalid PE offset' }
        return [BitConverter]::ToUInt16($bytes, [int]$Offset)
    }
    function Read-U32([long]$Offset) {
        if ($Offset -lt 0 -or $Offset + 4 -gt $bytes.LongLength) { throw 'Invalid PE offset' }
        return [BitConverter]::ToUInt32($bytes, [int]$Offset)
    }
    if ((Read-U16 0) -ne 0x5A4D) { throw 'Not a PE file' }
    $pe = [long](Read-U32 0x3C)
    if ((Read-U32 $pe) -ne 0x4550) { throw 'Invalid PE signature' }
    $machine = Read-U16 ($pe + 4)
    $sectionCount = Read-U16 ($pe + 6)
    $optionalSize = Read-U16 ($pe + 20)
    $optional = $pe + 24
    $magic = Read-U16 $optional
    $dataOffset = if ($magic -eq 0x20B) { 112 } elseif ($magic -eq 0x10B) { 96 } else { throw 'Unknown PE format' }
    if ($optionalSize -lt $dataOffset + 8 -or $sectionCount -gt 96) { throw 'Invalid PE headers' }
    $sections = @()
    for ($i = 0; $i -lt $sectionCount; $i++) {
        $entry = $optional + $optionalSize + $i * 40
        $sections += [pscustomobject]@{ rva = [long](Read-U32 ($entry + 12)); size = [long](Read-U32 ($entry + 16)); raw = [long](Read-U32 ($entry + 20)) }
    }
    function Map-Rva([long]$Rva) {
        foreach ($section in $sections) {
            if ($Rva -ge $section.rva -and $Rva -lt $section.rva + $section.size) {
                $offset = $section.raw + $Rva - $section.rva
                if ($offset -ge $bytes.LongLength) { throw 'PE RVA outside file' }
                return $offset
            }
        }
        throw 'PE RVA outside raw sections'
    }
    $exportRva = Read-U32 ($optional + $dataOffset)
    $exports = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
    if ($exportRva) {
        $export = Map-Rva $exportRva
        $count = Read-U32 ($export + 24)
        if ($count -gt 100000) { throw 'Implausible PE export count' }
        if ($count) {
            $names = Map-Rva (Read-U32 ($export + 32))
            for ($i = 0; $i -lt $count; $i++) {
                $start = Map-Rva (Read-U32 ($names + $i * 4))
                $end = $start
                while ($end -lt $bytes.LongLength -and $bytes[$end] -ne 0 -and $end - $start -lt 1024) { $end++ }
                if ($end -ge $bytes.LongLength -or $end - $start -ge 1024) { throw 'Invalid PE export name' }
                [void]$exports.Add([Text.Encoding]::ASCII.GetString($bytes, [int]$start, [int]($end - $start)))
            }
        }
    }
    return [pscustomobject]@{ machine = ('0x{0:X4}' -f $machine); names = $exports }
}
function Read-HklmValue {
    param([string]$Key, [string]$Value, [int]$View = 64)
    $registryView = if ($View -eq 64) { [Microsoft.Win32.RegistryView]::Registry64 } else { [Microsoft.Win32.RegistryView]::Registry32 }
    $hive = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $registryView)
    $sub = $null
    try { $sub = $hive.OpenSubKey($Key); if ($sub) { return $sub.GetValue($Value, $null) } }
    finally { if ($sub) { $sub.Dispose() }; $hive.Dispose() }
}
$apps = @()
foreach ($view in @(64, 32)) {
    $registryView = if ($view -eq 64) { [Microsoft.Win32.RegistryView]::Registry64 } else { [Microsoft.Win32.RegistryView]::Registry32 }
    $hive = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $registryView)
    $uninstall = $null
    try {
        $uninstall = $hive.OpenSubKey('SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall')
        if ($uninstall) {
            foreach ($name in $uninstall.GetSubKeyNames()) {
                $entry = $uninstall.OpenSubKey($name)
                try {
                    $display = [string]$entry.GetValue('DisplayName', '')
                    if ($display -match '(?i)National Instruments|^NI[ -]|StarLab|Ophir|Coherent|WebView2|Power Test|Visual C\+\+') {
                        $apps += [pscustomobject]@{ name = $display; version = [string]$entry.GetValue('DisplayVersion', ''); registryView = $view }
                    }
                } finally { $entry.Dispose() }
            }
        }
    } catch { $issues.Add("Application inventory ($view): $($_.Exception.Message)") }
    finally { if ($uninstall) { $uninstall.Dispose() }; $hive.Dispose() }
}
$drives = @([IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady -and $_.DriveType -eq [IO.DriveType]::Fixed } | ForEach-Object {
    [pscustomobject]@{ drive = $_.Name; format = $_.DriveFormat; freeBytes = $_.AvailableFreeSpace }
})
$pending = @()
foreach ($key in @('SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending', 'SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')) {
    if (Test-Path -LiteralPath ("Registry::HKEY_LOCAL_MACHINE\" + $key)) { $pending += $key }
}
if (Read-HklmValue 'SYSTEM\CurrentControlSet\Control\Session Manager' 'PendingFileRenameOperations') { $pending += 'PendingFileRenameOperations' }
$nativeFiles = @()
$requiredExports = @{
    'nicaiu.dll' = @('DAQmxGetSystemInfoAttribute', 'DAQmxGetDeviceAttribute', 'DAQmxGetTaskAttribute', 'DAQmxGetChanAttribute', 'DAQmxGetReadAttribute', 'DAQmxCreateTask', 'DAQmxClearTask', 'DAQmxStartTask', 'DAQmxStopTask', 'DAQmxCreateAIVoltageChan', 'DAQmxCfgSampClkTiming', 'DAQmxReadAnalogF64', 'DAQmxGetErrorString', 'DAQmxGetExtendedErrorInfo')
    'visa64.dll' = @('viOpenDefaultRM', 'viFindRsrc', 'viFindNext', 'viOpen', 'viClose', 'viRead', 'viWrite', 'viSetAttribute', 'viGetAttribute', 'viStatusDesc')
}
$nativeSystem = if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) { 'Sysnative' } else { 'System32' }
foreach ($name in @('nicaiu.dll', 'visa64.dll')) {
    $path = Join-Path $env:SystemRoot "$nativeSystem\$name"
    $entry = [ordered]@{ file = $name; path = $path; exists = (Test-Path -LiteralPath $path -PathType Leaf); version = $null; sha256 = $null; machine = $null; missingExports = @(); error = $null }
    if ($entry.exists) {
        try {
            $entry.version = (Get-Item -LiteralPath $path).VersionInfo.FileVersion
            $entry.sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
            $peInfo = Read-NativeExportNames $path
            $entry.machine = $peInfo.machine
            $entry.missingExports = @($requiredExports[$name] | Where-Object { -not $peInfo.names.Contains($_) })
        } catch { $entry.error = $_.Exception.Message; $issues.Add("Native file inventory ($name): $($_.Exception.Message)") }
    }
    $nativeFiles += [pscustomobject]$entry
}
$webViewVersion = Read-HklmValue 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}' 'pv' 32
$niVisaVersion = Read-HklmValue 'SOFTWARE\National Instruments\NI-VISA' 'CurrentVersion' 64
$report = [ordered]@{
    collectedAtUtc = [DateTime]::UtcNow.ToString('o')
    scope = 'Read-only readiness inventory; not an installation, offline-network or hardware acceptance test.'
    windowsProduct = Read-HklmValue 'SOFTWARE\Microsoft\Windows NT\CurrentVersion' 'ProductName'
    windowsBuild = Read-HklmValue 'SOFTWARE\Microsoft\Windows NT\CurrentVersion' 'CurrentBuildNumber'
    windowsRevision = Read-HklmValue 'SOFTWARE\Microsoft\Windows NT\CurrentVersion' 'UBR'
    is64BitOperatingSystem = [Environment]::Is64BitOperatingSystem
    is64BitProcess = [Environment]::Is64BitProcess
    processArchitecture = $env:PROCESSOR_ARCHITECTURE
    machineWebView2Version = $(if ($webViewVersion) { [string]$webViewVersion } else { $null })
    niVisaVersion = $(if ($niVisaVersion) { [string]$niVisaVersion } else { $null })
    nativeRuntimeFiles = $nativeFiles
    nativeExportScope = 'Static required export names only; not proof of ABI, supported driver version, successful loading or hardware operation.'
    candidateExistingComponents = $apps
    fixedDrives = $drives
    pendingRestartIndicators = $pending
    inventoryErrors = @($issues.ToArray())
    cleanBaselineVerified = $false
    networkDisconnectedVerified = $false
}
$full = [IO.Path]::GetFullPath($OutputPath)
[IO.File]::WriteAllText($full, ($report | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
Write-Host "Readiness report: $full"
Write-Host 'No components installed; no instruments scanned. Baseline and disconnected status still require confirmation.'
