"""Read-only spectrum browsing benchmark using generated XLSX fixtures, never devices.

Run from the repository root with the product Python environment. The fixture
folder is created only when missing and can be reused for a before/after run.
"""
from __future__ import annotations

import argparse
from array import array
import gc
import hashlib
import importlib.util
import json
import math
import os
import platform
from pathlib import Path
import resource
import shutil
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

from combined_test import data_browser as browser


def rss_mib() -> float:
    value = subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())], text=True)
    return int(value.strip()) / 1024


def fingerprint(detail: dict) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps({key: value for key, value in detail.items() if key != "spectra"}, sort_keys=True).encode())
    for spectrum in detail.get("spectra") or []:
        digest.update(json.dumps({key: value for key, value in spectrum.items() if key not in {"wavelengthNm", "intensity"}}, sort_keys=True).encode())
        for key in ("wavelengthNm", "intensity"):
            digest.update(array("d", spectrum[key]).tobytes())
    return digest.hexdigest()


def fixtures(root: Path, currents: int, pixels: int, count: int) -> list[Path]:
    import xlsxwriter
    root.mkdir(parents=True, exist_ok=True)
    source = root / f"spectrum-{currents}-{pixels}-00.xlsx"
    if not source.exists():
        with xlsxwriter.Workbook(source, {"constant_memory": True}) as workbook:
            sheet = workbook.add_worksheet("测试数据")
            labels = ["电流(A)", "电压(V)", "功率(W)", "电光效率", "中心波长(nm)", "质心波长(nm)", "FWHM(nm)", "PIB", "SMSR(dB)"]
            sheet.write_row(0, 0, labels)
            for current in range(currents):
                sheet.write(0, 10 + current * 2, f"{current + 1} A")
            for sample in range(pixels):
                if sample < currents:
                    current = sample + 1
                    sheet.write_row(sample + 1, 0, [current, 2 + current * .02, current * 1.5, .4, 976.2, 976.15, .5, .96, 35])
                wavelength = 970 + sample * 12 / (pixels - 1)
                for current in range(currents):
                    intensity = 50 + (10000 + current * 200) * math.exp(-(((wavelength - 976.2 - current * .005) / .4) ** 2))
                    sheet.write_row(sample + 1, 10 + current * 2, [wavelength, intensity])
            metadata = workbook.add_worksheet("会话信息")
            for row, pair in enumerate({"SN": "PERF-SYNTHETIC", "测试站别": "性能夹具", "流程状态": "completed", "出货报告格式版本": "1", "开始时间 UTC": "2026-10-15T01:00:00+00:00", "测试模式": "automatic"}.items()):
                metadata.write_row(row, 0, pair)
            metadata.hide()
    paths = [root / f"spectrum-{currents}-{pixels}-{index:02d}.xlsx" for index in range(count)]
    for path in paths[1:]:
        if not path.exists():
            shutil.copyfile(source, path)
    return paths


def run(root: Path, currents: int, pixels: int, count: int) -> dict:
    paths = fixtures(root, currents, pixels, count)
    original = browser._read_workbook
    reads: list[float] = []
    hashes: dict[str, str] = {}
    browser.clear_data_browser_cache()
    gc.collect()
    start_rss = rss_mib()
    rows = []

    def observed_read(*args, **kwargs):
        started = time.perf_counter()
        result = original(*args, **kwargs)
        reads.append((time.perf_counter() - started) * 1000)
        return result

    def inspect(path: Path, phase: str) -> None:
        read_start = len(reads)
        started = time.perf_counter()
        detail = browser.inspect_data_record({"path": str(path), "includeSpectra": True})
        elapsed = (time.perf_counter() - started) * 1000
        signature = fingerprint(detail)
        if path.name in hashes:
            assert hashes[path.name] == signature, "Revisiting changed the returned spectrum"
        hashes[path.name] = signature
        assert len(detail["spectra"]) == currents
        assert all(len(s["intensity"]) == pixels and len(s["wavelengthNm"]) == pixels for s in detail["spectra"])
        del detail
        gc.collect()
        cached_numbers = sum(len(s["intensity"]) + len(s["wavelengthNm"]) for value in browser._DETAILS.values() for s in value.get("spectra") or [])
        row = {"phase": phase, "file": path.name, "total_ms": elapsed, "parse_ms": sum(reads[read_start:]), "read_count": len(reads) - read_start,
               "rss_mib": rss_mib(), "cache_entries": len(browser._DETAILS), "cached_numbers": cached_numbers, "cache_owned_bytes": getattr(browser, "_DETAIL_CACHE_BYTES", None)}
        rows.append(row)
        print(json.dumps(row), flush=True)

    with patch.object(browser, "_read_workbook", side_effect=observed_read):
        for path in paths:
            inspect(path, "sequential")
        for _ in range(7):
            inspect(paths[-1], "warm")
        # Recent records should stay responsive while the longer browsing run is bounded.
        for path in paths[-5:]:
            inspect(path, "recent")
        for path in paths[:3]:
            inspect(path, "old_revisit")
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mib = peak_rss / (1024 ** 2 if sys.platform == "darwin" else 1024)
    browser.clear_data_browser_cache()
    gc.collect()
    return {"environment": {"python": platform.python_version(), "platform": platform.platform()},
            "workload": {"source": "synthetic XLSX copies; no user file modification; no hardware", "files": count, "currents": currents, "pixels": pixels, "xlsx_bytes_each": paths[0].stat().st_size},
            "start_rss_mib": start_rss, "peak_rss_mib": peak_mib, "after_clear_rss_mib": rss_mib(), "rows": rows, "hashes": hashes,
            "warm_median_ms": statistics.median(r["total_ms"] for r in rows if r["phase"] == "warm")}


def protocol_comparison(path: Path, baseline_source: Path) -> dict:
    """Interleave implementations, including the bridge's JSON wire encoding."""
    spec = importlib.util.spec_from_file_location("combined_test._browser_before", baseline_source)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    modules = {"before": baseline, "after": browser}
    rows = []
    signatures: set[str] = set()
    params = {"path": str(path), "includeSpectra": True}

    def measure(label: str, phase: str) -> None:
        module = modules[label]
        start = time.perf_counter()
        detail = module.inspect_data_record(params)
        inspected = time.perf_counter()
        # Same separators, Unicode handling and newline as _write_response.
        payload = (json.dumps({"v": 2, "id": "perf", "result": detail}, ensure_ascii=False,
                              separators=(",", ":")) + "\n").encode("utf-8")
        encoded = time.perf_counter()
        signatures.add(hashlib.sha256(payload).hexdigest())
        rows.append({"implementation": label, "phase": phase,
                     "inspect_ms": (inspected - start) * 1000,
                     "json_ms": (encoded - inspected) * 1000,
                     "total_ms": (encoded - start) * 1000, "bytes": len(payload)})

    for iteration in range(3):
        for label in (["before", "after"] if iteration % 2 == 0 else ["after", "before"]):
            modules[label].clear_data_browser_cache()
            measure(label, "cold")
    for iteration in range(9):
        for label in (["before", "after"] if iteration % 2 == 0 else ["after", "before"]):
            measure(label, "warm")
    assert len(signatures) == 1, "Public JSON wire response changed"
    return {"rows": rows, "wire_bytes_identical": True, "sha256": signatures.pop(),
            "limits": "Includes inspect and JSON encoding; excludes pipe I/O, Rust and WebView rendering"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--currents", type=int, default=24)
    parser.add_argument("--pixels", type=int, default=8192)
    parser.add_argument("--files", type=int, default=36)
    parser.add_argument("--protocol-baseline", type=Path, help="Compare against a saved data_browser.py instead of the continuous-memory workload")
    args = parser.parse_args()
    if args.currents < 1 or args.pixels < 2 or args.files < 1:
        parser.error("currents/files must be positive and pixels must be at least 2")
    if args.protocol_baseline:
        path = fixtures(args.fixtures.resolve(), args.currents, args.pixels, 1)[0]
        result = protocol_comparison(path, args.protocol_baseline)
    else:
        result = run(args.fixtures.resolve(), args.currents, args.pixels, args.files)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "peak_rss_mib": result.get("peak_rss_mib"), "warm_median_ms": result.get("warm_median_ms")}), flush=True)


if __name__ == "__main__":
    main()
