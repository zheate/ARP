$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$tauriRoot = Join-Path $projectRoot "tauri-ui"
$binaryRoot = Join-Path $tauriRoot "src-tauri\binaries"
$workRoot = Join-Path $projectRoot "build\tauri-sidecar"
$sidecarName = "arp-python-x86_64-pc-windows-msvc"
$sidecarPath = Join-Path $binaryRoot "$sidecarName.exe"

$condaCommand = Get-Command conda -ErrorAction SilentlyContinue
$condaExe = if ($condaCommand) { $condaCommand.Source } else { "D:\anaconda\Scripts\conda.exe" }
if (-not (Test-Path -LiteralPath $condaExe)) {
    throw "未找到 Conda：$condaExe"
}

$npmCommand = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCommand) {
    throw "未找到 npm。请先安装 Node.js LTS。"
}

New-Item -ItemType Directory -Force -Path $binaryRoot, $workRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $workRoot "work"), (Join-Path $workRoot "spec") | Out-Null
$pyInstallerArgs = @(
    "run", "-n", "sth_eb314", "pyinstaller",
    "--noconfirm", "--clean", "--onefile", "--console",
    "--name", $sidecarName,
    "--paths", $projectRoot,
    "--distpath", $binaryRoot,
    "--workpath", (Join-Path $workRoot "work"),
    "--specpath", (Join-Path $workRoot "spec"),
    "--runtime-hook", (Join-Path $PSScriptRoot "pyi_rth_safe_streams.py"),
    "--hidden-import", "combined_test.window",
    "--hidden-import", "combined_test.persistence",
    "--hidden-import", "combined_test.test_archive",
    "--hidden-import", "combined_test.ocean_direct_adapter",
    "--hidden-import", "tools.pd_daq_mvp",
    "--add-data", "$(Join-Path $projectRoot 'tools\spectrometer_mvp.py');tools",
    "--add-data", "$(Join-Path $projectRoot 'tools\legacy_ch341_control.py');tools"
)

$assetsRoot = Join-Path $projectRoot "assets"
if (Test-Path -LiteralPath $assetsRoot) {
    $pyInstallerArgs += @("--add-data", "$assetsRoot;assets")
}
else {
    Write-Warning "未找到 assets 目录；安装包不会内置 OceanDirect.dll，请在 Windows 构建机补齐驱动资源后再做光谱仪验收。"
}

$ch341Dll = Join-Path $projectRoot "local_drivers\ch341\CH341DLLA64.DLL"
if (Test-Path -LiteralPath $ch341Dll) {
    $pyInstallerArgs += @("--add-binary", "$ch341Dll;.")
}
$pyInstallerArgs += (Join-Path $PSScriptRoot "tauri_bridge_entry.py")

& $condaExe @pyInstallerArgs
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $sidecarPath)) {
    throw "Python sidecar 构建失败。"
}

Push-Location $tauriRoot
try {
    if (-not (Test-Path -LiteralPath (Join-Path $tauriRoot "node_modules"))) {
        & npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install 失败。" }
    }
    & npm run tauri build -- --config (Join-Path $PSScriptRoot "tauri.windows.conf.json")
    if ($LASTEXITCODE -ne 0) { throw "Tauri Windows 安装包构建失败。" }
}
finally {
    Pop-Location
}

Write-Host "构建完成。安装包位于：$tauriRoot\src-tauri\target\release\bundle"
