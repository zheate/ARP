from __future__ import annotations

import unittest
from unittest.mock import patch

from combined_test.devices import PowerMeterReaderThread
from combined_test.models import PowerMeterSettings
from tools import power_meter_mvp
from tools.power_meter_mvp import OphirPowerMeter


class _DelayedOphirController:
    def __init__(self, empty_reads: int, value: float) -> None:
        self.empty_reads = empty_reads
        self.value = value
        self.read_count = 0

    def StartStream(self, _handle: int, _channel: int) -> None:
        return None

    def GetData(self, _handle: int, _channel: int):
        self.read_count += 1
        if self.read_count <= self.empty_reads:
            return (), (), ()
        return (self.value,), (123.0,), (0,)


class _SampleClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.now += duration


class _InterruptedOphirController:
    def __init__(self, clock: _SampleClock, resumes_at: float | None = None) -> None:
        self.clock = clock
        self.resumes_at = resumes_at
        self.sent_first = False

    def StartStream(self, _handle: int, _channel: int) -> None:
        return None

    def GetData(self, _handle: int, _channel: int):
        if not self.sent_first:
            self.sent_first = True
            return (1.0,), (0.0,), (0,)
        if self.resumes_at is not None and self.clock.now >= self.resumes_at:
            return (2.0,), (self.clock.now,), (0,)
        return (), (), ()


class OphirPowerMeterReadTests(unittest.TestCase):
    @staticmethod
    def _meter(controller) -> OphirPowerMeter:
        meter = OphirPowerMeter.__new__(OphirPowerMeter)
        meter.controller = controller
        meter.handle = 1000
        meter.channel = 0
        meter._stream_started = False
        return meter

    def test_missing_new_samples_raise_instead_of_replaying_previous_power(self) -> None:
        clock = _SampleClock()
        meter = self._meter(_InterruptedOphirController(clock))
        with (
            patch("tools.power_meter_mvp.time.monotonic", clock.monotonic),
            patch("tools.power_meter_mvp.time.sleep", clock.sleep),
        ):
            self.assertEqual(meter.read_power_w(), 1.0)
            with self.assertRaisesRegex(RuntimeError, "未返回.*测量数据"):
                meter.read_power_w()

    def test_next_sample_can_arrive_after_a_half_second_gap(self) -> None:
        clock = _SampleClock()
        meter = self._meter(_InterruptedOphirController(clock, resumes_at=0.8))
        with (
            patch("tools.power_meter_mvp.time.monotonic", clock.monotonic),
            patch("tools.power_meter_mvp.time.sleep", clock.sleep),
        ):
            self.assertEqual(meter.read_power_w(), 1.0)
            self.assertEqual(meter.read_power_w(), 2.0)
        self.assertGreaterEqual(clock.now, 0.8)

    def test_acquisition_reports_stream_loss_without_publishing_cached_samples(self) -> None:
        clock = _SampleClock()
        meter = self._meter(_InterruptedOphirController(clock))
        reader = PowerMeterReaderThread(
            PowerMeterSettings("OPHIR::USB::TEST", 976.0, 1.0, 0, 3.0, 0.2, "ophir")
        )
        readings = []
        errors = []

        def capture(reading) -> None:
            readings.append(reading)
            # Bound the old buggy implementation, which republishes indefinitely.
            if len(readings) > 1:
                reader.stop()

        reader.reading.connect(capture)
        reader.failed.connect(errors.append)
        with (
            patch.object(power_meter_mvp, "OphirPowerMeter", return_value=meter),
            patch.object(meter, "test", return_value="OK"),
            patch.object(meter, "set_wavelength"),
            patch.object(meter, "close") as close,
            patch("tools.power_meter_mvp.time.monotonic", clock.monotonic),
            patch("tools.power_meter_mvp.time.sleep", clock.sleep),
        ):
            reader.run()
        self.assertEqual([reading.power_w for reading in readings], [1.0])
        self.assertEqual(len(errors), 1)
        self.assertIn("未返回", errors[0])
        self.assertFalse(reader.is_ready)
        close.assert_called_once()

    def test_probe_uses_sensor_serial_for_gain_identity(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.closed_handles: list[int] = []

            def ScanUSB(self):
                return ("METER001",)

            def OpenUSBDevice(self, serial_number: str) -> int:
                self.asserted_serial = serial_number
                return 42

            def GetSensorInfo(self, handle: int, channel: int):
                self.sensor_request = (handle, channel)
                return "PROBE009", "Thermopile", "50(150)A-BB-26"

            def Close(self, handle: int) -> None:
                self.closed_handles.append(handle)

            def CloseAll(self) -> None:
                return None

        class PythonCom:
            def CoUninitialize(self) -> None:
                return None

        controller = Controller()
        with patch.object(
            power_meter_mvp,
            "_create_ophir_controller",
            return_value=(controller, PythonCom()),
        ):
            results = OphirPowerMeter.probe_all()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].resource, "OPHIR::USB::METER001")
        self.assertEqual(results[0].serial_number, "PROBE009")
        self.assertIn("USB 表头 SN METER001", results[0].detail)
        self.assertIn("探头 SN PROBE009", results[0].detail)
        self.assertEqual(controller.sensor_request, (42, 0))
        self.assertEqual(controller.closed_handles, [42])

    def test_first_sample_allows_for_the_meter_stream_to_warm_up(self) -> None:
        meter = OphirPowerMeter.__new__(OphirPowerMeter)
        meter.controller = _DelayedOphirController(empty_reads=6, value=0.25)
        meter.handle = 1000
        meter.channel = 0
        meter._stream_started = False

        monotonic_values = iter(float(index) / 10 for index in range(20))
        with (
            patch("tools.power_meter_mvp.time.monotonic", side_effect=monotonic_values),
            patch("tools.power_meter_mvp.time.sleep"),
        ):
            try:
                result = meter.read_power_w()
            except RuntimeError as error:
                self.fail(f"first sample timed out during normal stream warm-up: {error}")

        self.assertEqual(result, 0.25)
        self.assertEqual(meter.controller.read_count, 7)

    def test_supported_numeric_wavelength_selects_its_exact_preset(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.selections: list[tuple[int, int, int]] = []

            def GetWavelengths(self, _handle: int, _channel: int):
                return 0, ("532 nm", "976.000000 nm", "1064 nm")

            def SetWavelength(self, handle: int, channel: int, index: int) -> None:
                self.selections.append((handle, channel, index))

        controller = Controller()
        meter = self._meter(controller)
        meter.set_wavelength(976.0000005)

        self.assertEqual(controller.selections, [(1000, 0, 1)])

    def test_unsupported_numeric_wavelength_does_not_select_nearest_preset(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.selections: list[tuple[int, int, int]] = []

            def GetWavelengths(self, _handle: int, _channel: int):
                return 0, ("532 nm", "976 nm", "1064 nm")

            def SetWavelength(self, handle: int, channel: int, index: int) -> None:
                self.selections.append((handle, channel, index))

        controller = Controller()
        meter = self._meter(controller)
        with self.assertRaisesRegex(RuntimeError, "不支持校准波长 975.8 nm"):
            meter.set_wavelength(975.8)

        self.assertEqual(controller.selections, [])

    def test_named_only_wavelength_presets_fail_without_changing_the_device(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.selections: list[tuple[int, int, int]] = []

            def GetWavelengths(self, _handle: int, _channel: int):
                return 0, ("Broadband", "VIS", "NIR")

            def SetWavelength(self, handle: int, channel: int, index: int) -> None:
                self.selections.append((handle, channel, index))

        controller = Controller()
        meter = self._meter(controller)
        with self.assertRaisesRegex(RuntimeError, "仅返回命名波长预设"):
            meter.set_wavelength(976.0)

        self.assertEqual(controller.selections, [])


if __name__ == "__main__":
    unittest.main()
