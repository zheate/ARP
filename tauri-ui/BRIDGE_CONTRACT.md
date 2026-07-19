# Tauri / Python Bridge Contract

## Transport

Tauri launches one persistent local Python process. Requests and responses are UTF-8 JSON objects separated by newlines. Standard output is reserved for protocol responses; diagnostics use standard error. The request version remains `1`.

```json
{"v":1,"id":"tauri-1","method":"app.snapshot","params":{}}
```

The Rust layer exposes `bridge_snapshot` and a generic `bridge_request(method, params)` command. Production packaging can replace the Python interpreter with a sidecar without changing this contract.

## Backend modes

- `active`: PySide6 and the project runtime loaded successfully. A hidden compatibility window owns the existing device threads, automatic controller, archive, export, plots, and PD acquisition.
- `read_only`: the active runtime was unavailable. No hardware is probed or controlled and all write controls remain disabled.

The bridge being connected never means a physical device is connected. Every device reports its own state.

## Methods

- Application: `system.ping`, `app.snapshot`, `app.configure`, `app.stopAll`, `app.shutdown`
- Discovery: `device.refresh`
- Power supply: `powerSupply.connect`, `powerSupply.disconnect`, `powerSupply.setCurrent`, `powerSupply.setVoltage`, `powerSupply.setOutput`, `powerSupply.read`
- Power meter: `powerMeter.start`, `powerMeter.stop`, `powerMeter.setRelativeZero`
- Spectrometer: `spectrometer.start`, `spectrometer.stop`, `spectrometer.saveCsv`
- Automatic test: `automatic.start`, `automatic.retry`, `automatic.end`, `automatic.reset`
- Records: `records.exportCurrent`, `records.setFilters`, `records.select`, `records.resume`, `records.reexport`, `records.compare`
- PD: `pd.refresh`, `pd.configure`, `pd.start`, `pd.stop`
- Charts: `charts.reset`

Every successful mutation returns a fresh full snapshot. The frontend polls `app.snapshot` once per second for live readings and state transitions.

## Safety invariants

- Python is the single owner of physical device commands and automatic-test state.
- UI actions call existing controller methods; JavaScript never generates current sequences or decides that shutdown succeeded.
- Automatic start remains gated by SN, station, output directory, power supply, selected measurement resources, and validated timing/current settings.
- TDK output enable still requires a programmed and measured zero-current state.
- End, emergency stop, disconnect, bridge shutdown, and process disposal use the existing zero-current/output-off boundary.
- An unconfirmed TDK output-off state is surfaced as `safety.outputShutdownUnconfirmed` and must never be presented as a successful terminal result.
- SQLite/session archive remains the source of truth. Excel is an export artifact.
