#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$SkipApplicationBuild,
    [string]$AppMsiPath,
    [string]$DeploymentLock,
    [string]$OutputFile
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$driverRoot = Join-Path $projectRoot 'local_drivers\current_project'
$tauriRoot = Join-Path $projectRoot 'tauri-ui'
foreach ($module in @('Common', 'Payload', 'Application')) { . (Join-Path $PSScriptRoot "offline\$module.ps1") }
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitProcess) { throw 'Build the offline installer on native x64 Windows PowerShell' }
if (-not $DeploymentLock) { $DeploymentLock = Join-Path $driverRoot 'offline-deployment.lock.json' }
if (-not (Test-Path -LiteralPath $DeploymentLock -PathType Leaf)) { throw "Reviewed deployment lock is missing: $DeploymentLock. See packaging/OFFLINE_DEPLOYMENT.md. Vendor commands are never guessed." }
$conda = (Get-Command conda.exe -CommandType Application -ErrorAction Stop).Source
$validator = Join-Path $PSScriptRoot 'offline_manifest.py'
& $conda run -n sth_eb314 python $validator --lock $DeploymentLock
if ($LASTEXITCODE -ne 0) { throw 'Offline deployment lock is not release-ready' }
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'tests\Test-OfflineScripts.ps1')
if ($LASTEXITCODE -ne 0) { throw 'PowerShell parser/logic tests failed' }
& $conda run -n sth_eb314 python -m unittest discover -s (Join-Path $PSScriptRoot 'tests') -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Offline packaging regression tests failed' }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'check_windows_build.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Windows packaging preflight failed' }

$appVersion = [string](Get-Content -LiteralPath (Join-Path $tauriRoot 'package.json') -Raw | ConvertFrom-Json).version
if ($appVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Application version must contain three numeric fields' }
$allDrivers = Join-Path $projectRoot 'local_drivers'
$ophir = @(Get-ChildItem -LiteralPath $allDrivers -Recurse -File -Filter 'StarLab_Setup.exe')
if ($ophir.Count -ne 1) { throw 'Expected exactly one pinned StarLab_Setup.exe; do not select an arbitrary duplicate' }
$coherent = Join-Path $allDrivers 'coherent\coherent-meter-connection-v1.4.2.3-release-setup.zip'
# Preserve the curated input pins. Updating these is a separate vendor review.
$pins = @(
    @{ path = (Join-Path $driverRoot '00_ni_package_manager\NIPackageManager24.0.0.exe'); hash = '92763B4AFCFE81BFBB2C73F84071707B3852BDDBEFE29AB47F5279BAFA3B88F8'; subject = '.' },
    @{ path = (Join-Path $driverRoot '09_webview2\MicrosoftEdgeWebView2RuntimeInstallerX64.exe'); hash = '6AC57A21414742AC1A6A03BF9516A048897317CEF04A49967B283093E29C31B7'; subject = 'Microsoft Corporation' },
    @{ path = $ophir[0].FullName; hash = '1FE3E5937EB054A3B50754F0AAF1A5E6BE2EE7426BD943DCEB6FF5989384DE1B'; subject = '.' },
    @{ path = $coherent; hash = '53907AFDC23F6163017DFEC6360C087F735AA30E331C5883BAE81CC976C9B194'; subject = '' }
)
foreach ($pin in $pins) {
    if (-not (Test-Path -LiteralPath $pin.path -PathType Leaf) -or (Get-FileHash -LiteralPath $pin.path -Algorithm SHA256).Hash -ne $pin.hash) { throw "Curated vendor input missing/hash mismatch: $($pin.path)" }
    if ($pin.subject) {
        $sig = Get-AuthenticodeSignature -LiteralPath $pin.path
        if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch $pin.subject) { throw "Invalid vendor input signature: $($pin.path)" }
    }
}
$verifyBundle = Join-Path $driverRoot 'verify_bundle.ps1'
if (-not (Test-Path -LiteralPath $verifyBundle -PathType Leaf)) { throw 'The curated driver bundle verification script is missing' }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyBundle
if ($LASTEXITCODE -ne 0) { throw 'Curated driver bundle verification failed' }

if ($SkipApplicationBuild) {
    if (-not $AppMsiPath) { throw '-SkipApplicationBuild requires an explicit -AppMsiPath; never pick an old MSI by modification time' }
} else {
    if ($AppMsiPath) { throw '-AppMsiPath is only supported with -SkipApplicationBuild' }
    $started = Get-Date
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'build_tauri_windows.ps1') -BundleTargets msi
    if ($LASTEXITCODE -ne 0) { throw 'Power Test application build failed' }
    $candidates = @(Get-ChildItem -LiteralPath (Join-Path $tauriRoot 'src-tauri\target\release\bundle\msi') -Filter '*.msi' -File | Where-Object { $_.LastWriteTime -ge $started })
    if ($candidates.Count -ne 1) { throw 'Expected exactly one freshly built MSI' }
    $AppMsiPath = $candidates[0].FullName
}
$AppMsiPath = [IO.Path]::GetFullPath($AppMsiPath)
$contract = Get-ApplicationContract $AppMsiPath $appVersion
foreach ($file in $contract.files) {
    $path = if ($file.name -eq 'arp-python.exe') { Join-Path $tauriRoot 'src-tauri\binaries\arp-python-x86_64-pc-windows-msvc.exe' } else { Join-Path $tauriRoot 'src-tauri\target\release\tauri-ui.exe' }
    $file | Add-Member -NotePropertyName sha256 -NotePropertyValue (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
}
$contract | Add-Member -NotePropertyName sha256 -NotePropertyValue (Get-FileHash -LiteralPath $AppMsiPath -Algorithm SHA256).Hash

# Unique staging directories prevent stale payloads and unsafe recursive deletes.
$buildRoot = Join-Path $projectRoot ('build\full-installer\' + [guid]::NewGuid().ToString('N'))
$inputs = Join-Path $buildRoot 'inputs'
$payload = Join-Path $buildRoot 'drivers'
New-Item -ItemType Directory -Force -Path $inputs | Out-Null
Get-ChildItem -LiteralPath $driverRoot -Force | Copy-Item -Destination $inputs -Recurse -Force
$ch341 = Join-Path $inputs '01_ch341_i2c\driver'
New-Item -ItemType Directory -Force -Path $ch341 | Out-Null
$ch341Files = @('CH341M64.SYS','CH341W64.SYS','CH341WDM.CAT','CH341WDM.INF','CH341WDM.SYS')
& "$env:SystemRoot\System32\tar.exe" -xf (Join-Path $inputs '01_ch341_i2c\CH341PAR.EXE') -C $ch341 @ch341Files
if ($LASTEXITCODE -ne 0) { throw 'CH341 driver extraction failed' }
foreach ($name in $ch341Files) { if (-not (Test-Path -LiteralPath (Join-Path $ch341 $name) -PathType Leaf)) { throw "CH341 file missing: $name" } }
foreach ($name in @('07_ophir_starlab', '08_coherent')) { New-Item -ItemType Directory -Force -Path (Join-Path $inputs $name) | Out-Null }
Copy-Item -LiteralPath $ophir[0].FullName -Destination (Join-Path $inputs '07_ophir_starlab\StarLab_Setup.exe')
Copy-Item -LiteralPath $coherent -Destination (Join-Path $inputs '08_coherent\coherent-meter-connection-setup.zip')
& $conda run -n sth_eb314 python $validator --lock $DeploymentLock --source $inputs --destination $payload
if ($LASTEXITCODE -ne 0) { throw 'Offline payload staging failed' }
$manifestPath = Join-Path $payload 'deployment.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-BuildSignatures $payload $manifest
$manifest | Add-Member -NotePropertyName application -NotePropertyValue $contract
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding($false)))
$manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
[void](Read-VerifiedDeployment $payload $manifestHash)
Assert-MsiHash $AppMsiPath $manifest

# Include vendor terms in the single consent page, using local reviewed text.
$notice = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'full_installer_notice.txt'), [Text.Encoding]::UTF8)
foreach ($name in $manifest.licenses) { $notice += "`r`n`r`n--- $name ---`r`n" + [IO.File]::ReadAllText((Get-PayloadPath $payload $name), [Text.Encoding]::UTF8) }
$licensePath = Join-Path $buildRoot 'offline-licenses.utf16.txt'
[IO.File]::WriteAllText($licensePath, $notice, (New-Object Text.UnicodeEncoding($false, $true)))
$makensis = Get-Command makensis.exe -ErrorAction SilentlyContinue
$nsisExe = if ($makensis) { $makensis.Source } else { Join-Path $env:LOCALAPPDATA 'tauri\NSIS\Bin\makensis.exe' }
if (-not (Test-Path -LiteralPath $nsisExe -PathType Leaf)) { throw 'Install the NSIS build tool on the controlled build machine' }
if (-not $OutputFile) { $OutputFile = Join-Path $projectRoot "release\full\Power Test_${appVersion}_x64_offline_setup.exe" }
elseif (-not [IO.Path]::IsPathRooted($OutputFile)) { $OutputFile = Join-Path $projectRoot $OutputFile }
New-Item -ItemType Directory -Force -Path (Split-Path $OutputFile -Parent) | Out-Null
$arguments = @('/V4', '/INPUTCHARSET', 'UTF8', "/DAPP_MSI=$AppMsiPath", "/DDRIVER_ROOT=$payload", "/DINSTALL_SCRIPT=$(Join-Path $PSScriptRoot 'install_full_bundle.ps1')", "/DOFFLINE_MODULES=$(Join-Path $PSScriptRoot 'offline')", "/DMANIFEST_HASH=$manifestHash", "/DLICENSE_FILE=$licensePath", "/DOUTPUT_FILE=$OutputFile", "/DAPP_ICON=$(Join-Path $tauriRoot 'src-tauri\icons\icon.ico')", "/DAPP_VERSION=$appVersion", (Join-Path $PSScriptRoot 'power_test_full_installer.nsi'))
& $nsisExe @arguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $OutputFile -PathType Leaf)) { throw 'Offline NSIS build failed; do not distribute a partial artifact' }
$item = Get-Item -LiteralPath $OutputFile
if ($item.Length -ge 2GB) { throw 'Installer exceeds the supported single-EXE size budget; review the payload before distribution' }
$hash = (Get-FileHash -LiteralPath $OutputFile -Algorithm SHA256).Hash
[IO.File]::WriteAllText("$OutputFile.sha256", "$hash  $($item.Name)`n", [Text.Encoding]::ASCII)
Write-Host "Offline build artifact: $OutputFile"
Write-Host "Bytes: $($item.Length); SHA-256: $hash"
Write-Host 'Build is not installation acceptance. Sign the final EXE and pass clean disconnected Windows 10/11 acceptance before release.'
