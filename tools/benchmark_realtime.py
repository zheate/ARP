"""Repeatable hardware-free benchmarks for Python and native spectrum paths.

Run from the repository root with the product Python environment. Timings include
public-call conversion costs; native kernel timings alone are not product speedups.
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import numpy as np
import scipy

from combined_test import spectrum_math
from combined_test.native_realtime import get_native_realtime_core, native_realtime_status


def measure(action, batches: int, iterations: int) -> dict[str, float]:
    for _ in range(10):
        action()
    samples = []
    for _ in range(batches):
        started = time.perf_counter_ns()
        for _ in range(iterations):
            action()
        samples.append((time.perf_counter_ns() - started) / iterations / 1_000)
    return {"median_us": float(np.median(samples)), "p95_us": float(np.percentile(samples, 95))}


def run(batches: int, iterations: int) -> dict:
    core = get_native_realtime_core()
    if core is None:
        raise RuntimeError("原生计算库未加载；请先构建，必要时设置 ARP_REALTIME_CORE_DLL，不能以跳过代替比较")
    rows = []
    rng = np.random.default_rng(20260918)
    for size in (1024, 3648, 8192):
        x = np.linspace(950, 1010, size)
        peak = 52000 * np.exp(-0.5 * ((x - 976.1) / 0.42) ** 2)
        cases = {
            "single_peak": peak + 75,
            "multi_peak": peak + 12000 * np.exp(-0.5 * ((x - 978.1) / 0.3) ** 2) + 75,
            "offset_noise": peak + 1000 + rng.uniform(0, 30, size),
            "flat": np.full(size, 75.0),
        }
        for name, y in cases.items():
            # Lists match the public acquisition API; conversion remains in timing.
            xl, yl = x.tolist(), y.tolist()
            result = {"points": size, "case": name}
            for operation, function in (("stats", spectrum_math.calculate_stats), ("pib", spectrum_math.calculate_pib)):
                with patch.object(spectrum_math, "get_native_realtime_core", return_value=None):
                    expected = function(xl, yl)
                    python = measure(lambda: function(xl, yl), batches, iterations)
                with patch.object(spectrum_math, "get_native_realtime_core", return_value=core):
                    actual = function(xl, yl)
                    native = measure(lambda: function(xl, yl), batches, iterations)
                a = list(asdict(actual).values()) if operation == "stats" else [actual]
                e = list(asdict(expected).values()) if operation == "stats" else [expected]
                np.testing.assert_allclose(a, e, rtol=1e-12, atol=1e-9, equal_nan=True)
                result[operation] = {"python": python, "native": native, "speedup": python["median_us"] / native["median_us"], "parity": True}
            result["array_conversion"] = measure(lambda: (core._array(xl), core._array(yl)), batches, iterations)
            result["native_stats_array_input"] = measure(lambda: core.calculate_spectrum_stats(x, y), batches, iterations)
            rows.append(result)
    return {"environment": {"platform": platform.platform(), "python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__, "native": native_realtime_status()}, "workload": {"seed": 20260918, "batches": batches, "iterations_per_batch": iterations, "source": "deterministic synthetic spectra; no hardware"}, "results": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batches", type=int, default=15)
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()
    if min(args.batches, args.iterations) < 1:
        parser.error("batches and iterations must be positive")
    report = run(args.batches, args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Verified {len(report['results'])} cases; report: {args.output}")


if __name__ == "__main__":
    main()
