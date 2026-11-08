from __future__ import annotations

import importlib.util
import io
import json
import math
import random
import struct
import subprocess
import sys
import threading
import unittest
from collections import namedtuple
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from tauri_bridge import __main__ as sidecar
from tauri_bridge import json_codec


def spectrum_response(values=None):
    return {"v": 2, "id": "json-test", "ok": True, "result": {
        "label": "完整光谱 🌈", "measurements": {"spectrum": {
            "gridRevision": 1,
            "intensity": values if values is not None else [i * 0.125 for i in range(2048)],
        }},
    }}


def legacy_bytes(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class JsonTransportTests(unittest.TestCase):
    def assert_wire_equal(self, response):
        expected = legacy_bytes(response)
        self.assertEqual(json_codec.encode_response(response), expected)
        output = io.BytesIO()
        with patch.object(sidecar, "_protocol_stdout", SimpleNamespace(buffer=output)):
            sidecar._write_response(response)
        self.assertEqual(output.getvalue(), expected)

    @unittest.skipUnless(importlib.util.find_spec("orjson"), "optional native encoder is not installed")
    def test_bulk_spectrum_avoids_slow_python_float_formatting(self):
        response = spectrum_response()
        expected = legacy_bytes(response)
        output = io.BytesIO()
        with patch.object(sidecar, "_protocol_stdout", SimpleNamespace(buffer=output)), \
             patch.object(sidecar.json, "dumps", side_effect=AssertionError("bulk spectrum used slow float formatter")):
            sidecar._write_response(response)
        self.assertEqual(output.getvalue(), expected)

    def test_fixed_notation_boundaries_and_negative_zero_are_byte_exact(self):
        edges = [0.0, -0.0, 0.0001, -0.0001, math.nextafter(0.0001, math.inf),
                 math.nextafter(1e16, 0.0), -math.nextafter(1e16, 0.0)]
        response = spectrum_response(edges * 512)
        self.assert_wire_equal(response)
        decoded = json.loads(json_codec.encode_response(response))["result"]["measurements"]["spectrum"]["intensity"]
        self.assertEqual(b"".join(struct.pack("<d", value) for value in decoded),
                         b"".join(struct.pack("<d", value) for value in edges * 512))

    def test_seeded_fixed_notation_double_corpus_is_byte_exact(self):
        rng = random.Random(20261015)
        values = [struct.unpack("<d", ((rng.getrandbits(1) << 63)
                    | (rng.randint(1010, 1075) << 52) | rng.getrandbits(52)).to_bytes(8, "little"))[0]
                  for _ in range(16384)]
        self.assertTrue(json_codec._native_values_compatible(values))
        self.assert_wire_equal(spectrum_response(values))

    def test_scientific_nonfinite_and_extreme_doubles_retain_stdlib_behavior(self):
        values = [math.nextafter(0.0001, 0.0), 1e-7, 1e16, 5e-324,
                  sys.float_info.max, math.nan, math.inf, -math.inf]
        for value in values:
            with self.subTest(value=value):
                response = spectrum_response([value] + [1.0] * 2048)
                self.assertFalse(json_codec._native_values_compatible(response))
                self.assert_wire_equal(response)

    def test_full_spectrum_columns_extra_fields_and_input_ownership(self):
        response = {"v": 2, "id": "columns", "ok": True, "result": {"spectra": [
            {"currentA": 2.0, "wavelengthNm": [975.0] * 2048, "intensity": [0.25] * 2048},
            {"currentA": 3.0, "wavelengthNm": [976.0] * 2048, "intensity": [-0.0] * 2048},
        ], "metadata": {"extra": [None, False, True, 2**63, "完整数据"]}}}
        before = legacy_bytes(response)
        self.assert_wire_equal(response)
        self.assertEqual(legacy_bytes(response), before)

    def test_unicode_controls_keys_and_whitespace_are_byte_exact(self):
        response = spectrum_response()
        response["result"]["文字\n键"] = "中文 /\\\"\b\f\n\r\t\u2028\u2029\ufeff🚀" + "".join(chr(i) for i in range(256))
        self.assert_wire_equal(response)

    def test_small_control_and_pd_responses_keep_stdlib_path(self):
        for response in [
            {"v": 2, "id": "stop", "ok": True, "result": {"stopped": True}},
            spectrum_response([1.0] * 16),
            {"v": 2, "ok": True, "result": {"pd": {"points": [{"elapsedS": 1.0, "value": 2.0}] * 13000}}},
        ]:
            self.assertFalse(json_codec._has_bulk_spectrum(response))
            self.assert_wire_equal(response)
        mixed = spectrum_response()
        mixed["result"]["pd"] = {"points": [{"elapsedS": 1.0, "value": 2.0}]}
        self.assertFalse(json_codec._has_bulk_spectrum(mixed))
        self.assert_wire_equal(mixed)

    def test_nonstring_keys_shared_containers_and_namedtuple_retain_stdlib_path(self):
        Point = namedtuple("Point", "x y")
        shared = [1.0, 2.0]
        for extra in [{1: "int", None: "none", False: "bool"}, [shared, shared], Point(1.0, 2.0)]:
            response = spectrum_response()
            response["result"]["extra"] = extra
            self.assertFalse(json_codec._native_values_compatible(response))
            self.assert_wire_equal(response)

    def test_large_integers_keep_arbitrary_precision(self):
        response = spectrum_response()
        response["result"]["integers"] = [2**64 - 1, 2**100, -2**100]
        self.assert_wire_equal(response)

    def test_deep_containers_keep_stdlib_nesting_limit(self):
        nested = "end"
        for _ in range(300):
            nested = [nested]
        response = spectrum_response()
        response["result"]["nested"] = nested
        self.assert_wire_equal(response)

    def test_invalid_values_keep_error_type_and_do_not_write_partial_lines(self):
        cycle = []
        cycle.append(cycle)
        for value in [cycle, Decimal("1.25"), datetime(2026, 10, 15), {1, 2}, "\ud800", b"bytes"]:
            response = spectrum_response()
            response["result"]["invalid"] = value
            try:
                legacy_bytes(response)
            except Exception as expected:
                output = io.BytesIO()
                with patch.object(sidecar, "_protocol_stdout", SimpleNamespace(buffer=output)):
                    with self.assertRaises(type(expected)) as actual:
                        sidecar._write_response(response)
                self.assertEqual(str(actual.exception), str(expected))
                self.assertEqual(output.getvalue(), b"")
            else:
                self.fail("invalid fixture unexpectedly encoded")

    def test_missing_native_library_keeps_original_writer(self):
        response = spectrum_response()
        with patch.object(json_codec, "_orjson", None):
            self.assert_wire_equal(response)
        script = "import sys; sys.modules['orjson'] = None; from tauri_bridge.json_codec import _orjson, encode_response; assert _orjson is None; sys.stdout.buffer.write(encode_response({'v':2,'ok':True}))"
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True, check=True)
        self.assertEqual(completed.stdout, legacy_bytes({"v": 2, "ok": True}))

    def test_text_only_stdout_keeps_unicode_and_single_newline(self):
        response = spectrum_response()
        output = io.StringIO()
        with patch.object(sidecar, "_protocol_stdout", output):
            sidecar._write_response(response)
        self.assertEqual(output.getvalue().encode("utf-8"), legacy_bytes(response))

    def test_concurrent_writers_keep_complete_lines_and_one_flush_each(self):
        class Output:
            def __init__(self):
                self.buffer = self
                self.parts = []
                self.flushes = 0

            def write(self, payload):
                self.parts.append(payload)

            def flush(self):
                self.flushes += 1

        responses = [spectrum_response() for _ in range(16)]
        for index, response in enumerate(responses):
            response["id"] = str(index)
        output = Output()
        barrier = threading.Barrier(len(responses))
        errors = []

        def write(response):
            try:
                barrier.wait(timeout=5)
                sidecar._write_response(response)
            except Exception as error:
                errors.append(error)

        with patch.object(sidecar, "_protocol_stdout", output):
            threads = [threading.Thread(target=write, args=(response,)) for response in responses]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(output.flushes, len(responses))
        self.assertCountEqual(output.parts, [legacy_bytes(response) for response in responses])

    def test_pipe_write_failures_are_not_swallowed(self):
        output = SimpleNamespace(write=lambda _: (_ for _ in ()).throw(BrokenPipeError("closed")),
                                 flush=lambda: self.fail("failed writes must not flush"))
        with patch.object(sidecar, "_protocol_stdout", SimpleNamespace(buffer=output)):
            with self.assertRaisesRegex(BrokenPipeError, "closed"):
                sidecar._write_response(spectrum_response())


if __name__ == "__main__":
    unittest.main()
