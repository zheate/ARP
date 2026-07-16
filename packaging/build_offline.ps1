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

& $condaExe run -n sth_eb314 pyinstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name PumpDriveTest `
    --distpath $distPath `
    --workpath $workPath `
    --specpath $specPath `
    --runtime-hook (Join-Path $PSScriptRoot "pyi_rth_safe_streams.py") `
    --add-data "$(Join-Path $projectRoot 'assets');assets" `
    --add-data "$(Join-Path $projectRoot 'tools\spectrometer_mvp.py');tools" `
    --add-data "$(Join-Path $projectRoot 'tools\legacy_ch341_control.py');tools" `
    --add-binary "$(Join-Path $projectRoot 'local_drivers\ch341\CH341DLLA64.DLL');." `
    --hidden-import tkinter `
    --hidden-import combined_test.ocean_direct_adapter `
    (Join-Path $projectRoot "main.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 构建失败，退出码：$LASTEXITCODE"
}

Write-Host "构建完成：$(Join-Path $distPath 'PumpDriveTest\PumpDriveTest.exe')"
