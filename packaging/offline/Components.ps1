function Test-RuntimeProbe {
    param([object]$Probe)
    switch ($Probe.kind) {
        'file-version' {
            $path = Get-SystemPath $Probe.path
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
            $info = (Get-Item -LiteralPath $path).VersionInfo
            $version = '{0}.{1}.{2}.{3}' -f $info.FileMajorPart, $info.FileMinorPart, $info.FileBuildPart, $info.FilePrivatePart
            return Test-VersionRange $version $Probe
        }
        'registry-version' {
            $view = if ($Probe.view -eq 64) { [Microsoft.Win32.RegistryView]::Registry64 } else { [Microsoft.Win32.RegistryView]::Registry32 }
            $hive = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $view)
            $key = $null
            try {
                $key = $hive.OpenSubKey([string]$Probe.key)
                if (-not $key) { return $false }
                return Test-VersionRange ([string]$key.GetValue([string]$Probe.value, '')) $Probe
            } finally { if ($key) { $key.Dispose() }; $hive.Dispose() }
        }
        'com' {
            if ($Probe.progId -ne 'OphirLMMeasurement.CoLMMeasurement') { throw 'Unexpected COM probe' }
            $instance = $null
            try {
                $instance = New-Object -ComObject $Probe.progId -ErrorAction Stop
                return $true
            } catch { return $false }
            finally { if ($instance) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($instance) } }
        }
        default { throw "Unknown probe: $($Probe.kind)" }
    }
}

function Test-WebViewRuntime {
    param([object]$Component)
    # Machine-wide only: the elevated administrator's HKCU is not the operator's.
    $probe = [pscustomobject]@{
        kind = 'registry-version'; view = 32
        key = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
        value = 'pv'; minimumVersion = $Component.minimumVersion
    }
    return Test-RuntimeProbe $probe
}

function Test-DriverStaged {
    param([string]$InfPath)
    # Structured DISM data avoids parsing localized pnputil console messages.
    $expected = (Get-FileHash -LiteralPath $InfPath -Algorithm SHA256).Hash
    $leaf = Split-Path $InfPath -Leaf
    foreach ($driver in @(Get-WindowsDriver -Online -All -ErrorAction Stop)) {
        if ((Split-Path $driver.OriginalFileName -Leaf) -ieq $leaf -and (Test-Path -LiteralPath $driver.OriginalFileName -PathType Leaf)) {
            if ((Get-FileHash -LiteralPath $driver.OriginalFileName -Algorithm SHA256).Hash -eq $expected) { return $true }
        }
    }
    return $false
}

function Install-PnpComponent {
    param([object]$Component)
    foreach ($name in $Component.inf) {
        $inf = Get-PayloadPath $script:Deploy.PayloadRoot $name
        # Exactly one INF per process gives attributable results. 259 is not a
        # global success code: only accept it after verifying the staged INF.
        [void](Invoke-DeployProcess $Component.id (Join-Path $env:SystemRoot 'System32\pnputil.exe') @('/add-driver', $inf, '/install') -SuccessCodes @(0, 259) -RebootCodes @(3010))
        if (-not $script:Deploy.DryRun -and -not (Test-DriverStaged $inf)) { throw "$($Component.id): driver was not found in Driver Store: $name" }
        Write-DeployEvent $Component.id $(if ($script:Deploy.DryRun) { 'planned' } else { 'staged' }) $name
    }
}

function Install-RuntimeComponent {
    param([object]$Component)
    $path = Get-PayloadPath $script:Deploy.PayloadRoot $Component.path
    if ($Component.kind -eq 'webview2') {
        $installed = (-not $script:Deploy.DryRun) -and (Test-WebViewRuntime $Component)
        $exe = $path
        $arguments = @('/silent', '/install')
    } else {
        $installed = $false
        if (-not $script:Deploy.DryRun) {
            $installed = @($Component.detect | Where-Object { -not (Test-RuntimeProbe $_) }).Count -eq 0
        }
        $arguments = @($Component.arguments | ForEach-Object {
            $_.Replace('{PayloadRoot}', $script:Deploy.PayloadRoot).Replace('{LogRoot}', $script:Deploy.LogRoot)
        })
        $exe = $path
        if ($Component.launcher -eq 'powershell64') {
            $exe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $path) + $arguments
        }
    }
    if ($installed) { Write-DeployEvent $Component.id 'already-satisfied' $null; return }
    [void](Invoke-DeployProcess $Component.id $exe $arguments -SuccessCodes $Component.successExitCodes -RebootCodes $Component.rebootExitCodes -TimeoutSeconds $Component.timeoutSeconds -WorkingDirectory (Split-Path $path -Parent))
    if ($script:Deploy.DryRun) { return }
    $valid = if ($Component.kind -eq 'webview2') {
        Test-WebViewRuntime $Component
    } else { @($Component.detect | Where-Object { -not (Test-RuntimeProbe $_) }).Count -eq 0 }
    if (-not $valid) { throw "$($Component.id): installer exited but runtime postcondition FAILED. A reboot flag does not prove installation succeeded." }
    Write-DeployEvent $Component.id 'verified' $null
}
