"""Hardware-free data browser benchmark; all databases live in a temporary directory.

Run: python -m tools.benchmark_data_library --output output/data-library.json
Compares public results to the original full-decode path on identical stored data.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import platform
import sqlite3
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

from combined_test.data_library import DataLibrary


def measure(action, repeats: int) -> dict:
    action()
    samples = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        action()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    gc.collect()
    tracemalloc.start()
    action()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"median_ms": statistics.median(samples), "max_ms": max(samples),
            "peak_python_mib": peak / 1024**2, "samples_ms": samples}


def full_decode(library: DataLibrary, record_id: str, include_spectra: bool) -> dict:
    with library._connect() as db:
        raw = db.execute("SELECT detail FROM records WHERE id=?", (record_id,)).fetchone()[0]
    excluded = {"attempts", "session"} | (set() if include_spectra else {"spectra"})
    return {k: v for k, v in json.loads(raw).items() if k not in excluded}


def run(repeats: int) -> dict:
    rows = []
    with tempfile.TemporaryDirectory(prefix="arp-library-benchmark-") as directory:
        root = Path(directory)
        library = DataLibrary(root / "library.sqlite3")
        for currents, pixels in ((20, 3648), (100, 8192)):
            record_id = f"synthetic-{currents}-{pixels}"
            wavelength = [950 + index * 60 / (pixels - 1) for index in range(pixels)]
            detail = {
                "record": {"id": record_id, "sn": record_id, "station": "合成数据",
                           "timeMs": 1, "flowStatus": "completed", "validPointCount": currents},
                "points": [{"currentA": index, "powerW": index * 2} for index in range(currents)],
                "spectra": [{"currentA": index, "wavelengthNm": wavelength,
                             "intensity": [100 + (50000 + index) * math.exp(-((w - 976) / .5)**2)
                                           for w in wavelength]} for index in range(currents)],
                "attempts": [{"current_a": index, "reason": "合成尝试"} for index in range(currents)],
                "session": {"settings": {}}, "metadata": {"来源": "固定合成数据"}, "warnings": [],
            }
            library.save(detail)
            for include_spectra in (False, True):
                expected = full_decode(library, record_id, include_spectra)
                assert library.inspect(record_id, include_spectra=include_spectra) == expected
                row = {"currents": currents, "pixels": pixels, "include_spectra": include_spectra,
                       "stored_json_bytes": len(json.dumps(detail, ensure_ascii=False).encode()), "parity": True}
                row["full_decode_reference"] = measure(lambda: full_decode(library, record_id, include_spectra), repeats)
                row["public_inspect"] = measure(lambda: library.inspect(record_id, include_spectra=include_spectra), repeats)
                rows.append(row)
        journal_root = root / "station-1"
        for index in range(200):
            path = journal_root / str(index) / "events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text("", encoding="utf-8")
        assert library.recover(journal_root) == []
        recovery = measure(lambda: library.recover(journal_root), repeats)
    return {"environment": {"platform": platform.platform(), "python": platform.python_version(),
                            "sqlite": sqlite3.sqlite_version},
            "workload": {"repeats": repeats, "source": "synthetic temporary database; no hardware or user records",
                         "memory": "tracemalloc Python allocations only; excludes SQLite native memory",
                         "timing": "warm filesystem cache; fresh connection on each public call"},
            "inspect": rows, "unchanged_recovery_200_journals": recovery}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=9)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    report = run(args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(report['inspect'])} parity cases; report: {args.output}")


if __name__ == "__main__":
    main()
