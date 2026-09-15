# Windows PowerShell 5.1 compatible; dot-sourced, never installs on import.
Set-StrictMode -Version Latest

function Get-PayloadPath {
    param([string]$Root, [string]$Relative)
    if ([string]::IsNullOrWhiteSpace($Relative) -or $Relative -match '(^[/\\]|:|[<>|"*?\x00-\x1f])') { throw "Unsafe payload path: $Relative" }
    $parts = $Relative -split '[/\\]'
    if (@($parts | Where-Object { $_ -in @('', '.', '..') -or $_ -match '[ .]$|^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$' }).Count) { throw "Unsafe payload path: $Relative" }
    $base = [IO.Path]::GetFullPath($Root)
    if ((Test-Path -LiteralPath $base) -and ((Get-Item -LiteralPath $base -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Reparse root rejected: $base" }
    $path = $base
    foreach ($part in $parts) {
        $path = Join-Path $path $part
        if (Test-Path -LiteralPath $path) {
            if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point rejected: $path" }
        }
    }
    return $path
}

function ConvertTo-NativeArguments {
    param([AllowEmptyCollection()][string[]]$Arguments = @())
    # Windows CommandLineToArgvW/MS CRT quoting, including empty strings and
    # backslashes before quotes or the closing quote. Never use cmd /c or iex.
    return (($Arguments | ForEach-Object {
        if ($_ -eq '' -or $_ -match '[\s"]') {
            '"' + ($_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
        } else { $_ }
    }) -join ' ')
}

function Write-DeployEvent {
    param([string]$Component, [string]$State, [object]$Detail = $null)
    $event = [ordered]@{ time = [DateTime]::UtcNow.ToString('o'); component = $Component; state = $State; detail = $Detail }
    Write-Host "[Power Test Setup][$Component][$State] $($Detail | ConvertTo-Json -Compress -Depth 6)"
    if (-not $script:Deploy.DryRun) {
        $json = $event | ConvertTo-Json -Compress -Depth 8
        [IO.File]::AppendAllText($script:Deploy.Journal, $json + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))
    }
}

function Invoke-DeployProcess {
    param(
        [string]$Component, [string]$FilePath, [string[]]$Arguments = @(),
        [int[]]$SuccessCodes = @(0), [int[]]$RebootCodes = @(3010),
        [ValidateRange(1, 14400)][int]$TimeoutSeconds = 1800,
        [string]$WorkingDirectory
    )
    $line = ConvertTo-NativeArguments $Arguments
    Write-DeployEvent $Component 'command' @{ executable = $FilePath; arguments = $line; timeoutSeconds = $TimeoutSeconds }
    if ($script:Deploy.DryRun) { return 0 }
    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) { throw "${Component}: executable is missing: $FilePath" }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = $line
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    if ($WorkingDirectory) { $info.WorkingDirectory = $WorkingDirectory }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $watch = [Diagnostics.Stopwatch]::StartNew()
    try {
        if (-not $process.Start()) { throw "${Component}: process did not start" }
        $statePath = Join-Path $script:Deploy.CacheRoot 'process-state.json'
        $running = @{ component = $Component; pid = $process.Id; started = $process.StartTime.ToUniversalTime().ToString('o'); executable = $FilePath }
        [IO.File]::WriteAllText($statePath, ($running | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $nextProgress = 15
        while (-not $process.WaitForExit(1000)) {
            if ($watch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                Write-DeployEvent $Component 'indeterminate-timeout' @{ pid = $process.Id; elapsedSeconds = $watch.Elapsed.TotalSeconds }
                # Killing an MSI client does not cancel its service transaction.
                # Leave the persistent verified cache intact and stop the chain.
                throw "${Component}: timeout; installation state UNKNOWN, process $($process.Id) may still run. No later component was started. Keep the installer cache; inspect logs before retry."
            }
            if ($watch.Elapsed.TotalSeconds -ge $nextProgress) {
                Write-Host "[$Component] running: $([int]$watch.Elapsed.TotalSeconds)s"
                $nextProgress += 15
            }
        }
        $code = [int]$process.ExitCode
        # Detached children retaining redirected handles are not waited forever.
        foreach ($pair in @(@('stdout', $stdout), @('stderr', $stderr))) {
            if ($pair[1].Wait(5000)) {
                [IO.File]::AppendAllText((Join-Path $script:Deploy.LogRoot "$Component-$($pair[0]).log"), $pair[1].Result, (New-Object Text.UTF8Encoding($false)))
            } else { Write-DeployEvent $Component 'output-pipe-still-open' $pair[0] }
        }
        Write-DeployEvent $Component 'exit' @{ code = $code; elapsedSeconds = $watch.Elapsed.TotalSeconds }
        if ($code -eq 1641) { throw "${Component}: installer initiated a reboot despite suppression; release contract violated" }
        Remove-Item -LiteralPath $statePath -Force
        if ($code -in $RebootCodes) { $script:Deploy.RebootRequired = $true }
        elseif ($code -notin $SuccessCodes) { throw "${Component}: installation failed with exit code $code. Logs: $($script:Deploy.LogRoot)" }
        return $code
    } finally { $watch.Stop(); $process.Dispose() }
}

function Get-SystemPath {
    param([string]$Value)
    $roots = @{
        System32 = (Join-Path $env:SystemRoot 'System32')
        ProgramFiles = [Environment]::GetFolderPath('ProgramFiles')
        ProgramFilesX86 = [Environment]::GetFolderPath('ProgramFilesX86')
        CommonProgramFiles = [Environment]::GetFolderPath('CommonProgramFiles')
    }
    if ($Value -notmatch '^\{(System32|ProgramFiles|ProgramFilesX86|CommonProgramFiles)\}[/\\](.+)$') { throw "Invalid system probe path: $Value" }
    return Get-PayloadPath $roots[$Matches[1]] $Matches[2]
}

function Test-VersionRange {
    param([string]$Actual, [object]$Probe)
    $version = $null
    if (-not [version]::TryParse($Actual, [ref]$version)) { return $false }
    if ($version -lt [version]$Probe.minimumVersion) { return $false }
    if ($Probe.PSObject.Properties['maximumVersion'] -and $version -gt [version]$Probe.maximumVersion) {
        throw "Installed version $version exceeds reviewed maximum $($Probe.maximumVersion); refusing an automatic downgrade"
    }
    return $true
}
