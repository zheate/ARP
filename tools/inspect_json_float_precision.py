"""Export original double bits and real protocol responses for Rust replay.

No backend is constructed, no hardware is opened, and workbooks are read only.
The serve fixture accepts app.snapshot requests and echoes one saved response
through the production Python protocol writer with the requested ID.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import struct
import sys
from pathlib import Path

from combined_test.data_browser import inspect_data_record
from tauri_bridge.json_codec import encode_response


def float_fields(value, pointer=""):
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"nonfinite fixture value at {pointer}")
        yield pointer, value
    elif isinstance(value, dict):
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from float_fields(child, pointer + "/" + escaped)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from float_fields(child, pointer + "/" + str(index))


def prepare(output):
    output.mkdir(parents=True, exist_ok=True)
    cases = []

    def save(name, result, source=None):
        response = {"v": 2, "id": name, "ok": True, "result": result}
        fields = list(float_fields(response))
        wire = encode_response(response)
        (output / f"{name}.input.json").write_bytes(wire)
        (output / f"{name}.pointers.json").write_text(json.dumps([path for path, _ in fields]) + "\n")
        (output / f"{name}.bits.bin").write_bytes(b"".join(struct.pack("<d", value) for _, value in fields))
        cases.append({"name": name, "source": source, "float_count": len(fields), "bytes": len(wire),
                      "wire_sha256": hashlib.sha256(wire).hexdigest()})

    for index, path in enumerate(sorted(Path("test_records").rglob("*.xlsx"))):
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        for full in (False, True):
            result = inspect_data_record({"path": str(path), "includeSpectra": full})
            save(f"real-{index}-{'full' if full else 'detail'}", result,
                 {"path": str(path), "sha256": before, "full_spectra": full})
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    rng = random.Random(20261015)
    values = [0.0, -0.0, 5e-324, -5e-324, sys.float_info.min, -sys.float_info.min,
              sys.float_info.max, -sys.float_info.max, 1e-7, 0.0001, 976.1, 65535.0]
    while len(values) < 100_000:
        value = struct.unpack("<d", rng.getrandbits(64).to_bytes(8, "little"))[0]
        if math.isfinite(value):
            values.append(value)
    save("finite-double-corpus", {"values": values})
    (output / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")
    # Frozen inputs for a separate parser-only timing test; not added to the
    # precision corpus, so real-data and stress-corpus counts stay independent.
    rng = random.Random(20261015)
    grid = [950 + i * 60 / 8191 for i in range(8192)]
    dense = {"measurements": {"spectrum": {"wavelengthNm": grid,
             "intensity": [rng.uniform(-200, 65_000) for _ in grid]}}}
    large = {"label": "完整光谱", "spectra": [
        {"currentA": current, "wavelengthNm": grid,
         "intensity": [rng.uniform(-200, 65_000) for _ in grid]}
        for current in range(24)]}
    for name, result in [("8192-dense-live-delta", dense), ("24x8192-full-spectra", large)]:
        (output / f"{name}.input.json").write_bytes(encode_response(
            {"v": 2, "id": name, "ok": True, "result": result}))
    print(json.dumps({"cases": len(cases), "float_fields": sum(case["float_count"] for case in cases)}))


def serve(path):
    from tauri_bridge import __main__ as sidecar

    response = json.loads(path.read_bytes())
    sidecar._reserve_protocol_stdout()
    for line in sidecar._read_request_lines():
        request = json.loads(line)
        assert request["method"] == "app.snapshot", "fixture accepts snapshots only"
        response["id"] = request["id"]
        sidecar._write_response(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve", type=Path)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve)
    elif args.output:
        prepare(args.output)
    else:
        parser.error("provide --output or --serve")


if __name__ == "__main__":
    main()
