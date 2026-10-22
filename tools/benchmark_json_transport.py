"""Hardware-free JSON encoding and pipe-write benchmark of the sidecar writer."""
from __future__ import annotations

import argparse
import ast
from array import array
import hashlib
import json
import os
import platform
import random
import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from tauri_bridge import __main__ as sidecar
from tauri_bridge import json_codec


class Sink:
    def __init__(self):
        self.last = b""
        self.flushes = 0

    def write(self, payload):
        self.last = payload
        return len(payload)

    def flush(self):
        self.flushes += 1


def stats(samples):
    values = sorted(samples)
    return {"median_ms": values[len(values) // 2], "p95_ms": values[min(len(values) - 1, len(values) * 95 // 100)]}


def measure(action, iterations):
    for _ in range(3):
        action()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        action()
        samples.append((time.perf_counter_ns() - started) / 1e6)
    return stats(samples)


def fixtures(directory):
    rows = []
    for name in ("8192-live_full", "8192-live_delta", "8192-live_unchanged", "8192-pd_full", "8192-pd_delta"):
        rows.append((name, json.loads((directory / f"{name}.json").read_text())))
    rng = random.Random(20261015)
    dense = json.loads(json.dumps(rows[1][1]))
    dense["result"]["measurements"]["spectrum"]["intensity"] = [rng.uniform(-200, 65_000) for _ in range(8192)]
    rows.append(("8192-dense-live-delta", dense))
    # Same full-spectrum column-list shape returned by the data browser.
    large = {"v": 2, "id": "large-spectrum", "ok": True, "result": {"label": "完整光谱", "spectra": [
        {"currentA": current, "wavelengthNm": [950 + i * 60 / 8191 for i in range(8192)],
         "intensity": [rng.uniform(-200, 65_000) for _ in range(8192)]}
        for current in range(24)
    ]}}
    rows.append(("24x8192-full-spectra", large))
    return rows


def pipe_write(payload, iterations):
    read_fd, write_fd = os.pipe()
    received = 0
    digest = hashlib.sha256()

    def drain():
        nonlocal received
        with os.fdopen(read_fd, "rb", buffering=0) as source:
            while data := source.read(65_536):
                received += len(data)
                digest.update(data)

    thread = threading.Thread(target=drain, name="benchmark-json-pipe")
    thread.start()
    try:
        with os.fdopen(write_fd, "wb") as target:
            result = measure(lambda: (target.write(payload), target.flush()), iterations)
    finally:
        thread.join(timeout=5)
        assert not thread.is_alive()
    expected = hashlib.sha256()
    for _ in range(iterations + 3):
        expected.update(payload)
    assert received == len(payload) * (iterations + 3)
    assert digest.digest() == expected.digest()
    return result


def legacy_writer(source, output):
    tree = ast.parse(source.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_write_response")
    namespace = {"json": json, "_protocol_stdout": output,
                 "_protocol_stdout_lock": sidecar._protocol_stdout_lock}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["_write_response"]


def paired(actions, iterations, validate):
    samples = [[], []]
    for index in range(iterations + 3):
        for version in ([0, 1] if index % 2 == 0 else [1, 0]):
            started = time.perf_counter_ns()
            actions[version]()
            elapsed = (time.perf_counter_ns() - started) / 1e6
            if index >= 3:
                samples[version].append(elapsed)
            validate()
    return {"before": stats(samples[0]), "after": stats(samples[1])}


def compare_pipe(response, payload, iterations, old_writer):
    read_fd, write_fd = os.pipe()
    received = 0
    digest = hashlib.sha256()

    def drain():
        nonlocal received
        with os.fdopen(read_fd, "rb", buffering=0) as source:
            while data := source.read(65_536):
                received += len(data)
                digest.update(data)

    thread = threading.Thread(target=drain, name="benchmark-json-paired-pipe")
    thread.start()
    try:
        with os.fdopen(write_fd, "wb") as target:
            output = SimpleNamespace(buffer=target)
            sidecar._protocol_stdout = output
            old_writer.__globals__["_protocol_stdout"] = output
            result = paired([lambda: old_writer(response), lambda: sidecar._write_response(response)], iterations, lambda: None)
    finally:
        thread.join(timeout=5)
        assert not thread.is_alive()
    expected = hashlib.sha256()
    for _ in range(2 * (iterations + 3)):
        expected.update(payload)
    assert received == len(payload) * 2 * (iterations + 3)
    assert digest.digest() == expected.digest()
    return result


def compare(args):
    assert json_codec._orjson is not None, "install the pinned native encoder before comparing"
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    previous_stdout = sidecar._protocol_stdout
    try:
        for name, response in fixtures(args.fixtures):
            iterations = args.iterations if not name.startswith("24x") else max(5, args.iterations // 5)
            sink = Sink()
            output = SimpleNamespace(buffer=sink)
            sidecar._protocol_stdout = output
            old_writer = legacy_writer(args.baseline_source, output)
            expected = (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            row = {"scenario": name, "iterations_per_version": iterations,
                   "native_eligible": json_codec._has_bulk_spectrum(response) and json_codec._native_values_compatible(response),
                   "bytes": len(expected), "sha256": hashlib.sha256(expected).hexdigest()}

            def validate():
                assert sink.last == expected, name

            row["writer_to_sink"] = paired([lambda: old_writer(response), lambda: sidecar._write_response(response)], iterations, validate)
            row["writer_to_os_pipe"] = compare_pipe(response, expected, iterations, old_writer)
            row["all_wire_bytes_equal"] = True
            rows.append(row)
            print(json.dumps(row), flush=True)
    finally:
        sidecar._protocol_stdout = previous_stdout
    report = {"environment": {"platform": platform.platform(), "python": platform.python_version(),
                              "orjson": json_codec._orjson.__version__},
              "baseline_source_sha256": hashlib.sha256(args.baseline_source.read_bytes()).hexdigest(),
              "workload": "same-process alternating original/current production writers; fixed synthetic data; includes compatibility guard; OS pipe has a hash-checking drain thread; no devices or WebView",
              "results": rows}
    (args.output / "paired.json").write_text(json.dumps(report, indent=2) + "\n")


def precision_corpus(args):
    assert json_codec._orjson is not None, "native encoder required for the compatibility corpus"
    args.output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261015)
    digest = hashlib.sha256()
    total_values = 0
    for batch in range(64):
        values = [struct.unpack("<d", ((rng.getrandbits(1) << 63)
                  | (rng.randint(1010, 1075) << 52) | rng.getrandbits(52)).to_bytes(8, "little"))[0]
                  for _ in range(16384)]
        response = {"v": 2, "id": str(batch), "ok": True, "result": {
            "measurements": {"spectrum": {"gridRevision": 1, "intensity": values}}}}
        assert json_codec._has_bulk_spectrum(response) and json_codec._native_values_compatible(response)
        expected = (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        actual = json_codec.encode_response(response)
        assert actual == expected, batch
        decoded = json.loads(actual)["result"]["measurements"]["spectrum"]["intensity"]
        assert array("d", decoded).tobytes() == array("d", values).tobytes(), batch
        digest.update(actual)
        total_values += len(values)
    report = {"seed": 20261015, "batches": 64, "native_path_finite_doubles": total_values,
              "orjson": json_codec._orjson.__version__, "json_bytes_equal": True,
              "python_roundtrip_double_bits_equal": True, "combined_sha256": digest.hexdigest()}
    (args.output / "precision.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    sink = Sink()
    previous_stdout = sidecar._protocol_stdout
    rows = []
    try:
        sidecar._protocol_stdout = SimpleNamespace(buffer=sink)
        for name, response in fixtures(args.fixtures):
            iterations = args.iterations if not name.startswith("24x") else max(5, args.iterations // 5)
            dumps = lambda: json.dumps(response, ensure_ascii=False, separators=(",", ":"))
            text = dumps()
            payload = (text + "\n").encode("utf-8")
            sidecar._write_response(response)
            assert sink.last == payload
            row = {"scenario": name, "iterations": iterations, "bytes": len(payload),
                   "sha256": hashlib.sha256(payload).hexdigest(),
                   "json_dumps": measure(dumps, iterations),
                   "newline_text_copy": measure(lambda: text + "\n", iterations),
                   "utf8_encode": measure(lambda: text.encode("utf-8"), iterations),
                   "production_writer_to_sink": measure(lambda: sidecar._write_response(response), iterations),
                   "preencoded_pipe_write_flush": pipe_write(payload, iterations)}
            rows.append(row)
            print(name, json.dumps(row), flush=True)
    finally:
        sidecar._protocol_stdout = previous_stdout
    report = {"environment": {"platform": platform.platform(), "python": platform.python_version()},
              "workload": "fixed synthetic responses; actual production writer; os.pipe with a draining/hash-checking thread; no devices",
              "results": rows}
    (args.output / "baseline.json").write_text(json.dumps(report, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("output/performance-realtime-20261015/before"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument("--precision-corpus", action="store_true")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    if args.precision_corpus:
        precision_corpus(args)
    elif args.baseline_source:
        compare(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
