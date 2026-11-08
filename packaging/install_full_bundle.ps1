[CmdletBinding()]
param(
    [string]$PayloadRoot,
    [string]$AppMsiPath,
    [string]$AppInstallDir,
    [string]$Components = 'all',
    [switch]$Resume,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'full_bundle_helpers.ps1')
$script:SetupDryRun = [bool]$DryRun
$script:SetupLogRoot = Join-Path $env:ProgramData 'Power Test/InstallerLogs'
$statePath = Join-Path $PSScriptRoot 'resume.json'
$taskName = 'Power Test Setup Resume'
$exitCode = 1
$mutex = $null
$ownsMutex = $false
$transcriptStarted = $false

try {
    if (-not $DryRun) {
        if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
            throw 'Setup requires 64-bit Windows PowerShell on 64-bit Windows.'
        }
        $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Setup must run as administrator.' }
        $mutex = New-Object Threading.Mutex($false, 'Global\ARP-PowerTest-FullSetup')
        try { $ownsMutex = $mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) { throw 'Another Power Test setup is running. Wait for it to finish.' }
        $activeRoot = Join-Path $env:CommonProgramFiles 'ARP/Setup'
        $null = New-Item -ItemType Directory -Path $activeRoot -Force
        $script:SetupActiveProcessPath = Join-Path $activeRoot 'active-process.json'
        Assert-SetupProcessIdle -StatePath $script:SetupActiveProcessPath
    }
    New-Item -ItemType Directory -Force -Path $script:SetupLogRoot | Out-Null
    $logPath = Join-Path $script:SetupLogRoot ("full-setup-{0:yyyyMMdd-HHmmss-fff}.log" -f (Get-Date))
    Start-Transcript -Path $logPath | Out-Null
    $transcriptStarted = $true
    $resumeAttempts = 0
    $completedSteps = @()
    if ($Resume) {
        $state = Read-SetupResumeState -StatePath $statePath -DryRun:$DryRun
        $PayloadRoot = Join-Path $PSScriptRoot 'drivers'
        $AppMsiPath = Join-Path $PSScriptRoot 'PowerTest.msi'
        $AppInstallDir = [string]$state.InstallDirectory
        $Components = [string]$state.Components
        $resumeAttempts = [int]$state.ResumeAttempts + 1
        $completedSteps = @($state.CompletedSteps)
    }
    if ([string]::IsNullOrWhiteSpace($AppInstallDir) -or -not [IO.Path]::IsPathRooted($AppInstallDir)) { throw 'An absolute application installation directory is required.' }
    if (-not $PayloadRoot -or -not $AppMsiPath) { throw 'Driver payload and application MSI paths are required.' }
    $selected = @(Get-SetupComponents -Components $Components)
    $plan = @(Get-SetupPlan -PayloadRoot $PayloadRoot -Components $selected)
    # Validate every selected payload before the first system change.
    Assert-SetupPayload -Plan $plan -PayloadRoot $PayloadRoot -MsiPath $AppMsiPath
    if (-not $DryRun) { Assert-SetupApplicationClosed -InstallDirectory $AppInstallDir }
    if (-not $DryRun -and -not $Resume) {
        # A new manual setup replaces any previously deferred selection.
        Remove-SetupContinuationTask
    }
    $script:SetupResumeState = @{
        InstallDirectory = $AppInstallDir
        Components = ($selected -join ',')
        ResumeAttempts = $resumeAttempts
        CompletedSteps = $completedSteps
    }
    $checkpoint = {
        param([string]$Id)
        if (-not $DryRun) {
            $script:SetupResumeState.CompletedSteps = @($script:SetupResumeState.CompletedSteps) + $Id
            Write-SetupJson -Path $statePath -Value $script:SetupResumeState
        }
    }
    $result = Invoke-FullSetupPlan -Plan $plan -PayloadRoot $PayloadRoot -Components $selected `
        -MsiPath $AppMsiPath -InstallDirectory $AppInstallDir -CompletedSteps $completedSteps -OnStepCompleted $checkpoint
    switch ($result) {
        'resume' {
            if (-not $DryRun) {
                # NSIS stores this script and its inputs under Common Files (admin-write only).
                # Never schedule an elevated task from a user-writable temporary directory.
                $resumeArguments = Get-SetupResumeParameters -ScriptPath $PSCommandPath `
                    -CommonFilesRoot $env:CommonProgramFiles -ResumeAttempts $resumeAttempts
                Write-SetupJson -Path $statePath -Value $script:SetupResumeState
                $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
                $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
                    -Argument $resumeArguments
                $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
                $taskPrincipal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Highest
                $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
                Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Trigger $trigger -Principal $taskPrincipal -Settings $settings -Force | Out-Null
            }
            Write-Host '[Power Test Setup] INCOMPLETE: restart Windows, then sign in with the same administrator account. Setup will continue automatically.'
            $exitCode = 20
        }
        { $_ -in @('complete', 'reboot') } {
            if (-not $DryRun) {
                Set-SetupInstallLocation -InstallDirectory $AppInstallDir
                Remove-SetupContinuationTask
                Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
            }
            $exitCode = if ($result -eq 'reboot') { 3010 } else { 0 }
            if ($DryRun) {
                Write-Host "[Power Test Setup] DRY RUN ONLY: no software or drivers were installed. Log: $logPath"
            } else {
                Write-Host "[Power Test Setup] Selected components installed. Result: $result. Log: $logPath"
            }
        }
        default { throw "Unexpected setup result: $result" }
    }
} catch {
    # Write-Error would throw again with ErrorActionPreference=Stop and obscure the result.
    Write-Host "[Power Test Setup] FAILED: $($_.Exception.Message)"
    $exitCode = 1
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
}
if ($Resume -and -not $DryRun) {
    $message = switch ($exitCode) {
        0 { 'Power Test、运行环境及所选驱动已安装。请启动 Power Test 完成激活。' }
        3010 { '所选组件已安装。请再次重启 Windows 后使用 Power Test。' }
        20 { '安装尚未完成。请重启 Windows，并使用同一管理员账户登录，安装将继续。' }
        default { "安装未完成。请查看日志：$script:SetupLogRoot。修复问题后重新运行同一个安装包。" }
    }
    $shell = New-Object -ComObject WScript.Shell
    $null = $shell.Popup($message, 0, 'Power Test 安装', $(if ($exitCode -eq 1) { 16 } else { 64 }))
}
exit $exitCode
