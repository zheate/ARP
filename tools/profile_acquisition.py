"""Profile real acquisition/automatic code using simulated device ports only.

Run from the repository root in sth_eb314. Does not discover/open instruments,
edit settings, replace clocks, or shorten production waits. All writes go into
TemporaryDirectory; JSON timing evidence goes to --output.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack
from datetime import datetime
from functools import wraps
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from combined_test import devices, spectrum_math
from combined_test.automation import (
    AutomaticTestSettings, MIN_POWER_SUPPLY_COMMAND_INTERVAL_S,
    ORDINARY_AUTO_POST_STABLE_DELAY_S, FINAL_AUTO_POST_STABLE_DELAY_S,
)
from combined_test.core import PowerStabilityDetector
from combined_test.models import PowerMeterSettings, SpectrometerSettings
from combined_test.excel_export import ExcelTestRecord
from combined_test.native_realtime import native_realtime_status
from combined_test.record_store import SessionStatus, SessionRecordStore
from combined_test.session_journal import _json_value
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge import export_queue
from tauri_bridge.json_codec import encode_response
from tauri_bridge.settings import JsonSettings
from tauri_bridge.station_automatic import automatic_power_stability_window_s


def distribution(values):
    values = list(values)
    if not values:
        return {"count": 0}
    return {"count": len(values), "median_ms": float(np.median(values)),
            "p95_ms": float(np.percentile(values, 95)), "max_ms": max(values),
            "total_ms": sum(values)}


class Spans:
    """Nested wall times; exclusive subtracts children in the same thread only."""

    def __init__(self):
        self.events = []
        self.local = threading.local()
        self.origin = time.perf_counter_ns()
        self.enabled = True

    def wrap(self, name, function):
        @wraps(function)
        def measured(*args, **kwargs):
            if not self.enabled:
                return function(*args, **kwargs)
            stack = getattr(self.local, "stack", None)
            if stack is None:
                stack = self.local.stack = []
            entry = [time.perf_counter_ns(), 0]
            stack.append(entry)
            try:
                return function(*args, **kwargs)
            finally:
                elapsed = time.perf_counter_ns() - entry[0]
                stack.pop()
                if stack:
                    stack[-1][1] += elapsed
                self.events.append({"name": name, "thread": threading.current_thread().name,
                                    "at_ms": (entry[0] - self.origin) / 1e6,
                                    "ms": elapsed / 1e6,
                                    "exclusive_ms": (elapsed - entry[1]) / 1e6})
        return measured

    def patch(self, context, target, attr, name):
        context.enter_context(patch.object(target, attr, self.wrap(name, getattr(target, attr))))

    def summary(self):
        grouped = defaultdict(list)
        for event in self.events:
            grouped[event["name"]].append(event)
        return {name: {"wall": distribution(e["ms"] for e in events),
                       "exclusive": distribution(e["exclusive_ms"] for e in events)}
                for name, events in sorted(grouped.items())}


def spectrum_values(size):
    x = np.linspace(950.0, 1010.0, size)
    # Resolved side peak exercises SMSR/annotation paths as well as PIB/stats.
    y = 75 + 40_000 * np.exp(-0.5 * ((x - 976.1) / 0.32) ** 2)
    y += 4_000 * np.exp(-0.5 * ((x - 977.3) / 0.20) ** 2)
    return x.tolist(), y.tolist()


def instrument_analysis(context, spans, reader):
    for attr, label in [("detect_spectrum_saturation", "spectrum.saturation"),
                        ("calculate_pib_and_smsr", "spectrum.shared_metrics"),
                        ("find_spectrum_peak_annotations", "spectrum.annotations")]:
        spans.patch(context, devices, attr, label)
    spans.patch(context, spectrum_math, "_calculate_pib_values", "spectrum.pib")
    spans.patch(context, spectrum_math, "_calculate_smsr_values", "spectrum.smsr")
    reader._calculate_stats = spans.wrap("spectrum.stats", spectrum_math.calculate_stats)
    spans.patch(context, reader._spectrum_averager, "add", "spectrum.average")
    spans.patch(context, reader, "_publish_latest_spectrum", "spectrum.copy_to_mailbox")
    spans.patch(context, reader, "_publish_averaged_spectrum", "spectrum.analysis_total")


def analysis_profile(iterations):
    reports = []
    for size in (3648, 8192):
        for average in (1, 5):
            x, y = spectrum_values(size)
            reader = devices.SpectrometerReaderThread(
                SpectrometerSettings(10_000, 100, average_frames=average), headless=True)
            reader._calculate_stats = spectrum_math.calculate_stats
            for _ in range(8):
                reader._publish_averaged_spectrum(x, y)
            expected = reader.take_latest_spectrum()
            spans = Spans()
            overhead = {"unrecorded": [], "recorded": []}
            with ExitStack() as context:
                instrument_analysis(context, spans, reader)
                for index in range(iterations):
                    # Alternate to quantify timestamp/list-recording overhead
                    # under the same CPU/GC conditions. No clocks are mocked.
                    for enabled in ((False, True) if index % 2 == 0 else (True, False)):
                        spans.enabled = enabled
                        started = time.perf_counter_ns()
                        reader._publish_averaged_spectrum(x, y)
                        overhead["recorded" if enabled else "unrecorded"].append(
                            (time.perf_counter_ns() - started) / 1e6)
                spans.enabled = True
            actual = reader.take_latest_spectrum()
            assert expected == actual, "timing wrappers changed the analyzed frame"
            assert len(actual.intensity) == size and actual.smsr is not None
            reports.append({"points": size, "average_frames": average,
                            "frame_equal": True, "stages": spans.summary(),
                            "recording_overhead_probe": {k: distribution(v) for k, v in overhead.items()}})
    return reports


def journal_profile(iterations):
    """Split conversion/JSON costs using an event captured from a real commit.

    CPU stages are measured independently; they are not added to a live trace.
    The actual commit/flush is retained and compared byte for byte.
    """
    import tempfile
    with tempfile.TemporaryDirectory(prefix="arp-journal-profile-") as directory:
        store = SessionRecordStore(Path(directory) / "journal")
        store.begin_session(Path(directory) / "excel", "SIMULATED-JOURNAL", datetime.now())
        store.configure_sequence((2.0,))
        x, y = spectrum_values(8192)
        record = ExcelTestRecord(2.0, 12.0, 3.5, 14.5, 976.1, 976.1, 0.75, 0.95, x, y)
        captured = []
        append = store._journal.append
        def capture(event):
            captured.append(event)
            append(event)
        with patch.object(store._journal, "append", capture):
            store.queue(record)
        event = captured[0]
        prepared = _json_value(event)
        def encode():
            return json.dumps(prepared, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8") + b"\n"
        expected = encode()
        assert store.journal_path.read_bytes().splitlines(keepends=True)[-1] == expected
        samples = {"dataclass_copy_and_json_value": [], "json_utf8": []}
        for index in range(iterations):
            actions = [("dataclass_copy_and_json_value", lambda: _json_value(event)),
                       ("json_utf8", encode)]
            for name, action in (actions if index % 2 == 0 else reversed(actions)):
                started = time.perf_counter_ns()
                action()
                samples[name].append((time.perf_counter_ns() - started) / 1e6)
        return {"points": 8192, "event_bytes": len(expected), "actual_journal_bytes_equal": True,
                "stages": {name: distribution(times) for name, times in samples.items()}}


class SimulatedSupply:
    def __init__(self):
        self.is_connected = True
        self.output_enabled = True
        self.current_a = 0.0
        self.voltage_v = 12.0
        self.commands = []

    def set_output_current(self, value):
        self.commands.append(("current", time.monotonic()))
        self.current_a = float(value)

    def read_output_voltage(self):
        self.commands.append(("voltage", time.monotonic()))
        return self.voltage_v

    def read_output_current(self):
        self.commands.append(("read_current", time.monotonic()))
        return self.current_a

    def set_output_enabled(self, enabled):
        self.output_enabled = bool(enabled)


def wall_profile(directory, size, rounds, realtime_seconds):
    backend = HeadlessBackend(JsonSettings(directory / "settings.json"))
    station = backend.runtime
    config = station.configuration
    # Keep runtime defaults; only choose a short standard current plan and
    # direct its records to an isolated temporary directory.
    config.update(sn="PROFILE-SIMULATED", outputDir=str(directory / "excel"))
    supply = SimulatedSupply()
    station.power_supply_controller = supply
    station.power_supply_kind = "ch341"
    config["powerSupplyKind"] = "ch341"
    settings_before = dict(config)
    x, y = spectrum_values(size)
    spans = Spans()
    failures = []
    publication_times = defaultdict(list)
    snapshot_gaps = []
    stop_snapshots = threading.Event()
    reports = []

    class SimulatedMeter:
        device_type = "PROFILE SIMULATION"
        probe_serial_number = "SIMULATED-PROBE"

        def __init__(self, _resource): pass
        def test(self): return "OK"
        def set_wavelength(self, _value): pass
        def close(self): pass
        def read_power_w(self): return 3.5

    class SimulatedSpectrum:
        def set_integration_time(self, _value): pass
        def get_maximum_intensity(self): return 65535.0
        def read_spectrum(self): return x.copy(), y.copy()
        def close(self): pass

    power_reader = devices.PowerMeterReaderThread(PowerMeterSettings(
        "SIMULATED", config["powerMeterWavelengthNm"], config["softwareGain"],
        config["powerMeterIntervalMs"], config["stableWindowS"],
        config["stabilityLimitPercent"]))
    spectrum_reader = devices.SpectrometerReaderThread(SpectrometerSettings(
        config["integrationTimeUs"], config["spectrometerIntervalMs"],
        average_frames=config["spectrumAverageFrames"],
        auto_integration_enabled=config["autoIntegration"]), headless=True)
    station.power_meter_reader = power_reader
    station.spectrometer_reader = spectrum_reader

    def on_power(reading):
        publication_times["power"].append(time.perf_counter_ns())
        station._on_power_reading(reading)

    def on_spectrum(reading):
        publication_times["spectrum"].append(time.perf_counter_ns())
        station._on_spectrometer_reading(reading)

    power_reader.reading.connect(on_power)
    spectrum_reader.reading.connect(on_spectrum)
    power_reader.failed.connect(failures.append)
    spectrum_reader.failed.connect(failures.append)
    import_module = importlib.import_module
    fake_power_module = SimpleNamespace(CaihuangPowerMeter=SimulatedMeter,
        LaserPointPowerMeter=SimulatedMeter, normalize_resource=str)

    def safe_import(name, *args, **kwargs):
        if name == "tools.power_meter_mvp":
            return fake_power_module
        return import_module(name, *args, **kwargs)

    def snapshots():
        since = None
        cursor = None
        while not stop_snapshots.is_set():
            start = time.perf_counter_ns()
            params = {"view": "automatic"}
            if since is not None:
                params.update(since=since, cursors={"power": cursor})
            response = {"id": "profile", "ok": True, "result": backend.snapshot(params)}
            payload = spans.wrap("snapshot.production_json", encode_response)(response)
            decoded = json.loads(payload)["result"]
            # Restore the current wavelength grid on every changed spectrum.
            spectrum = decoded.get("measurements", {}).get("spectrum")
            if spectrum is not None:
                assert len(spectrum["intensity"]) == size
            since = decoded["seriesRevisions"]
            power_patch = decoded.get("seriesPatches", {}).get("power", {}).get("points")
            power_received = (decoded.get("measurements", {}).get("power", [])
                              if power_patch is None else power_patch)
            if power_received:
                cursor = power_received[-1]["elapsedS"]
            snapshot_gaps.append((time.perf_counter_ns() - start) / 1e6)
            # Same post-response 100 ms wait as the production native stream.
            stop_snapshots.wait(0.1)

    try:
        with ExitStack() as context:
            context.enter_context(patch.object(devices.importlib, "import_module", safe_import))
            context.enter_context(patch.object(devices, "load_spectrometer_components",
                return_value=(SimulatedSpectrum, spans.wrap("spectrum.stats", spectrum_math.calculate_stats))))
            context.enter_context(patch.object(devices, "open_spectrometer_device", return_value=0))
            instrument_analysis(context, spans, spectrum_reader)
            for target, attr, label in [
                (PowerStabilityDetector, "add_sample", "power.stability_calculation"),
                (station, "_on_power_reading", "power.backend_callback"),
                (station, "_on_spectrometer_reading", "spectrum.backend_callback"),
                (backend, "snapshot", "snapshot.build"),
                (station, "_wait_for_automatic_acquisition", "automatic.wait_ready"),
                (station, "_set_automatic_current", "automatic.set_current_with_safety_wait"),
                (station, "_wait_for_automatic_stability", "automatic.initial_stability"),
                (station, "_wait_for_automatic_stable_hold", "automatic.stable_hold"),
                (station, "_reserve_power_supply_command", "automatic.reserve_command_slot"),
                (station, "_run_automatic_point", "automatic.point_total"),
                (station, "safe_ramp_down", "automatic.safe_ramp_down"),
                (station.record_store, "queue", "record.commit_point"),
                (station.record_store._journal, "_append_locked", "record.journal_total"),
                (station, "_save_record_store", "export.workbook_total"),
                (export_queue, "save_test_records", "export.worker_write_workbook"),
                (supply, "set_output_current", "SIMULATED.supply_write"),
                (supply, "read_output_voltage", "SIMULATED.voltage_read"),
                (SimulatedSpectrum, "read_spectrum", "SIMULATED.spectrum_read"),
                (SimulatedMeter, "read_power_w", "SIMULATED.power_read"),
                (os, "fsync", "record.fsync"),
            ]:
                spans.patch(context, target, attr, label)
            power_reader.start()
            spectrum_reader.start()
            deadline = time.monotonic() + 5
            while not (power_reader.is_ready and spectrum_reader.is_ready and station.latest_spectrum):
                if failures or time.monotonic() >= deadline:
                    raise RuntimeError(f"simulated acquisition failed: {failures}")
                time.sleep(0.01)
            def run_snapshots():
                try:
                    snapshots()
                except Exception as exc:
                    failures.append(f"snapshot: {exc}")
            snapshot_thread = threading.Thread(target=run_snapshots, name="profile-snapshot", daemon=True)
            snapshot_thread.start()
            time.sleep(realtime_seconds)
            for run in range(rounds):
                settings = AutomaticTestSettings(initial_current_a=2.0, target_current_a=4.0,
                    current_step_a=2.0, use_spectrometer=True)
                station.automatic.start(settings, power_meter_ready=True, spectrum_meter_ready=True)
                station._begin_recording_session(settings)
                # Start with a recently occupied command slot to measure the
                # real 1.1s gate. This is fixture initial state, not a shorter wait.
                station._last_power_supply_command_at = time.monotonic()
                run_start = time.perf_counter_ns()
                for index, current in enumerate(station.automatic.currents):
                    station.automatic.current_index = index
                    station._run_automatic_point(index, current, settings)
                station.safe_ramp_down()
                station.record_store.complete_session(SessionStatus.COMPLETED,
                    "hardware-free timing completed", shutdown_confirmed=True)
                station.automatic.complete()
                path = station._save_record_store()
                records = station.record_store.snapshot()
                assert path and path.exists() and len(records) == 2
                assert all(len(record.intensity) == size for record in records)
                reports.append({"round": run + 1,
                    "elapsed_ms_including_export": (time.perf_counter_ns() - run_start) / 1e6,
                    "saved_points": len(records), "spectrum_points_each": size,
                    "workbook_bytes": path.stat().st_size})
                print(f"automatic round {run + 1}/{rounds} finished", flush=True)
            # setCurrentA follows the simulated run and returns to zero at
            # shutdown; measurement/timing settings must remain identical.
            assert {k: v for k, v in config.items() if k != "setCurrentA"} == {
                k: v for k, v in settings_before.items() if k != "setCurrentA"
            }, "profile changed measurement/timing configuration"
    finally:
        stop_snapshots.set()
        if "snapshot_thread" in locals():
            snapshot_thread.join(5)
        power_reader.stop()
        spectrum_reader.stop()
        power_reader.wait(5000)
        spectrum_reader.wait(5000)
        backend.export_worker.graceful_stop()
    assert not failures, failures
    slots = [when for kind, when in supply.commands if kind in ("current", "voltage")]
    gaps = [(b - a) * 1000 for a, b in zip(slots, slots[1:])]
    assert min(gaps) >= MIN_POWER_SUPPLY_COMMAND_INTERVAL_S * 1000 - 5, gaps
    return {"rounds": reports, "stages": spans.summary(), "trace": spans.events,
            "cadence": {name: distribution(np.diff(times) / 1e6)
                        for name, times in publication_times.items()},
            "snapshot_build_encode_decode": distribution(snapshot_gaps),
            "command_gaps": {**distribution(gaps), "min_ms": min(gaps), "samples_ms": gaps},
            "measurement_and_timing_configuration_unchanged": True,
            "settings": settings_before,
            "stability_window_at_2A_s": automatic_power_stability_window_s(
                config["selectedScriptId"], config["stableWindowS"], 2.0)}


def source_hashes():
    # This process executes Python only. Frontend profiling has its own isolated
    # checkout and hash inventory, so concurrent frontend edits cannot invalidate
    # or contaminate this measurement.
    roots = (Path("combined_test"), Path("tauri_bridge"))
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for root in roots for path in sorted(root.rglob("*"))
            if path.suffix in (".py", ".ts", ".tsx", ".rs")}


def main():
    import tempfile
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=60)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--points", type=int, choices=(3648, 8192), default=8192)
    parser.add_argument("--realtime-seconds", type=float, default=5)
    parser.add_argument("--analysis-only", action="store_true",
                        help="repeat CPU-stage/recording-overhead measurements without a wall-clock run")
    args = parser.parse_args()
    if min(args.iterations, args.rounds, args.realtime_seconds) <= 0:
        parser.error("iterations, rounds, realtime-seconds must be positive")
    before = source_hashes()
    report = {"environment": {"platform": platform.platform(), "python": platform.python_version(),
                               "native": native_realtime_status()},
              "scope": "production methods; SIMULATED instruments; real clocks/waits/fsync/Excel; no WebView",
              "constants": {"ch341_minimum_s": MIN_POWER_SUPPLY_COMMAND_INTERVAL_S,
                            "ordinary_hold_s": ORDINARY_AUTO_POST_STABLE_DELAY_S,
                            "final_hold_s": FINAL_AUTO_POST_STABLE_DELAY_S},
              "analysis": analysis_profile(args.iterations)}
    report["journal_cpu"] = journal_profile(args.iterations)
    if not args.analysis_only:
        with tempfile.TemporaryDirectory(prefix="arp-acquisition-profile-") as directory:
            report["live_and_automatic"] = wall_profile(Path(directory), args.points,
                                                       args.rounds, args.realtime_seconds)
    after = source_hashes()
    assert before == after, "Python product sources changed during profiling"
    report["source_hashes"] = before
    report["python_product_sources_unchanged"] = True
    report["environment"]["native_after_run"] = native_realtime_status()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(f"Timing report: {args.output}")


if __name__ == "__main__":
    main()
