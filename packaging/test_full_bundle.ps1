# Focused, side-effect-free tests. Runs under Windows PowerShell 5.1 or PowerShell 7.
$ErrorActionPreference = 'Stop'
$helperPath = Join-Path $PSScriptRoot 'full_bundle_helpers.ps1'
$script:Passed = 0
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('arp-setup-tests-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null

function Assert-Equal($Actual, $Expected) {
    if ($Actual -cne $Expected) { throw "Expected <$Expected>, got <$Actual>" }
}
function Assert-True($Value) { if (-not $Value) { throw 'Expected true.' } }
function Assert-Throws([scriptblock]$Action, [string]$Pattern) {
    $caught = $false
    try { & $Action } catch {
        $caught = $true
        if ($_.Exception.Message -notmatch $Pattern) { throw "Unexpected error: $($_.Exception.Message)" }
    }
    if (-not $caught) { throw "Expected an error matching $Pattern" }
}
function Test-Case([string]$Name, [scriptblock]$Body) {
    . $helperPath
    $script:SetupDryRun = $false
    $script:SetupLogRoot = $testRoot
    $script:Calls = @()
    # Service control is a Windows boundary; plan tests do not touch host services.
    function Start-SetupMsiService {}
    function Test-NipkgOfflineSupport { return $true }
    & $Body
    $script:Passed++
    Write-Host "PASS $Name"
}
function Write-FixtureManifest([string]$Root) {
    $entries = @(Get-ChildItem -LiteralPath $Root -Recurse -File | Where-Object Name -ne 'payload-sha256.json' | ForEach-Object {
        [pscustomobject]@{ Path = $_.Name; SHA256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    })
    ConvertTo-Json -InputObject $entries | Set-Content -LiteralPath (Join-Path $Root 'payload-sha256.json') -Encoding UTF8
}

try {
    Test-Case 'full, custom, empty, and invalid component selections' {
        Assert-Equal (@(Get-SetupComponents).Count) 8
        Assert-Equal ((Get-SetupComponents 'visa, ch341,visa') -join ',') 'ch341,visa'
        Assert-Equal (@(Get-SetupComponents 'none').Count) 0
        Assert-Equal (@(Get-SetupComponents '').Count) 0
        Assert-Throws { Get-SetupComponents 'visa,invalid' } 'Unknown setup component'
    }
    Test-Case 'minimal install still includes WebView2' {
        $plan = @(Get-SetupPlan -PayloadRoot $testRoot -Components @())
        Assert-Equal $plan.Count 1
        Assert-Equal $plan[0].Id 'webview'
    }
    Test-Case 'NI is included once for VISA and DAQ and absent for USB-only' {
        $plan = @(Get-SetupPlan -PayloadRoot $testRoot -Components @('visa', 'daq'))
        Assert-Equal (($plan.Id) -join ',') 'webview,ni'
        $plan = @(Get-SetupPlan -PayloadRoot $testRoot -Components @('ocean'))
        Assert-Equal (($plan.Id) -join ',') 'webview,ocean'
    }
    Test-Case 'Windows argument quoting preserves spaces, empty arguments, quotes and trailing slashes' {
        Assert-Equal (Format-ArgumentList @('plain', '', 'C:\Program Files\ARP\', 'a"b')) 'plain "" "C:\Program Files\ARP\\" "a\"b"'
    }
    Test-Case 'success, reboot, incomplete, and fatal return codes are distinct' {
        Assert-Equal (Resolve-SetupExitCode 0) 'complete'
        Assert-Equal (Resolve-SetupExitCode 3010) 'reboot'
        Assert-Equal (Resolve-SetupExitCode 1641) 'reboot'
        Assert-Equal (Resolve-SetupExitCode -125071 @(0, -125071)) 'reboot'
        Assert-Equal (Resolve-SetupExitCode -126300 @(0, -126300)) 'resume'
        Assert-Throws { Resolve-SetupExitCode 1603 } 'error 1603'
        Assert-Throws { Resolve-SetupExitCode 259 } 'error 259'
        Assert-Throws { Resolve-SetupExitCode -126300 } 'error -126300'
    }
    Test-Case 'existing machine-wide WebView2 skips the installer' {
        function Test-WebViewRuntime { return $true }
        function Invoke-SetupProcess { throw 'Must not install.' }
        Assert-Equal (Invoke-SetupStep ([pscustomobject]@{Kind='webview';Path='unused'}) $testRoot @()) 'complete'
    }
    Test-Case 'new WebView2 must be detected after installer success' {
        function Test-WebViewRuntime { return $false }
        function Invoke-SetupProcess { return 'complete' }
        Assert-Throws { Invoke-SetupStep ([pscustomobject]@{Kind='webview';Path='unused'}) $testRoot @() } 'did not produce'
    }
    Test-Case 'WebView2 is checked again after installation' {
        $script:Checks = 0
        function Test-WebViewRuntime { $script:Checks++; return $script:Checks -gt 1 }
        function Invoke-SetupProcess { param($ArgumentLine); Assert-Equal $ArgumentLine '/silent /install'; return 'complete' }
        Assert-Equal (Invoke-SetupStep ([pscustomobject]@{Kind='webview';Path='unused'}) $testRoot @()) 'complete'
        Assert-Equal $script:Checks 2
    }
    Test-Case 'all INF files are installed; required reboot propagates' {
        $driverRoot = Join-Path $testRoot 'infs'
        New-Item -ItemType Directory -Path $driverRoot | Out-Null
        Set-Content (Join-Path $driverRoot 'one.inf') 'test'
        Set-Content (Join-Path $driverRoot 'two.inf') 'test'
        function Invoke-SetupProcess { param($ArgumentLine); $script:Calls += $ArgumentLine; return 'reboot' }
        $step = [pscustomobject]@{Kind='inf';Id='serial';Path=$driverRoot}
        Assert-Equal (Invoke-SetupStep $step $testRoot @('serial')) 'reboot'
        Assert-Equal $script:Calls.Count 2
        Assert-True ($script:Calls[0] -match '/add-driver .*one.inf.* /install')
        Assert-True ($script:Calls[1] -notmatch '/reboot')
    }
    Test-Case 'Ophir waits for MSI and requires the 64-bit COM registration' {
        function Test-OphirComRegistration { return $false }
        function Invoke-SetupProcess { param($ArgumentLine); $script:Calls += $ArgumentLine; return 'complete' }
        Assert-Throws { Invoke-SetupStep ([pscustomobject]@{Kind='ophir';Path='unused'}) $testRoot @('ophir') } 'COM interface'
        Assert-Equal $script:Calls[0] '/s /w /v"/qn /norestart REBOOT=ReallySuppress"'
    }
    Test-Case 'Coherent reports required reboots instead of silently returning zero' {
        function Invoke-SetupProcess { param($ArgumentLine); Assert-True ($ArgumentLine -match '/NORESTART /RESTARTEXITCODE=3010'); return 'reboot' }
        Assert-Equal (Invoke-SetupStep ([pscustomobject]@{Kind='coherent';Path='unused'}) $testRoot @('coherent')) 'reboot'
    }
    Test-Case 'NI bootstrap reboot stops before runtime and application installation' {
        function Get-NipkgPath { return $null }
        function Invoke-SetupProcess { param($Name); $script:Calls += $Name; return 'resume' }
        Assert-Equal (Invoke-NiRuntimeSetup $testRoot @('visa')) 'resume'
        Assert-Equal ($script:Calls -join ',') 'NI Package Manager'
    }
    Test-Case 'existing NI uses private offline configuration and selected runtime only' {
        function Get-NipkgPath { return 'nipkg.exe' }
        function Invoke-SetupProcess { param($ArgumentLine); $script:Calls += $ArgumentLine; return 'complete' }
        Assert-Equal (Invoke-NiRuntimeSetup (Join-Path $testRoot 'payload') @('visa')) 'complete'
        Assert-Equal $script:Calls.Count 4
        Assert-True ($script:Calls[0] -match 'nipkg.enable-NI-connectivity false')
        Assert-True ($script:Calls[2] -match '^update .*arp-offline-runtime$')
        Assert-True ($script:Calls[3] -match '--prevent-reboot ni-visa-runtime$')
        Assert-True ($script:Calls[3] -notmatch 'ni-daqmx|allow-downgrade|allow-uninstall')
        foreach ($call in $script:Calls) { Assert-True ($call -match '--config=') }
    }
    Test-Case 'application installs only after every prerequisite succeeds' {
        function Invoke-SetupStep { param($Step); $script:Calls += $Step.Id; return 'complete' }
        function Invoke-SetupProcess { param($Name, $ArgumentLine); $script:Calls += 'app'; Assert-True ($ArgumentLine -match '/qn /norestart'); Assert-True ($ArgumentLine.Contains('INSTALLDIR="C:\Program Files\Power Test"')); return 'complete' }
        $plan = @([pscustomobject]@{Id='webview'}, [pscustomobject]@{Id='ni'})
        Assert-Equal (Invoke-FullSetupPlan $plan $testRoot @('visa') 'app.msi' 'C:\Program Files\Power Test') 'complete'
        Assert-Equal ($script:Calls -join ',') 'webview,ni,app'
    }
    Test-Case 'prerequisite failure cannot reach application installation' {
        function Invoke-SetupStep { throw 'driver failed' }
        function Invoke-SetupProcess { throw 'Application must not run.' }
        Assert-Throws { Invoke-FullSetupPlan @([pscustomobject]@{Id='driver'}) $testRoot @() 'app.msi' 'app' } 'driver failed'
    }
    Test-Case 'incomplete prerequisite cannot be reported as a completed application install' {
        function Invoke-SetupStep { return 'resume' }
        function Invoke-SetupProcess { throw 'Application must not run.' }
        Assert-Equal (Invoke-FullSetupPlan @([pscustomobject]@{Id='ni'}) $testRoot @('daq') 'app.msi' 'app') 'resume'
    }
    Test-Case 'a successful driver reboot requirement survives application success' {
        function Invoke-SetupStep { return 'reboot' }
        function Invoke-SetupProcess { return 'complete' }
        Assert-Equal (Invoke-FullSetupPlan @([pscustomobject]@{Id='driver'}) $testRoot @() 'app.msi' 'app') 'reboot'
    }
    Test-Case 'corruption, missing files, extra files, duplicates, and traversal are rejected' {
        $payload = Join-Path $testRoot 'hashes'
        New-Item -ItemType Directory -Path $payload | Out-Null
        $file = Join-Path $payload 'test.inf'
        Set-Content $file 'original'
        Write-FixtureManifest $payload
        Assert-SetupHashes $payload
        Set-Content $file 'corrupted'
        Assert-Throws { Assert-SetupHashes $payload } 'SHA-256 mismatch'
        Write-FixtureManifest $payload
        Remove-Item $file
        Assert-Throws { Assert-SetupHashes $payload } 'file is missing'
        Set-Content $file 'original'
        Write-FixtureManifest $payload
        Set-Content (Join-Path $payload 'extra.exe') 'extra'
        Assert-Throws { Assert-SetupHashes $payload } 'not in the integrity manifest'
        $entries = @([pscustomobject]@{Path='../outside.exe'; SHA256=('A'*64)})
        ConvertTo-Json -InputObject $entries | Set-Content (Join-Path $payload 'payload-sha256.json')
        Assert-Throws { Assert-SetupHashes $payload } 'Invalid or duplicate'
        $entry = [pscustomobject]@{Path='test.inf'; SHA256=(Get-FileHash $file).Hash}
        ConvertTo-Json -InputObject @($entry,$entry) | Set-Content (Join-Path $payload 'payload-sha256.json')
        Assert-Throws { Assert-SetupHashes $payload } 'Invalid or duplicate'
    }
    Test-Case 'missing selected input fails before setup is executed' {
        $msi = Join-Path $testRoot 'app.msi'
        Set-Content $msi 'fixture'
        Assert-Throws { Assert-SetupPayload @([pscustomobject]@{Id='ocean';Kind='inf';Path=(Join-Path $testRoot 'missing.inf')}) $testRoot $msi -SkipHashCheck } 'payload is missing'
    }
    Test-Case 'automatic resume is restricted to protected cache and bounded retries' {
        $common = Join-Path $testRoot 'Common Files'
        $scriptFile = Join-Path $common 'ARP/Setup/0.1.0/run/install_full_bundle.ps1'
        $argsLine = Get-SetupResumeParameters $scriptFile $common 0
        Assert-True ($argsLine -match '-File ".*Common Files.*install_full_bundle.ps1" -Resume$')
        Assert-Throws { Get-SetupResumeParameters (Join-Path $testRoot 'user-writable/setup.ps1') $common 0 } 'protected ARP setup cache'
        Assert-Throws { Get-SetupResumeParameters $scriptFile $common 2 } 'after two resume attempts'
        $null = Get-SetupResumeParameters $scriptFile $common 1
    }
    Test-Case 'continuation task is removed when found; removal failures are not hidden' {
        function Get-ScheduledTask { param($TaskName, $ErrorAction); return [pscustomobject]@{TaskName='Power Test Setup Resume';TaskPath='\'} }
        function Unregister-ScheduledTask { param($TaskName, $TaskPath, $Confirm, $ErrorAction); Assert-Equal $TaskName 'Power Test Setup Resume'; $script:Calls += $TaskName }
        Remove-SetupContinuationTask
        Assert-Equal $script:Calls.Count 1
        function Unregister-ScheduledTask { throw 'task removal failed' }
        Assert-Throws { Remove-SetupContinuationTask } 'task removal failed'
    }
    Test-Case 'unknown step results cannot be treated as installation success' {
        function Invoke-SetupStep { return 'unknown' }
        Assert-Throws { Invoke-FullSetupPlan @([pscustomobject]@{Id='driver'}) $testRoot @() 'app.msi' 'app' } 'Invalid result'
    }
    Test-Case 'real process success and failure are read from exit codes' {
        $processPath = (Get-Process -Id $PID).Path
        Assert-Equal (Invoke-SetupProcess -Name 'exit-zero fixture' -FilePath $processPath -ArgumentLine '-NoProfile -Command "exit 0"') 'complete'
        Assert-Throws { Invoke-SetupProcess -Name 'exit-seven fixture' -FilePath $processPath -ArgumentLine '-NoProfile -Command "exit 7"' } 'exit 7'
    }
    Test-Case 'NI feed must reference present files and both supported runtimes' {
        $feedRoot = Join-Path $testRoot 'ni-feed'
        New-Item -ItemType Directory -Path $feedRoot | Out-Null
        $index = Join-Path $feedRoot 'Packages.gz'
        $fileStream = [IO.File]::Create($index)
        $gzip = New-Object IO.Compression.GZipStream($fileStream, [IO.Compression.CompressionMode]::Compress)
        $writer = New-Object IO.StreamWriter($gzip)
        $writer.Write("Package: ni-visa-runtime`nFilename: visa.nipkg`n`nPackage: ni-daqmx-runtime-core`nFilename: daq.nipkg`n")
        $writer.Dispose()
        $fileStream.Dispose()
        Set-Content (Join-Path $feedRoot 'visa.nipkg') 'fixture'
        Assert-Throws { Assert-NiFeedFiles $feedRoot } 'daq.nipkg'
        Set-Content (Join-Path $feedRoot 'daq.nipkg') 'fixture'
        Assert-NiFeedFiles $feedRoot
    }
    Test-Case 'serial device components always include their required VISA runtime' {
        foreach ($component in @('ftdi', 'serial', 'coherent')) {
            Assert-True ('visa' -in @(Get-SetupComponents $component))
        }
        Assert-True ('visa' -notin @(Get-SetupComponents 'ocean,ophir,ch341'))
    }
    Test-Case 'Windows Installer service is started and awaited only when needed' {
        . $helperPath
        $script:TestService = [pscustomobject]@{Status='Stopped'}
        $script:ServiceWaited = $false
        $script:TestService | Add-Member -MemberType ScriptMethod -Name WaitForStatus -Value { param($Status, $Timeout); $script:ServiceWaited = $true }
        function Get-Service { param($Name, $ErrorAction); Assert-Equal $Name 'msiserver'; return $script:TestService }
        function Start-Service { param($Name, $ErrorAction); $script:Calls += $Name }
        Start-SetupMsiService
        Assert-Equal ($script:Calls -join ',') 'msiserver'
        Assert-True $script:ServiceWaited
        $script:TestService.Status = [System.ServiceProcess.ServiceControllerStatus]::Running
        $script:Calls = @()
        Start-SetupMsiService
        Assert-Equal $script:Calls.Count 0
    }
    Test-Case 'resume skips completed vendor installers and checkpoints new successes' {
        function Invoke-SetupStep { param($Step); $script:Calls += $Step.Id; return 'complete' }
        function Invoke-SetupProcess { $script:Calls += 'app'; return 'complete' }
        $script:SavedSteps = @()
        $plan = @([pscustomobject]@{Id='webview'}, [pscustomobject]@{Id='ophir'}, [pscustomobject]@{Id='ni'})
        $result = Invoke-FullSetupPlan $plan $testRoot @('ophir','visa') 'app.msi' 'app' -CompletedSteps @('webview','ophir') -OnStepCompleted { param($Id); $script:SavedSteps += $Id }
        Assert-Equal $result 'complete'
        Assert-Equal ($script:Calls -join ',') 'ni,app'
        Assert-Equal ($script:SavedSteps -join ',') 'ni'
        Assert-Throws { Invoke-FullSetupPlan $plan $testRoot @() 'app.msi' 'app' -CompletedSteps @('unknown') } 'Invalid completed setup component'
    }
    Test-Case 'a failed component is never checkpointed as completed' {
        function Invoke-SetupStep { param($Step); if ($Step.Id -eq 'ni') { throw 'NI failure' }; return 'complete' }
        $script:SavedSteps = @()
        $plan = @([pscustomobject]@{Id='webview'}, [pscustomobject]@{Id='ni'})
        Assert-Throws { Invoke-FullSetupPlan $plan $testRoot @() 'app.msi' 'app' -OnStepCompleted { param($Id); $script:SavedSteps += $Id } } 'NI failure'
        Assert-Equal ($script:SavedSteps -join ',') 'webview'
    }
    Test-Case 'disconnected or newer INF devices do not abort driver preinstallation' {
        function Get-SetupInfFiles { return @([pscustomobject]@{Name='device.inf';FullName='device.inf'}) }
        function Invoke-SetupProcess {
            param($Name, $FilePath, $ArgumentLine, [int[]]$SuccessCodes = @(0,3010,1641))
            return Resolve-SetupExitCode 259 $SuccessCodes
        }
        Assert-Equal (Invoke-SetupStep ([pscustomobject]@{Kind='inf';Id='ocean';Path='unused'}) $testRoot @('ocean')) 'complete'
    }
    Test-Case 'an unavailable native process exit code is never converted to success' {
        function Start-Process {
            $fake = [pscustomobject]@{Id=123;ExitCode=$null;Handle=[IntPtr]1;StartTime=[datetime]::UtcNow;HasExited=$true}
            $fake | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($Milliseconds); return $true }
            return $fake
        }
        Assert-Throws { Invoke-SetupProcess -Name 'unknown-exit' -FilePath 'unused' -ArgumentLine 'unused' } 'exit code.*unavailable|unavailable.*exit code'
    }
    Test-Case 'corrupt or missing resume state still removes the one-shot task first' {
        function Remove-SetupContinuationTask { $script:Calls += 'removed' }
        $stateFile = Join-Path $testRoot 'broken-resume.json'
        Set-Content $stateFile '{ invalid'
        Assert-Throws { Read-SetupResumeState $stateFile } 'JSON|Unexpected|Invalid'
        Assert-Equal ($script:Calls -join ',') 'removed'
        Remove-Item $stateFile
        $script:Calls = @()
        Assert-Throws { Read-SetupResumeState $stateFile } 'does not exist|Cannot find'
        Assert-Equal ($script:Calls -join ',') 'removed'
        Set-Content $stateFile '{"InstallDirectory":"fixture","Components":"visa","ResumeAttempts":-1,"CompletedSteps":[]}'
        Assert-Throws { Read-SetupResumeState $stateFile } 'Invalid resume attempt'
    }
    Test-Case 'JSON checkpoint replacement preserves complete Unicode data' {
        $checkpoint = Join-Path $testRoot 'checkpoint.json'
        Write-SetupJson $checkpoint @{Components='visa';CompletedSteps=@('webview')}
        $unicodeDirectory = [string][char]0x4E2D + [char]0x6587
        Write-SetupJson $checkpoint @{Components='visa,daq';CompletedSteps=@('webview','ophir');InstallDirectory=$unicodeDirectory}
        $saved = Get-Content $checkpoint -Raw | ConvertFrom-Json
        Assert-Equal $saved.Components 'visa,daq'
        Assert-Equal $saved.InstallDirectory $unicodeDirectory
        Assert-Equal ($saved.CompletedSteps -join ',') 'webview,ophir'
        Assert-Equal (@(Get-ChildItem -LiteralPath $testRoot -Filter '*.tmp').Count) 0
    }
    Test-Case 'older NI Package Manager is upgraded before using offline-only settings' {
        function Get-NipkgPath { return 'old-nipkg.exe' }
        $script:VersionChecks = 0
        function Test-NipkgOfflineSupport { $script:VersionChecks++; return $script:VersionChecks -gt 1 }
        function Invoke-SetupProcess { param($Name, $ArgumentLine); $script:Calls += $Name; return 'complete' }
        Assert-Equal (Invoke-NiRuntimeSetup (Join-Path $testRoot 'payload') @('visa')) 'complete'
        Assert-Equal $script:Calls[0] 'NI Package Manager'
        Assert-Equal $script:Calls.Count 5
    }
    Test-Case 'an NI upgrade requiring reboot cannot run unsupported offline commands' {
        function Get-NipkgPath { return 'old-nipkg.exe' }
        function Test-NipkgOfflineSupport { return $false }
        function Invoke-SetupProcess { param($Name); $script:Calls += $Name; return 'reboot' }
        Assert-Equal (Invoke-NiRuntimeSetup $testRoot @('visa')) 'resume'
        Assert-Equal ($script:Calls -join ',') 'NI Package Manager'
    }
    Test-Case 'a timed-out installer blocks retry until that exact process exits' {
        $marker = Join-Path $testRoot 'active.json'
        $started = [datetime]::UtcNow
        Write-SetupJson $marker @{ProcessId=123;StartTimeUtcTicks=[string]$started.Ticks;Name='long installer'}
        $script:FakeStarted = $started
        function Get-Process { param($Id, $ErrorAction); return [pscustomobject]@{StartTime=$script:FakeStarted} }
        Assert-Throws { Assert-SetupProcessIdle $marker } 'still running'
        Assert-True (Test-Path $marker)
        $script:FakeStarted = $started.AddMinutes(1)
        Assert-SetupProcessIdle $marker
        Assert-True (-not (Test-Path $marker))
    }
    Test-Case 'an exited timed-out installer no longer blocks retry' {
        $marker = Join-Path $testRoot 'active-gone.json'
        Write-SetupJson $marker @{ProcessId=123;StartTimeUtcTicks='1';Name='old installer'}
        function Get-Process { return $null }
        Assert-SetupProcessIdle $marker
        Assert-True (-not (Test-Path $marker))
    }
    Test-Case 'installation directory is saved after initial or resumed success' {
        function New-Item { param($Path, [switch]$Force); $script:Calls += 'key' }
        function New-ItemProperty { param($Path, $Name, $Value, $PropertyType, [switch]$Force); $script:Calls += $Value; Assert-Equal $Name 'InstallDir' }
        Set-SetupInstallLocation -InstallDirectory 'C:\Program Files\Power Test'
        Assert-Equal ($script:Calls -join ',') 'key,C:\Program Files\Power Test'
    }
    Test-Case 'installation stops while the installed operator app is running' {
        function Get-ItemProperty { return $null }
        function Get-Process { return [pscustomobject]@{ProcessName='tauri-ui';Path=(Join-Path $testRoot 'application/tauri-ui.exe');Id=456} }
        Assert-Throws { Assert-SetupApplicationClosed (Join-Path $testRoot 'application') } 'Safely finish the test'
    }
    Test-Case 'a running sidecar also blocks installation without killing it' {
        function Get-ItemProperty { return $null }
        function Get-Process { return [pscustomobject]@{ProcessName='arp-python';Id=456} }
        Assert-Throws { Assert-SetupApplicationClosed (Join-Path $testRoot 'application') } 'will not terminate'
    }
    Test-Case 'no operator process allows installation to proceed' {
        function Get-ItemProperty { return $null }
        function Get-Process { return @() }
        Assert-SetupApplicationClosed (Join-Path $testRoot 'application')
    }
    Test-Case 'a real timed-out child keeps its marker until it has exited' {
        $script:SetupActiveProcessPath = Join-Path $testRoot 'real-timeout.json'
        $fixtureHost = (Get-Process -Id $PID).Path
        try {
            Assert-Throws { Invoke-SetupProcess -Name 'timeout fixture' -FilePath $fixtureHost -ArgumentLine '-NoProfile -Command "Start-Sleep -Seconds 3; exit 0"' -TimeoutMinutes 0 } 'exceeded'
            Assert-True (Test-Path $script:SetupActiveProcessPath)
            Assert-Throws { Assert-SetupProcessIdle $script:SetupActiveProcessPath } 'still running'
        } finally {
            if (Test-Path $script:SetupActiveProcessPath) {
                $saved = Get-Content $script:SetupActiveProcessPath -Raw | ConvertFrom-Json
                $fixture = Get-Process -Id $saved.ProcessId -ErrorAction SilentlyContinue
                if ($null -ne $fixture) { $null = $fixture.WaitForExit(10000) }
                Assert-SetupProcessIdle $script:SetupActiveProcessPath
            }
        }
        Assert-True (-not (Test-Path $script:SetupActiveProcessPath))
    }
    Write-Host "INSTALLER_TESTS_PASS $script:Passed"
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
