# Shared by the elevated installer and focused tests. Keep compatible with Windows PowerShell 5.1.
Set-StrictMode -Version 2.0
$script:SetupActiveProcessPath = $null

function Format-ArgumentList {
    param([AllowEmptyCollection()][string[]]$Arguments = @())
    return ($Arguments | ForEach-Object {
        if ($_ -eq '' -or $_ -match '[\s"]') {
            '"' + ($_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
        } else { $_ }
    }) -join ' '
}

function Get-SetupComponents {
    param([AllowEmptyString()][string]$Components = 'all')
    $known = @('ch341', 'ocean', 'ftdi', 'serial', 'visa', 'daq', 'ophir', 'coherent')
    if ($Components -eq 'all') { return $known }
    if ([string]::IsNullOrWhiteSpace($Components) -or $Components -eq 'none') { return @() }
    $selected = @($Components.Split(',') | ForEach-Object { $_.Trim().ToLowerInvariant() } | Select-Object -Unique)
    foreach ($component in $selected) {
        if ($component -notin $known) { throw "Unknown setup component: $component" }
    }
    # These adapters are consumed through PyVISA by the product. A custom
    # selection must not accidentally omit its required instrument runtime.
    if (@($selected | Where-Object { $_ -in @('ftdi', 'serial', 'coherent') }).Count -gt 0 -and 'visa' -notin $selected) {
        $selected += 'visa'
    }
    # Always use a stable order, independently of the order selected by the user.
    return @($known | Where-Object { $_ -in $selected })
}

function Get-SetupPlan {
    param([string]$PayloadRoot, [AllowEmptyCollection()][string[]]$Components)
    $plan = @([pscustomobject]@{ Id = 'webview'; Kind = 'webview'; Path = '09_webview2/MicrosoftEdgeWebView2RuntimeInstallerX64.exe' })
    $drivers = @{
        ch341 = '01_ch341_i2c/driver/CH341WDM.INF'
        ocean = '03_ocean_hr4/driver/OOI_OceanHR4.inf'
        ftdi = '04_laserpoint_ftdi/driver'
        serial = '05_serial_adapters'
        ophir = '07_ophir_starlab/StarLab_Setup.exe'
        coherent = '08_coherent/Setup.exe'
    }
    foreach ($id in @('ch341', 'ocean', 'ftdi', 'serial', 'ophir', 'coherent')) {
        if ($id -notin $Components) { continue }
        $kind = if ($id -in @('ophir', 'coherent')) { $id } else { 'inf' }
        $plan += [pscustomobject]@{ Id = $id; Kind = $kind; Path = $drivers[$id] }
    }
    if ('visa' -in $Components -or 'daq' -in $Components) {
        $plan += [pscustomobject]@{ Id = 'ni'; Kind = 'ni'; Path = '06_ni_runtime/feed/Packages.gz' }
    }
    foreach ($step in $plan) { $step.Path = Join-Path $PayloadRoot $step.Path }
    return $plan
}

function Test-WebViewRuntime {
    # https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
    # Check the machine-wide runtime in both registry views. An elevated install
    # must not mistake the administrator's per-user runtime for every user's runtime.
    $clientKey = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry32, [Microsoft.Win32.RegistryView]::Registry64)) {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $view)
        try {
            $key = $base.OpenSubKey($clientKey)
            if ($null -eq $key) { continue }
            try {
                $version = $null
                if ([version]::TryParse([string]$key.GetValue('pv'), [ref]$version) -and $version -gt [version]'0.0.0.0') { return $true }
            } finally { $key.Dispose() }
        } finally { $base.Dispose() }
    }
    return $false
}

function Resolve-SetupExitCode {
    param([int]$Code, [int[]]$SuccessCodes = @(0, 3010, 1641))
    if ($Code -notin $SuccessCodes) { throw "Installer returned error $Code" }
    if ($Code -eq -126300) { return 'resume' }
    if ($Code -in @(3010, 1641, -125071)) { return 'reboot' }
    return 'complete'
}

function Write-SetupJson {
    param([string]$Path, [object]$Value)
    $temporary = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        $utf8 = New-Object Text.UTF8Encoding($true)
        [IO.File]::WriteAllText($temporary, (ConvertTo-Json -InputObject $Value -Depth 5), $utf8)
        if ([IO.File]::Exists($Path)) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) }
        else { [IO.File]::Move($temporary, $Path) }
    } finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}

function Assert-SetupProcessIdle {
    param([string]$StatePath)
    if (-not $StatePath -or -not (Test-Path -LiteralPath $StatePath -PathType Leaf)) { return }
    $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    $active = Get-Process -Id ([int]$state.ProcessId) -ErrorAction SilentlyContinue
    if ($null -ne $active -and [string]$active.StartTime.ToUniversalTime().Ticks -eq [string]$state.StartTimeUtcTicks) {
        throw "An installer is still running (PID $($state.ProcessId), $($state.Name)). Wait for it to finish or restart Windows before retrying."
    }
    # Start time prevents a reused PID from blocking a later installation.
    Remove-Item -LiteralPath $StatePath -Force
}

function Invoke-SetupProcess {
    param(
        [string]$Name, [string]$FilePath, [string]$ArgumentLine,
        [int[]]$SuccessCodes = @(0, 3010, 1641), [int]$TimeoutMinutes = 30
    )
    Write-Host "[Power Test Setup] $Name"
    Write-Host "> $FilePath $ArgumentLine"
    if ($script:SetupDryRun) { return 'complete' }
    # Vendor commands must wait for their own children (InstallShield uses /w).
    # Retain a deadline and progress instead of an unbounded Start-Process -Wait.
    $processLog = Join-Path $script:SetupLogRoot ([guid]::NewGuid().ToString('N'))
    $startOptions = @{
        FilePath = $FilePath
        ArgumentList = $ArgumentLine
        PassThru = $true
        RedirectStandardOutput = "$processLog.stdout.log"
        RedirectStandardError = "$processLog.stderr.log"
    }
    if ($env:OS -eq 'Windows_NT') { $startOptions.NoNewWindow = $true }
    Write-Host "  Process logs: $processLog.*.log"
    Assert-SetupProcessIdle -StatePath $script:SetupActiveProcessPath
    $process = Start-Process @startOptions
    # Cache the handle before waiting. Never cast an unavailable ExitCode to 0.
    $null = $process.Handle
    if ($script:SetupActiveProcessPath -and -not $process.HasExited) {
        Write-SetupJson -Path $script:SetupActiveProcessPath -Value @{
            ProcessId = $process.Id
            StartTimeUtcTicks = [string]$process.StartTime.ToUniversalTime().Ticks
            Name = $Name
        }
    }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $nextProgress = 10
    while (-not $process.WaitForExit(1000)) {
        if ($timer.Elapsed.TotalMinutes -ge $TimeoutMinutes) {
            # Do not kill Windows Installer midway through a transaction.
            throw "$Name (PID $($process.Id)) exceeded $TimeoutMinutes minutes. Installation may still be running; do not start another setup. See $script:SetupLogRoot"
        }
        if ($timer.Elapsed.TotalSeconds -ge $nextProgress) {
            Write-Host "  $Name is still running ($([int]$timer.Elapsed.TotalSeconds) s)..."
            $nextProgress += 10
        }
    }
    if ($script:SetupActiveProcessPath -and (Test-Path -LiteralPath $script:SetupActiveProcessPath)) {
        Remove-Item -LiteralPath $script:SetupActiveProcessPath -Force
    }
    if ($null -eq $process.ExitCode) { throw "$Name exit code is unavailable. Installation cannot be confirmed. Logs: $script:SetupLogRoot" }
    $code = [int]$process.ExitCode
    foreach ($outputFile in @("$processLog.stdout.log", "$processLog.stderr.log")) {
        if (Test-Path -LiteralPath $outputFile) {
            Get-Content -LiteralPath $outputFile | ForEach-Object { Write-Host $_ }
        }
    }
    Write-Host "  $Name exit code: $code"
    try { return Resolve-SetupExitCode -Code $code -SuccessCodes $SuccessCodes }
    catch { throw "$Name failed (exit $code). Logs: $script:SetupLogRoot" }
}

function Assert-SetupPayload {
    param([object[]]$Plan, [string]$PayloadRoot, [string]$MsiPath, [switch]$SkipHashCheck)
    if (-not (Test-Path -LiteralPath $MsiPath -PathType Leaf)) { throw "Application MSI is missing: $MsiPath" }
    if (-not $SkipHashCheck) { Assert-SetupHashes -PayloadRoot $PayloadRoot }
    foreach ($step in $Plan) {
        if (-not (Test-Path -LiteralPath $step.Path)) { throw "Required $($step.Id) payload is missing: $($step.Path)" }
        if ($step.Kind -eq 'inf') {
            $infs = @(Get-SetupInfFiles -Path $step.Path)
            if ($infs.Count -eq 0) { throw "No INF driver found for $($step.Id)" }
        }
    }
    if (@($Plan | Where-Object { $_.Kind -eq 'ni' }).Count -gt 0) {
        $nipm = Join-Path $PayloadRoot '00_ni_package_manager/NIPackageManager24.0.0.exe'
        if (-not (Test-Path -LiteralPath $nipm -PathType Leaf)) { throw "NI Package Manager payload is missing: $nipm" }
    }
}

function Assert-SetupHashes {
    param([string]$PayloadRoot)
    $manifestPath = Join-Path $PayloadRoot 'payload-sha256.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Driver integrity manifest is missing.' }
    $entries = @(Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json)
    if ($entries.Count -eq 0) { throw 'Driver integrity manifest is empty.' }
    $prefix = [IO.Path]::GetFullPath($PayloadRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $seen = @{}
    foreach ($entry in $entries) {
        $path = [IO.Path]::GetFullPath((Join-Path $PayloadRoot $entry.Path))
        if (-not $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or $seen.ContainsKey($path)) {
            throw "Invalid or duplicate driver manifest path: $($entry.Path)"
        }
        $seen[$path] = $true
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Driver file is missing: $($entry.Path)" }
        if ($entry.SHA256 -notmatch '^[0-9A-Fa-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.SHA256) {
            throw "Driver SHA-256 mismatch: $($entry.Path)"
        }
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $PayloadRoot -Recurse -File)) {
        if ($file.FullName -ne $manifestPath -and -not $seen.ContainsKey($file.FullName)) {
            throw "Driver file is not in the integrity manifest: $($file.FullName)"
        }
    }
}

function Assert-NiFeedFiles {
    param([string]$FeedRoot)
    $index = Join-Path $FeedRoot 'Packages.gz'
    $inputStream = [IO.File]::OpenRead($index)
    try {
        $gzip = New-Object IO.Compression.GZipStream($inputStream, [IO.Compression.CompressionMode]::Decompress)
        $reader = New-Object IO.StreamReader($gzip)
        try { $text = $reader.ReadToEnd() } finally { $reader.Dispose() }
    } finally { $inputStream.Dispose() }
    foreach ($package in @('ni-visa-runtime', 'ni-daqmx-runtime-core')) {
        if ($text -notmatch "(?m)^Package: $([regex]::Escape($package))\r?$") { throw "Offline NI feed does not contain $package" }
    }
    $files = [regex]::Matches($text, '(?m)^Filename: (.+)\r?$')
    if ($files.Count -eq 0) { throw 'Offline NI feed contains no package file references.' }
    $prefix = [IO.Path]::GetFullPath($FeedRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    foreach ($file in $files) {
        $relative = $file.Groups[1].Value.Trim()
        $path = [IO.Path]::GetFullPath((Join-Path $FeedRoot $relative))
        if (-not $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Offline NI package is missing or outside the feed: $relative"
        }
    }
}

function Get-SetupInfFiles {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path -PathType Leaf) { return @(Get-Item -LiteralPath $Path) }
    return @(Get-ChildItem -LiteralPath $Path -Recurse -File -Filter '*.inf' | Sort-Object FullName)
}

function Get-NipkgPath {
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $root) { continue }
        $candidate = Join-Path $root 'National Instruments/NI Package Manager/nipkg.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

function Test-NipkgOfflineSupport {
    param([string]$Path)
    # This installation flow is pinned to the bundled NIPM 24.0 or newer. Older
    # NIPM must not silently ignore the option that disables online discovery.
    if (-not $Path) { return $false }
    $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
    return $version.FileMajorPart -ge 24
}

function Invoke-NiRuntimeSetup {
    param([string]$PayloadRoot, [string[]]$Components)
    $nipkg = Get-NipkgPath
    $requiresReboot = $false
    if (-not $nipkg -or -not (Test-NipkgOfflineSupport -Path $nipkg)) {
        $result = Invoke-SetupProcess -Name 'NI Package Manager' `
            -FilePath (Join-Path $PayloadRoot '00_ni_package_manager/NIPackageManager24.0.0.exe') `
            -ArgumentLine '--quiet --accept-eulas --prevent-reboot' `
            -SuccessCodes @(0, 3010, -125071, -126300)
        if ($result -eq 'resume') { return 'resume' }
        $requiresReboot = $result -eq 'reboot'
        $nipkg = Get-NipkgPath
        if ($script:SetupDryRun) { $nipkg = 'nipkg.exe' }
        if (-not $nipkg -or (-not $script:SetupDryRun -and -not (Test-NipkgOfflineSupport -Path $nipkg))) {
            if ($requiresReboot) { return 'resume' }
            throw 'NI Package Manager 24.0 or newer is not available after installation.'
        }
    }
    # https://www.ni.com/docs/en-US/bundle/package-manager/page/cli-package-manager.html
    # A private NI configuration keeps the customer's feeds/settings intact.
    # Disable automatic ni.com discovery so missing offline dependencies fail.
    $feed = Join-Path $PayloadRoot '06_ni_runtime/feed'
    $feedName = 'arp-offline-runtime'
    $configPath = Join-Path (Split-Path -Parent $PayloadRoot) 'ni-offline.ini'
    if (-not $script:SetupDryRun) { Set-Content -LiteralPath $configPath -Value '' -Encoding ASCII }
    $configOption = "--config=$configPath"
    $null = Invoke-SetupProcess -Name 'Configure offline NI installation' -FilePath $nipkg `
        -ArgumentLine (Format-ArgumentList @('config-set', $configOption, 'nipkg.enable-NI-connectivity', 'false')) -SuccessCodes @(0)
    $null = Invoke-SetupProcess -Name 'Register local NI feed' -FilePath $nipkg `
        -ArgumentLine (Format-ArgumentList @('feed-add', $configOption, "--name=$feedName", $feed)) -SuccessCodes @(0)
    $null = Invoke-SetupProcess -Name 'Read local NI feed' -FilePath $nipkg `
        -ArgumentLine (Format-ArgumentList @('update', $configOption, $feedName)) -SuccessCodes @(0)
    $packages = @()
    if ('visa' -in $Components) { $packages += 'ni-visa-runtime' }
    if ('daq' -in $Components) { $packages += 'ni-daqmx-runtime-core' }
    $result = Invoke-SetupProcess -Name 'NI instrument runtimes' -FilePath $nipkg `
        -ArgumentLine (Format-ArgumentList (@('install', $configOption, '--accept-eulas', '--yes', '--prevent-reboot') + $packages)) `
        -SuccessCodes @(0, 3010, -125071, -126300) -TimeoutMinutes 90
    if ($result -eq 'resume') { return 'resume' }
    if ($result -eq 'reboot' -or $requiresReboot) { return 'reboot' }
    return 'complete'
}

function Test-OphirComRegistration {
    $type = [type]::GetTypeFromProgID('OphirLMMeasurement.CoLMMeasurement')
    return $null -ne $type
}

function Invoke-SetupStep {
    param([object]$Step, [string]$PayloadRoot, [string[]]$Components)
    switch ($Step.Kind) {
        'webview' {
            if (-not $script:SetupDryRun -and (Test-WebViewRuntime)) {
                Write-Host '[Power Test Setup] WebView2 already installed; skipping.'
                return 'complete'
            }
            $result = Invoke-SetupProcess -Name 'Microsoft WebView2' -FilePath $Step.Path -ArgumentLine '/silent /install'
            if (-not $script:SetupDryRun -and -not (Test-WebViewRuntime)) { throw 'WebView2 installation did not produce a usable machine-wide runtime.' }
            return $result
        }
        'inf' {
            $result = 'complete'
            foreach ($inf in @(Get-SetupInfFiles -Path $Step.Path)) {
                $itemResult = Invoke-SetupProcess -Name "$($Step.Id): $($inf.Name)" `
                    -FilePath "$env:SystemRoot/System32/pnputil.exe" `
                    -ArgumentLine (Format-ArgumentList @('/add-driver', $inf.FullName, '/install')) `
                    -SuccessCodes @(0, 259, 3010, 1641) # 259: staged/no matching device, or a newer driver is already used.
                if ($itemResult -eq 'reboot') { $result = 'reboot' }
            }
            return $result
        }
        'ophir' {
            # InstallShield parses /v itself. Do not run this raw command through
            # generic argv quoting: /v must be adjacent to its quoted MSI options.
            $result = Invoke-SetupProcess -Name 'Ophir StarLab' -FilePath $Step.Path `
                -ArgumentLine '/s /w /v"/qn /norestart REBOOT=ReallySuppress"'
            if (-not $script:SetupDryRun -and -not (Test-OphirComRegistration)) {
                if ($result -eq 'reboot') { return 'resume' }
                throw 'Ophir installer completed but the 64-bit COM interface is not registered.'
            }
            return $result
        }
        'coherent' {
            $vendorLog = Join-Path $script:SetupLogRoot 'coherent.log'
            return Invoke-SetupProcess -Name 'Coherent Meter Connection' -FilePath $Step.Path `
                -ArgumentLine (Format-ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/RESTARTEXITCODE=3010', '/SP-', "/LOG=$vendorLog"))
        }
        'ni' { return Invoke-NiRuntimeSetup -PayloadRoot $PayloadRoot -Components $Components }
        default { throw "Unsupported setup step: $($Step.Kind)" }
    }
}

function Get-SetupResumeParameters {
    param([string]$ScriptPath, [string]$CommonFilesRoot, [int]$ResumeAttempts)
    if ($ResumeAttempts -ge 2) { throw 'A prerequisite still requires restart after two resume attempts. Setup is incomplete; see the logs.' }
    $cacheRoot = [IO.Path]::GetFullPath((Join-Path $CommonFilesRoot 'ARP/Setup')) + [IO.Path]::DirectorySeparatorChar
    $fullScriptPath = [IO.Path]::GetFullPath($ScriptPath)
    if (-not $fullScriptPath.StartsWith($cacheRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Automatic resume requires the protected ARP setup cache.'
    }
    return '-NoProfile -ExecutionPolicy Bypass -File ' + (Format-ArgumentList @($fullScriptPath)) + ' -Resume'
}

function Remove-SetupContinuationTask {
    $task = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -eq 'Power Test Setup Resume' -and $_.TaskPath -eq '\' })
    if ($task.Count -gt 0) { Unregister-ScheduledTask -TaskName 'Power Test Setup Resume' -TaskPath '\' -Confirm:$false -ErrorAction Stop }
}

function Read-SetupResumeState {
    param([string]$StatePath, [switch]$DryRun)
    # Remove the one-shot task before reading state: corrupt/missing JSON must not
    # cause the elevated installer to run again at every sign-in.
    if (-not $DryRun) { Remove-SetupContinuationTask }
    $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    foreach ($property in @('InstallDirectory', 'Components', 'ResumeAttempts', 'CompletedSteps')) {
        if ($null -eq $state -or $property -notin $state.PSObject.Properties.Name) { throw "Invalid resume state: missing $property" }
    }
    $attempts = 0
    if (-not [int]::TryParse([string]$state.ResumeAttempts, [ref]$attempts) -or $attempts -lt 0 -or $attempts -gt 2) {
        throw 'Invalid resume attempt count.'
    }
    return $state
}

function Assert-SetupApplicationClosed {
    param([string]$InstallDirectory)
    $knownDirectories = @($InstallDirectory)
    $registered = Get-ItemProperty -Path 'HKLM:\Software\ARP\Power Test' -Name InstallDir -ErrorAction SilentlyContinue
    if ($null -ne $registered) { $knownDirectories += [string]$registered.InstallDir }
    $candidates = @(Get-Process -Name @('tauri-ui', 'Power Test', 'arp-python', 'arp-python-x86_64-pc-windows-msvc') -ErrorAction SilentlyContinue)
    foreach ($candidate in $candidates) {
        $isPowerTest = $candidate.ProcessName -ne 'tauri-ui'
        if (-not $isPowerTest) {
            $path = $candidate.Path
            foreach ($directory in $knownDirectories) {
                $prefix = [IO.Path]::GetFullPath($directory).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
                if ($path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { $isPowerTest = $true }
            }
            if (-not $isPowerTest) { $isPowerTest = [Diagnostics.FileVersionInfo]::GetVersionInfo($path).ProductName -eq 'Power Test' }
        }
        if ($isPowerTest) {
            throw "Power Test is running (PID $($candidate.Id)). Safely finish the test and close Power Test before installing; setup will not terminate it."
        }
    }
}

function Set-SetupInstallLocation {
    param([string]$InstallDirectory)
    $key = 'HKLM:\Software\ARP\Power Test'
    $null = New-Item -Path $key -Force
    $null = New-ItemProperty -Path $key -Name 'InstallDir' -Value $InstallDirectory -PropertyType String -Force
}

function Start-SetupMsiService {
    if ($script:SetupDryRun) { return }
    $service = Get-Service -Name 'msiserver' -ErrorAction Stop
    if ($service.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Running) {
        Start-Service -Name 'msiserver' -ErrorAction Stop
        $service.WaitForStatus([System.ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(30))
    }
}

function Invoke-FullSetupPlan {
    param(
        [object[]]$Plan, [string]$PayloadRoot, [string[]]$Components, [string]$MsiPath, [string]$InstallDirectory,
        [string[]]$CompletedSteps = @(), [scriptblock]$OnStepCompleted
    )
    foreach ($id in $CompletedSteps) {
        if ($id -notin @($Plan | ForEach-Object { $_.Id })) { throw "Invalid completed setup component: $id" }
    }
    $rebootRequired = $false
    foreach ($step in $Plan) {
        if ($step.Id -in $CompletedSteps) {
            Write-Host "[Power Test Setup] $($step.Id) completed before reboot; skipping."
            continue
        }
        $result = Invoke-SetupStep -Step $step -PayloadRoot $PayloadRoot -Components $Components
        if ($result -notin @('complete', 'reboot', 'resume')) { throw "Invalid result from $($step.Id): $result" }
        if ($result -eq 'resume') { return 'resume' }
        if ($result -eq 'reboot') { $rebootRequired = $true }
        if ($null -ne $OnStepCompleted) { & $OnStepCompleted $step.Id }
    }
    Start-SetupMsiService
    $msiLog = Join-Path $script:SetupLogRoot 'application-msi.log'
    # MSI properties use NAME="value". Preserve the working installer quoting,
    # including paths with spaces, instead of quoting the entire NAME=value token.
    $msiArguments = @('/i', (Format-ArgumentList @($MsiPath)),
        ('INSTALLDIR=' + (Format-ArgumentList @($InstallDirectory))),
        '/qn', '/norestart', '/L*v', (Format-ArgumentList @($msiLog))) -join ' '
    $result = Invoke-SetupProcess -Name 'Power Test application' -FilePath "$env:SystemRoot/System32/msiexec.exe" `
        -ArgumentLine $msiArguments
    if ($result -notin @('complete', 'reboot')) { throw "Invalid application setup result: $result" }
    if ($result -eq 'reboot' -or $rebootRequired) { return 'reboot' }
    return 'complete'
}
