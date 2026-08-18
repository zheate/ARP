# Ophir Resource Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure Ophir display labels are converted to stable resource IDs, always use the Ophir driver, migrate inconsistent saved settings, and persist asynchronous detection results immediately.

**Architecture:** Add one frontend pure function for select-value parsing and reuse the existing backend resource extraction boundary for defensive normalization. `StationRuntime` will normalize resource/driver pairs at load and configure time, while `HeadlessBackend` will persist migrations and successful asynchronous detection as atomic configuration snapshots.

**Tech Stack:** React 19, TypeScript, Node test runner, Python 3, `unittest`, existing `JsonSettings` and worker signal abstractions.

---

## File Structure

- Create `tauri-ui/src/lib/power-meter-resource.ts`: frontend-only stable resource extraction.
- Create `tauri-ui/src/lib/power-meter-resource.test.ts`: parser regression tests.
- Modify `tauri-ui/src/App.tsx`: import the tested parser instead of keeping an untestable local implementation.
- Modify `combined_test/devices.py`: extend the shared backend extractor to recognize Ophir identifiers embedded in display labels.
- Modify `tauri_bridge/headless_backend.py`: synchronize driver selection, migrate loaded configurations, and persist detected selections.
- Modify `tests/test_dual_station_runtime.py`: backend migration, runtime synchronization, and asynchronous persistence regressions.

### Task 1: Frontend Stable Resource Parsing

**Files:**
- Create: `tauri-ui/src/lib/power-meter-resource.test.ts`
- Create: `tauri-ui/src/lib/power-meter-resource.ts`
- Modify: `tauri-ui/src/App.tsx:212-215`

- [ ] **Step 1: Write the failing parser tests**

```ts
import assert from "node:assert/strict"
import test from "node:test"

import { powerMeterResourceValue } from "./power-meter-resource.ts"

test("extracts an Ophir resource embedded in a display label", () => {
  assert.equal(
    powerMeterResourceValue("Ophir USB | OPHIR::USB::3103403 | USB SN 3103403"),
    "OPHIR::USB::3103403",
  )
})

test("normalizes raw Ophir and serial VISA resources", () => {
  assert.equal(powerMeterResourceValue(" ophir::usb::abc123 "), "OPHIR::USB::ABC123")
  assert.equal(powerMeterResourceValue("Coherent | asrl10::instr | SN 42"), "ASRL10::INSTR")
})

test("preserves an unknown resource after trimming whitespace", () => {
  assert.equal(powerMeterResourceValue(" custom-resource "), "custom-resource")
})
```

- [ ] **Step 2: Run the test and verify RED**

Run: `npm test -- src/lib/power-meter-resource.test.ts` from `tauri-ui`.

Expected: FAIL because `power-meter-resource.ts` does not exist.

- [ ] **Step 3: Implement the minimal parser**

```ts
const ophirResourcePattern = /OPHIR::USB::[^|\s]+/i
const visaSerialResourcePattern = /ASRL\d+::INSTR/i

export function powerMeterResourceValue(label: string): string {
  const value = label.trim()
  return value.match(ophirResourcePattern)?.[0].toUpperCase()
    ?? value.match(visaSerialResourcePattern)?.[0].toUpperCase()
    ?? value
}
```

Import this function in `App.tsx` and remove the local implementation.

- [ ] **Step 4: Run frontend tests and verify GREEN**

Run: `npm test` from `tauri-ui`.

Expected: all Node tests PASS.

- [ ] **Step 5: Run the frontend build**

Run: `npm run build` from `tauri-ui`.

Expected: TypeScript and Vite build complete successfully.

- [ ] **Step 6: Commit the frontend parser**

Run: `git add tauri-ui/src/lib/power-meter-resource.ts tauri-ui/src/lib/power-meter-resource.test.ts tauri-ui/src/App.tsx && git commit -m "fix: normalize Ophir resource labels"`.

### Task 2: Backend Resource and Driver Normalization

**Files:**
- Modify: `combined_test/devices.py:230-241`
- Modify: `tauri_bridge/headless_backend.py:276-302`
- Modify: `tauri_bridge/headless_backend.py:762-829`
- Test: `tests/test_dual_station_runtime.py`

- [ ] **Step 1: Write failing migration and configure tests**

```python
def test_loaded_ophir_display_label_migrates_resource_and_driver(self) -> None:
    settings = JsonSettings(Path(self.temp_dir.name) / "ophir-settings.json")
    settings.set(
        "runtime/stations",
        {
            "1": {
                "powerMeterResource": "Ophir USB | OPHIR::USB::3103403 | USB SN 3103403",
                "powerMeterDriverKind": "caihuang",
            }
        },
    )
    backend = HeadlessBackend(settings)
    try:
        config = backend.stations["1"].configuration
        self.assertEqual(config["powerMeterResource"], "OPHIR::USB::3103403")
        self.assertEqual(config["powerMeterDriverKind"], "ophir")
        persisted = settings.get("runtime/stations")["1"]
        self.assertEqual(persisted["powerMeterResource"], "OPHIR::USB::3103403")
        self.assertEqual(persisted["powerMeterDriverKind"], "ophir")
    finally:
        backend.export_worker.stop()

def test_configuring_ophir_resource_synchronizes_driver_kind(self) -> None:
    self.backend.dispatch(
        "app.configure",
        {
            "stationId": "1",
            "powerMeterResource": "Ophir USB | OPHIR::USB::ABC123 | USB SN ABC123",
        },
    )
    config = self.backend.stations["1"].configuration
    self.assertEqual(config["powerMeterResource"], "OPHIR::USB::ABC123")
    self.assertEqual(config["powerMeterDriverKind"], "ophir")
```

- [ ] **Step 2: Run the backend tests and verify RED**

Run: `python -m unittest tests.test_dual_station_runtime.DualStationRuntimeTests.test_loaded_ophir_display_label_migrates_resource_and_driver tests.test_dual_station_runtime.DualStationRuntimeTests.test_configuring_ophir_resource_synchronizes_driver_kind -v`.

Expected: FAIL because the resource remains the full label and the driver remains `caihuang`.

- [ ] **Step 3: Extend the shared backend extractor**

Update `extract_power_resource_name` to prefer an embedded Ophir resource before checking ASRL:

```python
def extract_power_resource_name(value: str) -> str:
    normalized = normalize_power_resource_name(value)
    ophir_match = re.search(r"\bOPHIR::USB::[^|\s]+", normalized)
    if ophir_match is not None:
        return ophir_match.group(0)
    serial_match = re.search(r"\bASRL\d+::INSTR\b", normalized)
    return serial_match.group(0) if serial_match is not None else normalized
```

- [ ] **Step 4: Add one backend normalization boundary**

Add a helper that returns whether it changed the configuration:

```python
def _normalize_power_meter_configuration(
    configuration: dict[str, Any],
    options: Sequence[PowerMeterOption] = (),
) -> bool:
    before = (
        configuration.get("powerMeterResource"),
        configuration.get("powerMeterDriverKind"),
    )
    resource = extract_power_resource_name(str(before[0] or ""))
    configuration["powerMeterResource"] = resource
    if resource.startswith("OPHIR::USB::"):
        configuration["powerMeterDriverKind"] = "ophir"
    else:
        matched = next((item for item in options if item.resource == resource), None)
        if matched is not None:
            configuration["powerMeterDriverKind"] = matched.driver_kind
        elif str(configuration.get("powerMeterDriverKind", "")).lower() == "ophir":
            configuration["powerMeterDriverKind"] = "caihuang"
    after = (
        configuration.get("powerMeterResource"),
        configuration.get("powerMeterDriverKind"),
    )
    return after != before
```

Add `Sequence` to the existing `typing` import. Call the helper after loading `initial_config` and after processing `app.configure`. Store the load-time result on `StationRuntime` so `HeadlessBackend.__init__` can call `_persist_configuration()` once when any station was migrated.

- [ ] **Step 5: Run the focused backend tests and verify GREEN**

Run the command from Step 2.

Expected: both tests PASS.

- [ ] **Step 6: Commit backend normalization**

Run: `git add combined_test/devices.py tauri_bridge/headless_backend.py tests/test_dual_station_runtime.py && git commit -m "fix: synchronize Ophir resource drivers"`.

### Task 3: Persist Asynchronous Detection Results

**Files:**
- Modify: `tauri_bridge/headless_backend.py:2836-2852`
- Test: `tests/test_dual_station_runtime.py`

- [ ] **Step 1: Write the failing asynchronous persistence test**

```python
def test_power_meter_detection_persists_resource_and_driver_together(self) -> None:
    option = PowerMeterOption(
        resource="OPHIR::USB::3103403",
        device_type="Ophir USB",
        detail="USB SN 3103403",
        driver_kind="ophir",
    )

    class FakeDetectThread:
        detected = Signal(object)
        status = Signal(str)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, preferred_resource: str = "") -> None:
            self.preferred_resource = preferred_resource

        def start(self) -> None:
            self.detected.emit([option])
            self.finished.emit()

    with patch("tauri_bridge.headless_backend.PowerMeterDetectThread", FakeDetectThread):
        self.backend.dispatch("device.refresh", {"stationId": "1", "device": "powerMeter"})

    persisted = self.backend.settings.get("runtime/stations")["1"]
    self.assertEqual(persisted["powerMeterResource"], "OPHIR::USB::3103403")
    self.assertEqual(persisted["powerMeterDriverKind"], "ophir")
```

Add `Signal` and `PowerMeterOption` to the test imports.

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m unittest tests.test_dual_station_runtime.DualStationRuntimeTests.test_power_meter_detection_persists_resource_and_driver_together -v`.

Expected: FAIL because the callback updates only memory after the dispatch-level persistence has already run.

- [ ] **Step 3: Persist after the atomic pair update**

After setting `powerMeterResource` and `powerMeterDriverKind` inside `on_detected`, call:

```python
self._persist_configuration()
```

Do not persist when `options` is empty.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 5: Commit asynchronous persistence**

Run: `git add tauri_bridge/headless_backend.py tests/test_dual_station_runtime.py && git commit -m "fix: persist detected power meters"`.

### Task 4: Regression Verification

**Files:**
- No production file changes expected.

- [ ] **Step 1: Run all focused backend tests**

Run: `python -m unittest tests.test_dual_station_runtime tests.test_headless_backend tests.test_coherent_power_meter -v`.

Expected: all tests PASS.

- [ ] **Step 2: Run all frontend tests and build**

Run: `npm test` and `npm run build` from `tauri-ui`.

Expected: all tests PASS and production build succeeds without TypeScript errors.

- [ ] **Step 3: Run repository regression tests**

Run: `python -m unittest discover -v`.

Expected: all tests PASS. Hardware-only tests may skip when their required vendor libraries or devices are unavailable; no new failures are allowed.

- [ ] **Step 4: Verify the exact original configuration no longer reaches PyVISA**

Load a temporary `JsonSettings` containing the original full Ophir label with `caihuang`, initialize `HeadlessBackend`, and assert the resulting `PowerMeterSettings` inputs are `OPHIR::USB::3103403` and `ophir` without opening hardware.

- [ ] **Step 5: Review the final diff**

Run: `git diff --check` and `git status --short`.

Expected: no whitespace errors; only planned source/test files plus pre-existing unrelated workspace changes are present.
