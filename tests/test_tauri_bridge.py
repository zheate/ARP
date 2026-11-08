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

from combined_test.automation import AutomaticTestState
from tauri_bridge.__main__ import _read_request_lines, _write_response
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.protocol import PROTOCOL_VERSION
from tauri_bridge.service import BACKGROUND_FILE_TASK_METHODS, SAFETY_TASK_METHODS, BridgeService
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

    def test_runtime_identity_distinguishes_restart_from_older_series_revisions(self) -> None:
        first = self.backend.snapshot({"view": "automatic"})["backend"]["runtimeId"]
        self.assertTrue(first)
        self.assertEqual(first, self.backend.snapshot({"view": "pd"})["backend"]["runtimeId"])
        restarted = HeadlessBackend(JsonSettings(Path(self.temporary_directory.name) / "restarted.json"))
        try:
            self.assertNotEqual(first, restarted.snapshot()["backend"]["runtimeId"])
        finally:
            restarted.export_worker.graceful_stop(2)

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

    def test_live_snapshot_only_sends_new_power_points(self) -> None:
        self.backend.power_points.extend(
            [
                {"elapsedS": 0.1, "powerW": 1.0},
                {"elapsedS": 0.2, "powerW": 2.0},
            ]
        )
        self.backend.runtime.series_revisions["power"] = 2
        initial = self.backend.snapshot({"view": "automatic"})

        self.backend.power_points.append({"elapsedS": 0.3, "powerW": 3.0})
        self.backend.runtime.series_revisions["power"] += 1
        incremental = self.backend.snapshot(
            {
                "view": "automatic",
                "since": initial["seriesRevisions"],
                "cursors": {"power": 0.2},
            }
        )

        self.assertNotIn("power", incremental["measurements"])
        self.assertEqual(
            incremental["seriesPatches"]["power"],
            {
                "startX": 0.1,
                "points": [{"elapsedS": 0.3, "powerW": 3.0}],
            },
        )
        self.assertNotIn("stable", incremental["measurements"])
        self.assertNotIn("spectrum", incremental["measurements"])

        unchanged = self.backend.snapshot(
            {
                "view": "automatic",
                "since": incremental["seriesRevisions"],
                "cursors": {"power": 0.3},
            }
        )
        self.assertEqual(unchanged["measurements"], {})
        self.assertNotIn("seriesPatches", unchanged)

    def test_live_snapshot_falls_back_to_full_power_series_for_stale_cursor(self) -> None:
        self.backend.power_points.extend(
            [
                {"elapsedS": 1.0, "powerW": 1.0},
                {"elapsedS": 1.1, "powerW": 2.0},
            ]
        )
        self.backend.runtime.series_revisions["power"] = 2

        snapshot = self.backend.snapshot(
            {
                "view": "automatic",
                "since": {"power": 1, "stable": 0, "spectrum": 0, "pd": 0},
                "cursors": {"power": 0.9},
            }
        )

        self.assertEqual(snapshot["measurements"]["power"], list(self.backend.power_points))
        self.assertNotIn("seriesPatches", snapshot)

    def test_live_snapshot_only_sends_aging_power_when_it_changes(self) -> None:
        station = self.backend.runtime
        station.aging_power_points.append({"elapsedMinutes": 5.0, "powerW": 1.2})
        station.series_revisions["aging"] = 1
        initial = self.backend.snapshot({"view": "automatic"})

        station.aging_power_points.append({"elapsedMinutes": 10.0, "powerW": 1.18})
        station.series_revisions["aging"] += 1
        incremental = self.backend.snapshot(
            {"view": "automatic", "since": initial["seriesRevisions"]}
        )

        self.assertEqual(
            incremental["measurements"]["agingPower"],
            [
                {"elapsedMinutes": 5.0, "powerW": 1.2},
                {"elapsedMinutes": 10.0, "powerW": 1.18},
            ],
        )
        self.assertNotIn("power", incremental["measurements"])
        self.assertNotIn("stable", incremental["measurements"])
        self.assertNotIn("spectrum", incremental["measurements"])

        unchanged = self.backend.snapshot(
            {"view": "automatic", "since": incremental["seriesRevisions"]}
        )
        self.assertEqual(unchanged["measurements"], {})

    def test_incremental_spectrum_reuses_unchanged_wavelength_grid(self) -> None:
        self.backend.latest_spectrum = SimpleNamespace(
            wavelength=[974.0, 974.5, 975.0],
            intensity=[1.0, 4.0, 1.0],
            saturation=None,
            stats=SimpleNamespace(
                peak_wavelength_nm=974.5,
                centroid_nm=974.5,
                fwhm_nm=0.5,
            ),
            smsr=None,
            peak_annotations=(),
        )
        self.backend.runtime.series_revisions["spectrum"] = 1
        initial = self.backend.snapshot({"view": "automatic"})

        self.backend.latest_spectrum.intensity = [2.0, 8.0, 2.0]
        self.backend.runtime.series_revisions["spectrum"] += 1
        incremental = self.backend.snapshot(
            {"view": "automatic", "since": initial["seriesRevisions"]}
        )

        spectrum = incremental["measurements"]["spectrum"]
        self.assertNotIn("wavelengthNm", spectrum)
        self.assertEqual(spectrum["intensity"], [2.0, 8.0, 2.0])
        self.assertEqual(
            spectrum["gridRevision"],
            initial["measurements"]["spectrum"]["gridRevision"],
        )

        self.backend.latest_spectrum.wavelength = [975.0, 975.5, 976.0]
        self.backend.runtime.series_revisions["spectrum"] += 1
        changed_grid = self.backend.snapshot(
            {"view": "automatic", "since": incremental["seriesRevisions"]}
        )
        self.assertEqual(
            changed_grid["measurements"]["spectrum"]["wavelengthNm"],
            [975.0, 975.5, 976.0],
        )

    def test_pd_snapshot_only_sends_new_points(self) -> None:
        self.backend.pd_state["points"] = [
            {"elapsedS": 0.1, "value": 0.5},
            {"elapsedS": 0.2, "value": 0.6},
        ]
        self.backend.series_revisions["pd"] = 1
        initial = self.backend.snapshot({"view": "pd"})

        self.backend.pd_state["points"].append({"elapsedS": 0.3, "value": 0.7})
        self.backend.series_revisions["pd"] += 1
        incremental = self.backend.snapshot(
            {
                "view": "pd",
                "since": initial["seriesRevisions"],
                "cursors": {"pd": 0.2},
            }
        )

        self.assertNotIn("points", incremental["pd"])
        self.assertEqual(
            incremental["seriesPatches"]["pd"],
            {
                "startX": 0.1,
                "points": [{"elapsedS": 0.3, "value": 0.7}],
            },
        )

    def test_ch341_rejects_current_above_twenty_amperes(self) -> None:
        controller = _FakePowerController()
        self.backend.power_supply_controller = controller
        self.backend.power_supply_kind = "ch341"

        with self.assertRaisesRegex(ValueError, "CH341 最大电流不能超过 20 A"):
            self.backend.dispatcher._set_current({"currentA": 20.1})

        self.assertEqual(controller.current_writes, [])

    def test_tdk_keeps_the_existing_higher_current_range(self) -> None:
        controller = _FakePowerController()
        self.backend.power_supply_controller = controller
        self.backend.power_supply_kind = "tdk"

        self.backend.dispatcher._set_current({"currentA": 25.0})

        self.assertEqual(controller.current_writes, [25.0])
        self.assertEqual(self.backend.active_current_a, 25.0)

    def test_shutdown_requires_a_confirmed_zero_current(self) -> None:
        controller = _FakePowerController(measured_current_a=0.5)
        self.backend.power_supply_controller = controller
        self.backend.power_supply_kind = "tdk"

        result = self.backend._shutdown()

        self.assertFalse(result["shutdownConfirmed"])
        self.assertTrue(result["exportsDrained"])
        self.assertFalse(result["shutdown"]["confirmed"])
        self.assertEqual(result["shutdown"]["measuredCurrentA"], 0.5)
        self.assertIn("无法确认电源输出电流已归零", result["shutdown"]["reason"])

    def test_safe_shutdown_returns_explicit_confirmation(self) -> None:
        controller = _FakePowerController(measured_current_a=0.0)
        self.backend.power_supply_controller = controller
        self.backend.power_supply_kind = "tdk"

        result = self.backend._shutdown()
        self.assertTrue(result["shutdownConfirmed"])
        self.assertTrue(result["exportsDrained"])
        self.assertTrue(result["shutdown"]["outputOffConfirmed"])
        self.assertEqual(result["shutdown"]["measuredCurrentA"], 0.0)
        self.assertEqual(controller.current_writes[-1], 0.0)

    def test_automatic_failure_uses_unconfirmed_terminal_outcome_when_shutdown_fails(self) -> None:
        controller = _FakePowerController(measured_current_a=0.5)
        station = self.backend.runtime
        station.power_supply_controller = controller
        station.power_supply_kind = "tdk"
        station.automatic.state = AutomaticTestState.STARTING

        station._run_automatic()

        self.assertEqual(station.automatic_terminal_outcome, "shutdown_unconfirmed")
        self.assertFalse(station.shutdown_snapshot()["confirmed"])
        self.assertEqual(station.automatic.state, AutomaticTestState.COMPLETED)


class TauriBridgeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = BridgeService()

    def test_file_tasks_are_owned_by_the_shared_service_contract(self) -> None:
        self.assertEqual(
            BACKGROUND_FILE_TASK_METHODS,
            frozenset(
                {
                    "dataBrowser.list",
                    "dataBrowser.inspect",
                    "dataBrowser.import",
                    "automatic.exportDiagnostics",
                    "shippingReport.inspect",
                    "shippingReport.batchInspect",
                    "shippingReport.preview",
                    "shippingReport.generate",
                    "shippingReport.batchGenerateItem",
                }
            ),
        )
        self.assertEqual(
            SAFETY_TASK_METHODS,
            frozenset({"app.stopAll", "app.shutdown", "automatic.end", "powerSupply.safeRampDown"}),
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
        self.assertTrue(response["result"]["shutdownConfirmed"])
        self.assertTrue(response["result"]["exportsDrained"])
        self.assertTrue(response["result"]["shutdown"]["confirmed"])
        self.assertNotIn("stations", response["result"])

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
