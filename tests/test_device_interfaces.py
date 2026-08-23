from __future__ import annotations

import math
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from combined_test.device_interfaces import ControllerPowerSupply, PowerSupply
from combined_test.devices import (
    open_spectrometer_device,
    SpectrumFrameAverager,
    SpectrometerReaderThread,
    TdkReaderThread,
    TdkReading,
)
from combined_test.excel_export import ExcelTestRecord
from combined_test.models import SpectrumFrame, SpectrometerSettings
from combined_test.record_store import AttemptValidity, RecordStore, SessionRecordStore


class FakeController:
    is_connected = True
    output_enabled = True

    def __init__(self) -> None:
        self.writes: list[tuple[int, list[int]]] = []
        self.voltage_v = 0.0

    def i2c_write(self, address: int, command: list[int]) -> tuple[bool, str]:
        self.writes.append((address, command))
        return True, "ok"

    def i2c_write_read(
        self,
        _address: int,
        command: list[int],
        _read_length: int,
    ) -> tuple[bool, list[int]]:
        values = {0x88: 24.68, 0x8B: 12.34, 0x8C: 4.56, 0x8D: 37.25}
        value = values[command[1]]
        integer = int(value)
        return True, [0, 0, integer, round((value - integer) * 100)]

    def disconnect_device(self) -> bool:
        self.is_connected = False
        return True

    def set_output_voltage(self, voltage_v: float) -> None:
        self.voltage_v = voltage_v

    def set_output_enabled(self, enabled: bool) -> None:
        self.output_enabled = enabled


class DeviceInterfaceTests(unittest.TestCase):
    def test_stale_spectrometer_id_rebinds_to_the_only_online_device(self) -> None:
        class Control:
            def __init__(self) -> None:
                self.opened: list[int] = []

            def find_usb_devices(self) -> None:
                return

            def get_device_ids(self) -> list[int]:
                return [7]

            def open_device(self, device_id: int) -> None:
                self.opened.append(device_id)

        spectrometer = SimpleNamespace(control=Control(), device_id=None)

        opened_id = open_spectrometer_device(spectrometer, 5)

        self.assertEqual(opened_id, 7)
        self.assertEqual(spectrometer.device_id, 7)
        self.assertEqual(spectrometer.control.opened, [7])

    def test_stale_spectrometer_id_does_not_guess_between_multiple_devices(self) -> None:
        control = SimpleNamespace(
            find_usb_devices=lambda: None,
            get_device_ids=lambda: [7, 9],
        )
        spectrometer = SimpleNamespace(control=control, device_id=None)

        with self.assertRaisesRegex(RuntimeError, r"设备 ID 5.*\[7, 9\]"):
            open_spectrometer_device(spectrometer, 5)

    def test_remembered_spectrometer_serial_resolves_the_current_device_id(self) -> None:
        class Control:
            def __init__(self) -> None:
                self.opened: list[int] = []

            def find_usb_devices(self) -> None:
                return None

            def get_device_ids(self) -> list[int]:
                return [7, 9]

            def get_serial_number(self, device_id: int) -> str:
                return {7: "OCEAN-A", 9: "OCEAN-B"}[device_id]

            def open_device(self, device_id: int) -> None:
                self.opened.append(device_id)

        spectrometer = SimpleNamespace(control=Control(), device_id=None)

        opened_id = open_spectrometer_device(spectrometer, 5, "OCEAN-B")

        self.assertEqual(opened_id, 9)
        self.assertEqual(spectrometer.device_id, 9)
        self.assertEqual(spectrometer.control.opened, [9])

    def test_spectrum_frame_averager_emits_rolling_native_point_mean(self) -> None:
        averager = SpectrumFrameAverager(3)
        wavelength = [975.0, 976.0]

        self.assertIsNone(averager.add(wavelength, [3.0, 30.0]))
        self.assertIsNone(averager.add(wavelength, [6.0, 60.0]))
        self.assertEqual(
            averager.add(wavelength, [9.0, 90.0]),
            (wavelength, [6.0, 60.0]),
        )
        self.assertEqual(
            averager.add(wavelength, [12.0, 120.0]),
            (wavelength, [9.0, 90.0]),
        )

    def test_spectrum_frame_averager_restarts_when_wavelength_grid_changes(self) -> None:
        averager = SpectrumFrameAverager(2)

        self.assertIsNone(averager.add([975.0], [10.0]))
        self.assertIsNone(averager.add([976.0], [20.0]))
        self.assertEqual(averager.add([976.0], [30.0]), ([976.0], [25.0]))

    def test_spectrometer_publishes_and_analyzes_averaged_spectrum(self) -> None:
        frames = iter(([3.0, 30.0], [6.0, 60.0], [9.0, 90.0]))

        class FakeSpectrometer:
            def open_first(self) -> int:
                return 7

            def get_minimum_integration_time(self) -> int:
                return 3_800

            def get_maximum_integration_time(self) -> int:
                return 300_000

            def set_integration_time(self, _value: int) -> None:
                return

            def read_spectrum(self) -> tuple[list[float], list[float]]:
                return [975.0, 976.0], next(frames)

            def close(self) -> None:
                return

        analyzed: list[tuple[list[float], list[float]]] = []

        def calculate_stats(wavelength, intensity):
            analyzed.append((list(wavelength), list(intensity)))
            return SimpleNamespace(
                peak_wavelength_nm=976.0,
                centroid_nm=976.0,
                fwhm_nm=0.2,
            )

        reader = SpectrometerReaderThread(
            SpectrometerSettings(10_000, 50, average_frames=3)
        )
        wait_count = 0

        def stop_after_average(_timeout: float) -> bool:
            nonlocal wait_count
            wait_count += 1
            if wait_count >= 3:
                reader.stop()
                return True
            return False

        with (
            patch(
                "combined_test.devices.load_spectrometer_components",
                return_value=(FakeSpectrometer, calculate_stats),
            ),
            patch.object(
                reader._stop_requested,
                "wait",
                side_effect=stop_after_average,
            ),
        ):
            reader.run()

        expected = ([975.0, 976.0], [6.0, 60.0])
        latest_frame = reader.take_latest_spectrum()
        self.assertIsNotNone(latest_frame)
        self.assertEqual((latest_frame.wavelength, latest_frame.intensity), expected)
        self.assertIsNotNone(latest_frame.stats)
        self.assertEqual(analyzed, [expected])
        self.assertIsNotNone(latest_frame.saturation)
        self.assertFalse(latest_frame.saturation.saturated)
        self.assertEqual(latest_frame.peak_intensity, 60.0)
        self.assertTrue(math.isnan(latest_frame.pib))

    def test_analyzed_frame_keeps_pib_for_headless_snapshot(self) -> None:
        reader = SpectrometerReaderThread(
            SpectrometerSettings(10_000, 50),
            headless=True,
        )
        stats = SimpleNamespace(
            peak_wavelength_nm=976.0,
            centroid_nm=976.0,
            fwhm_nm=0.2,
        )
        reader._publish_analyzed_spectrum(
            [975.0, 976.0, 977.0],
            [10.0, 100.0, 20.0],
            stats,
        )

        frame = reader.take_latest_spectrum()
        self.assertIsNotNone(frame)
        self.assertTrue(math.isfinite(frame.pib))
        self.assertIsNotNone(frame.saturation)
        self.assertFalse(frame.saturation.saturated)
        self.assertEqual(frame.peak_intensity, 100.0)
        self.assertIsNotNone(frame.smsr)
        self.assertIsInstance(frame.peak_annotations, tuple)

    def test_analyzed_frame_marks_saturation_and_skips_smsr(self) -> None:
        reader = SpectrometerReaderThread(SpectrometerSettings(10_000, 50))
        stats = SimpleNamespace(
            peak_wavelength_nm=976.0,
            centroid_nm=976.0,
            fwhm_nm=0.2,
        )
        saturation_floor = reader.full_scale_counts * 0.995
        saturated = [saturation_floor + 1.0, saturation_floor + 2.0, saturation_floor + 1.0]
        reader._publish_analyzed_spectrum(
            [975.0, 976.0, 977.0],
            saturated,
            stats,
        )

        frame = reader.take_latest_spectrum()
        self.assertIsNotNone(frame)
        self.assertIsNotNone(frame.saturation)
        self.assertTrue(frame.saturation.saturated)
        self.assertIsNone(frame.smsr)
        self.assertEqual(frame.peak_annotations, ())
        self.assertTrue(math.isnan(frame.pib))

    def test_spectrometer_mailbox_keeps_only_the_latest_frame(self) -> None:
        reader = SpectrometerReaderThread(SpectrometerSettings(10_000, 50))
        first = SpectrumFrame([975.0], [10.0], None)
        latest = SpectrumFrame([976.0], [20.0], None)

        reader._publish_latest_spectrum(first)
        reader._publish_latest_spectrum(latest)

        self.assertEqual(reader.take_latest_spectrum(), latest)
        self.assertIsNone(reader.take_latest_spectrum())

    def test_spectrometer_mailbox_copies_reused_native_buffers(self) -> None:
        reader = SpectrometerReaderThread(SpectrometerSettings(10_000, 50))
        wavelength = [975.0, 976.0]
        intensity = [10.0, 20.0]

        reader._publish_latest_spectrum(SpectrumFrame(wavelength, intensity, None))
        wavelength[:] = [985.0, 986.0]
        intensity[:] = [60_000.0, 60_000.0]

        self.assertEqual(
            reader.take_latest_spectrum(),
            SpectrumFrame([975.0, 976.0], [10.0, 20.0], None),
        )

    def test_spectrometer_auto_integration_waits_for_stable_center_before_targeting_full_scale(self) -> None:
        integration_times: list[int] = []
        read_count = 0

        class FakeSpectrometer:
            def open_first(self) -> int:
                return 7

            def get_minimum_integration_time(self) -> int:
                return 1_000

            def get_maximum_integration_time(self) -> int:
                return 1_000_000

            def get_maximum_intensity(self) -> float:
                return 65_535.0

            def set_integration_time(self, value: int) -> None:
                integration_times.append(value)

            def read_spectrum(self) -> tuple[list[float], list[float]]:
                nonlocal read_count
                read_count += 1
                peak = 10_000.0 if read_count <= 5 else 40_000.0
                return [975.0, 976.0], [0.0, peak]

            def close(self) -> None:
                return

        reader = SpectrometerReaderThread(
            SpectrometerSettings(10_000, 50, auto_integration_enabled=True)
        )
        readings: list[object] = []
        full_scales: list[float] = []
        reader.reading.connect(readings.append)
        reader.full_scale_changed.connect(full_scales.append)
        wait_count = 0

        def stop_after_valid_frame(_timeout: float) -> bool:
            nonlocal wait_count
            wait_count += 1
            if wait_count >= 6:
                reader.stop()
                return True
            return False

        with (
            patch(
                "combined_test.devices.load_spectrometer_components",
                return_value=(
                    FakeSpectrometer,
                    lambda _x, _y: SimpleNamespace(
                        peak_wavelength_nm=976.0,
                        centroid_nm=976.0,
                        fwhm_nm=0.2,
                    ),
                ),
            ),
            patch.object(reader._stop_requested, "wait", side_effect=stop_after_valid_frame),
        ):
            reader.run()

        self.assertEqual(integration_times, [3_800, 151_900])
        self.assertEqual(full_scales, [65_535.0])
        self.assertEqual(len(readings), 5)

    def test_spectrometer_auto_integration_does_not_increase_without_center_wavelength(self) -> None:
        integration_times: list[int] = []

        class FakeSpectrometer:
            def open_first(self) -> int:
                return 7

            def get_minimum_integration_time(self) -> int:
                return 1_000

            def get_maximum_integration_time(self) -> int:
                return 1_000_000

            def get_maximum_intensity(self) -> float:
                return 65_535.0

            def set_integration_time(self, value: int) -> None:
                integration_times.append(value)

            def read_spectrum(self) -> tuple[list[float], list[float]]:
                return [975.0, 976.0], [0.0, 10_000.0]

            def close(self) -> None:
                return

        reader = SpectrometerReaderThread(
            SpectrometerSettings(10_000, 50, auto_integration_enabled=True)
        )
        wait_count = 0

        def stop_after_no_center_frames(_timeout: float) -> bool:
            nonlocal wait_count
            wait_count += 1
            if wait_count >= 6:
                reader.stop()
                return True
            return False

        with (
            patch(
                "combined_test.devices.load_spectrometer_components",
                return_value=(
                    FakeSpectrometer,
                    lambda _x, _y: SimpleNamespace(
                        peak_wavelength_nm=math.nan,
                        centroid_nm=math.nan,
                        fwhm_nm=math.nan,
                    ),
                ),
            ),
            patch.object(reader._stop_requested, "wait", side_effect=stop_after_no_center_frames),
        ):
            reader.run()

        self.assertEqual(integration_times, [3_800])

    def test_spectrometer_can_switch_from_auto_to_configured_integration_time(self) -> None:
        integration_times: list[int] = []
        read_count = 0

        class FakeSpectrometer:
            def open_first(self) -> int:
                return 7

            def get_minimum_integration_time(self) -> int:
                return 1_000

            def get_maximum_integration_time(self) -> int:
                return 1_000_000

            def get_maximum_intensity(self) -> float:
                return 65_535.0

            def set_integration_time(self, value: int) -> None:
                integration_times.append(value)

            def read_spectrum(self) -> tuple[list[float], list[float]]:
                nonlocal read_count
                read_count += 1
                if read_count == 1:
                    reader.use_fixed_integration_time(25_000)
                else:
                    reader.stop()
                return [975.0, 976.0], [0.0, 10_000.0]

            def close(self) -> None:
                return

        reader = SpectrometerReaderThread(
            SpectrometerSettings(10_000, 50, auto_integration_enabled=True)
        )
        with (
            patch(
                "combined_test.devices.load_spectrometer_components",
                return_value=(
                    FakeSpectrometer,
                    lambda _x, _y: SimpleNamespace(
                        peak_wavelength_nm=976.0,
                        centroid_nm=976.0,
                        fwhm_nm=0.2,
                    ),
                ),
            ),
            patch.object(reader._stop_requested, "wait", return_value=False),
        ):
            reader.run()

        self.assertEqual(integration_times, [3_800, 25_000])

    def test_spectrometer_does_not_publish_frame_from_previous_integration_time(self) -> None:
        integration_times: list[int] = []

        class FakeSpectrometer:
            def open_first(self) -> int:
                return 7

            def get_minimum_integration_time(self) -> int:
                return 3_800

            def get_maximum_integration_time(self) -> int:
                return 300_000

            def get_maximum_intensity(self) -> float:
                return 64_000.0

            def set_integration_time(self, value: int) -> None:
                integration_times.append(value)

            def read_spectrum(self) -> tuple[list[float], list[float]]:
                return [975.0, 976.0], [0.0, 10_000.0]

            def close(self) -> None:
                return

        reader = SpectrometerReaderThread(
            SpectrometerSettings(10_000, 50, auto_integration_enabled=True)
        )

        wait_count = 0

        def stop_after_adjustment(_timeout: float) -> bool:
            nonlocal wait_count
            wait_count += 1
            if wait_count >= 5:
                reader.stop()
                return True
            return False

        with (
            patch(
                "combined_test.devices.load_spectrometer_components",
                return_value=(
                    FakeSpectrometer,
                    lambda _x, _y: SimpleNamespace(
                        peak_wavelength_nm=976.0,
                        centroid_nm=976.0,
                        fwhm_nm=0.2,
                    ),
                ),
            ),
            patch.object(
                reader._stop_requested,
                "wait",
                side_effect=stop_after_adjustment,
            ),
        ):
            reader.run()

        self.assertEqual(integration_times, [3_800, 151_900])
        self.assertIsNone(reader.take_latest_spectrum())

    def test_spectrometer_auto_integration_pauses_after_ten_adjustments(self) -> None:
        class FakeSpectrometer:
            def open_first(self) -> int:
                return 7

            def get_minimum_integration_time(self) -> int:
                return 3_800

            def get_maximum_integration_time(self) -> int:
                return 300_000

            def get_maximum_intensity(self) -> float:
                return 64_000.0

            def set_integration_time(self, _value: int) -> None:
                return

            def read_spectrum(self) -> tuple[list[float], list[float]]:
                return [975.0, 976.0], [0.0, 64_000.0]

            def close(self) -> None:
                return

        reader = SpectrometerReaderThread(
            SpectrometerSettings(10_000, 50, auto_integration_enabled=True)
        )
        failures: list[dict[str, object]] = []

        def capture_failure(details: dict[str, object]) -> None:
            failures.append(details)
            reader.stop()

        reader.auto_integration_failed.connect(capture_failure)
        with (
            patch(
                "combined_test.devices.load_spectrometer_components",
                return_value=(
                    FakeSpectrometer,
                    lambda _x, _y: SimpleNamespace(
                        peak_wavelength_nm=976.0,
                        centroid_nm=976.0,
                        fwhm_nm=0.2,
                    ),
                ),
            ),
            patch.object(reader._stop_requested, "wait", return_value=False),
        ):
            reader.run()

        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["adjustment_count"], 10)
        self.assertEqual(failures[0]["peak_counts"], 64_000.0)

    def test_spectrometer_startup_retries_once_after_transient_failure(self) -> None:
        attempts: list[object] = []

        class FakeSpectrometer:
            def __init__(self) -> None:
                attempts.append(self)

            def open_first(self) -> int:
                if len(attempts) == 1:
                    raise OSError("transient USB session")
                return 7

            def get_minimum_integration_time(self) -> int:
                return 3800

            def get_maximum_integration_time(self) -> int:
                return 10_000_000

            def set_integration_time(self, _value: int) -> None:
                return

            def read_spectrum(self) -> tuple[list[float], list[float]]:
                reader.stop()
                return [975.0, 976.0], [10.0, 20.0]

            def close(self) -> None:
                return

        reader = SpectrometerReaderThread(SpectrometerSettings(10_000, 50))
        ready: list[bool] = []
        opened_device_ids: list[int] = []
        failures: list[str] = []
        reader.ready.connect(lambda: ready.append(True))
        reader.device_opened.connect(opened_device_ids.append)
        reader.failed.connect(failures.append)
        with (
            patch(
                "combined_test.devices.load_spectrometer_components",
                return_value=(
                    FakeSpectrometer,
                    lambda _x, _y: SimpleNamespace(
                        peak_wavelength_nm=976.0,
                        centroid_nm=976.0,
                        fwhm_nm=0.2,
                    ),
                ),
            ),
            patch.object(reader._stop_requested, "wait", return_value=False),
        ):
            reader.run()

        self.assertEqual(len(attempts), 2)
        self.assertEqual(opened_device_ids, [7])
        self.assertEqual(ready, [True])
        self.assertEqual(failures, [])

    def test_spectrometer_startup_failure_keeps_original_exception(self) -> None:
        class FailingSpectrometer:
            def open_first(self) -> int:
                raise OSError("USB endpoint unavailable")

            def close(self) -> None:
                return

        reader = SpectrometerReaderThread(SpectrometerSettings(10_000, 50))
        failures: list[str] = []
        reader.failed.connect(failures.append)
        with (
            patch(
                "combined_test.devices.load_spectrometer_components",
                return_value=(FailingSpectrometer, lambda _x, _y: None),
            ),
            patch.object(reader._stop_requested, "wait", return_value=False),
        ):
            reader.run()

        self.assertEqual(len(failures), 1)
        self.assertIn("OSError: USB endpoint unavailable", failures[0])

    def test_spectrometer_stop_during_retry_delay_does_not_reopen_device(self) -> None:
        attempts: list[object] = []

        class FailingSpectrometer:
            def __init__(self) -> None:
                attempts.append(self)

            def open_first(self) -> int:
                raise OSError("USB endpoint unavailable")

            def close(self) -> None:
                return

        reader = SpectrometerReaderThread(SpectrometerSettings(10_000, 50))

        def request_stop(_timeout: float) -> bool:
            reader.stop()
            return True

        failures: list[str] = []
        reader.failed.connect(failures.append)
        with (
            patch(
                "combined_test.devices.load_spectrometer_components",
                return_value=(FailingSpectrometer, lambda _x, _y: None),
            ),
            patch.object(reader._stop_requested, "wait", side_effect=request_stop),
        ):
            reader.run()

        self.assertEqual(len(attempts), 1)
        self.assertEqual(failures, [])

    def test_controller_adapter_exposes_semantic_power_supply_operations(self) -> None:
        controller = FakeController()
        supply = ControllerPowerSupply(controller)

        self.assertIsInstance(supply, PowerSupply)
        supply.set_current(3.25)

        self.assertEqual(controller.writes, [(0x41, [0xB4, 0xFF, 3, 25])])
        self.assertAlmostEqual(supply.read_input_voltage(), 24.68)
        self.assertAlmostEqual(supply.read_output_voltage(), 12.34)
        self.assertAlmostEqual(supply.read_output_current(), 4.56)
        self.assertAlmostEqual(supply.read_temperature(), 37.25)
        supply.set_voltage(24.0)
        supply.set_output_enabled(False)
        self.assertEqual(controller.voltage_v, 24.0)
        self.assertFalse(controller.output_enabled)

    def test_serial_controller_uses_direct_operations_without_i2c_compatibility(self) -> None:
        class FakeSerialController:
            is_connected = True
            output_enabled = True

            def __init__(self) -> None:
                self.current_a = 0.0

            def set_output_current(self, current_a: float) -> None:
                self.current_a = current_a

            def read_output_voltage(self) -> float:
                return 29.5

            def read_output_current(self) -> float:
                return 2.0

            def i2c_write(self, *_args: object) -> tuple[bool, str]:
                raise AssertionError("RS-232 controller must not use i2c_write")

            def i2c_write_read(self, *_args: object) -> tuple[bool, list[int]]:
                raise AssertionError("RS-232 controller must not use i2c_write_read")

        controller = FakeSerialController()
        supply = ControllerPowerSupply(controller)

        supply.set_current(2.0)

        self.assertEqual(controller.current_a, 2.0)
        self.assertEqual(supply.read_output_voltage(), 29.5)
        self.assertEqual(supply.read_output_current(), 2.0)

    def test_controller_adapter_rejects_unconfirmed_disconnect(self) -> None:
        controller = FakeController()
        controller.disconnect_device = lambda: False  # type: ignore[method-assign]
        supply = ControllerPowerSupply(controller)

        with self.assertRaisesRegex(RuntimeError, "无法确认设备已经断开"):
            supply.disconnect()

    def test_session_record_store_owns_session_and_pending_record_state(self) -> None:
        store = SessionRecordStore()
        started_at = datetime(2026, 7, 12, 9, 30, 0)
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "records"
            path = store.begin_session(
                output_dir,
                "SN-1",
                started_at,
                test_station="老化站 1",
            )
            self.assertEqual(
                path,
                output_dir / "SN-1" / "老化站 1" / "2026_07_12_09_30.xlsx",
            )
            self.assertTrue(path.parent.is_dir())
            self.assertFalse((output_dir / "index.sqlite3").exists())

            record = ExcelTestRecord(1, 2, 3, 0.5, 976, 976, 1, 0.8, [975, 976], [1, 2])

            store.queue(record)

            self.assertIsInstance(store, RecordStore)
            self.assertEqual(store.unsaved_records(), (record,))
            store.mark_saved((record,))
            self.assertEqual(store.unsaved_records(), ())

    def test_session_record_store_keeps_attempts_in_memory_without_a_database(self) -> None:
        store = SessionRecordStore()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "records"
            store.begin_session(
                output_dir,
                "SN-DB",
                datetime(2026, 7, 12, 9, 30, 0),
                test_station="老化站 1",
            )
            record = ExcelTestRecord(1, 2, 3, 0.5, 976, 976, 1, 0.8, [975, 976], [1, 2])
            store.queue(record)
            session_id = store.current_session.session_id  # type: ignore[union-attr]

            self.assertEqual(len(store.list_attempts(session_id)), 1)
            self.assertFalse(hasattr(store, "archive"))
            self.assertFalse((output_dir / "index.sqlite3").exists())

    def test_session_record_store_appends_shutdown_diagnostics_to_invalid_attempt(self) -> None:
        store = SessionRecordStore()
        with tempfile.TemporaryDirectory() as temp_dir:
            store.begin_session(
                Path(temp_dir),
                "SN-PROTECTION",
                datetime(2026, 7, 30, 9, 30, 0),
                test_station="老化站 1",
            )
            store.record_invalid_attempt(
                10.0,
                AttemptValidity.DEVICE_ERROR,
                "功率下降保护",
            )
            session_id = store.current_session.session_id  # type: ignore[union-attr]

            updated = store.append_latest_invalid_attempt_reason(
                10.0,
                "I2C 通信失败；保护下电未确认",
            )

            self.assertTrue(updated)
            attempts = store.list_attempts(session_id)
            self.assertIn("功率下降保护", attempts[-1].invalid_reason)
            self.assertIn("I2C 通信失败", attempts[-1].invalid_reason)
            self.assertIn("保护下电未确认", attempts[-1].invalid_reason)


class TdkReaderThreadTests(unittest.TestCase):
    def test_polls_current_and_voltage_and_publishes_readings(self) -> None:
        class FakeSupply:
            def __init__(self) -> None:
                self.pairs = [(1.0, 12.0), (2.0, 12.5)]
                self.current_calls = 0
                self.voltage_calls = 0

            def read_output_current(self) -> float:
                pair = self.pairs[min(self.current_calls, len(self.pairs) - 1)]
                self.current_calls += 1
                return pair[0]

            def read_output_voltage(self) -> float:
                pair = self.pairs[min(self.voltage_calls, len(self.pairs) - 1)]
                self.voltage_calls += 1
                return pair[1]

        reader = TdkReaderThread(FakeSupply())
        emissions: list[TdkReading] = []
        reader.reading.connect(emissions.append)
        wait_count = 0

        def stop_after_two_cycles(_timeout: float) -> bool:
            nonlocal wait_count
            wait_count += 1
            if wait_count >= 2:
                reader.stop()
                return True
            return False

        with patch.object(reader._stop_requested, "wait", side_effect=stop_after_two_cycles):
            reader.run()

        self.assertEqual(
            emissions,
            [TdkReading(1.0, 12.0), TdkReading(2.0, 12.5)],
        )
        self.assertEqual(reader.take_latest_reading(), TdkReading(2.0, 12.5))
        self.assertIsNone(reader.take_latest_reading())

    def test_reports_failure_once_then_recovers(self) -> None:
        class FlakySupply:
            def __init__(self) -> None:
                self.current_calls = 0

            def read_output_current(self) -> float:
                self.current_calls += 1
                if self.current_calls <= 2:
                    raise RuntimeError("TDK 无响应")
                return 3.0

            def read_output_voltage(self) -> float:
                return 13.0

        reader = TdkReaderThread(FlakySupply())
        failures: list[str] = []
        statuses: list[str] = []
        emissions: list[TdkReading] = []
        reader.failed.connect(failures.append)
        reader.status.connect(statuses.append)
        reader.reading.connect(emissions.append)
        wait_count = 0

        def stop_after_four_cycles(_timeout: float) -> bool:
            nonlocal wait_count
            wait_count += 1
            if wait_count >= 4:
                reader.stop()
                return True
            return False

        with patch.object(reader._stop_requested, "wait", side_effect=stop_after_four_cycles):
            reader.run()

        self.assertEqual(failures, ["TDK 无响应"])
        self.assertEqual(statuses, ["TDK 读数已恢复"])
        self.assertEqual(
            emissions,
            [TdkReading(3.0, 13.0), TdkReading(3.0, 13.0)],
        )


if __name__ == "__main__":
    unittest.main()
