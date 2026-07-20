from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from tauri_bridge.protocol import PROTOCOL_VERSION
from tauri_bridge.legacy_backend import LegacyWindowBackend, _downsample_spectrum
from tauri_bridge.__main__ import _read_request_lines, _write_response
from tauri_bridge.service import BridgeService


REPO_ROOT = Path(__file__).resolve().parent.parent


class SpectrumPayloadTests(unittest.TestCase):
    def test_downsampling_bounds_payload_and_preserves_narrow_peak(self) -> None:
        wavelength = [950.0 + index * 0.01 for index in range(2400)]
        intensity = [100.0 for _index in range(2400)]
        intensity[1234] = 16000.0

        chart_wavelength, chart_intensity = _downsample_spectrum(
            wavelength, intensity, limit=800
        )

        self.assertLessEqual(len(chart_wavelength), 800)
        self.assertEqual(len(chart_wavelength), len(chart_intensity))
        self.assertEqual(chart_wavelength[0], wavelength[0])
        self.assertEqual(chart_wavelength[-1], wavelength[-1])
        self.assertIn(16000.0, chart_intensity)

    def test_short_spectrum_is_not_resampled(self) -> None:
        wavelength = [975.0, 976.0, 977.0]
        intensity = [10.0, 100.0, 12.0]

        self.assertEqual(
            _downsample_spectrum(wavelength, intensity),
            (wavelength, intensity),
        )

    def test_same_acquired_frame_reuses_spectrum_analysis(self) -> None:
        backend = object.__new__(LegacyWindowBackend)
        backend._spectrum_cache_source = None
        backend._spectrum_cache_payload = ([], [], None, [])
        wavelength = [975.0, 976.0, 977.0]
        intensity = [10.0, 100.0, 12.0]
        annotation = SimpleNamespace(
            label="主峰",
            centroid_nm=976.0,
            peak_wavelength_nm=976.0,
            peak_intensity=100.0,
        )

        with (
            patch("tauri_bridge.legacy_backend.calculate_smsr") as calculate,
            patch(
                "tauri_bridge.legacy_backend.find_spectrum_peak_annotations",
                return_value=[annotation],
            ) as find_peaks,
        ):
            calculate.return_value = SimpleNamespace(smsr_db=32.0)
            first = backend._spectrum_snapshot(wavelength, intensity, False)
            second = backend._spectrum_snapshot(wavelength, intensity, False)

        self.assertIs(first, second)
        self.assertEqual(first[2], 32.0)
        calculate.assert_called_once()
        find_peaks.assert_called_once()


class HistoryPayloadCacheTests(unittest.TestCase):
    def test_unchanged_archive_reuses_history_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "index.sqlite3"
            database_path.touch()
            backend = object.__new__(LegacyWindowBackend)
            backend.window = SimpleNamespace(
                _archive_for_history=lambda: SimpleNamespace(database_path=database_path)
            )
            backend.history_filters = {}
            backend.selected_history_session_id = ""
            backend.comparison_session_ids = []
            backend._history_cache_key = None
            backend._history_cache_payload = ([], {}, None, [], [])

            with (
                patch.object(backend, "_history", return_value=([{"sessionId": "1"}], {"sessions": 1})) as history,
                patch.object(backend, "_history_detail", return_value=(None, [])) as detail,
                patch.object(backend, "_comparison", return_value=[]) as comparison,
            ):
                first = backend._history_snapshot()
                second = backend._history_snapshot()

            self.assertIs(first, second)
            history.assert_called_once()
            detail.assert_called_once()
            comparison.assert_called_once()


class TauriBridgeServiceTests(unittest.TestCase):
    def test_request_pipe_is_decoded_as_utf8_on_windows(self) -> None:
        class BinaryStdin:
            def __init__(self) -> None:
                self.buffer = io.BytesIO("中文测试站一号\n".encode("utf-8"))

        with patch("tauri_bridge.__main__.sys.stdin", BinaryStdin()):
            self.assertEqual(list(_read_request_lines()), ["中文测试站一号\n"])

    def test_protocol_response_is_written_as_utf8(self) -> None:
        class BinaryStdout:
            def __init__(self) -> None:
                self.buffer = io.BytesIO()

        output = BinaryStdout()
        with patch("tauri_bridge.__main__.sys.stdout", output):
            _write_response({"label": "CH341 I²C"})

        self.assertEqual(
            output.buffer.getvalue().decode("utf-8"),
            '{"label": "CH341 I²C"}\n',
        )

    def setUp(self) -> None:
        self.service = BridgeService()

    def test_snapshot_is_read_only_and_does_not_claim_devices_are_connected(self) -> None:
        response = self.service.handle_line(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "id": "snapshot-1",
                    "method": "app.snapshot",
                    "params": {},
                }
            )
        )

        self.assertTrue(response["ok"])
        snapshot = response["result"]
        self.assertTrue(snapshot["backend"]["connected"])
        self.assertEqual(snapshot["backend"]["mode"], "read_only")
        self.assertFalse(snapshot["safety"]["hardwareAccess"])
        self.assertFalse(snapshot["automaticTest"]["controlsEnabled"])
        self.assertTrue(
            all(
                device["state"] == "disconnected"
                for device in snapshot["devices"].values()
            )
        )

    def test_unknown_method_returns_structured_error(self) -> None:
        response = self.service.handle_line(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "id": "unknown-1",
                    "method": "devices.connect",
                    "params": {},
                }
            )
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "method_not_found")

    def test_injected_backend_owns_snapshot_and_mutating_commands(self) -> None:
        class FakeBackend:
            def __init__(self) -> None:
                self.calls = []
                self.snapshot_params = None

            def ping(self):
                return {"status": "ok", "mode": "active"}

            def snapshot(self, params):
                self.snapshot_params = params
                return {"backend": {"mode": "active"}, "value": 1}

            def dispatch(self, method, params):
                self.calls.append((method, params))
                return {"backend": {"mode": "active"}, "value": params["value"]}

        backend = FakeBackend()
        service = BridgeService(backend)
        response = service.handle_line(
            json.dumps(
                {
                    "v": 1,
                    "id": "request-3",
                    "method": "app.configure",
                    "params": {"value": 7},
                }
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["value"], 7)
        self.assertEqual(backend.calls, [("app.configure", {"value": 7})])

    def test_snapshot_view_is_forwarded_to_active_backend(self) -> None:
        class FakeBackend:
            def __init__(self) -> None:
                self.params = None

            def snapshot(self, params):
                self.params = params
                return {"view": params.get("view")}

        backend = FakeBackend()
        response = BridgeService(backend).handle_line(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "id": "snapshot-view-1",
                    "method": "app.snapshot",
                    "params": {"view": "records"},
                }
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"], {"view": "records"})
        self.assertEqual(backend.params, {"view": "records"})

    def test_snapshot_rejects_non_object_params(self) -> None:
        response = self.service.handle_line(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "id": "snapshot-invalid-params",
                    "method": "app.snapshot",
                    "params": [],
                }
            )
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_params")

    def test_cli_round_trip_returns_one_json_line(self) -> None:
        request = json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "id": "ping-1",
                "method": "system.ping",
                "params": {},
            }
        )
        completed = subprocess.run(
            [sys.executable, "-m", "tauri_bridge"],
            cwd=REPO_ROOT,
            input=request + "\n",
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )

        response = json.loads(completed.stdout.strip())
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["status"], "ok")
        self.assertIn(response["result"]["mode"], {"active", "read_only"})


class LegacyWindowBackendCurrentLimitTests(unittest.TestCase):
    @staticmethod
    def make_backend(kind: str):
        class CurrentSpin:
            value = None

            def setValue(self, value):
                self.value = value

        class Window:
            def __init__(self):
                self.set_current_spin = CurrentSpin()
                self.applied = False

            def _selected_power_supply_kind(self):
                return kind

            def apply_output_current(self):
                self.applied = True

        backend = object.__new__(LegacyWindowBackend)
        backend.window = Window()
        return backend

    def test_ch341_rejects_current_above_20_a(self) -> None:
        backend = self.make_backend("ch341")

        with self.assertRaisesRegex(ValueError, "CH341 最大电流不能超过 20 A"):
            backend._set_current({"currentA": 20.1})

        self.assertIsNone(backend.window.set_current_spin.value)
        self.assertFalse(backend.window.applied)

    def test_tdk_keeps_existing_higher_current_range(self) -> None:
        backend = self.make_backend("tdk")

        backend._set_current({"currentA": 25.0})

        self.assertEqual(backend.window.set_current_spin.value, 25.0)
        self.assertTrue(backend.window.applied)


if __name__ == "__main__":
    unittest.main()
