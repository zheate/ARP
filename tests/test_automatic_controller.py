from __future__ import annotations

import csv
import io
import os
import tempfile
import time
import types
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from combined_test.automatic_controller import AutomaticTestTerminalOutcome
from combined_test.automatic_samples import AutomaticSampleRecorder, automatic_sample_path
from combined_test.automation import AutomaticTestState
from combined_test.models import PowerMeterReading
from combined_test.spectrum_math import calculate_stats
from combined_test.window import MainWindow


class AutomaticTestControllerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        settings_path = Path(self.temp_dir.name) / "controller.ini"
        self.window = MainWindow(QSettings(str(settings_path), QSettings.Format.IniFormat))
        self.power_protection_alerts: list[str] = []
        self.window.show_power_protection_alert = (  # type: ignore[method-assign]
            lambda reason: self.power_protection_alerts.append(reason)
        )

    def tearDown(self) -> None:
        self.window.automatic_pause_safety_timer.stop()
        self.window.automatic_test_state = AutomaticTestState.IDLE
        self.window.close()
        self.temp_dir.cleanup()

    def _prepare_zero_current_terminal_path(self, state: AutomaticTestState) -> list[str]:
        self.window.automatic_test_settings = self.window.collect_automatic_test_settings()
        self.window.automatic_test_currents = (8.0,)
        self.window.automatic_test_current_index = 0
        self.window.active_output_current_a = 0.0
        self.window.automatic_test_state = state
        result_details: list[str] = []
        self.window.show_automatic_result = (  # type: ignore[method-assign]
            lambda _record, detail: result_details.append(detail)
        )
        return result_details

    def _retry_without_modal(self) -> None:
        original_information = QMessageBox.information
        QMessageBox.information = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok
        )
        try:
            self.window.retry_automatic_test()
        finally:
            QMessageBox.information = original_information  # type: ignore[method-assign]

    def test_point_timeout_accounts_for_the_final_ten_second_hold(self) -> None:
        self.window.stable_window_spin.setValue(3.0)
        self.window.auto_point_timeout_spin.setValue(12.9)

        with self.assertRaisesRegex(ValueError, "13.0"):
            self.window.collect_automatic_test_settings()

    def test_custom_point_hold_delays_recording_and_extends_point_timeout(self) -> None:
        class Controller:
            is_connected = True

            def i2c_write(self, _address: int, _command: list[int]) -> tuple[bool, str]:
                return True, "OK"

        class Reader:
            def reset_stability_window(self) -> int:
                return 1

        self.window.automatic_current_plan_mode = "custom"
        self.window.auto_initial_current_spin.setValue(3.0)
        self.window.auto_target_current_spin.setValue(3.0)
        self.window.automatic_custom_current_points = [
            {"current_a": 3.0, "dwell_s": 300.0}
        ]
        settings = self.window.collect_automatic_test_settings()
        self.window.automatic_test_settings = settings
        self.window.automatic_orchestrator.settings = settings
        self.window.automatic_test_currents = (3.0,)
        self.window.automatic_orchestrator.dwell_times_s = (300.0,)
        self.window.automatic_test_current_index = 0
        self.window.manual_ch341_controller = Controller()
        self.window.power_meter_reader = Reader()  # type: ignore[assignment]

        self.window.write_automatic_current(3.0, "test")

        self.assertGreaterEqual(self.window.automatic_point_timer.remainingTime(), 419_000)
        self.window.recorded_stable_point_current_a = 3.0
        self.window.recorded_stable_point_generation = 1
        self.window.automatic_controller.on_stable_power_captured(3.0)
        self.window.power_meter_reader = None
        self.window.schedule_auto_vout_read()
        self.assertGreaterEqual(self.window.auto_vout_timer.remainingTime(), 299_000)

    def test_designated_point_dwell_starts_after_stability(self) -> None:
        self.window.automatic_current_plan_mode = "custom"
        self.window.auto_initial_current_spin.setValue(3.0)
        self.window.auto_target_current_spin.setValue(3.0)
        self.window.automatic_custom_current_points = [
            {"current_a": 3.0, "dwell_s": 20.0}
        ]
        settings = self.window.collect_automatic_test_settings()
        self.window.automatic_test_settings = settings
        self.window.automatic_test_currents = (3.0,)
        self.window.automatic_test_current_index = 0
        self.window.automatic_orchestrator.dwell_times_s = (20.0,)
        self.window.automatic_hold_started_monotonic_s = time.monotonic() - 3.0
        self.window.active_output_current_a = 3.0
        self.window.recorded_stable_point_current_a = 3.0
        self.window.recorded_stable_point_generation = 1
        self.window.automatic_test_state = AutomaticTestState.WAITING_STABLE

        self.window.schedule_auto_vout_read()

        self.assertGreaterEqual(self.window.auto_vout_timer.remainingTime(), 16_900)
        self.assertLessEqual(self.window.auto_vout_timer.remainingTime(), 17_100)

    def test_custom_hold_mode_ramps_from_zero_without_stability_checks_between_steps(self) -> None:
        class TdkController:
            is_connected = True
            output_enabled = True

            def __init__(self) -> None:
                self.currents: list[float] = []

            def set_output_current(self, current_a: float) -> None:
                self.currents.append(float(current_a))

            def set_output_enabled(self, enabled: bool) -> None:
                self.output_enabled = bool(enabled)

        class Reader:
            def __init__(self) -> None:
                self.reset_calls = 0

            def reset_stability_window(self) -> int:
                self.reset_calls += 1
                return self.reset_calls

        controller = TdkController()
        reader = Reader()
        tdk_index = self.window.power_supply_controller_combo.findData("tdk")
        self.window.power_supply_controller_combo.setCurrentIndex(tdk_index)
        self.window.power_supply_controller_kind = "tdk"
        self.window.manual_ch341_controller = controller
        self.window.power_meter_reader = reader  # type: ignore[assignment]
        self.window.automatic_current_plan_mode = "custom"
        self.window.auto_initial_current_spin.setValue(1.0)
        self.window.auto_target_current_spin.setValue(3.0)
        self.window.automatic_custom_current_points = [
            {"current_a": 3.0, "dwell_s": 60.0}
        ]
        settings = self.window.collect_automatic_test_settings()
        self.window.automatic_orchestrator.start(
            settings,
            power_meter_ready=True,
            spectrum_meter_ready=True,
        )
        self.window.active_output_current_a = 0.0

        self.window.begin_automatic_current_point()

        self.assertEqual(controller.currents, [])
        self.assertEqual(self.window.pending_automatic_current_a, 0.5)
        self.assertEqual(self.window.pending_automatic_command_kind, "ramp_up")
        self.assertGreaterEqual(self.window.automatic_command_timer.remainingTime(), 450)

        for expected_current in (0.5, 1.0, 1.5, 2.0, 2.5):
            self.window.automatic_command_timer.stop()
            self.window.on_automatic_command_timer_timeout()
            self.assertEqual(self.window.active_output_current_a, expected_current)
            self.assertEqual(self.window.automatic_test_state, AutomaticTestState.SETTING_CURRENT)
            self.assertIsNone(self.window.pending_stable_point_current_a)
            self.assertFalse(self.window.automatic_point_timer.isActive())
            self.assertEqual(reader.reset_calls, 0)

        self.window.automatic_command_timer.stop()
        self.window.on_automatic_command_timer_timeout()

        self.assertEqual(controller.currents, [0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
        self.assertEqual(self.window.active_output_current_a, 3.0)
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.WAITING_STABLE)
        self.assertEqual(self.window.pending_stable_point_current_a, 3.0)
        self.assertEqual(reader.reset_calls, 1)
        self.assertTrue(self.window.automatic_point_timer.isActive())
        self.window.automatic_point_timer.stop()
        self.window.active_output_current_a = 0.0
        controller.output_enabled = False
        self.window.power_meter_reader = None

    def test_ch341_custom_hold_mode_does_not_use_half_amp_ramp(self) -> None:
        class Controller:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        self.window.manual_ch341_controller = Controller()
        self.window.automatic_current_plan_mode = "custom"
        self.window.auto_initial_current_spin.setValue(1.0)
        self.window.auto_target_current_spin.setValue(1.0)
        self.window.automatic_custom_current_points = [
            {"current_a": 1.0, "dwell_s": 5.0}
        ]
        settings = self.window.collect_automatic_test_settings()
        self.window.automatic_orchestrator.start(
            settings,
            power_meter_ready=True,
            spectrum_meter_ready=True,
        )
        self.window.active_output_current_a = 0.0

        self.window.begin_automatic_current_point()

        self.assertEqual(self.window.active_output_current_a, 1.0)
        self.assertEqual(len(self.window.manual_ch341_controller.writes), 1)
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.WAITING_STABLE)
        self.assertFalse(self.window.automatic_command_timer.isActive())
        self.window.active_output_current_a = 0.0

    def test_designated_hold_is_not_reset_by_temporary_power_instability(self) -> None:
        self.window.automatic_test_state = AutomaticTestState.WAITING_VOLTAGE
        self.window.automatic_test_current_index = 0
        self.window.automatic_orchestrator.dwell_times_s = (60.0,)
        self.window.active_output_current_a = 3.0
        self.window.recorded_stable_point_current_a = 3.0
        self.window.recorded_stable_point_generation = 7
        self.window.pending_auto_vout_current_a = 3.0
        self.window.pending_auto_vout_generation = 7
        hold_started_at = time.monotonic() - 20.0
        self.window.automatic_hold_started_monotonic_s = hold_started_at

        self.window.capture_stable_power_point(
            PowerMeterReading(
                elapsed_s=21.0,
                power_w=10.5,
                stable=False,
                stable_span_w=0.2,
                stable_window_s=3.0,
                stable_median_w=10.5,
                stability_generation=7,
            )
        )

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.WAITING_VOLTAGE)
        self.assertEqual(self.window.automatic_hold_started_monotonic_s, hold_started_at)
        self.assertEqual(self.window.pending_auto_vout_current_a, 3.0)

    def test_designated_hold_advances_when_duration_ends_even_if_latest_reading_moves(self) -> None:
        self.window.automatic_test_state = AutomaticTestState.WAITING_VOLTAGE
        self.window.automatic_test_current_index = 0
        self.window.automatic_orchestrator.dwell_times_s = (60.0,)
        self.window.active_output_current_a = 3.0
        self.window.recorded_stable_point_current_a = 3.0
        self.window.recorded_stable_point_generation = 7
        self.window.automatic_hold_started_monotonic_s = time.monotonic() - 61.0
        self.window.pending_auto_vout_current_a = 3.0
        self.window.pending_auto_vout_generation = 7
        self.window.pending_auto_vout_power_elapsed_s = 10.0
        self.window.latest_power_meter_reading = PowerMeterReading(
            elapsed_s=11.0,
            power_w=10.5,
            stable=False,
            stable_span_w=0.2,
            stable_window_s=3.0,
            stable_median_w=10.5,
            stability_generation=7,
        )
        automatic_calls: list[bool] = []
        self.window.read_output_voltage = (  # type: ignore[method-assign]
            lambda automatic=False: automatic_calls.append(automatic)
        )

        self.window.on_auto_vout_timer_timeout()

        self.assertEqual(automatic_calls, [True])
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.WAITING_VOLTAGE)

    def test_automatic_point_writes_complete_one_second_csv_sample(self) -> None:
        class Supply:
            def read_output_current(self) -> float:
                return 3.0

            def read_output_voltage(self) -> float:
                return 23.0

        buffer = io.StringIO()
        self.window.automatic_sample_recorder = AutomaticSampleRecorder(
            Path("unused.csv"),
            file=buffer,
        )
        self.window.power_supply_controller_kind = "tdk"
        self.window.get_power_supply = lambda: Supply()  # type: ignore[method-assign]
        self.window.automatic_test_state = AutomaticTestState.WAITING_STABLE
        self.window.automatic_test_current_index = 0
        self.window.automatic_orchestrator.dwell_times_s = (5.0,)
        self.window.active_output_current_a = 3.0
        self.window.automatic_hold_started_monotonic_s = time.monotonic() - 1.0
        self.window.latest_power_meter_reading = PowerMeterReading(
            elapsed_s=1.0,
            power_w=30.0,
            stable=False,
            stable_span_w=0.0,
            stable_window_s=1.0,
            stable_median_w=30.0,
        )
        self.window.latest_spectrum_wavelength = [975.0, 976.0, 977.0]
        self.window.latest_spectrum_intensity = [1.0, 5.0, 1.0]
        self.window.latest_spectrum_stats = calculate_stats(
            self.window.latest_spectrum_wavelength,
            self.window.latest_spectrum_intensity,
        )

        self.assertTrue(self.window.record_automatic_second_sample())

        rows = list(csv.DictReader(io.StringIO(buffer.getvalue())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target_current_a"], "3")
        self.assertEqual(rows[0]["output_current_a"], "3")
        self.assertEqual(rows[0]["output_voltage_raw_v"], "23")
        self.assertEqual(rows[0]["optical_power_w"], "30")
        self.assertEqual(rows[0]["spectrum_wavelengths_nm"], "975;976;977")
        self.assertGreater(float(rows[0]["efficiency_percent"]), 0.0)

        self.window.automatic_hold_started_monotonic_s = time.monotonic() - 8.0
        self.assertTrue(self.window.record_automatic_second_sample(force=True))
        self.assertFalse(self.window.record_automatic_second_sample(force=True))
        rows = list(csv.DictReader(io.StringIO(buffer.getvalue())))
        self.assertEqual(rows[-1]["point_elapsed_s"], "5.000000")
        self.assertEqual(len(rows), 2)

    def test_final_excel_success_deletes_consumed_second_sample_csv(self) -> None:
        workbook_path = Path(self.temp_dir.name) / "final.xlsx"
        sample_path = automatic_sample_path(workbook_path)
        sample_path.write_text("sample data", encoding="utf-8")
        self.window.automatic_sample_csv_path = sample_path
        self.window.excel_save_thread = types.SimpleNamespace(  # type: ignore[assignment]
            records=[],
            path=workbook_path,
            automatic_samples_path=sample_path,
        )
        presentations: list[tuple[object | None, str]] = []
        object.__setattr__(
            self.window.automatic_controller,
            "finish_automatic_test_presentation",
            lambda record, message: presentations.append((record, message)),
        )
        self.window._automatic_finalization_pending = (None, "测试完成")

        self.window.on_excel_save_succeeded(0.1)

        self.assertFalse(sample_path.exists())
        self.assertIsNone(self.window.automatic_sample_csv_path)
        self.assertEqual(presentations, [(None, "测试完成")])
        self.window.excel_save_thread = None

    def test_operator_pause_during_save_is_rejected_and_save_can_complete(self) -> None:
        result_details = self._prepare_zero_current_terminal_path(AutomaticTestState.SAVING_POINT)
        self.window.record_store.recorded_currents.add(8.0)

        self.window.pause_automatic_test("操作者暂停", operator_requested=True)

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.SAVING_POINT)
        self.assertFalse(self.window.automatic_pause_safety_timer.isActive())
        self.assertIn("保存", self.window.statusBar().currentMessage())

        self.window.automatic_controller.on_record_saved()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.SUCCEEDED,
        )
        self.assertIn("完整完成", result_details[-1])

    def test_save_failure_can_still_pause_for_retry(self) -> None:
        self._prepare_zero_current_terminal_path(AutomaticTestState.SAVING_POINT)

        self.window.automatic_controller.on_record_save_failed("文件被占用")

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(
            self.window.automatic_paused_from_state,
            AutomaticTestState.SAVING_POINT,
        )
        self.assertTrue(self.window.automatic_pause_safety_timer.isActive())
        self.assertIn("Excel 保存失败", self.window.automatic_pause_reason)

    def test_acquisition_fault_during_save_retries_from_the_next_point_after_save(self) -> None:
        self._prepare_zero_current_terminal_path(AutomaticTestState.SAVING_POINT)
        self.window.automatic_test_currents = (8.0, 10.0)
        self.window.record_store.recorded_currents.add(8.0)

        self.window.automatic_controller.on_acquisition_failed("功率计", "通信中断")

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)

        self.window.automatic_controller.on_record_saved()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(self.window.automatic_test_current_index, 0)
        self.assertIn("已成功保存", self.window.statusBar().currentMessage())
        self.assertIn("功率计错误", self.window.automatic_pause_reason)
        self.assertIsNone(self.window.automatic_controller.terminal_outcome)

        self._retry_without_modal()

        self.assertEqual(self.window.automatic_test_current_index, 1)
        self.assertNotEqual(self.window.automatic_test_state, AutomaticTestState.SAVING_POINT)
        self.assertIn("未连接", self.window.automatic_pause_reason)

    def test_acquisition_fault_after_final_save_aborts_safely(self) -> None:
        result_details = self._prepare_zero_current_terminal_path(AutomaticTestState.SAVING_POINT)
        self.window.record_store.recorded_currents.add(8.0)

        self.window.automatic_controller.on_acquisition_failed("功率计", "通信中断")
        self.window.automatic_controller.on_record_saved()
        self._retry_without_modal()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.ABORTED_SAFELY,
        )
        self.assertIn("功率计错误", self.window.automatic_controller.terminal_reason)
        self.assertIn("异常中止", result_details[-1])

    def test_operator_end_reports_stopped_by_operator_after_safe_ramp_down(self) -> None:
        result_details = self._prepare_zero_current_terminal_path(AutomaticTestState.WAITING_STABLE)

        self.window.end_automatic_test()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.STOPPED_BY_OPERATOR,
        )
        self.assertIn("操作者提前结束", self.window.automatic_controller.terminal_reason)
        self.assertIn("安全下电", result_details[-1])

    def test_power_drop_over_five_seconds_trips_legacy_supply(self) -> None:
        class ConnectedLegacyController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        result_details = self._prepare_zero_current_terminal_path(
            AutomaticTestState.SETTING_CURRENT
        )
        controller = ConnectedLegacyController()
        self.window.manual_ch341_controller = controller
        self.window.active_output_current_a = 8.0
        self.window.set_current_spin.setValue(8.0)

        self.window.on_power_meter_reading(
            PowerMeterReading(1.0, 100.0, False, 5.0, 0.1, 100.0)
        )
        self.window.on_power_meter_reading(
            PowerMeterReading(6.0, 69.0, False, 31.0, 5.0, 69.0)
        )
        self.window.on_power_meter_reading(
            PowerMeterReading(6.1, 68.5, False, 31.5, 5.1, 68.5)
        )
        self.window.on_power_meter_reading(
            PowerMeterReading(6.2, 68.0, False, 32.0, 5.2, 68.0)
        )

        self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x00, 0x00]])
        self.assertEqual(self.window.active_output_current_a, 0.0)
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.ABORTED_SAFELY,
        )
        self.assertIn("5 秒前 100.000 W", self.window.automatic_controller.terminal_reason)
        self.assertIn("功率保护已触发", result_details[-1])
        self.assertEqual(len(self.power_protection_alerts), 1)
        self.assertIn("当前 68.000 W", self.power_protection_alerts[0])
        self.assertIn("连续 3 个样本", self.power_protection_alerts[0])
        self.window.manual_ch341_controller = None

    def test_power_drop_immediately_sets_zero_and_disables_tdk_output(self) -> None:
        class ConnectedTdkController:
            is_connected = True
            output_enabled = True

            def __init__(self) -> None:
                self.commands: list[str] = []

            def set_output_current(self, current_a: float) -> None:
                self.commands.append(f"current:{current_a:g}")

            def set_output_enabled(self, enabled: bool) -> None:
                self.commands.append(f"output:{int(enabled)}")
                self.output_enabled = enabled

        self._prepare_zero_current_terminal_path(AutomaticTestState.WAITING_VOLTAGE)
        controller = ConnectedTdkController()
        self.window.manual_ch341_controller = controller
        self.window.power_supply_controller_kind = "tdk"
        self.window.active_output_current_a = 5.0
        self.window.set_current_spin.setValue(5.0)

        self.window.on_power_meter_reading(
            PowerMeterReading(1.0, 50.0, False, 5.0, 0.1, 50.0)
        )
        self.window.on_power_meter_reading(
            PowerMeterReading(6.0, 34.0, False, 16.0, 5.0, 34.0)
        )
        self.window.on_power_meter_reading(
            PowerMeterReading(6.1, 33.5, False, 16.5, 5.1, 33.5)
        )
        self.window.on_power_meter_reading(
            PowerMeterReading(6.2, 33.0, False, 17.0, 5.2, 33.0)
        )

        self.assertEqual(controller.commands, ["current:0", "output:0"])
        self.assertFalse(controller.output_enabled)
        self.assertEqual(self.window.active_output_current_a, 0.0)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.ABORTED_SAFELY,
        )
        self.assertEqual(len(self.power_protection_alerts), 1)
        self.assertIn("当前 33.000 W", self.power_protection_alerts[0])
        self.window.manual_ch341_controller = None

    def test_power_drop_protection_is_inactive_during_normal_ramp_down(self) -> None:
        class ConnectedLegacyController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                return True, "OK"

        controller = ConnectedLegacyController()
        self.window.manual_ch341_controller = controller
        self.window.active_output_current_a = 8.0
        self.window.automatic_test_state = AutomaticTestState.WAITING_STABLE
        self.assertFalse(
            self.window.automatic_controller.on_automatic_power_sample(100.0)
        )

        self.window.automatic_test_state = AutomaticTestState.RAMPING_DOWN
        self.assertFalse(
            self.window.automatic_controller.on_automatic_power_sample(0.0)
        )

        self.assertEqual(controller.writes, [])
        self.assertEqual(self.window.active_output_current_a, 8.0)
        self.window.manual_ch341_controller = None

    def test_fault_pause_timeout_reports_aborted_safely(self) -> None:
        result_details = self._prepare_zero_current_terminal_path(AutomaticTestState.WAITING_STABLE)

        self.window.pause_automatic_test("功率计通信中断")
        self.window.automatic_pause_safety_timer.stop()
        self.window.on_automatic_pause_safety_timeout()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.ABORTED_SAFELY,
        )
        self.assertIn("功率计通信中断", self.window.automatic_controller.terminal_reason)
        self.assertIn("异常中止", result_details[-1])
        self.assertIn("安全下电", result_details[-1])

    def test_saturation_pause_holds_current_until_operator_action(self) -> None:
        self.window.automatic_test_settings = self.window.collect_automatic_test_settings()
        self.window.automatic_test_state = AutomaticTestState.WAITING_STABLE
        self.window.active_output_current_a = 10.0

        self.window.pause_automatic_test(
            "光谱饱和",
            hold_current_until_operator=True,
        )

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(self.window.active_output_current_a, 10.0)
        self.assertFalse(self.window.automatic_pause_safety_timer.isActive())

        # Even a stale timeout event must not start a ramp-down.
        self.window.on_automatic_pause_safety_timeout()
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertEqual(self.window.active_output_current_a, 10.0)

    def test_terminal_outcome_is_hidden_while_ramp_down_is_in_progress(self) -> None:
        class ConnectedLegacyController:
            is_connected = True

            def i2c_write(self, _address: int, _command: list[int]) -> tuple[bool, str]:
                return True, "OK"

        result_details = self._prepare_zero_current_terminal_path(
            AutomaticTestState.WAITING_STABLE
        )
        self.window.manual_ch341_controller = ConnectedLegacyController()
        self.window.active_output_current_a = 8.0
        self.window.last_power_supply_command_monotonic_s = time.monotonic() - 1.0

        self.window.end_automatic_test()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.RAMPING_DOWN)
        self.assertIsNone(self.window.automatic_controller.terminal_outcome)
        self.assertEqual(self.window.automatic_controller.terminal_reason, "")
        self.assertEqual(result_details, [])
        self.window.automatic_ramp_down_timer.stop()

    def test_configured_current_point_is_written_directly_and_retried(self) -> None:
        class FlakyLegacyController:
            is_connected = True

            def __init__(self) -> None:
                self.writes: list[list[int]] = []
                self.five_amp_attempts = 0

            def i2c_write(self, _address: int, command: list[int]) -> tuple[bool, str]:
                self.writes.append(command)
                if command == [0xB4, 0xFF, 0x05, 0x00]:
                    self.five_amp_attempts += 1
                    if self.five_amp_attempts == 1:
                        return False, "写入失败"
                return True, "OK"

        class ReaderStub:
            def reset_stability_window(self) -> int:
                return 1

        controller = FlakyLegacyController()
        self.window.manual_ch341_controller = controller
        self.window.power_meter_reader = ReaderStub()  # type: ignore[assignment]
        self.window.auto_initial_current_spin.setValue(2.0)
        self.window.auto_target_current_spin.setValue(5.0)
        self.window.auto_current_step_spin.setValue(3.0)
        self.window.automatic_test_settings = self.window.collect_automatic_test_settings()
        self.window.automatic_test_currents = (2.0, 5.0)
        self.window.automatic_test_current_index = 1
        self.window.active_output_current_a = 2.0

        self.window.begin_automatic_current_point()

        self.assertEqual(controller.writes, [[0xB4, 0xFF, 0x05, 0x00]])
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.SETTING_CURRENT)
        self.assertTrue(self.window.automatic_command_timer.isActive())

        self.window.automatic_command_timer.stop()
        self.window.last_power_supply_command_monotonic_s = time.monotonic() - 2.0
        self.window.on_automatic_command_timer_timeout()

        self.assertEqual(
            controller.writes,
            [
                [0xB4, 0xFF, 0x05, 0x00],
                [0xB4, 0xFF, 0x05, 0x00],
            ],
        )
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.WAITING_STABLE)
        self.window.power_meter_reader = None

    def test_missing_tdk_controller_never_reports_safe_shutdown(self) -> None:
        result_details = self._prepare_zero_current_terminal_path(AutomaticTestState.RAMPING_DOWN)
        self.window.power_supply_controller_kind = "tdk"
        self.window.manual_ch341_controller = None
        self.window.active_output_current_a = 4.0
        self.window.set_current_spin.setValue(4.0)

        self.window.complete_automatic_test()
        self.window.manual_ch341_controller = None

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertIsNone(self.window.automatic_controller.terminal_outcome)
        self.assertEqual(self.window.automatic_controller.terminal_reason, "")
        self.assertTrue(self.window.automatic_controller.output_shutdown_unconfirmed)
        self.assertEqual(self.window.active_output_current_a, 4.0)
        self.assertEqual(result_details, [])
        self.assertIn("TDK 输出关闭失败", self.window.automatic_pause_reason)

    def test_csv_close_failure_pauses_completion_instead_of_stalling(self) -> None:
        result_details = self._prepare_zero_current_terminal_path(AutomaticTestState.RAMPING_DOWN)
        import combined_test.automatic_controller as controller_module

        original_critical = controller_module.QMessageBox.critical
        dialogs: list[str] = []
        controller_module.QMessageBox.critical = (  # type: ignore[method-assign]
            lambda _parent, _title, message: dialogs.append(message)
        )

        class FailingRecorder:
            def close(self) -> None:
                raise OSError("磁盘已满")

        sample_path = Path(self.temp_dir.name) / "incomplete.csv"
        sample_path.write_text("partial data", encoding="utf-8")
        self.window.automatic_sample_recorder = FailingRecorder()  # type: ignore[assignment]
        self.window.automatic_sample_csv_path = sample_path
        finalized_sample_paths: list[Path | None] = []
        self.window.finalize_automatic_session_workbook = (  # type: ignore[method-assign]
            lambda _record, _message: finalized_sample_paths.append(
                self.window.automatic_sample_csv_path
            )
            or False
        )
        try:
            self.window.complete_automatic_test()
        finally:
            controller_module.QMessageBox.critical = original_critical  # type: ignore[method-assign]

        # The workflow must land in a recoverable state (paused with a
        # reason), not stay stuck in RAMPING_DOWN with no retry or reset.
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertIn("逐秒", self.window.automatic_pause_reason)
        self.assertEqual(result_details, [])
        self.assertTrue(dialogs)
        self.assertIsNone(self.window.automatic_sample_recorder)
        self.assertIsNone(self.window.automatic_sample_csv_path)
        self.assertTrue(sample_path.exists())

        self._retry_without_modal()

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.ABORTED_SAFELY,
        )
        self.assertIn("逐秒采样记录收尾失败", self.window.automatic_controller.terminal_reason)
        self.assertEqual(finalized_sample_paths, [None])
        self.assertTrue(sample_path.exists())
        self.assertIn("异常中止", result_details[-1])

    def test_tdk_output_timeout_keeps_original_current_and_result_pending(self) -> None:
        class TimedOutOutputController:
            is_connected = True
            output_enabled = True

            def set_output_enabled(self, _enabled: bool) -> None:
                raise RuntimeError("OUT 0 timeout")

        result_details = self._prepare_zero_current_terminal_path(
            AutomaticTestState.RAMPING_DOWN
        )
        self.window.power_supply_controller_kind = "tdk"
        self.window.manual_ch341_controller = TimedOutOutputController()
        self.window.active_output_current_a = 4.0
        self.window.set_current_spin.setValue(4.0)

        self.window.complete_automatic_test()
        self.window.manual_ch341_controller = None

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertIsNone(self.window.automatic_controller.terminal_outcome)
        self.assertEqual(self.window.automatic_controller.terminal_reason, "")
        self.assertTrue(self.window.automatic_controller.output_shutdown_unconfirmed)
        self.assertEqual(self.window.active_output_current_a, 4.0)
        self.assertEqual(self.window.set_current_spin.value(), 4.0)
        self.assertEqual(result_details, [])
        self.assertIn("TDK 输出关闭失败", self.window.automatic_pause_reason)

    def test_retrying_tdk_output_shutdown_promotes_original_pending_outcome(self) -> None:
        class FlakyOutputController:
            is_connected = True
            output_enabled = True

            def __init__(self) -> None:
                self.shutdown_attempts = 0

            def set_output_enabled(self, enabled: bool) -> None:
                self.shutdown_attempts += 1
                if self.shutdown_attempts == 1:
                    raise RuntimeError("OUT 0 timeout")
                self.output_enabled = enabled

        result_details = self._prepare_zero_current_terminal_path(
            AutomaticTestState.RAMPING_DOWN
        )
        controller = FlakyOutputController()
        self.window.power_supply_controller_kind = "tdk"
        self.window.manual_ch341_controller = controller

        self.window.begin_automatic_ramp_down(
            terminal_outcome=AutomaticTestTerminalOutcome.SUCCEEDED,
            terminal_reason="所有计划测试点均已保存",
        )

        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.PAUSED)
        self.assertIsNone(self.window.automatic_controller.terminal_outcome)
        self.assertTrue(self.window.automatic_controller.output_shutdown_unconfirmed)
        self.assertEqual(result_details, [])

        self._retry_without_modal()

        self.assertEqual(controller.shutdown_attempts, 2)
        self.assertEqual(self.window.automatic_test_state, AutomaticTestState.COMPLETED)
        self.assertEqual(
            self.window.automatic_controller.terminal_outcome,
            AutomaticTestTerminalOutcome.SUCCEEDED,
        )
        self.assertEqual(
            self.window.automatic_controller.terminal_reason,
            "所有计划测试点均已保存",
        )
        self.assertFalse(self.window.automatic_controller.output_shutdown_unconfirmed)
        self.assertIn("完整完成", result_details[-1])

    def test_automatic_tdk_shutdown_syncs_manual_and_prepare_output_buttons(self) -> None:
        class ConnectedTdkController:
            is_connected = True
            output_enabled = True

            def set_output_enabled(self, enabled: bool) -> None:
                self.output_enabled = enabled

        self.window.power_supply_controller_combo.setCurrentIndex(
            self.window.power_supply_controller_combo.findData("tdk")
        )
        controller = ConnectedTdkController()
        self.window.manual_ch341_controller = controller
        self.window.power_supply_controller_kind = "tdk"
        self.window.sync_tdk_output_controls(True)
        self.window.automatic_test_state = AutomaticTestState.RAMPING_DOWN

        self.window.complete_automatic_test()

        self.assertFalse(controller.output_enabled)
        self.assertEqual(self.window.tdk_output_status_label.text(), "输出关闭")
        self.assertEqual(self.window.tdk_output_button.text(), "开启输出")
        self.assertEqual(self.window.prepare_tdk_output_button.text(), "开启输出")

    def test_completion_keeps_running_measurement_devices_open(self) -> None:
        class ReaderStub:
            is_ready = True

            def __init__(self) -> None:
                self.stop_calls = 0

            def stop(self) -> None:
                self.stop_calls += 1

        power_meter = ReaderStub()
        spectrometer = ReaderStub()
        self.window.power_meter_reader = power_meter  # type: ignore[assignment]
        self.window.spectrometer_reader = spectrometer  # type: ignore[assignment]
        self.window.automatic_test_state = AutomaticTestState.RAMPING_DOWN

        self.window.complete_automatic_test()

        self.assertIs(self.window.power_meter_reader, power_meter)
        self.assertIs(self.window.spectrometer_reader, spectrometer)
        self.assertEqual(power_meter.stop_calls, 0)
        self.assertEqual(spectrometer.stop_calls, 0)

    def test_completion_leaves_unused_spectrometer_off(self) -> None:
        self.window.auto_use_spectrometer_check.setChecked(False)
        self.window.automatic_test_settings = self.window.collect_automatic_test_settings()
        self.window.spectrometer_reader = None
        spectrometer_starts: list[bool] = []
        self.window.start_spectrometer = (  # type: ignore[method-assign]
            lambda: spectrometer_starts.append(True)
        )
        self.window.automatic_test_state = AutomaticTestState.RAMPING_DOWN

        self.window.complete_automatic_test()

        self.assertIsNone(self.window.spectrometer_reader)
        self.assertEqual(spectrometer_starts, [])


if __name__ == "__main__":
    unittest.main()
