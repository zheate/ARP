# Tauri / Python Bridge Contract

## Transport

Tauri launches one persistent local Python process. Requests and responses are UTF-8 JSON objects separated by newlines. Standard output is reserved for protocol responses; diagnostics use standard error. The request version is `2`.

```json
{"v":2,"id":"tauri-1","method":"app.snapshot","params":{"view":"automatic"}}
```

The Rust layer exposes `bridge_snapshot` and a generic `bridge_request(method, params)` command. Production packaging can replace the Python interpreter with a sidecar without changing this contract.

## Backend modes

- `active`: the headless project runtime loaded successfully. A toolkit-free backend owns device threads, automatic control, Excel/PDF export, plots, and PD acquisition.
- `read_only`: the active runtime was unavailable. No hardware is probed or controlled and all write controls remain disabled.

The bridge being connected never means a physical device is connected. Every device reports its own state.

## Methods

- Application: `system.ping`, `app.snapshot`, `app.configure`, `app.stopAll`, `app.shutdown`
- Discovery: `device.refresh`
- Power supply: `powerSupply.connect`, `powerSupply.disconnect`, `powerSupply.setCurrent`, `powerSupply.setVoltage`, `powerSupply.setOutput`, `powerSupply.read`
- Power meter: `powerMeter.start`, `powerMeter.stop`, `powerMeter.setRelativeZero`
- Spectrometer: `spectrometer.start`, `spectrometer.stop`, `spectrometer.saveCsv`
- Automatic test: `automatic.start`, `automatic.pause`, `automatic.retry`, `automatic.end`, `automatic.reset`
- Shipping report: `shippingReport.inspect`, `shippingReport.batchInspect`, `shippingReport.batchGenerateItem`, `shippingReport.preview`, `shippingReport.generate`
- PD: `pd.refresh`, `pd.configure`, `pd.start`, `pd.stop`
- Charts: `charts.reset`

Every successful mutation returns a fresh full snapshot. The routine snapshot stream passes the active `view` (`automatic`, `manual`, `pd`, or `shipping`) so large chart and PD point arrays are only serialized for their consumer. Automatic and manual live views refresh at most five times per second, while PD and shipping refresh at most twice per second. Streaming pauses while the WebView is hidden. The acquisition threads continue at their configured hardware sampling rates.

`automatic.start` automatically resolves the newest completed record for the current SN from the output directory and returns it in `automaticTest.reference`. Missing or unreadable history remains non-blocking. The UI only exposes script selection; script parameters remain part of the normal station configuration and are saved through `app.configure`.

`testScripts` contains the built-in script choices and may include `powerSupplyKinds` to restrict availability. `aging` is CH341-only: its parameters are fixed to 2 A through 20 A in 2 A increments, with a one-second wait requested between steps (the CH341 command guard keeps physical commands at least 1.1 seconds apart). It starts the configured power meter, performs no power or spectrum stability decision at 20 A, and starts a fixed 7200-second hold immediately. During that hold, `measurements.agingPower` records the latest fresh power reading every five active minutes at 5, 10, through 120 minutes. The automatic UI shows this elapsed-minute power curve only while `aging` is selected and restores the normal power/efficiency curve for every other script. While `aging` is selected, every power-controller menu keeps `TDK RS232` visible but disabled and explains that the script is unavailable for it. TDK configurations selecting `aging` are rejected by Python even if the UI is bypassed.

After the first full response, the frontend returns `seriesRevisions` as `params.since`. Unchanged live-power, stable-point, aging-power, spectrum, and PD arrays are omitted and the frontend retains the previous arrays. Power and PD additionally send their last displayed `elapsedS` values in `params.cursors`; when the bounded history is continuous, the response uses `seriesPatches` to append only newer points and supplies `startX` so the frontend can discard points that left the display window. A missing cursor, reset curve, page change, or explicit refresh falls back to a complete array.

On Windows, the Rust layer sends the live stream through WebView2's native `PostWebMessageAsJson` API. Each message carries a random subscription ID, and the frontend removes its matching `window.chrome.webview` listener before unsubscribing. This avoids Tauri Channel's per-message dynamic-script delivery path during long acquisitions. Other platforms retain a Tauri Channel fallback. Spectrum samples use equal-length `measurements.spectrum.wavelengthNm` and `measurements.spectrum.intensity` arrays; legacy per-point objects are rejected. The wavelength axis is hardware-defined, so it is sent once per `gridRevision` and omitted on later frames; the frontend merges the axis from its cached copy only when the revision matches, and falls back to an empty axis otherwise. Every valid native spectrometer sample in the selected wavelength window is transported and drawn, and the live spectrum is never downsampled. Raw acquisition and export data remain Python-owned.

The WebView2 renderer uses V8's normal heap sizing and garbage-collection policy. The frontend does not expose or periodically force collection; transient snapshot allocations are allowed to return to their natural low-water mark without introducing scheduled full-GC pauses.

The Tauri development command also starts Vite with `NODE_ENV=production`. Rust and frontend hot reload remain available, but the embedded WebView runs React's production runtime and does not perform development-only duplicate renders during continuous acquisition.

The stream fingerprints business state and series revisions after removing transport-only fields such as `capturedAt`, full measurement arrays, and append patches. If that fingerprint has not changed, no channel message is emitted. An idle application therefore performs lightweight backend checks without continuously allocating WebView2 IPC messages.

```json
{
  "v": 2,
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
Backend notices are retained as a bounded queue of the most recent eight entries. The frontend drops dismissed-notice keys after their matching notice leaves that queue.

## Safety invariants

- Python is the single owner of physical device commands and automatic-test state.
- UI actions call existing controller methods; JavaScript never generates current sequences or decides that shutdown succeeded.
- Ordinary automatic tests remain gated by SN, station, output directory, power supply, selected measurement resources, and validated timing/current settings. The CH341-only aging script requires only its power supply because it does not collect or judge measurement points.
- TDK output enable still requires a programmed and measured zero-current state.
- End, emergency stop, disconnect, bridge shutdown, and process disposal use the existing zero-current/output-off boundary.
- An unconfirmed TDK output-off state is surfaced as `safety.outputShutdownUnconfirmed` and must never be presented as a successful terminal result.
- Test points remain in memory until the existing Excel export workflow saves them.
