from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tauri_bridge.__main__ import _read_request_lines, _write_response
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.protocol import PROTOCOL_VERSION
from tauri_bridge.service import BACKGROUND_FILE_TASK_METHODS, BridgeService
from tauri_bridge.settings import JsonSettings


REPO_ROOT = Path(__file__).resolve().parents[1]


class JsonSettingsMigrationTests(unittest.TestCase):
    def test_legacy_report_preferences_are_migrated_without_overwriting_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = (
                Path(temp_dir)
                / "Changguang Huaxin"
                / "Pump Driver Integrated Test"
                / "settings.json"
            )
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(
                json.dumps({"shipping_report/pole/manual_values": '{"coolingTemperature":"20"}'}),
                encoding="utf-8",
            )
            legacy_values = {
                "shipping_report/pole/selected_fields": '["coolingTemperature","power"]',
                "shipping_report/pole/manual_values": '{"coolingTemperature":"25"}',
                "shipping_report/spectrum/axis_mode": "counts",
            }

            with (
                patch.dict(os.environ, {"LOCALAPPDATA": temp_dir, "ARP_SETTINGS_PATH": ""}),
                patch("tauri_bridge.settings._legacy_shipping_report_settings", return_value=legacy_values),
            ):
                settings = JsonSettings()

            self.assertEqual(
                settings.get("shipping_report/pole/selected_fields"),
                '["coolingTemperature","power"]',
            )
            self.assertEqual(
                settings.get("shipping_report/pole/manual_values"),
                '{"coolingTemperature":"20"}',
            )
            self.assertEqual(settings.get("shipping_report/spectrum/axis_mode"), "counts")


class _FakePowerController:
    def __init__(self, *, measured_current_a: float = 0.0) -> None:
        self.is_connected = True
        self.output_enabled = False
        self.measured_current_a = measured_current_a
        self.current_writes: list[float] = []

    def set_output_current(self, current_a: float) -> None:
        self.current_writes.append(float(current_a))

    def read_output_current(self) -> float:
        return self.measured_current_a

    def set_output_enabled(self, enabled: bool) -> None:
        self.output_enabled = bool(enabled)

    def disconnect_device(self) -> bool:
        self.is_connected = False
        return True


class HeadlessBackendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        settings_path = Path(self.temporary_directory.name) / "settings.json"
        self.backend = HeadlessBackend(JsonSettings(settings_path))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_spectrum_snapshot_keeps_every_native_sample(self) -> None:
        self.backend.latest_spectrum = SimpleNamespace(
            wavelength=[974.0, 974.5, 975.0, 975.5, 976.0],
            intensity=[1.0, 4.0, 9.0, 4.0, 1.0],
            saturation=None,
            stats=SimpleNamespace(
                peak_wavelength_nm=975.0,
                centroid_nm=975.0,
                fwhm_nm=1.0,
            ),
            smsr=None,
            peak_annotations=(),
        )

        snapshot = self.backend.snapshot({"view": "manual"})

        self.assertEqual(
            snapshot["measurements"]["spectrum"]["wavelengthNm"],
            [974.0, 974.5, 975.0, 975.5, 976.0],
        )
        self.assertEqual(
            snapshot["measurements"]["spectrum"]["intensity"],
            [1.0, 4.0, 9.0, 4.0, 1.0],
        )

    def test_view_scopes_large_measurement_payloads(self) -> None:
        self.backend.power_points.append({"elapsedS": 0.1, "powerW": 3.2})
        self.backend.pd_state["points"] = [{"elapsedS": 0.1, "value": 0.5}]

        automatic = self.backend.snapshot({"view": "automatic"})
        pd = self.backend.snapshot({"view": "pd"})
        shipping = self.backend.snapshot({"view": "shipping"})

        self.assertIn("power", automatic["measurements"])
        self.assertNotIn("points", automatic["pd"])
        self.assertEqual(pd["pd"]["points"], [{"elapsedS": 0.1, "value": 0.5}])
        self.assertEqual(pd["measurements"], {})
        self.assertEqual(shipping["measurements"], {})
        self.assertNotIn("points", shipping["pd"])

    def test_ch341_rejects_current_above_twenty_amperes(self) -> None:
        controller = _FakePowerController()
        self.backend.power_supply_controller = controller
        self.backend.power_supply_kind = "ch341"

        with self.assertRaisesRegex(ValueError, "CH341 最大电流不能超过 20 A"):
            self.backend._set_current({"currentA": 20.1})

        self.assertEqual(controller.current_writes, [])

    def test_tdk_keeps_the_existing_higher_current_range(self) -> None:
        controller = _FakePowerController()
        self.backend.power_supply_controller = controller
        self.backend.power_supply_kind = "tdk"

        self.backend._set_current({"currentA": 25.0})

        self.assertEqual(controller.current_writes, [25.0])
        self.assertEqual(self.backend.active_current_a, 25.0)

    def test_shutdown_requires_a_confirmed_zero_current(self) -> None:
        controller = _FakePowerController(measured_current_a=0.5)
        self.backend.power_supply_controller = controller
        self.backend.power_supply_kind = "tdk"

        with self.assertRaisesRegex(RuntimeError, "无法确认电源输出电流已归零"):
            self.backend._shutdown()

        self.assertTrue(self.backend.automatic_output_shutdown_unconfirmed)

    def test_safe_shutdown_returns_explicit_confirmation(self) -> None:
        controller = _FakePowerController(measured_current_a=0.0)
        self.backend.power_supply_controller = controller
        self.backend.power_supply_kind = "tdk"

        self.assertEqual(self.backend._shutdown(), {"shutdownConfirmed": True})
        self.assertEqual(controller.current_writes[-1], 0.0)


class TauriBridgeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = BridgeService()

    def test_file_tasks_are_owned_by_the_shared_service_contract(self) -> None:
        self.assertEqual(
            BACKGROUND_FILE_TASK_METHODS,
            frozenset(
                {
                    "automatic.references",
                    "shippingReport.inspect",
                    "shippingReport.batchInspect",
                }
            ),
        )

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
        with (
            patch("tauri_bridge.__main__._protocol_stdout", None),
            patch("tauri_bridge.__main__.sys.stdout", output),
        ):
            _write_response({"label": "CH341 I²C"})

        self.assertEqual(
            output.buffer.getvalue().decode("utf-8"),
            '{"label":"CH341 I²C"}\n',
        )

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
            all(device["state"] == "disconnected" for device in snapshot["devices"].values())
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

    def test_read_only_shutdown_is_immediately_confirmed(self) -> None:
        response = self.service.handle_line(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
                    "id": "shutdown-read-only-1",
                    "method": "app.shutdown",
                    "params": {},
                }
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"], {"shutdownConfirmed": True})

    def test_injected_backend_owns_snapshot_and_mutating_commands(self) -> None:
        class FakeBackend:
            def __init__(self) -> None:
                self.calls = []

            def dispatch(self, method, params):
                self.calls.append((method, params))
                return {"value": params["value"]}

        backend = FakeBackend()
        response = BridgeService(backend).handle_line(
            json.dumps(
                {
                    "v": PROTOCOL_VERSION,
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
                    "params": {"view": "manual"},
                }
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"], {"view": "manual"})
        self.assertEqual(backend.params, {"view": "manual"})

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

    def test_cli_round_trip_uses_the_headless_backend(self) -> None:
        request = json.dumps(
            {
                "v": PROTOCOL_VERSION,
                "id": "ping-1",
                "method": "system.ping",
                "params": {},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            environment = {"ARP_SETTINGS_PATH": str(Path(temp_dir) / "settings.json")}
            completed = subprocess.run(
                [sys.executable, "-m", "tauri_bridge"],
                cwd=REPO_ROOT,
                env={**os.environ, **environment},
                input=request + "\n",
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )

        response = json.loads(completed.stdout.strip())
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["status"], "ok")
        self.assertEqual(response["result"]["mode"], "active")


if __name__ == "__main__":
    unittest.main()
