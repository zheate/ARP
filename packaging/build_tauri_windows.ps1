$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$tauriRoot = Join-Path $projectRoot "tauri-ui"
$binaryRoot = Join-Path $tauriRoot "src-tauri\binaries"
$workRoot = Join-Path $projectRoot "build\tauri-sidecar"
$sidecarName = "arp-python-x86_64-pc-windows-msvc"
$sidecarPath = Join-Path $binaryRoot "$sidecarName.exe"

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

New-Item -ItemType Directory -Force -Path $binaryRoot, $workRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $workRoot "work"), (Join-Path $workRoot "spec") | Out-Null

$fontProbe = & $condaExe run -n sth_eb314 python -c "from pathlib import Path; import matplotlib; print(Path(matplotlib.get_data_path()) / 'fonts' / 'ttf' / 'DejaVuSans.ttf')"
$qtFontSource = $fontProbe | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Last 1
if (-not $qtFontSource) {
    throw "DejaVuSans.ttf was not found in the sth_eb314 environment."
}
$qtFontLicense = Join-Path (Split-Path -Parent $qtFontSource) "LICENSE_DEJAVU"

$pyInstallerArgs = @(
    "run", "-n", "sth_eb314", "pyinstaller",
    "--noconfirm", "--clean", "--onefile", "--console",
    "--name", $sidecarName,
    "--paths", $projectRoot,
    "--distpath", $binaryRoot,
    "--workpath", (Join-Path $workRoot "work"),
    "--specpath", (Join-Path $workRoot "spec"),
    "--runtime-hook", (Join-Path $PSScriptRoot "pyi_rth_safe_streams.py"),
    "--add-data", "$qtFontSource;PySide6/lib/fonts",
    "--hidden-import", "combined_test.window",
    "--hidden-import", "combined_test.persistence",
    "--hidden-import", "combined_test.test_archive",
    "--hidden-import", "combined_test.ocean_direct_adapter",
    "--hidden-import", "tools.pd_daq_mvp",
    "--add-data", "$(Join-Path $projectRoot 'tools\spectrometer_mvp.py');tools",
    "--add-data", "$(Join-Path $projectRoot 'tools\legacy_ch341_control.py');tools"
)

if (Test-Path -LiteralPath $qtFontLicense) {
    $pyInstallerArgs += @("--add-data", "$qtFontLicense;licenses")
}

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
    & $npmExe run tauri build -- --config (Join-Path $PSScriptRoot "tauri.windows.conf.json")
    if ($LASTEXITCODE -ne 0) { throw "Tauri Windows installer build failed." }
}
finally {
    Pop-Location
}

Write-Host "Build completed. Installer location: $tauriRoot\src-tauri\target\release\bundle"
