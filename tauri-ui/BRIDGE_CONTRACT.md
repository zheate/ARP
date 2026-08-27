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

Requests are dispatched by ownership. `app.stopAll`, `app.shutdown`, and `automatic.end` use a dedicated safety executor. Report inspection, preview, and generation use a report executor. Ordinary commands remain serialized separately, so a report or device operation cannot keep an emergency request in the normal command queue.

## Methods

- Application: `system.ping`, `app.snapshot`, `app.configure`, `app.stopAll`, `app.shutdown`
- Discovery: `device.refresh`
- TDK discovery removes serial resources already identified as power meters, then keeps only ports that acknowledge `ADR 6` and return a valid numeric `MV?` readback. The probe uses a short timeout, never sends remote/output/setpoint commands, and always closes its temporary VISA session.
- Power supply: `powerSupply.connect`, `powerSupply.disconnect`, `powerSupply.setCurrent`, `powerSupply.setVoltage`, `powerSupply.setOutput`, `powerSupply.read`
- Power meter: `powerMeter.start`, `powerMeter.stop`, `powerMeter.setRelativeZero`
- Spectrometer: `spectrometer.start`, `spectrometer.stop`, `spectrometer.saveCsv`
- Automatic test: `automatic.start`, `automatic.pause`, `automatic.resume`, `automatic.retryPoint`, `automatic.recoverPowerMeter`, `automatic.recoverSpectrometer`, `automatic.adjustSpectrum`, `automatic.retryPersist`, `automatic.end`, `automatic.nextDevice`, `automatic.retryExport`
- Shipping report: `shippingReport.inspect`, `shippingReport.batchInspect`, `shippingReport.batchGenerateItem`, `shippingReport.preview`, `shippingReport.generate`
- PD: `pd.refresh`, `pd.configure`, `pd.start`, `pd.stop`
- Charts: `charts.reset`

Every successful mutation returns a fresh full snapshot. The routine snapshot stream passes the active `view` (`automatic`, `manual`, `pd`, or `shipping`) so large chart and PD point arrays are only serialized for their consumer. Automatic and manual live views refresh at most five times per second, while PD and shipping refresh at most twice per second. Streaming pauses while the WebView is hidden. The acquisition threads continue at their configured hardware sampling rates.

`automatic.start` automatically resolves the newest completed record for the current SN from the output directory and returns it in `automaticTest.reference`. Missing or unreadable history remains non-blocking. The UI only exposes script selection; script parameters remain part of the normal station configuration and are saved through `app.configure`.

`testScripts` contains the built-in script choices and may include `powerSupplyKinds` to restrict availability. `aging` is CH341-only: its parameters are fixed to 2 A through 20 A in 2 A increments, with a one-second wait requested between steps (the CH341 command guard keeps physical commands at least 1.1 seconds apart). It starts the configured power meter, performs no power or spectrum stability decision at 20 A, and starts a fixed 7200-second hold immediately. During that hold, `measurements.agingPower` records the latest fresh power reading every five active minutes at 5, 10, through 120 minutes. The automatic UI shows this elapsed-minute power curve only while `aging` is selected and restores the normal power/efficiency curve for every other script. While `aging` is selected, every power-controller menu keeps `TDK RS232` visible but disabled and explains that the script is unavailable for it. TDK configurations selecting `aging` are rejected by Python even if the UI is bypassed.

After the first full response, the frontend returns `seriesRevisions` as `params.since`. Unchanged live-power, stable-point, aging-power, spectrum, and PD arrays are omitted and the frontend retains the previous arrays. Power and PD additionally send their last displayed `elapsedS` values in `params.cursors`; when the bounded history is continuous, the response uses `seriesPatches` to append only newer points and supplies `startX` so the frontend can discard points that left the display window. A missing cursor, reset curve, page change, or explicit refresh falls back to a complete array.

On Windows, the Rust layer sends the live stream through WebView2's native `PostWebMessageAsJson` API. Each send waits for the WebView2 callback; dispatch failures, post failures, and callback timeouts terminate the stream and are written to the native diagnostic log instead of being treated as successful delivery. Each message carries a random subscription ID, and the frontend removes its matching `window.chrome.webview` listener before unsubscribing. This avoids Tauri Channel's per-message dynamic-script delivery path during long acquisitions. Other platforms retain a Tauri Channel fallback. Spectrum samples use equal-length `measurements.spectrum.wavelengthNm` and `measurements.spectrum.intensity` arrays; legacy per-point objects are rejected. The wavelength axis is hardware-defined, so it is sent once per `gridRevision` and omitted on later frames; the frontend merges the axis from its cached copy only when the revision matches, and falls back to an empty axis otherwise. Every valid native spectrometer sample in the selected wavelength window is transported and drawn, and the live spectrum is never downsampled. Raw acquisition and export data remain Python-owned.

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
- `automaticTest.readiness` and `automatic.start` call the same backend validation path. It gates SN, output directory, script/controller compatibility, zero-current/output state, the power-meter resource, and validated timing/current settings; the UI only presents the returned blockers. A normal automatic test includes spectrum only when the spectrometer is already connected and ready at start; otherwise it follows the power-only path without starting or waiting for a spectrometer, even if a spectrometer resource was selected earlier. The CH341-only aging script never uses a spectrometer, but still requires its configured power meter because aging power is recorded.
- TDK output enable still requires a programmed and measured zero-current state.
- A global emergency stop broadcasts both station stop events before waiting for either station, then performs station cleanup concurrently. A blocked station must not delay the other station from starting its ramp-down.
- End, emergency stop, disconnect, bridge shutdown, and process disposal use the same zero-current/output-off boundary. Configuration persistence is never a prerequisite for this safety path.
- `safety.controlOwner` and `safety.manualControlsAllowed` are authoritative. While an automatic flow is not `idle` or `completed`, the backend rejects manual power-supply, power-meter, spectrometer, discovery, configuration, and manual-export commands even if the UI is bypassed.
- Paused automatic tests retain controller ownership. Recovery uses only the actions listed by `automaticTest.pauseActions`; it never unlocks the manual hardware page. `pauseKind`, `pauseReason`, `pauseStartedAtUtc`, and `pauseDeadlineAtUtc` define the visible recovery state and safety countdown.
- `safety.shutdown` is the active station's machine-readable result:

```json
{
  "requested": true,
  "confirmed": false,
  "measuredCurrentA": 0.37,
  "outputOffConfirmed": false,
  "reason": "电源通信超时"
}
```

- Automatic terminal outcomes are `succeeded`, `stopped_shutdown_confirmed`, `aborted_shutdown_confirmed`, or `shutdown_unconfirmed`. Only an explicitly confirmed shutdown may use a safe-completion presentation.
- Each accepted test point is first appended to a per-session JSONL journal under the local application-data directory and flushed with `fsync`. The journal retains the complete native spectrum and remains on disk after Excel succeeds.
- `automaticTest.savedPointCount` is derived from points durably committed to JSONL. `exportedPointCount` separately reports points materialized into Excel; the frontend must not infer either value from the current plan index.
- Automatic testing never waits for Excel generation. At a terminal state the current JSONL-backed session is queued for background Excel materialization. Excel failure does not reclassify the test point as unsaved and exposes `canRetryExport`, `exportError`, and the retained `journalPath`.
- Power-meter and spectrometer snapshots include `sampledAt`, `ageMs`, and `quality` (`live`, `stale`, `invalid`, or `saturated`). Only `live` measurements may be presented as current values or used by the realtime metric cards; stopped devices retain a grey last reading briefly, and expired or saturated measurements display no current metric.
- `app.shutdown` stops accepting new Excel exports and waits for every already accepted task before returning `exportsDrained: true`. A timeout returns `exportsDrained: false`; the UI keeps the close flow blocked unless the operator explicitly forces exit.
- `automatic.nextDevice` is accepted only after the current automatic state is `completed` and shutdown is confirmed. It keeps device connections, model, batch, station, script, and measurement settings; clears SN, charts, terminal state, and the active record store; and requires `automatic.start` to create the next `sessionId`.
