[CmdletBinding()]
param(
    [string]$OutputDirectory = "",
    [ValidateSet("all", "msi", "nsis")]
    [string]$BundleTargets = "all"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $IsWindows -and $env:OS -ne 'Windows_NT') { throw 'Build on Windows with the configured driver toolchain.' }
$rustHost = @(& rustc -vV)
if ($LASTEXITCODE -ne 0 -or 'host: x86_64-pc-windows-msvc' -notin $rustHost) { throw 'Use the x64 Windows MSVC Rust toolchain for the supported device runtime.' }
$env:MPLBACKEND = 'Agg'
$gpuiRoot = Join-Path $projectRoot "gpui-ui"
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot "release\gpui\Power Test" }
$binaryRoot = $OutputDirectory
$workRoot = Join-Path $projectRoot "build\gpui-sidecar"
$sidecarName = "arp-python"
$sidecarPath = Join-Path $binaryRoot "$sidecarName.exe"
$realtimeManifest = Join-Path $projectRoot "rust-realtime-core\Cargo.toml"
$realtimeDll = Join-Path $projectRoot "rust-realtime-core\target\release\arp_realtime_core.dll"

$condaExe = (Get-Command conda.exe -CommandType Application).Source

# Keep Matplotlib on its headless renderer during PyInstaller analysis. Without
# this, its hook probes locally installed GUI bindings and can pull shiboken6
# into the sidecar even though the application never uses a Qt backend.
$env:MPLBACKEND = "Agg"

New-Item -ItemType Directory -Force -Path $binaryRoot, $workRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $workRoot "work"), (Join-Path $workRoot "spec") | Out-Null

& cargo build --manifest-path $realtimeManifest --release --locked
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $realtimeDll)) {
    throw "Rust realtime core build failed: $realtimeDll"
}

$pyInstallerArgs = @(
    "run", "-n", "sth_eb314", "pyinstaller",
    "--noconfirm", "--clean", "--onefile", "--console",
    "--name", $sidecarName,
    "--paths", $projectRoot,
    "--distpath", $binaryRoot,
    "--workpath", (Join-Path $workRoot "work"),
    "--specpath", (Join-Path $workRoot "spec"),
    "--runtime-hook", (Join-Path $PSScriptRoot "pyi_rth_safe_streams.py"),
    "--hidden-import", "combined_test.persistence",
    "--exclude-module", "PySide6",
    "--exclude-module", "shiboken6",
    "--exclude-module", "matplotlib.backends.backend_qt",
    "--exclude-module", "matplotlib.backends.backend_qt5",
    "--exclude-module", "matplotlib.backends.backend_qtagg",
    "--exclude-module", "matplotlib.backends.qt_compat",
    "--hidden-import", "combined_test.ocean_direct_adapter",
    "--hidden-import", "tools.pd_daq_mvp",
    "--hidden-import", "tools.power_meter_mvp",
    "--hidden-import", "tools.spectrometer_mvp",
    "--hidden-import", "combined_test.shipping_report_headless",
    "--hidden-import", "matplotlib.backends.backend_agg",
    "--hidden-import", "win32com.client",
    "--hidden-import", "pythoncom",
    "--hidden-import", "pywintypes",
    "--add-binary", "$realtimeDll;.",
    "--add-data", "$(Join-Path $projectRoot 'tools\spectrometer_mvp.py');tools",
    "--add-data", "$(Join-Path $projectRoot 'tools\legacy_ch341_control.py');tools"
)

$assetsRoot = Join-Path $projectRoot "assets"
$pyInstallerArgs += @("--add-data", "$assetsRoot;assets")

$ch341Dll = Join-Path $projectRoot "local_drivers\ch341\CH341DLLA64.DLL"
$pyInstallerArgs += @("--add-binary", "$ch341Dll;.")

$pyInstallerArgs += (Join-Path $PSScriptRoot "tauri_bridge_entry.py")

& $condaExe @pyInstallerArgs
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $sidecarPath)) {
    throw "Python sidecar build failed."
}


& cargo build --manifest-path (Join-Path $gpuiRoot 'Cargo.toml') --release --locked
if ($LASTEXITCODE -ne 0) { throw 'GPUI build failed.' }
Copy-Item -LiteralPath (Join-Path $gpuiRoot 'target\release\arp-gpui.exe') -Destination (Join-Path $binaryRoot 'Power Test.exe') -Force
# Native rendering has no WebView2 prerequisite. Keep the working directory and
# driver assets beside the executable for device wrappers that resolve cwd.
New-Item -ItemType Directory -Force -Path (Join-Path $binaryRoot 'assets') | Out-Null
Copy-Item -Path (Join-Path $assetsRoot '*') -Destination (Join-Path $binaryRoot 'assets') -Recurse -Force
Copy-Item -LiteralPath $ch341Dll -Destination $binaryRoot -Force
Write-Host "GPUI application folder: $binaryRoot"
Write-Host 'Windows installation and real-device acceptance must be completed separately.'

# Reuse the existing MSI/NSIS tooling and upgrade identity. The packaged main
# executable is the GPUI build; no Tauri runtime or frontend is compiled here.
$tauriRoot = Join-Path $projectRoot 'tauri-ui'
$bundleBinRoot = Join-Path $tauriRoot 'src-tauri\binaries'
$bundleTargetRoot = Join-Path $projectRoot 'build\gpui-installer-target'
$bundleReleaseRoot = Join-Path $bundleTargetRoot 'release'
New-Item -ItemType Directory -Force -Path $bundleBinRoot, $bundleReleaseRoot | Out-Null
Copy-Item -LiteralPath $sidecarPath -Destination (Join-Path $bundleBinRoot 'arp-python-x86_64-pc-windows-msvc.exe') -Force
Copy-Item -LiteralPath (Join-Path $binaryRoot 'Power Test.exe') -Destination (Join-Path $bundleReleaseRoot 'tauri-ui.exe') -Force
Copy-Item -LiteralPath (Join-Path $binaryRoot 'Power Test.exe') -Destination (Join-Path $bundleReleaseRoot 'Power Test.exe') -Force
$npmExe = (Get-Command npm.cmd -CommandType Application).Source
$previousCargoTargetDir = $env:CARGO_TARGET_DIR
Push-Location $tauriRoot
try {
    $env:CARGO_TARGET_DIR = $bundleTargetRoot
    & $npmExe ci
    if ($LASTEXITCODE -ne 0) { throw 'Installer toolchain installation failed.' }
    . (Join-Path $PSScriptRoot 'gpui_bundle_helpers.ps1')
    $nativeVersion = Get-GpuiAppVersion -ManifestPath (Join-Path $gpuiRoot 'Cargo.toml')
    $bundleOverrides = Join-Path $workRoot 'native-bundle.json'
    Write-GpuiBundleConfiguration -ManifestPath (Join-Path $gpuiRoot 'Cargo.toml') -AssetsDirectory (Join-Path $binaryRoot 'assets') -OutputPath $bundleOverrides
    $bundleArgs = @('run', 'tauri', '--', 'bundle', '--ci', '--config', (Join-Path $PSScriptRoot 'gpui.windows.bundle.conf.json'), '--config', $bundleOverrides)
    if ($BundleTargets -ne 'all') { $bundleArgs += @('--bundles', $BundleTargets) }
    $bundleStartedUtc = [DateTime]::UtcNow
    & $npmExe @bundleArgs
    if ($LASTEXITCODE -ne 0) { throw 'GPUI installer bundling failed.' }
    $bundleRoot = Join-Path $bundleReleaseRoot 'bundle'
    $artifacts = @(Get-ChildItem -LiteralPath $bundleRoot -Recurse -File | Where-Object { $_.LastWriteTimeUtc -ge $bundleStartedUtc -and $_.Extension -in @('.msi', '.exe') -and $_.Name -like "*$nativeVersion*" })
    Write-GpuiInstallerBuildStamp -StampPath (Join-Path $bundleRoot 'gpui-build.json') -Version $nativeVersion -BinaryPath (Join-Path $bundleReleaseRoot 'tauri-ui.exe') -Artifacts $artifacts
} finally {
    if ($null -eq $previousCargoTargetDir) { Remove-Item Env:CARGO_TARGET_DIR -ErrorAction SilentlyContinue } else { $env:CARGO_TARGET_DIR = $previousCargoTargetDir }
    Pop-Location
}
