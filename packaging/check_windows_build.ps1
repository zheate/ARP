[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "Windows packaging preflight must run on Windows."
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$tauriRoot = Join-Path $projectRoot "tauri-ui"

$requiredCommands = @("conda.exe", "npm.cmd", "cargo.exe")
foreach ($command in $requiredCommands) {
    if (-not (Get-Command $command -CommandType Application -ErrorAction SilentlyContinue)) {
        throw "Required build command was not found: $command"
    }
}

$requiredFiles = @(
    (Join-Path $projectRoot "requirements-build.txt"),
    (Join-Path $projectRoot "packaging\tauri_bridge_entry.py"),
    (Join-Path $projectRoot "rust-realtime-core\Cargo.toml"),
    (Join-Path $tauriRoot "package.json"),
    (Join-Path $tauriRoot "package-lock.json"),
    (Join-Path $tauriRoot "src-tauri\Cargo.toml"),
    (Join-Path $tauriRoot "src-tauri\tauri.conf.json"),
    (Join-Path $projectRoot "assets\libs\ocean_direct\OceanDirect.dll"),
    (Join-Path $projectRoot "local_drivers\ch341\CH341DLLA64.DLL")
)
foreach ($path in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required Windows packaging input is missing: $path"
    }
}

$package = Get-Content -LiteralPath (Join-Path $tauriRoot "package.json") -Raw | ConvertFrom-Json
if ([string]$package.version -notmatch '^\d+\.\d+\.\d+$') {
    throw "tauri-ui/package.json must contain a three-part semantic version."
}

$tauriConfig = Get-Content -LiteralPath (Join-Path $tauriRoot "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
if ([string]$tauriConfig.version -ne "../package.json") {
    throw "Tauri must read its application version from ../package.json."
}

& conda.exe run -n sth_eb314 python -c "import matplotlib, nidaqmx, numpy, openpyxl, orjson, pyvisa, scipy, win32com.client, xlsxwriter; import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "The sth_eb314 environment is missing one or more build/runtime dependencies."
}

& cargo.exe metadata --manifest-path (Join-Path $projectRoot "rust-realtime-core\Cargo.toml") --format-version 1 --no-deps | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "The Rust realtime core manifest is invalid."
}

& cargo.exe metadata --manifest-path (Join-Path $tauriRoot "src-tauri\Cargo.toml") --format-version 1 --no-deps | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "The Tauri Rust manifest is invalid."
}

Write-Host "Windows packaging preflight passed for Power Test $($package.version)."
