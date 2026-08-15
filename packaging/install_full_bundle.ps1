[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadRoot,

    [Parameter(Mandatory = $true)]
    [string]$AppMsiPath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$AppInstallDir,

    [string]$DriverDestination = (Join-Path $env:ProgramData "Power Test\Drivers"),

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

    Write-Host "[Power Test Setup] $Message"
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

function Invoke-ApplicationInstaller {
    param(
        [Parameter(Mandatory = $true)][string]$MsiPath,
        [Parameter(Mandatory = $true)][string]$InstallDirectory
    )

    $msiLogPath = Join-Path $logRoot ("application-msi-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
    $argumentParts = @(
        "/i",
        (Format-ArgumentList @($MsiPath)),
        ("INSTALLDIR=" + (Format-ArgumentList @($InstallDirectory))),
        "/qn",
        "/norestart",
        "/L*v",
        (Format-ArgumentList @($msiLogPath))
    )
    $argumentLine = $argumentParts -join " "
    $display = "$env:SystemRoot\System32\msiexec.exe $argumentLine"
    Write-Host "  MSI log: $msiLogPath"
    Write-Host "> $display"
    if ($DryRun) {
        return
    }

    $installerService = Get-Service -Name "msiserver" -ErrorAction Stop
    if ($installerService.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Running) {
        Write-Host "  Starting Windows Installer service..."
        Start-Service -Name "msiserver"
        $installerService.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Running,
            [TimeSpan]::FromSeconds(30)
        )
    }

    $process = Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" `
        -ArgumentList $argumentLine `
        -PassThru

    $timeout = [TimeSpan]::FromMinutes(5)
    $progressInterval = [TimeSpan]::FromSeconds(10)
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    $nextProgress = $progressInterval
    while (-not $process.WaitForExit(1000)) {
        if ($stopwatch.Elapsed -ge $timeout) {
            try {
                $process.Kill()
                $process.WaitForExit()
            }
            catch {
                Write-Warning "Could not stop timed-out msiexec process $($process.Id): $_"
            }
            throw "Power Test MSI timed out after 5 minutes. See $msiLogPath"
        }
        if ($stopwatch.Elapsed -ge $nextProgress) {
            Write-Host ("  Power Test MSI is still running ({0:mm\:ss} elapsed)..." -f $stopwatch.Elapsed)
            $nextProgress = $nextProgress.Add($progressInterval)
        }
    }
    $stopwatch.Stop()
    $exitCode = [int]$process.ExitCode
    Write-Host "  exit code: $exitCode; elapsed: $($stopwatch.Elapsed.ToString('mm\:ss'))"
    if ($exitCode -eq 3010) {
        $script:RebootRequired = $true
    }
    if ($exitCode -notin @(0, 3010)) {
        throw "Power Test application installation failed with exit code $exitCode. See $msiLogPath"
    }
}

try {
    if (-not $DryRun -and -not (Test-Administrator)) {
        throw "Power Test Setup must run as administrator."
    }
    if (-not (Test-Path -LiteralPath $PayloadRoot -PathType Container)) {
        throw "Driver payload is missing: $PayloadRoot"
    }
    $driverGuidePath = Join-Path $PayloadRoot "DRIVER-INSTALLATION.txt"
    if (-not (Test-Path -LiteralPath $driverGuidePath -PathType Leaf)) {
        throw "Driver installation guide is missing: $driverGuidePath"
    }
    if (-not (Test-Path -LiteralPath $AppMsiPath -PathType Leaf)) {
        throw "Power Test MSI is missing: $AppMsiPath"
    }

    Write-Step "Installing Power Test application"
    Invoke-ApplicationInstaller -MsiPath $AppMsiPath -InstallDirectory $AppInstallDir

    Write-Step "Copying offline driver files without installing them"
    Write-Host "> Copy driver files: $PayloadRoot -> $DriverDestination"
    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path $DriverDestination | Out-Null
        Get-ChildItem -LiteralPath $PayloadRoot -Force | Copy-Item `
            -Destination $DriverDestination `
            -Recurse `
            -Force
    }

    Write-Step "No driver or WebView2 runtime was installed automatically."
    Write-Step "Driver files: $DriverDestination"
    Write-Step "Manual instructions: $(Join-Path $DriverDestination 'DRIVER-INSTALLATION.txt')"
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
