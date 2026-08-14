[CmdletBinding()]
param(
    [switch]$SkipApplicationBuild,
    [string]$OutputFile
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$driverRoot = Join-Path $projectRoot "local_drivers\current_project"
$allDriverRoot = Join-Path $projectRoot "local_drivers"
$ophirInstallerItem = Get-ChildItem -LiteralPath $allDriverRoot -Recurse -File -Filter "StarLab_Setup.exe" |
    Select-Object -First 1
$ophirInstaller = if ($ophirInstallerItem) {
    $ophirInstallerItem.FullName
} else {
    Join-Path $allDriverRoot "missing\StarLab_Setup.exe"
}
$coherentArchive = Join-Path $projectRoot "local_drivers\coherent\coherent-meter-connection-v1.4.2.3-release-setup.zip"
$tauriRoot = Join-Path $projectRoot "tauri-ui"
$bundleRoot = Join-Path $tauriRoot "src-tauri\target\release\bundle"
$buildRoot = Join-Path $projectRoot "build\full-installer"
$driverPayloadRoot = Join-Path $buildRoot "drivers"
$outputRoot = Join-Path $projectRoot "release\full"
if ([string]::IsNullOrWhiteSpace($OutputFile)) {
    $OutputFile = Join-Path $outputRoot "Power Test_0.1.0_x64-all-drivers-setup.exe"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputFile)) {
    $OutputFile = Join-Path $projectRoot $OutputFile
}
$installScript = Join-Path $PSScriptRoot "install_full_bundle.ps1"
$licenseFile = Join-Path $PSScriptRoot "full_installer_notice.txt"
$encodedLicenseFile = Join-Path $buildRoot "full_installer_notice.utf16.txt"
$nsiScript = Join-Path $PSScriptRoot "power_test_full_installer.nsi"
$appIcon = Join-Path $tauriRoot "src-tauri\icons\icon.ico"
$nipmInstaller = Join-Path $driverRoot "00_ni_package_manager\NIPackageManager24.0.0.exe"
$expectedNipmHash = "92763B4AFCFE81BFBB2C73F84071707B3852BDDBEFE29AB47F5279BAFA3B88F8"
$expectedOphirHash = "1FE3E5937EB054A3B50754F0AAF1A5E6BE2EE7426BD943DCEB6FF5989384DE1B"
$expectedCoherentHash = "53907AFDC23F6163017DFEC6360C087F735AA30E331C5883BAE81CC976C9B194"

$requiredPaths = @(
    $driverRoot,
    (Join-Path $driverRoot "01_ch341_i2c\CH341PAR.EXE"),
    (Join-Path $driverRoot "03_ocean_hr4\driver\OOI_OceanHR4.inf"),
    (Join-Path $driverRoot "04_laserpoint_ftdi\Win-10-11-Drivers-rev2026.zip"),
    (Join-Path $driverRoot "05_serial_adapters\wch_ch341ser\CH341SER.INF"),
    (Join-Path $driverRoot "05_serial_adapters\wch_ch343ser\CH343SER.INF"),
    (Join-Path $driverRoot "06_ni_runtime\feed\Packages.gz"),
    $ophirInstaller,
    $coherentArchive,
    $nipmInstaller,
    $installScript,
    $licenseFile,
    $nsiScript,
    $appIcon
)
foreach ($path in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required full-installer input is missing: $path"
    }
}

$nipmHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $nipmInstaller).Hash
if ($nipmHash -ne $expectedNipmHash) {
    throw "NI Package Manager SHA-256 mismatch. Expected $expectedNipmHash, received $nipmHash"
}
$nipmSignature = Get-AuthenticodeSignature -LiteralPath $nipmInstaller
if ($nipmSignature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "NI Package Manager signature is not valid: $($nipmSignature.Status)"
}

$ophirHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ophirInstaller).Hash
if ($ophirHash -ne $expectedOphirHash) {
    throw "Ophir StarLab SHA-256 mismatch. Expected $expectedOphirHash, received $ophirHash"
}
$ophirSignature = Get-AuthenticodeSignature -LiteralPath $ophirInstaller
if ($ophirSignature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "Ophir StarLab signature is not valid: $($ophirSignature.Status)"
}

$coherentHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $coherentArchive).Hash
if ($coherentHash -ne $expectedCoherentHash) {
    throw "Coherent driver SHA-256 mismatch. Expected $expectedCoherentHash, received $coherentHash"
}

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $driverRoot "verify_bundle.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "The curated driver bundle failed verification."
}

if (-not $SkipApplicationBuild) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build_tauri_windows.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Power Test application build failed."
    }
}

$appMsi = Get-ChildItem -LiteralPath (Join-Path $bundleRoot "msi") -File -Filter "*.msi" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $appMsi) {
    throw "Power Test MSI was not found under $bundleRoot\msi"
}

$makensisCandidates = @(
    (Join-Path $env:LOCALAPPDATA "tauri\NSIS\Bin\makensis.exe"),
    (Join-Path $env:LOCALAPPDATA "Tauri\NSIS\Bin\makensis.exe")
)
$makensis = $makensisCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $makensis) {
    $makensisCommand = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($makensisCommand) {
        $makensis = $makensisCommand.Source
    }
}
if (-not $makensis) {
    throw "makensis.exe was not found. Run a normal Tauri NSIS build first."
}

New-Item -ItemType Directory -Force -Path $buildRoot, $outputRoot | Out-Null

# Stage every driver used by the active backend. Historical BWTEK/Yokogawa
# packages are excluded because the application has no adapter for them.
$resolvedBuildRoot = [System.IO.Path]::GetFullPath($buildRoot).TrimEnd('\') + '\'
$resolvedPayloadRoot = [System.IO.Path]::GetFullPath($driverPayloadRoot)
if (-not $resolvedPayloadRoot.StartsWith($resolvedBuildRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to recreate driver payload outside the build directory: $resolvedPayloadRoot"
}
if (Test-Path -LiteralPath $driverPayloadRoot) {
    Remove-Item -LiteralPath $driverPayloadRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $driverPayloadRoot | Out-Null
Copy-Item -Path (Join-Path $driverRoot '*') -Destination $driverPayloadRoot -Recurse -Force

$ophirPayloadRoot = Join-Path $driverPayloadRoot "07_ophir_starlab"
$coherentPayloadRoot = Join-Path $driverPayloadRoot "08_coherent"
New-Item -ItemType Directory -Force -Path $ophirPayloadRoot, $coherentPayloadRoot | Out-Null
Copy-Item -LiteralPath $ophirInstaller -Destination (Join-Path $ophirPayloadRoot "StarLab_Setup.exe") -Force
Copy-Item -LiteralPath $coherentArchive -Destination (Join-Path $coherentPayloadRoot "coherent-meter-connection-setup.zip") -Force

# NSIS reads external LicenseData using a Windows text encoding. Generate an
# explicit UTF-16 LE BOM copy so Chinese text cannot be decoded as ANSI.
$strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
$utf16LeBom = New-Object System.Text.UnicodeEncoding($false, $true, $true)
$licenseText = [System.IO.File]::ReadAllText($licenseFile, $strictUtf8)
$licenseText = $licenseText -replace "`r?`n", "`r`n"
[System.IO.File]::WriteAllText($encodedLicenseFile, $licenseText, $utf16LeBom)

$nsisArguments = @(
    "/V4",
    "/INPUTCHARSET",
    "UTF8",
    "/DAPP_MSI=$($appMsi.FullName)",
    "/DDRIVER_ROOT=$driverPayloadRoot",
    "/DINSTALL_SCRIPT=$installScript",
    "/DLICENSE_FILE=$encodedLicenseFile",
    "/DOUTPUT_FILE=$OutputFile",
    "/DAPP_ICON=$appIcon",
    $nsiScript
)
& $makensis @nsisArguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $OutputFile)) {
    throw "Power Test Full Setup build failed."
}

$item = Get-Item -LiteralPath $OutputFile
$hash = Get-FileHash -Algorithm SHA256 -LiteralPath $OutputFile
Write-Host "Full Setup completed: $($item.FullName)"
Write-Host ("Size: {0:N2} MiB" -f ($item.Length / 1MB))
Write-Host "SHA-256: $($hash.Hash)"
