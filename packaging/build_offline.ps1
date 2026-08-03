$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$condaCommand = Get-Command conda -ErrorAction SilentlyContinue
$condaExe = if ($condaCommand) {
    $condaCommand.Source
} else {
    "D:\anaconda\Scripts\conda.exe"
}

if (-not (Test-Path -LiteralPath $condaExe)) {
    throw "未找到 Conda：$condaExe"
}

$distPath = Join-Path $projectRoot "release\泵驱一体离线版"
$workPath = Join-Path $projectRoot "build\pyinstaller"
$specPath = Join-Path $projectRoot "build\pyinstaller-spec"
New-Item -ItemType Directory -Force -Path $distPath, $workPath, $specPath | Out-Null

$usb2xxxRoot = Join-Path $projectRoot "local_drivers\usb2xxx"
$usb2xxxBinaryArgs = @()
if (
    (Test-Path -LiteralPath (Join-Path $usb2xxxRoot "USB2XXX.dll")) -and
    (Test-Path -LiteralPath (Join-Path $usb2xxxRoot "libusb-1.0.dll"))
) {
    $usb2xxxDll = Join-Path $usb2xxxRoot "USB2XXX.dll"
    $libusbDll = Join-Path $usb2xxxRoot "libusb-1.0.dll"
    $usb2xxxBinaryArgs += @("--add-binary", "$usb2xxxDll;.")
    $usb2xxxBinaryArgs += @("--add-binary", "$libusbDll;.")
}
else {
    Write-Warning "未找到 USB2XXX.dll/libusb-1.0.dll，离线版将不包含 USB2XXX 支持。"
}

$pyInstallerArgs = @(
    "run", "-n", "sth_eb314", "pyinstaller",
    "--noconfirm", "--clean", "--onedir", "--windowed",
    "--name", "PumpDriveTest",
    "--distpath", $distPath,
    "--workpath", $workPath,
    "--specpath", $specPath,
    "--runtime-hook", (Join-Path $PSScriptRoot "pyi_rth_safe_streams.py"),
    "--add-data", "$(Join-Path $projectRoot 'assets');assets",
    "--add-data", "$(Join-Path $projectRoot 'tools\spectrometer_mvp.py');tools",
    "--add-data", "$(Join-Path $projectRoot 'tools\legacy_ch341_control.py');tools",
    "--add-data", "$(Join-Path $projectRoot 'tools\usb2xxx_control.py');tools",
    "--add-binary", "$(Join-Path $projectRoot 'local_drivers\ch341\CH341DLLA64.DLL');.",
    "--hidden-import", "tkinter",
    "--hidden-import", "combined_test.ocean_direct_adapter"
)
$pyInstallerArgs += $usb2xxxBinaryArgs
$pyInstallerArgs += (Join-Path $projectRoot "main.py")

& $condaExe @pyInstallerArgs

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 构建失败，退出码：$LASTEXITCODE"
}

Write-Host "构建完成：$(Join-Path $distPath 'PumpDriveTest\PumpDriveTest.exe')"
