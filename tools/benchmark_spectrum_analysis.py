"""Same-process alternating full-frame benchmark against frozen Python sources.

--reference must contain the original devices.py, spectrum.py, spectrum_math.py.
No acquisition loop, sleep, settings or real instrument is opened or changed.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import fields, is_dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import struct
import sys
import time
from unittest.mock import patch

import numpy as np

from combined_test import devices, spectrum, spectrum_math
from combined_test.models import SpectrometerSettings
from combined_test.native_realtime import get_native_realtime_core, native_realtime_status


def exact(value):
    """Include every array entry and binary64 bit pattern, including signed zero."""
    if is_dataclass(value):
        return {f.name: exact(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (float, np.floating)):
        return {"f64": struct.pack(">d", float(value)).hex()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [exact(v) for v in value]
    if isinstance(value, dict):
        return {k: exact(v) for k, v in value.items()}
    return value


def digest(value):
    return hashlib.sha256(json.dumps(exact(value), sort_keys=True).encode()).hexdigest()


def reference_modules(directory):
    modules = []
    for filename, suffix in [("spectrum_math.py", "math"), ("spectrum.py", "spectrum"),
                             ("devices.py", "devices")]:
        name = "combined_test._spectrum_reference_" + suffix
        path = directory / filename
        source = path.read_text()
        source = source.replace("from .spectrum_math import", "from ._spectrum_reference_math import")
        source = source.replace("from .spectrum import", "from ._spectrum_reference_spectrum import")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        exec(compile(source, str(path), "exec"), module.__dict__)
        modules.append(module)
    return modules


def values(size, kind="multi"):
    x = np.linspace(950.0, 1010.0, size)
    y = 75 + 40_000 * np.exp(-0.5 * ((x - 976.1) / 0.32) ** 2)
    if kind == "multi":
        y += 4_000 * np.exp(-0.5 * ((x - 977.3) / 0.20) ** 2)
    elif kind == "noise":
        y += np.random.default_rng(718).normal(0, 4, size)
    elif kind == "saturated":
        y[size // 2:size // 2 + 4] = 65535.0
    return x.tolist(), y.tolist()


def reader(device_module, math_module, average):
    obj = device_module.SpectrometerReaderThread(
        SpectrometerSettings(10_000, 100, average_frames=average), headless=True)
    obj.full_scale_counts = 65535.0
    obj._calculate_stats = math_module.calculate_stats
    return obj


def parity(reference, native, recorded=()):
    old_math, old_spectrum, old_devices = reference
    core = get_native_realtime_core() if native else None
    if native and core is None:
        raise RuntimeError("native parity requested but native calculation library is missing")
    count = 0
    hashes = []
    with ExitStack() as context:
        for mod in (old_math, spectrum_math):
            context.enter_context(patch.object(mod, "get_native_realtime_core", return_value=core))
        cases = []
        for size in (41, 401, 3648, 8192):
            for kind in ("single", "multi", "noise", "saturated"):
                x, y = values(size, kind)
                cases.append((f"{size}-{kind}", x, y))
        for name, x, y in list(cases[:8]):
            cases.append((name + "-descending", x[::-1], y[::-1]))
        rng = np.random.default_rng(73015)
        for index in range(40):
            size = int(rng.integers(8, 500))
            x = np.sort(rng.uniform(950, 1010, size))
            if index % 5 == 0:
                x[size // 2] = x[size // 2 - 1]
            y = rng.normal(100, 5, size) + 1000 * np.exp(-((x - 976) / 1.5) ** 2)
            if index % 3 == 0:
                order = rng.permutation(size)
                x, y = x[order], y[order]
            cases.append((f"irregular-{index}", x.tolist(), y.tolist()))
        cases.extend([
            ("visible", *[v.tolist() for v in (np.linspace(420, 460, 401),
                2 + 1000 * np.exp(-((np.linspace(420, 460, 401) - 450) / 0.3) ** 2))]),
            ("flat", [950 + i for i in range(61)], [75.0] * 61),
            ("empty", [], []), ("two", [975.0, 976.0], [1.0, 2.0]),
            ("signed-zero", [950.0, 976.0, 1010.0], [-0.0, 0.0, -0.0]),
            ("nan-gap", [950.0, 976.0, 976.1, 976.2, 1010.0],
                [0.0, 65535.0, float("nan"), 65535.0, 0.0]),
            ("infinity", [950.0, 976.0, 1010.0], [float("inf"), 10.0, -float("inf")]),
        ])
        x = [400.0 + i * 0.1 for i in range(6001)]
        y = [0.0] * len(x)
        y[499:502] = y[5759:5762] = [360.0, 600.0, 360.0]
        cases.append(("tied-visible-infrared-unsorted", x[::-1], y[::-1]))
        cases.extend(recorded)
        for name, x, y in cases:
            before = digest((x, y))
            for average in (1, 5):
                a, b = reader(old_devices, old_math, average), reader(devices, spectrum_math, average)
                emitted_a, emitted_b = [], []
                a.reading.connect(emitted_a.append)
                b.reading.connect(emitted_b.append)
                # Distinct frames protect rolling arithmetic and stale-data
                # handling; comparison includes raw saturation leaving a mean.
                for frame in range(8):
                    shifted = (list(y) if name.startswith("recorded-") else
                               [v + frame * 0.00001 if np.isfinite(v) else v for v in y])
                    a._publish_averaged_spectrum(x, shifted)
                    b._publish_averaged_spectrum(x, shifted)
                    old_frame, new_frame = a.take_latest_spectrum(), b.take_latest_spectrum()
                    assert exact(old_frame) == exact(new_frame), (name, average, frame)
                    assert exact(emitted_a) == exact(emitted_b), (name, "signal")
                    hashes.append(digest(new_frame))
                    count += 1
            assert digest((x, y)) == before, (name, "mutated input")
        # Public saturation preserves nonfinite pixel positions, signed zero,
        # per-device thresholds, and exception behavior.
        for y in ([], [-0.0, 0.0], [float("nan")], [float("inf"), 1.0],
                  [64000.0, float("nan"), 64000.0, 64000.0],
                  ["0", "19900", "19900", "19900"]):
            for full_scale in (64000.0, 20000.0, None, float("nan")):
                assert exact(old_spectrum.detect_spectrum_saturation(y, full_scale)) == exact(
                    spectrum.detect_spectrum_saturation(y, full_scale))
                count += 1
    return {"native": native, "comparisons": count,
            "all_fields_and_f64_bits_equal": True,
            "frames_sha256": digest(hashes)}


def timing(reference, iterations):
    old_math, _, old_devices = reference
    reports = []
    for size in (3648, 8192):
        for average in (1, 5):
            for kind in ("multi", "noise", "saturated"):
                x, y = values(size, kind)
                a, b = reader(old_devices, old_math, average), reader(devices, spectrum_math, average)
                for _ in range(10):
                    a._publish_averaged_spectrum(x, y)
                    b._publish_averaged_spectrum(x, y)
                samples = [[], []]
                for index in range(iterations):
                    for version in ((0, 1) if index % 2 == 0 else (1, 0)):
                        obj = (a, b)[version]
                        start = time.perf_counter_ns()
                        obj._publish_averaged_spectrum(x, y)
                        samples[version].append((time.perf_counter_ns() - start) / 1e6)
                assert exact(a.take_latest_spectrum()) == exact(b.take_latest_spectrum())
                row = {"points": size, "average_frames": average, "case": kind}
                for name, times in zip(("before", "after"), samples):
                    row[name] = {"median_ms": float(np.median(times)),
                                 "p95_ms": float(np.percentile(times, 95))}
                row["reduction_percent"] = 100 * (1 - row["after"]["median_ms"] / row["before"]["median_ms"])
                reports.append(row)
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--recorded-fixtures", type=Path,
                        help="read-only directory of prior real-*-full.forwarded.json responses")
    parser.add_argument("--parity-only", action="store_true")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    reference = reference_modules(args.reference)
    recorded = []
    recorded_hashes = {}
    if args.recorded_fixtures:
        for path in sorted(args.recorded_fixtures.glob("real-*-full.forwarded.json")):
            payload = path.read_bytes()
            recorded_hashes[path.name] = hashlib.sha256(payload).hexdigest()
            response = json.loads(payload)["message"]["snapshot"]
            for index, curve in enumerate(response["spectra"]):
                recorded.append((f"recorded-{path.stem}-{index}", curve["wavelengthNm"], curve["intensity"]))
        if not recorded:
            parser.error("no recorded full-spectrum responses were found")
    report = {"environment": {"platform": platform.platform(), "python": platform.python_version(),
                               "native": native_realtime_status()},
        "scope": "same process alternating frozen/current production analysis; no hardware, acquisition scheduling or I/O",
        "iterations_per_version": args.iterations,
        "reference_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in args.reference.glob("*.py")},
        "recorded_source_sha256": recorded_hashes,
        "recorded_curves": len(recorded),
        "parity": [parity(reference, False, recorded), parity(reference, True, recorded)],
        "timings": [] if args.parity_only else timing(reference, args.iterations)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({"parity": report["parity"], "timings": report["timings"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
