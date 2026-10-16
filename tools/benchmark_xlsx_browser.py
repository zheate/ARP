"""Offline paired benchmark; never imports or commands a device backend.

Run from the repository root with python -m tools.benchmark_xlsx_browser --help.
Inputs are read-only. Application caches are cleared outside each timed cold
read; this does not claim a cold operating-system disk cache.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import platform
import statistics
import struct
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import openpyxl

from combined_test import data_browser as browser


def original_rows(sheet, *, min_row, max_col):
    return sheet.iter_rows(min_row=min_row, max_col=max_col, values_only=True)


def fingerprint(value):
    """Stable output hash including each float's exact binary64 representation."""
    def encode(item):
        if isinstance(item, float):
            return ["float64", struct.pack(">d", item).hex()]
        if isinstance(item, dict):
            return {key: encode(value) for key, value in sorted(item.items())}
        if isinstance(item, (list, tuple)):
            return [encode(value) for value in item]
        return item
    return hashlib.sha256(json.dumps(encode(value), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def paired(operation, rounds):
    samples = {"original": [], "optimized": []}
    expected = None
    last = None
    for round_index in range(rounds):
        order = ("original", "optimized") if round_index % 2 == 0 else ("optimized", "original")
        for variant in order:
            browser.clear_data_browser_cache()
            gc.collect()
            context = patch.object(browser, "iter_browser_rows", original_rows) if variant == "original" else nullcontext()
            with context:
                start = time.perf_counter()
                last = operation()
                elapsed = (time.perf_counter() - start) * 1000
            samples[variant].append(elapsed)
            comparable = {key: value for key, value in last.items() if key != "indexedAtMs"}
            digest = fingerprint(comparable)
            if expected is None:
                expected = digest
            if digest != expected:
                raise AssertionError(f"Output changed in {variant}, round {round_index}")
        print(f"  paired round {round_index + 1}/{rounds} complete", flush=True)
    result = {key: {"median_ms": statistics.median(values), "samples_ms": values} for key, values in samples.items()}
    result["reduction_percent"] = (1 - result["optimized"]["median_ms"] / result["original"]["median_ms"]) * 100
    result["identical_output_sha256_with_float_bits"] = expected
    return result, last


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", action="append", type=Path, required=True)
    parser.add_argument("--index-root", type=Path)
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--index-rounds", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 1 or args.index_rounds < 1:
        parser.error("round counts must be positive")
    files = set(args.file)
    if args.index_root:
        files.update(args.index_root.rglob("*.xlsx"))
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)}
    report = {"environment": {"python": platform.python_version(), "platform": platform.platform(),
                               "openpyxl": openpyxl.__version__},
              "cache_conditions": "application cache cleared; gc collected before timing; OS disk cache not cleared; alternating order",
              "reads": [], "source_sha256": hashes}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    for path in args.file:
        for full in (False, True):
            print(f"Read {path.name}: includeSpectra={full}", flush=True)
            result, detail = paired(lambda: browser.inspect_data_record({"path": str(path), "includeSpectra": full}), args.rounds)
            report["reads"].append({"path": str(path), "includeSpectra": full, **result,
                                    "points": len(detail["points"]), "spectra": len(detail.get("spectra") or []),
                                    "samples": sum(len(item["intensity"]) for item in detail.get("spectra") or [])})
            save()
    if args.index_root:
        print(f"Index {args.index_root}", flush=True)
        result, index = paired(lambda: browser.list_data_records({"rootPath": str(args.index_root), "pageSize": 200}), args.index_rounds)
        report["index"] = {**result, "fileCount": index["fileCount"], "errorCount": index["errorCount"]}
        # The previous paired operation leaves a populated optimized index.
        refresh = []
        for _ in range(10):
            start = time.perf_counter()
            refreshed = browser.list_data_records({"rootPath": str(args.index_root), "refresh": True, "pageSize": 200})
            refresh.append((time.perf_counter() - start) * 1000)
            if fingerprint({k: v for k, v in refreshed.items() if k != "indexedAtMs"}) != result["identical_output_sha256_with_float_bits"]:
                raise AssertionError("Unchanged refresh changed index results")
        report["index"]["unchanged_refresh_median_ms"] = statistics.median(refresh)
        save()
    report["source_files_unchanged"] = all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    if not report["source_files_unchanged"]:
        raise AssertionError("Source workbook changed during benchmark")
    save()
    print(f"Saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
