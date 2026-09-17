# Read-only preflight. Existing instrument applications must keep their NI stack.
function Get-ExistingNiSoftware {
    $items = @()
    foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32)) {
        $hive = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $view)
        $uninstall = $null
        try {
            $uninstall = $hive.OpenSubKey('SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall')
            if ($uninstall) {
                foreach ($name in $uninstall.GetSubKeyNames()) {
                    $key = $uninstall.OpenSubKey($name)
                    try {
                        if (-not $key) { continue }
                        $display = [string]$key.GetValue('DisplayName', '')
                        # NIPM alone is a deployment tool, not evidence of an
                        # existing DAQ/VISA or LabVIEW application installation.
                        if ($display -match '(?i)NI[- ]DAQmx|NI[- ]VISA|LabVIEW|LabWindows|TestStand|Measurement Studio|SignalExpress') {
                            $items += [pscustomobject]@{ name = $display; version = [string]$key.GetValue('DisplayVersion', ''); view = [string]$view }
                        }
                    } finally { if ($key) { $key.Dispose() } }
                }
            }
        } finally { if ($uninstall) { $uninstall.Dispose() }; $hive.Dispose() }
    }
    # Catch runtime-only deployments with no matching uninstall record.
    foreach ($name in @('nicaiu.dll', 'visa64.dll')) {
        $path = Join-Path $env:SystemRoot "System32\$name"
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $items += [pscustomobject]@{ name = $name; version = (Get-Item -LiteralPath $path).VersionInfo.FileVersion; view = 'native-x64' }
        }
    }
    return $items
}

function Assert-NiPreservation {
    param([object]$Manifest)
    $existing = @(Get-ExistingNiSoftware)
    if (-not $existing.Count) { return $false }
    $runtime = @($Manifest.components | Where-Object { $_.id -eq 'ni-runtime' })
    if ($runtime.Count -ne 1 -or -not @($runtime[0].detect).Count) { throw 'Missing NI runtime preservation probes' }
    if (@($runtime[0].detect | Where-Object { -not (Test-RuntimeProbe $_) }).Count -eq 0) {
        # NIPM is unnecessary when the existing runtime already qualifies.
        return $true
    }
    $summary = ($existing | Select-Object -First 5 | ForEach-Object { "$($_.name) $($_.version)" }) -join '; '
    throw "Existing NI software must be preserved: $summary. This payload has no qualified reuse path for that NI stack. No NI package manager, runtime upgrade or uninstall is allowed; qualify the existing runtime before deployment."
}
