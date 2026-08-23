from __future__ import annotations

import unittest
from unittest.mock import patch

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


class OphirPowerMeterReadTests(unittest.TestCase):
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
        meter._last_power_w = None

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


if __name__ == "__main__":
    unittest.main()
