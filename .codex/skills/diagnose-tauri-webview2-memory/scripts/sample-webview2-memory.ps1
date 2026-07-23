param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$AppName = "tauri-ui",

    [Parameter()]
    [ValidateRange(1, 10000)]
    [int]$SampleCount = 21,

    [Parameter()]
    [ValidateRange(1, 3600)]
    [int]$IntervalSeconds = 15
)

$app = Get-CimInstance Win32_Process -Filter "Name='$AppName.exe'" |
    Sort-Object CreationDate -Descending |
    Select-Object -First 1

if (-not $app) {
    throw "No $AppName.exe process is running."
}

$webviews = Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'"
$browser = $webviews |
    Where-Object { $_.ParentProcessId -eq $app.ProcessId } |
    Select-Object -First 1

if (-not $browser) {
    throw "No WebView2 browser process is attached to $AppName PID $($app.ProcessId)."
}

$renderer = $webviews |
    Where-Object {
        $_.ParentProcessId -eq $browser.ProcessId -and
        $_.CommandLine -match '(?:^|\s)--type=renderer(?:\s|$)'
    } |
    Select-Object -First 1

if (-not $renderer) {
    throw "No WebView2 renderer process is attached to browser PID $($browser.ProcessId)."
}

$mode = if ($app.ExecutablePath -match '[\\/]target[\\/]debug[\\/]') {
    "debug"
} elseif ($app.ExecutablePath -match '[\\/]target[\\/]release[\\/]') {
    "release"
} else {
    "unknown"
}

[pscustomobject]@{
    RecordType = "metadata"
    AppPid = $app.ProcessId
    RendererPid = $renderer.ProcessId
    Mode = $mode
    ExecutablePath = $app.ExecutablePath
    Heap64 = $renderer.CommandLine -match 'max-old-space-size=64'
    ExposeGc = $renderer.CommandLine -match 'expose-gc'
    RemoteDebug = $renderer.CommandLine -match 'remote-debugging-port'
}

for ($index = 0; $index -lt $SampleCount; $index += 1) {
    $process = Get-Process -Id $renderer.ProcessId -ErrorAction Stop
    [pscustomobject]@{
        RecordType = "sample"
        Timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
        Sample = $index + 1
        PrivateMB = [math]::Round($process.PrivateMemorySize64 / 1MB, 1)
        WorkingSetMB = [math]::Round($process.WorkingSet64 / 1MB, 1)
    }
    if ($index + 1 -lt $SampleCount) {
        Start-Sleep -Seconds $IntervalSeconds
    }
}
