from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from tauri_bridge.protocol import PROTOCOL_VERSION
from tauri_bridge.legacy_backend import (
    LegacyWindowBackend,
    SPECTRUM_CHART_POINT_LIMIT,
    _downsample_pd_history,
    _downsample_spectrum,
    _paired_series,
)
from tauri_bridge.__main__ import _read_request_lines, _write_response
from tauri_bridge.service import BridgeService


REPO_ROOT = Path(__file__).resolve().parent.parent


class SpectrumPayloadTests(unittest.TestCase):
    def test_invalid_pair_is_removed_without_shifting_the_other_axis(self) -> None:
        wavelength, intensity = _paired_series(
            [975.0, float("nan"), 977.0],
            [10.0, 20.0, 30.0],
            limit=None,
        )

        self.assertEqual(wavelength, [975.0, 977.0])
        self.assertEqual(intensity, [10.0, 30.0])

    def test_downsampling_bounds_payload_and_preserves_narrow_peak(self) -> None:
        wavelength = [950.0 + index * 0.01 for index in range(8000)]
        intensity = [100.0 for _index in range(8000)]
        intensity[4234] = 16000.0

        chart_wavelength, chart_intensity = _downsample_spectrum(wavelength, intensity)

        self.assertLessEqual(len(chart_wavelength), SPECTRUM_CHART_POINT_LIMIT)
        self.assertEqual(len(chart_wavelength), len(chart_intensity))
        self.assertEqual(chart_wavelength[0], wavelength[0])
        self.assertEqual(chart_wavelength[-1], wavelength[-1])
        self.assertIn(16000.0, chart_intensity)

    def test_ocean_hr4_sized_spectrum_keeps_every_sample(self) -> None:
        wavelength = [700.0 + index * 0.1 for index in range(3648)]
        intensity = [float(index) for index in range(3648)]

        self.assertEqual(
            _downsample_spectrum(wavelength, intensity),
            (wavelength, intensity),
        )

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
        backend._spectrum_cache_payload = ([], [], None, [], None)
        wavelength = [975.0, 976.0, 977.0]
        intensity = [10.0, 100.0, 12.0]
        annotation = SimpleNamespace(
            label="主峰",
            centroid_nm=976.0,
            peak_wavelength_nm=976.0,
            peak_intensity=100.0,
        )

        with (
            patch("tauri_bridge.legacy_backend.calculate_stats") as calculate_frame_stats,
            patch("tauri_bridge.legacy_backend.calculate_smsr") as calculate,
            patch(
                "tauri_bridge.legacy_backend.find_spectrum_peak_annotations",
                return_value=[annotation],
            ) as find_peaks,
        ):
            calculate_frame_stats.return_value = SimpleNamespace(
                peak_wavelength_nm=976.0,
                centroid_nm=976.0,
                fwhm_nm=1.0,
            )
            calculate.return_value = SimpleNamespace(smsr_db=32.0)
            first = backend._spectrum_snapshot(wavelength, intensity, False)
            second = backend._spectrum_snapshot(wavelength, intensity, False)

        self.assertIs(first, second)
        self.assertEqual(first[2], 32.0)
        calculate_frame_stats.assert_called_once()
        calculate.assert_called_once()
        find_peaks.assert_called_once()

    def test_new_frame_revision_invalidates_reused_spectrum_buffers(self) -> None:
        backend = object.__new__(LegacyWindowBackend)
        backend._spectrum_cache_source = None
        backend._spectrum_cache_payload = ([], [], None, [], None)
        wavelength = [975.0, 976.0, 977.0]
        intensity = [60_000.0, 60_000.0, 60_000.0]

        with patch(
            "tauri_bridge.legacy_backend.find_spectrum_peak_annotations",
            return_value=[],
        ):
            saturated = backend._spectrum_snapshot(
                wavelength,
                intensity,
                True,
                frame_revision=1,
            )
            intensity[:] = [10.0, 100.0, 12.0]
            adjusted = backend._spectrum_snapshot(
                wavelength,
                intensity,
                False,
                frame_revision=2,
            )

        self.assertEqual(saturated[1], [60_000.0, 60_000.0, 60_000.0])
        self.assertEqual(adjusted[1], [10.0, 100.0, 12.0])
        self.assertIsNot(saturated, adjusted)

    def test_headless_window_smsr_is_not_recalculated_by_bridge(self) -> None:
        backend = object.__new__(LegacyWindowBackend)
        backend._spectrum_cache_source = None
        backend._spectrum_cache_payload = ([], [], None, [], None)
        wavelength = [975.0, 976.0, 977.0]
        intensity = [10.0, 100.0, 12.0]
        frame_stats = SimpleNamespace(
            peak_wavelength_nm=976.0,
            centroid_nm=976.1,
            fwhm_nm=1.0,
        )
        backend.window = SimpleNamespace(
            latest_spectrum_wavelength=wavelength,
            latest_spectrum_intensity=intensity,
            latest_spectrum_analysis_revision=7,
            latest_spectrum_stats=frame_stats,
            latest_spectrum_smsr_db=31.5,
            latest_spectrum_peak_annotations=[],
        )

        with (
            patch("tauri_bridge.legacy_backend.calculate_stats") as calculate_frame_stats,
            patch("tauri_bridge.legacy_backend.calculate_smsr") as calculate,
            patch(
                "tauri_bridge.legacy_backend.find_spectrum_peak_annotations",
                return_value=[],
            ) as find_peaks,
        ):
            result = backend._spectrum_snapshot(
                wavelength,
                intensity,
                False,
                frame_revision=7,
            )

        self.assertEqual(result[2], 31.5)
        self.assertIs(result[4], frame_stats)
        calculate_frame_stats.assert_not_called()
        calculate.assert_not_called()
        find_peaks.assert_not_called()

    def test_locked_center_only_scopes_chart_and_invalidates_cached_frame(self) -> None:
        backend = object.__new__(LegacyWindowBackend)
        backend._spectrum_cache_source = None
        backend._spectrum_cache_payload = ([], [], None, [], None)
        wavelength = [940.0, 960.0, 976.0, 992.0, 1010.0]
        intensity = [50.0, 60.0, 100.0, 70.0, 55.0]
        analyzed_points = []

        def capture_points(points, **_kwargs):
            analyzed_points.append(list(points))
            return []

        with patch(
            "tauri_bridge.legacy_backend.find_spectrum_peak_annotations",
            side_effect=capture_points,
        ) as find_peaks:
            backend._spectrum_snapshot(wavelength, intensity, False)
            backend._spectrum_snapshot(wavelength, intensity, False)
            locked_result = backend._spectrum_snapshot(wavelength, intensity, False, 976.0)

        self.assertEqual(find_peaks.call_count, 2)
        self.assertEqual(analyzed_points[0], list(zip(wavelength, intensity)))
        self.assertEqual(analyzed_points[1], list(zip(wavelength, intensity)))
        self.assertEqual(locked_result[:2], ([960.0, 976.0, 992.0], [60.0, 100.0, 70.0]))


class PdPayloadTests(unittest.TestCase):
    def test_downsampling_keeps_the_complete_pd_time_window_and_narrow_spike(self) -> None:
        times = [index / 200.0 for index in range(2400)]
        values = [0.01 for _index in range(2400)]
        values[1234] = 0.5

        points = _downsample_pd_history(times, values)

        self.assertLessEqual(len(points), 4 * 120)
        self.assertEqual(points[0], {"elapsedS": 0.0, "value": 0.01})
        self.assertEqual(points[-1], {"elapsedS": 11.995, "value": 0.01})
        self.assertIn({"elapsedS": 6.17, "value": 0.5}, points)

    def test_snapshot_reuses_the_compacted_pd_history(self) -> None:
        backend = object.__new__(LegacyWindowBackend)
        backend._pd_cache_key = None
        backend._pd_cache_payload = []
        backend._series_revisions = {"power": 0, "stable": 0, "spectrum": 0, "pd": 0}
        panel = SimpleNamespace(
            plot_times=[index / 200.0 for index in range(2400)],
            plot_values=[float(index) for index in range(2400)],
            _plot_revision=1,
        )

        points = backend._pd_snapshot(panel)
        cached_points = backend._pd_snapshot(panel)

        self.assertIs(cached_points, points)
        self.assertLessEqual(len(points), 4 * 120)
        self.assertEqual(points[0], {"elapsedS": 0.0, "value": 0.0})
        self.assertEqual(points[-1], {"elapsedS": 11.995, "value": 2399.0})


class HeadlessWindowTests(unittest.TestCase):
    def test_custom_current_plan_round_trips_through_tauri_configuration(self) -> None:
        script = """
import json
from PySide6.QtWidgets import QApplication
from combined_test.window import MainWindow
from tauri_bridge.legacy_backend import LegacyWindowBackend

app = QApplication.instance() or QApplication([])
window = MainWindow(headless=True)
backend = LegacyWindowBackend(window)
backend._configure({
    "currentPlanMode": "custom",
    "customCurrentPoints": [
        {"currentA": 3, "dwellS": 300},
        {"currentA": 5, "dwellS": 120},
    ],
})
snapshot = backend.snapshot({"view": "automatic"})
settings = window.collect_automatic_test_settings()
print(json.dumps({
    "mode": snapshot["configuration"]["currentPlanMode"],
    "points": snapshot["configuration"]["customCurrentPoints"],
    "currents": list(window.automatic_orchestrator.currents),
    "configuredCurrents": [point.current_a for point in settings.custom_current_points],
    "configuredDwells": [point.dwell_s for point in settings.custom_current_points],
}))
window.close()
"""
        environment = dict(os.environ)
        environment["QT_QPA_PLATFORM"] = "offscreen"
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )

        result = json.loads(completed.stdout.strip())
        self.assertEqual(result["mode"], "custom")
        self.assertEqual(
            result["points"],
            [{"currentA": 3.0, "dwellS": 300.0}, {"currentA": 5.0, "dwellS": 120.0}],
        )
        self.assertEqual(result["configuredCurrents"], [3.0, 5.0])
        self.assertEqual(result["configuredDwells"], [300.0, 120.0])

    def test_tdk_preflight_can_start_with_required_output_enabled(self) -> None:
        script = """
import json
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication
from combined_test.window import MainWindow
from tauri_bridge.legacy_backend import LegacyWindowBackend

app = QApplication.instance() or QApplication([])
window = MainWindow(headless=True)
window.power_supply_controller_combo.setCurrentIndex(
    window.power_supply_controller_combo.findData("tdk")
)
window.manual_ch341_controller = SimpleNamespace(
    is_connected=True,
    output_enabled=True,
)
window.power_supply_controller_kind = "tdk"
window.start_automatic_test_button.setEnabled(True)
snapshot = LegacyWindowBackend(window).snapshot({"view": "automatic"})
print(json.dumps(snapshot["automaticTest"]["canStart"]))
window.manual_ch341_controller = None
window.close()
"""
        environment = dict(os.environ)
        environment["QT_QPA_PLATFORM"] = "offscreen"
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )

        self.assertTrue(json.loads(completed.stdout.strip()))

    def test_headless_window_does_not_load_matplotlib(self) -> None:
        script = """
import json
import sys
from PySide6.QtWidgets import QApplication
from combined_test.window import MainWindow
from tauri_bridge.legacy_backend import LegacyWindowBackend

app = QApplication.instance() or QApplication([])
window = MainWindow(headless=True)
backend = LegacyWindowBackend(window)
window.active_output_current_a = 10.0
window.show_spectrum_saturation_alert("光谱饱和（峰值 60000 counts，连续 3 个像素）")
window.spectrum_center_locked_nm = 976.25
wavelength = [956.0 + index * 0.1 for index in range(401)]
intensity = [float((index % 5) - 2) for index in range(401)]
intensity[199:202] = [600.0, 1000.0, 600.0]
window.on_spectrum_curve(wavelength, intensity)
backend.latest_spectrometer_reading = type("StaleReading", (), {
    "peak_wavelength_nm": 900.0,
    "centroid_nm": 901.0,
    "fwhm_nm": 99.0,
})()
window.live_plots.update_power(0.0, 0.1)
snapshot = backend.snapshot({"view": "automatic"})
pd_snapshot = backend.snapshot({"view": "pd"})
window.live_plots.update_power(0.1, 0.2)
incremental = backend.snapshot({
    "view": "automatic",
    "since": snapshot["seriesRevisions"],
    "cursors": {"power": snapshot["measurements"]["power"][-1]["elapsedS"]},
})
unchanged = backend.snapshot({
    "view": "automatic",
    "since": incremental["seriesRevisions"],
    "cursors": {"power": incremental["seriesPatches"]["power"]["points"][-1]["elapsedS"]},
})
print(json.dumps({
    "headless": window.headless,
    "plots": type(window.live_plots).__name__,
    "backendMode": snapshot["backend"]["mode"],
    "framePeak": snapshot["devices"]["spectrometer"]["peakWavelengthNm"],
    "frameFwhm": snapshot["devices"]["spectrometer"]["fwhmNm"],
    "frameSmsr": snapshot["devices"]["spectrometer"]["smsrDb"],
    "framePeaks": snapshot["measurements"]["spectrumPeaks"],
    "saturationNotice": snapshot["backend"]["notices"][-1],
    "spectrumWindow": snapshot["devices"]["spectrometer"]["spectrumWindow"],
    "measurementKeys": sorted(snapshot["measurements"]),
    "automaticHasPdPoints": "points" in snapshot["pd"],
    "pdMeasurementKeys": sorted(pd_snapshot["measurements"]),
    "pdHasPoints": "points" in pd_snapshot["pd"],
    "incrementalMeasurementKeys": sorted(incremental["measurements"]),
    "powerPatchPoints": incremental["seriesPatches"]["power"]["points"],
    "unchangedMeasurementKeys": sorted(unchanged["measurements"]),
    "unchangedHasPatches": "seriesPatches" in unchanged,
    "matplotlibLoaded": any(name.startswith("matplotlib") for name in sys.modules),
}))
window.close()
"""
        environment = dict(os.environ)
        environment["QT_QPA_PLATFORM"] = "offscreen"
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )

        result = json.loads(completed.stdout.strip())
        self.assertTrue(result["headless"])
        self.assertEqual(result["plots"], "NullLivePlots")
        self.assertEqual(result["backendMode"], "active")
        self.assertNotEqual(result["framePeak"], 900.0)
        self.assertNotEqual(result["frameFwhm"], 99.0)
        self.assertIsNone(result["frameSmsr"])
        self.assertEqual([peak["label"] for peak in result["framePeaks"]], ["P1"])
        self.assertEqual(result["framePeak"], result["framePeaks"][0]["centroidNm"])
        self.assertEqual(result["saturationNotice"]["title"], "光谱饱和")
        self.assertEqual(result["saturationNotice"]["level"], "warning")
        self.assertTrue(result["saturationNotice"]["id"])
        self.assertIn("自动测试已暂停", result["saturationNotice"]["message"])
        self.assertIn("当前电流保持在 10.0 A", result["saturationNotice"]["message"])
        self.assertEqual(
            result["spectrumWindow"],
            {"centerNm": 976.25, "halfRangeNm": 20.0},
        )
        self.assertEqual(
            result["measurementKeys"],
            ["power", "spectrum", "spectrumPeaks", "stable"],
        )
        self.assertFalse(result["automaticHasPdPoints"])
        self.assertEqual(result["pdMeasurementKeys"], [])
        self.assertTrue(result["pdHasPoints"])
        self.assertNotIn("power", result["incrementalMeasurementKeys"])
        self.assertEqual(len(result["powerPatchPoints"]), 1)
        self.assertEqual(result["powerPatchPoints"][0]["elapsedS"], 0.1)
        self.assertEqual(result["unchangedMeasurementKeys"], [])
        self.assertFalse(result["unchangedHasPatches"])
        self.assertFalse(result["matplotlibLoaded"])


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

class LegacyWindowBackendPowerMeterTests(unittest.TestCase):
    def test_resource_selection_uses_qt_discovery_identity(self) -> None:
        class Combo:
            selected_index = None
            edited_text = None

            def setCurrentIndex(self, index):
                self.selected_index = index

            def findText(self, _text):
                return -1

            def isEditable(self):
                return True

            def setEditText(self, text):
                self.edited_text = text

        combo = Combo()
        window = SimpleNamespace(
            power_meter_combo=combo,
            _find_power_meter_resource_index=lambda resource: (
                2 if resource == "ASRL4::INSTR" else -1
            ),
        )
        backend = object.__new__(LegacyWindowBackend)
        backend.window = window

        backend._set_power_meter_resource("ASRL4::INSTR")

        self.assertEqual(combo.selected_index, 2)
        self.assertIsNone(combo.edited_text)

    def test_start_and_zero_are_rejected_during_detection(self) -> None:
        backend = object.__new__(LegacyWindowBackend)
        backend.window = SimpleNamespace(
            power_meter_detect_thread=object(),
            start_power_meter=lambda: self.fail("must not start while detecting"),
            set_power_meter_relative_zero=lambda _enabled: self.fail(
                "must not zero while detecting"
            ),
        )

        with self.assertRaisesRegex(ValueError, "正在识别"):
            backend._start_power_meter()
        with self.assertRaisesRegex(ValueError, "正在识别"):
            backend._set_relative_zero({"enabled": True})


class LegacyWindowBackendShutdownTests(unittest.TestCase):
    @staticmethod
    def make_backend(*, active_current_a: float, output_enabled: bool):
        backend = object.__new__(LegacyWindowBackend)
        backend.window = SimpleNamespace(
            emergency_stop=lambda: None,
            power_meter_reader=None,
            spectrometer_reader=None,
            pd_panel=SimpleNamespace(reader=None),
            excel_save_thread=None,
            manual_ch341_controller=SimpleNamespace(output_enabled=output_enabled),
            active_output_current_a=active_current_a,
            power_supply_controller_kind="tdk",
            automatic_controller=SimpleNamespace(output_shutdown_unconfirmed=False),
        )
        return backend

    def test_tdk_shutdown_requires_confirmed_zero_current(self) -> None:
        backend = self.make_backend(active_current_a=5.0, output_enabled=False)

        with self.assertRaisesRegex(RuntimeError, "无法确认 TDK 输出"):
            backend._shutdown()

    def test_safe_tdk_shutdown_returns_explicit_confirmation(self) -> None:
        backend = self.make_backend(active_current_a=0.0, output_enabled=False)

        self.assertEqual(backend._shutdown(), {"shutdownConfirmed": True})

class LegacyWindowBackendShippingBatchTests(unittest.TestCase):
    @staticmethod
    def make_backend():
        backend = object.__new__(LegacyWindowBackend)
        backend.window = SimpleNamespace(
            input_settings=object(),
            statusBar=lambda: SimpleNamespace(showMessage=lambda _message: None),
            add_log=lambda _message: None,
        )
        return backend

    def test_batch_inspect_exposes_scan_payload_and_preferences(self) -> None:
        backend = self.make_backend()
        batch = SimpleNamespace(to_payload=lambda: {"rootPath": "/batch", "items": []})
        with (
            patch("tauri_bridge.legacy_backend.inspect_shipping_report_batch", return_value=batch) as inspect_batch,
            patch("tauri_bridge.legacy_backend.load_shipping_report_preferences", return_value={"pole": {}}),
        ):
            result = backend._inspect_shipping_report_batch({"rootPath": "/batch"})

        inspect_batch.assert_called_once_with("/batch")
        self.assertEqual(result["rootPath"], "/batch")
        self.assertEqual(result["preferences"], {"pole": {}})

    def test_batch_generate_item_returns_identity_and_saves_preferences(self) -> None:
        backend = self.make_backend()
        request = SimpleNamespace(sn="SN-001")
        with (
            patch(
                "tauri_bridge.legacy_backend.generate_shipping_report_batch_item",
                return_value=(Path("/output/SN-001_出货报告.pdf"), request),
            ) as generate_item,
            patch("tauri_bridge.legacy_backend.save_shipping_report_preferences") as save_preferences,
        ):
            result = backend._generate_shipping_report_batch_item(
                {"sourcePath": "/batch/source.xlsx", "outputDirectory": "/output"}
            )

        generate_item.assert_called_once()
        save_preferences.assert_called_once_with(backend.window.input_settings, request)
        self.assertEqual(result["sn"], "SN-001")
        self.assertEqual(result["sourcePath"], "/batch/source.xlsx")
        self.assertTrue(result["outputPath"].endswith("SN-001_出货报告.pdf"))

if __name__ == "__main__":
    unittest.main()
