# Tauri / Python Bridge Contract

## Transport

Tauri launches one persistent local Python process. Requests and responses are UTF-8 JSON objects separated by newlines. Standard output is reserved for protocol responses; diagnostics use standard error. The request version remains `1`.

```json
{"v":1,"id":"tauri-1","method":"app.snapshot","params":{"view":"automatic"}}
```

The Rust layer exposes `bridge_snapshot` and a generic `bridge_request(method, params)` command. Production packaging can replace the Python interpreter with a sidecar without changing this contract.

## Backend modes

- `active`: PySide6 and the project runtime loaded successfully. A hidden compatibility window owns the existing device threads, automatic controller, Excel export, plots, and PD acquisition.
- `read_only`: the active runtime was unavailable. No hardware is probed or controlled and all write controls remain disabled.

The bridge being connected never means a physical device is connected. Every device reports its own state.

## Methods

- Application: `system.ping`, `app.snapshot`, `app.configure`, `app.stopAll`, `app.shutdown`
- Discovery: `device.refresh`
- Power supply: `powerSupply.connect`, `powerSupply.disconnect`, `powerSupply.setCurrent`, `powerSupply.setVoltage`, `powerSupply.setOutput`, `powerSupply.read`
- Power meter: `powerMeter.start`, `powerMeter.stop`, `powerMeter.setRelativeZero`
- Spectrometer: `spectrometer.start`, `spectrometer.stop`, `spectrometer.saveCsv`
- Automatic test: `automatic.start`, `automatic.retry`, `automatic.end`, `automatic.reset`
- PD: `pd.refresh`, `pd.configure`, `pd.start`, `pd.stop`
- Charts: `charts.reset`

Every successful mutation returns a fresh full snapshot. The routine snapshot stream passes the active `view` (`automatic`, `manual`, or `pd`) so large chart and PD point arrays are only serialized for their consumer. Live views refresh at most twice per second, and streaming pauses while the WebView is hidden. The acquisition threads continue at their configured hardware sampling rates.

After the first full response, the frontend returns `seriesRevisions` as `params.since`. Unchanged power, stable-point, spectrum, and PD arrays are omitted and the frontend retains the previous arrays. Power and PD additionally send their last displayed `elapsedS` values in `params.cursors`; when the bounded history is continuous, the response uses `seriesPatches` to append only newer points and supplies `startX` so the frontend can discard points that left the display window. A missing cursor, reset curve, page change, or explicit refresh falls back to a complete array.

On Windows, the Rust layer sends the live stream through WebView2's native `PostWebMessageAsJson` API. Each message carries a random subscription ID, and the frontend removes its matching `window.chrome.webview` listener before unsubscribing. This avoids Tauri Channel's per-message dynamic-script delivery path during long acquisitions. Other platforms retain a Tauri Channel fallback. Spectrum display data remains extrema-preserving and bounded to 160 points.

The WebView2 renderer starts with a 64 MB V8 old-generation limit and exposes its garbage collector. While the snapshot hook is mounted, the frontend requests a collection every 30 seconds so transient React and IPC objects do not wait for WebView2's much larger default pressure threshold. This affects only the UI JavaScript heap, not the Python device process or the hardware sampling rate.

The Tauri development command also starts Vite with `NODE_ENV=production`. Rust and frontend hot reload remain available, but the embedded WebView runs React's production runtime and does not perform development-only duplicate renders during continuous acquisition.

The stream fingerprints business state and series revisions after removing transport-only fields such as `capturedAt`, full measurement arrays, and append patches. If that fingerprint has not changed, no channel message is emitted. An idle application therefore performs lightweight backend checks without continuously allocating WebView2 IPC messages.

```json
{
  "v": 1,
  "id": "tauri-2",
  "method": "app.snapshot",
  "params": {
    "view": "automatic",
    "since": {"power": 18, "stable": 2, "spectrum": 7, "pd": 0},
    "cursors": {"power": 12.5}
  }
}
```

Routine snapshots only expose the current status message. The potentially unbounded operator log is intentionally not copied into every WebView response.

## Safety invariants

- Python is the single owner of physical device commands and automatic-test state.
- UI actions call existing controller methods; JavaScript never generates current sequences or decides that shutdown succeeded.
- Automatic start remains gated by SN, station, output directory, power supply, selected measurement resources, and validated timing/current settings.
- TDK output enable still requires a programmed and measured zero-current state.
- End, emergency stop, disconnect, bridge shutdown, and process disposal use the existing zero-current/output-off boundary.
- An unconfirmed TDK output-off state is surfaced as `safety.outputShutdownUnconfirmed` and must never be presented as a successful terminal result.
- Test points remain in memory until the existing Excel export workflow saves them.
