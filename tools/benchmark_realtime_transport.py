"""Measure the production snapshot/JSON path with synthetic data and no devices.

The --serve fixture runs the real sidecar queues and protocol writer with an
isolated backend. It accepts snapshots only and never starts device discovery.
"""
from __future__ import annotations

import argparse
import json
import platform
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

from combined_test.models import SpectrumFrame
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.service import BridgeService
from tauri_bridge.settings import JsonSettings


class TimedRLock:
    """Record only outermost state-lock holds, including nested acquisitions."""

    def __init__(self):
        self.lock = threading.RLock()
        self.local = threading.local()
        self.holds_ms = []

    def __enter__(self):
        self.lock.acquire()
        depth = getattr(self.local, "depth", 0)
        if depth == 0:
            self.local.started = time.perf_counter_ns()
        self.local.depth = depth + 1
        return self

    def __exit__(self, *_):
        self.local.depth -= 1
        if self.local.depth == 0:
            self.holds_ms.append((time.perf_counter_ns() - self.local.started) / 1e6)
        self.lock.release()


def fixture(directory: str, points: int) -> HeadlessBackend:
    backend = HeadlessBackend(JsonSettings(Path(directory) / "settings.json"))
    station = backend.runtime
    wavelength = np.linspace(950.0, 1010.0, points)
    intensity = 75 + 52_000 * np.exp(-0.5 * ((wavelength - 976.1) / 0.42) ** 2)
    # SpectrumAverager and the reader publish independent Python float lists.
    station.latest_spectrum = SpectrumFrame(wavelength.tolist(), intensity.tolist(), None)
    station.spectrum_revision = 1
    station.power_points.extend({"elapsedS": i * 0.1, "powerW": 2 + i * 0.001} for i in range(1200))
    station.pd_state["points"] = [{"elapsedS": i * 0.01, "value": i * 0.001} for i in range(13_000)]
    station.series_revisions.update(power=1, spectrum=1, pd=1)
    return backend


def stats(samples):
    return {"median_ms": float(np.median(samples)), "p95_ms": float(np.percentile(samples, 95))}


def measure(action, iterations):
    for _ in range(5):
        action()
    samples = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        action()
        samples.append((time.perf_counter_ns() - start) / 1e6)
    return stats(samples)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def lock_probe(backend, request, polls=30):
    """Probe state-lock waits at 200 Hz while snapshots poll at the real 10 Hz.

    This measures mutex acquisition once the probe thread is scheduled; it is
    not a device callback latency or an operating-system scheduling guarantee.
    """
    stop = threading.Event()
    waits = []

    def probe():
        while not stop.wait(0.005):
            started = time.perf_counter_ns()
            with backend.runtime._lock:
                waits.append((time.perf_counter_ns() - started) / 1e6)

    thread = threading.Thread(target=probe, name="benchmark-state-lock")
    thread.start()
    service = BridgeService(backend)
    try:
        deadline = time.perf_counter()
        for _ in range(polls):
            assert service.handle_line(request)["ok"]
            deadline += 0.1
            stop.wait(max(0.0, deadline - time.perf_counter()))
    finally:
        stop.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert waits
    return {**stats(waits), "max_ms": max(waits), "attempts": len(waits),
            "snapshot_polls": polls, "snapshot_interval_ms": 100, "probe_interval_ms": 5}


def run(output: Path, iterations: int):
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    lock_waits = []
    for points in (3648, 8192):
        with tempfile.TemporaryDirectory(prefix="arp-transport-") as directory:
            backend = fixture(directory, points)
            station = backend.runtime
            try:
                lock = TimedRLock()
                station._lock = lock
                service = BridgeService(backend)
                previous = backend.snapshot({"view": "automatic"})["seriesRevisions"]
                scenarios = {
                    "live_full": {"view": "automatic"},
                    "live_delta": {"view": "automatic", "since": {**previous, "power": 0, "spectrum": 0}, "cursors": {"power": 119.8}},
                    "live_unchanged": {"view": "automatic", "since": previous},
                    "pd_full": {"view": "pd"},
                    "pd_delta": {"view": "pd", "since": {**previous, "pd": 0}, "cursors": {"pd": 129.9}},
                }
                for name, params in scenarios.items():
                    request = json.dumps({"v": 2, "id": "benchmark", "method": "app.snapshot", "params": params})
                    action = lambda: service.handle_line(request)
                    lock.holds_ms.clear()
                    build = measure(action, iterations)
                    holds = stats(lock.holds_ms[5:])
                    response = action()
                    assert response["ok"], response
                    payload = encode(response)
                    (output / f"{points}-{name}.json").write_bytes(payload)
                    row = {"points": points, "scenario": name, "snapshot": build, "state_lock": holds,
                           "json_encode": measure(lambda: encode(response), iterations),
                           "response_bytes": len(payload)}
                    rows.append(row)
                rows[-5]["spectrum_conversion"] = measure(lambda: station.build_spectrum_snapshot(allow_cached_grid=True), iterations)
                station._lock = lock.lock
                request = json.dumps({"v": 2, "id": "lock-probe", "method": "app.snapshot", "params": scenarios["live_delta"]})
                lock_waits.append({"points": points, **lock_probe(backend, request)})
            finally:
                backend.export_worker.graceful_stop()
    report = {"environment": {"platform": platform.platform(), "python": platform.python_version(), "numpy": np.__version__},
              "workload": {"iterations": iterations, "source": "synthetic reader-shaped lists; 1200 power points; 13000 PD points; no hardware"},
              "results": rows, "state_lock_wait_probe": lock_waits}
    (output / "python.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def serve(points: int):
    from tauri_bridge import __main__ as sidecar

    with tempfile.TemporaryDirectory(prefix="arp-transport-pipe-") as directory:
        backend = fixture(directory, points)
        original_snapshot = backend.snapshot

        def snapshot(params=None):
            # Simulate exactly one newly published frame/power point per request.
            # No scheduling, driver or device-control method is called.
            with backend.runtime._lock:
                backend.runtime.series_revisions["spectrum"] += 1
                backend.runtime.series_revisions["power"] += 1
                last = backend.runtime.power_points[-1]
                backend.runtime.power_points.append({"elapsedS": last["elapsedS"] + 0.1, "powerW": 3.2})
                if (params or {}).get("view") == "pd":
                    with backend.runtime._pd_lock:
                        pd_points = backend.runtime.pd_state["points"]
                        pd_points.append({"elapsedS": pd_points[-1]["elapsedS"] + 0.01, "value": 1.2})
                        del pd_points[:len(pd_points) - 13_000]
                        backend.runtime.series_revisions["pd"] += 1
            return original_snapshot(params)

        backend.snapshot = snapshot
        original_handle = BridgeService.handle_line

        def handle(service, line, **kwargs):
            request = json.loads(line)
            assert request.get("method") == "app.snapshot", "benchmark fixture accepts snapshots only"
            return original_handle(service, line, **kwargs)

        try:
            sidecar._reserve_protocol_stdout()
            with patch("tauri_bridge.headless_backend.HeadlessBackend", return_value=backend), \
                 patch.object(backend, "start_remembered_device_discovery"), \
                 patch.object(BridgeService, "handle_line", handle):
                sidecar._run_headless_backend()
        finally:
            backend.export_worker.graceful_stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--points", type=int, default=8192)
    args = parser.parse_args()
    if args.points < 3 or args.iterations < 1:
        parser.error("points must be >= 3 and iterations must be positive")
    if args.serve:
        serve(args.points)
    elif args.output:
        run(args.output, args.iterations)
    else:
        parser.error("--output is required unless --serve is selected")


if __name__ == "__main__":
    main()
