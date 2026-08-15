[CmdletBinding()]
param(
    [ValidateSet("all", "msi", "nsis")]
    [string]$BundleTargets = "all"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$tauriRoot = Join-Path $projectRoot "tauri-ui"
$binaryRoot = Join-Path $tauriRoot "src-tauri\binaries"
$workRoot = Join-Path $projectRoot "build\tauri-sidecar"
$sidecarName = "arp-python-x86_64-pc-windows-msvc"
$sidecarPath = Join-Path $binaryRoot "$sidecarName.exe"
$realtimeManifest = Join-Path $projectRoot "rust-realtime-core\Cargo.toml"
$realtimeDll = Join-Path $projectRoot "rust-realtime-core\target\release\arp_realtime_core.dll"

$condaCommand = Get-Command conda.exe -CommandType Application -ErrorAction SilentlyContinue
$condaExe = if ($condaCommand) { $condaCommand.Source } else { "D:\anaconda\Scripts\conda.exe" }
if (-not (Test-Path -LiteralPath $condaExe)) {
    throw "Conda was not found: $condaExe"
}

$npmCommand = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $npmCommand) {
    throw "npm was not found. Install Node.js LTS first."
}
$npmExe = $npmCommand.Source

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
if (Test-Path -LiteralPath $assetsRoot) {
    $pyInstallerArgs += @("--add-data", "$assetsRoot;assets")
}
else {
    Write-Warning "The assets directory was not found; OceanDirect.dll will not be bundled."
}

$ch341Dll = Join-Path $projectRoot "local_drivers\ch341\CH341DLLA64.DLL"
if (Test-Path -LiteralPath $ch341Dll) {
    $pyInstallerArgs += @("--add-binary", "$ch341Dll;.")
}

$pyInstallerArgs += (Join-Path $PSScriptRoot "tauri_bridge_entry.py")

& $condaExe @pyInstallerArgs
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $sidecarPath)) {
    throw "Python sidecar build failed."
}

Push-Location $tauriRoot
try {
    if (-not (Test-Path -LiteralPath (Join-Path $tauriRoot "node_modules"))) {
        & $npmExe install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed." }
    }
    $tauriArgs = @(
        "run", "tauri", "build", "--",
        "--config", (Join-Path $PSScriptRoot "tauri.windows.conf.json")
    )
    if ($BundleTargets -ne "all") {
        $tauriArgs += @("--bundles", $BundleTargets)
    }
    & $npmExe @tauriArgs
    if ($LASTEXITCODE -ne 0) { throw "Tauri Windows installer build failed." }
}
finally {
    Pop-Location
}

Write-Host "Build completed. Installer location: $tauriRoot\src-tauri\target\release\bundle"
