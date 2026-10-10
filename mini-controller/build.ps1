[CmdletBinding()]
param([switch]$PrepareOnly, [switch]$SkipTests)
#Requires -Version 7.0
$ErrorActionPreference = "Stop"
$miniRoot = $PSScriptRoot
$projectRoot = Split-Path -Parent $miniRoot
$buildRoot = Join-Path $projectRoot "build\ch341-mini"
$driverPayload = Join-Path $buildRoot "driver"
$resourceRoot = Join-Path $miniRoot "resources"
$releaseRoot = Join-Path $projectRoot "release\ch341-mini"
$sfx = Join-Path $projectRoot "local_drivers\current_project\01_ch341_i2c\CH341PAR.EXE"
$expectedHash = "DFA374C7079D41D70C0EBA6CCFDED64EF2668591FD9AA03B6312D999B33B5A43"
$pythonPrefix = ((& conda run -n sth_eb314 python -c "import sys; print(sys.prefix)") | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonPrefix -PathType Container)) { throw "Cannot resolve sth_eb314 Python environment" }
# Prefer this environment's dependent DLLs over unrelated tooling DLLs on PATH.
$env:PATH = "$pythonPrefix;$pythonPrefix\Library\bin;$pythonPrefix\Scripts;$env:PATH"
if (-not (Test-Path -LiteralPath $sfx -PathType Leaf)) { throw "Missing CH341PAR vendor media: $sfx" }
if ((Get-FileHash -LiteralPath $sfx -Algorithm SHA256).Hash -ne $expectedHash) { throw "CH341PAR source hash mismatch" }
New-Item -ItemType Directory -Force -Path $driverPayload, $resourceRoot, $releaseRoot | Out-Null
# Keep the complete, unmodified signed INF package. Its CH347 DLLs are referenced by
# the same WCH INF/catalog; they are not a separate optional instrument driver.
$driverFiles = @("CH341WDM.INF", "CH341WDM.CAT", "CH341WDM.SYS", "CH341W64.SYS", "CH341M64.SYS", "CH341DLL.DLL", "CH341DLLA64.DLL", "CH347DLL.DLL", "CH347DLLA64.DLL")
& "$env:SystemRoot\System32\tar.exe" -xf $sfx -C $driverPayload @driverFiles
if ($LASTEXITCODE -ne 0) { throw "CH341 driver extraction failed" }
foreach ($name in $driverFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $driverPayload $name) -PathType Leaf)) { throw "Missing INF payload: $name" }
}
if (@(Compare-Object $driverFiles @(Get-ChildItem -LiteralPath $driverPayload -File | ForEach-Object Name)).Count) { throw "Unexpected files in CH341 driver staging folder" }
$signature = Get-AuthenticodeSignature -LiteralPath (Join-Path $driverPayload "CH341WDM.CAT")
if ($signature.Status -ne "Valid") { throw "CH341 catalog signature invalid: $($signature.Status)" }
Copy-Item -LiteralPath (Join-Path $driverPayload "CH341DLLA64.DLL") -Destination $resourceRoot -Force
& conda run -n sth_eb314 python (Join-Path $miniRoot "prepare_icon.py")
if ($LASTEXITCODE -ne 0) { throw "Application icon rendering failed" }
if ($PrepareOnly) { Write-Host "Prepared CH341-only build resources"; exit 0 }
if (-not $SkipTests) {
    & conda run -n sth_eb314 python -m unittest discover -s (Join-Path $miniRoot "tests") -v
    if ($LASTEXITCODE -ne 0) { throw "Mini controller tests failed" }
}
$icon = Join-Path $resourceRoot "controller.ico"
$pyArgs = @("run", "-n", "sth_eb314", "python", "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--name", "CH341Mini", "--icon", $icon, "--add-data", "$icon;.", "--distpath", $releaseRoot, "--workpath", (Join-Path $buildRoot "pyinstaller"), "--specpath", $buildRoot, "--add-binary", "$(Join-Path $resourceRoot 'CH341DLLA64.DLL');.")
foreach ($module in @("PIL", "PySide6", "PyQt5", "PyQt6", "numpy", "scipy", "pandas", "matplotlib", "pyvisa", "nidaqmx", "openpyxl", "xlsxwriter", "win32com", "combined_test", "scripts_runner", "tauri_bridge")) {
    $pyArgs += @("--exclude-module", $module)
}
$pyArgs += @("--version-file", (Join-Path $miniRoot "version_info.txt"), (Join-Path $miniRoot "app.py"))
& conda @pyArgs
if ($LASTEXITCODE -ne 0) { throw "Mini EXE build failed" }
$appExe = Join-Path $releaseRoot "CH341Mini.exe"
$report = Join-Path $buildRoot "frozen-self-check.json"
$check = Start-Process -FilePath $appExe -ArgumentList @("--self-check", ('"' + $report + '"')) -WindowStyle Hidden -Wait -PassThru
if ($check.ExitCode -ne 0) { throw "Frozen self-check failed: $report" }
$checkData = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $checkData.ok -or $checkData.hardware_opened) { throw "Frozen runtime check failed" }
& conda run -n sth_eb314 python (Join-Path $miniRoot "verify_frozen.py") $appExe (Join-Path $resourceRoot "CH341DLLA64.DLL") (Join-Path $buildRoot "archive-check.json")
if ($LASTEXITCODE -ne 0) { throw "Frozen archive verification failed" }
$makensis = Join-Path $env:LOCALAPPDATA "tauri\NSIS\Bin\makensis.exe"
if (-not (Test-Path -LiteralPath $makensis)) {
    $makensis = (Get-Command makensis.exe -ErrorAction Stop).Source
}
$outputFile = Join-Path $releaseRoot "CH341Mini_0.1.0_x64-setup.exe"
& $makensis /WX /V3 /INPUTCHARSET UTF8 "/DAPP_EXE=$appExe" "/DAPP_ICON=$icon" "/DDRIVER_ROOT=$driverPayload" "/DOUTPUT_FILE=$outputFile" (Join-Path $miniRoot "installer.nsi")
if ($LASTEXITCODE -ne 0) { throw "Mini installer build failed" }
$manifest = [ordered]@{
    version = "0.1.0"
    createdAt = (Get-Date).ToString("o")
    sourceCh341PackageSha256 = $expectedHash
    driverSigner = $signature.SignerCertificate.Subject
    hardwareOpened = $false
    runtimeCheck = $checkData
    archiveCheck = (Get-Content -LiteralPath (Join-Path $buildRoot "archive-check.json") -Raw -Encoding UTF8 | ConvertFrom-Json)
    files = @(Get-Item -LiteralPath $appExe, $outputFile | ForEach-Object { @{ name = $_.Name; bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash } })
    driverFiles = @($driverFiles | ForEach-Object { @{ name = $_; sha256 = (Get-FileHash -LiteralPath (Join-Path $driverPayload $_) -Algorithm SHA256).Hash } })
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $releaseRoot "build-manifest.json") -Encoding UTF8
Write-Host "Built one-click CH341-only installer: $outputFile"
