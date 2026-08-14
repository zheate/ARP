[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadRoot,

    [Parameter(Mandatory = $true)]
    [string]$AppMsiPath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$AppInstallDir,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$script:RebootRequired = $false
$logRoot = Join-Path $env:ProgramData "Power Test\InstallerLogs"
$logPath = Join-Path $logRoot ("full-setup-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
Start-Transcript -Path $logPath -Append | Out-Null

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)

    Write-Host "[Power Test Full Setup] $Message"
}

function Test-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Format-ArgumentList {
    param([string[]]$Arguments)

    return ($Arguments | ForEach-Object {
        if ($_ -match '[\s"]') {
            '"' + ($_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
        }
        else {
            $_
        }
    }) -join ' '
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [int[]]$SuccessCodes = @(0),
        [switch]$HideWindow
    )

    $display = "$FilePath $(Format-ArgumentList $Arguments)".Trim()
    Write-Host "> $display"
    if ($DryRun) {
        return 0
    }

    $startParameters = @{
        FilePath = $FilePath
        ArgumentList = (Format-ArgumentList $Arguments)
        PassThru = $true
        Wait = $true
    }
    if ($HideWindow) {
        $startParameters.WindowStyle = "Hidden"
    }
    $process = Start-Process @startParameters
    $exitCode = [int]$process.ExitCode
    Write-Host "  exit code: $exitCode"

    if ($exitCode -in @(3010, -125071)) {
        $script:RebootRequired = $true
    }
    if ($exitCode -notin $SuccessCodes) {
        throw "Command failed with exit code ${exitCode}: $display"
    }
    return $exitCode
}

function Install-InfDriver {
    param([Parameter(Mandatory = $true)][string]$InfPath)

    if (-not (Test-Path -LiteralPath $InfPath)) {
        throw "Driver INF is missing: $InfPath"
    }
    Invoke-External -FilePath "$env:SystemRoot\System32\pnputil.exe" `
        -Arguments @("/add-driver", $InfPath, "/install") | Out-Null
}

function Get-NipkgPath {
    $candidates = @(
        (Join-Path $env:ProgramFiles "National Instruments\NI Package Manager\nipkg.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "National Instruments\NI Package Manager\nipkg.exe")
    )
    return $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}

try {
    if (-not $DryRun -and -not (Test-Administrator)) {
        throw "Power Test Full Setup must run as administrator."
    }
    if (-not (Test-Path -LiteralPath $PayloadRoot -PathType Container)) {
        throw "Driver payload is missing: $PayloadRoot"
    }
    if (-not (Test-Path -LiteralPath $AppMsiPath -PathType Leaf)) {
        throw "Power Test MSI is missing: $AppMsiPath"
    }

    Write-Step "Installing WCH CH341 I2C driver"
    $ch341Installer = Join-Path $PayloadRoot "01_ch341_i2c\CH341PAR.EXE"
    Invoke-External -FilePath $ch341Installer -Arguments @("/S") -SuccessCodes @(0, 1) | Out-Null

    Write-Step "Installing OceanHR4 WinUSB driver"
    Install-InfDriver -InfPath (Join-Path $PayloadRoot "03_ocean_hr4\driver\OOI_OceanHR4.inf")

    Write-Step "Installing LaserPoint FTDI driver"
    $ftdiArchive = Join-Path $PayloadRoot "04_laserpoint_ftdi\Win-10-11-Drivers-rev2026.zip"
    $ftdiExtractRoot = Join-Path $PayloadRoot "_expanded_ftdi"
    if (-not $DryRun) {
        if (Test-Path -LiteralPath $ftdiExtractRoot) {
            Remove-Item -LiteralPath $ftdiExtractRoot -Recurse -Force
        }
        Expand-Archive -LiteralPath $ftdiArchive -DestinationPath $ftdiExtractRoot -Force
    }
    else {
        Write-Host "> Expand-Archive $ftdiArchive -> $ftdiExtractRoot"
    }
    if (-not $DryRun) {
        $ftdiInfFiles = @(Get-ChildItem -LiteralPath $ftdiExtractRoot -Recurse -File -Filter "*.inf")
        if ($ftdiInfFiles.Count -eq 0) {
            throw "The LaserPoint FTDI archive contains no INF files."
        }
        foreach ($inf in $ftdiInfFiles) {
            Install-InfDriver -InfPath $inf.FullName
        }
    }

    Write-Step "Installing WCH CH341/CH343 serial adapter drivers"
    Install-InfDriver -InfPath (Join-Path $PayloadRoot "05_serial_adapters\wch_ch341ser\CH341SER.INF")
    Install-InfDriver -InfPath (Join-Path $PayloadRoot "05_serial_adapters\wch_ch343ser\CH343SER.INF")

    Write-Step "Installing NI Package Manager when required"
    $nipkgPath = Get-NipkgPath
    if (-not $nipkgPath) {
        $nipmInstaller = Join-Path $PayloadRoot "00_ni_package_manager\NIPackageManager24.0.0.exe"
        $nipmExitCode = Invoke-External -FilePath $nipmInstaller `
            -Arguments @("--quiet", "--accept-eulas", "--prevent-reboot") `
            -SuccessCodes @(0, 3010, -125071, -126300)

        for ($attempt = 0; $attempt -lt 30 -and -not $DryRun; $attempt++) {
            $nipkgPath = Get-NipkgPath
            if ($nipkgPath) {
                break
            }
            Start-Sleep -Seconds 2
        }
        if (-not $DryRun -and -not $nipkgPath) {
            if ($nipmExitCode -eq -126300) {
                Write-Warning "NI Package Manager requires a reboot before NI runtimes can be installed. Run Full Setup again after reboot."
                exit 3010
            }
            throw "NI Package Manager completed but nipkg.exe was not found."
        }
    }
    elseif ($DryRun) {
        Write-Host "> NI Package Manager already available: $nipkgPath"
    }

    Write-Step "Installing offline NI-DAQmx and NI-VISA runtimes"
    $feedPath = Join-Path $PayloadRoot "06_ni_runtime\feed"
    $feedName = "arp-ni-runtime-23.5"
    if ($DryRun -and -not $nipkgPath) {
        $nipkgPath = "C:\Program Files\National Instruments\NI Package Manager\nipkg.exe"
    }

    $feedList = if ($DryRun) { @() } else { @(& $nipkgPath feed-list) }
    if (-not $DryRun -and $LASTEXITCODE -ne 0) {
        throw "Could not read the NI Package Manager feed list."
    }
    if ($feedList | Select-String -Pattern "^$([regex]::Escape($feedName))\s" -Quiet) {
        Invoke-External -FilePath $nipkgPath -Arguments @("feed-remove", $feedName) | Out-Null
    }
    Invoke-External -FilePath $nipkgPath `
        -Arguments @("feed-add", "--system", "--name=$feedName", $feedPath) | Out-Null
    Invoke-External -FilePath $nipkgPath -Arguments @("update", $feedName) | Out-Null
    Invoke-External -FilePath $nipkgPath -Arguments @(
        "install",
        "--accept-eulas",
        "--yes",
        "--language=zh-CN",
        "ni-daqmx-runtime-core:windows_x64=23.5.0.49391-0+f239",
        "ni-visa-runtime:windows_x64=23.5.0.49318-0+f166"
    ) -SuccessCodes @(0, 3010, -125071) | Out-Null

    Write-Step "Installing Power Test application"
    Invoke-External -FilePath "$env:SystemRoot\System32\msiexec.exe" `
        -Arguments @("/i", $AppMsiPath, "INSTALLDIR=$AppInstallDir", "/qn", "/norestart") `
        -SuccessCodes @(0, 3010) | Out-Null

    Write-Step "Installation completed. Log: $logPath"
    if ($script:RebootRequired) {
        exit 3010
    }
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
finally {
    try {
        Stop-Transcript | Out-Null
    }
    catch {
        # Transcript may not have started if initialization failed.
    }
}
