from __future__ import annotations

import unittest
from unittest.mock import patch

from combined_test.devices import PowerMeterReaderThread
from combined_test.models import PowerMeterSettings
from tools import power_meter_mvp


class PowerMeterReconnectTests(unittest.TestCase):
    @staticmethod
    def _settings() -> PowerMeterSettings:
        return PowerMeterSettings(
            resource="ASRL7::INSTR",
            wavelength_nm=974.0,
            software_gain=0.935,
            interval_ms=0,
            stable_window_s=3.0,
            stability_limit_percent=0.2,
            driver_kind="caihuang",
            serial_number="PROBE-224050702",
        )

    def test_system_error_reopens_meter_and_continues_acquisition(self) -> None:
        actions: list[object] = []
        open_count = 0

        class FakeCaihuangPowerMeter:
            device_type = "Caihuang CHLP-P"

            def __init__(self, resource: str) -> None:
                nonlocal open_count
                open_count += 1
                self.open_number = open_count
                self.probe_serial_number = "PROBE-224050702"
                actions.append(("open", self.open_number, resource))

            def test(self) -> str:
                actions.append(("test", self.open_number))
                return "OK"

            def set_wavelength(self, wavelength_nm: float) -> None:
                actions.append(("wavelength", self.open_number, wavelength_nm))

            def read_power_w(self) -> float:
                actions.append(("read", self.open_number))
                if self.open_number == 1:
                    raise power_meter_mvp.pyvisa.errors.VisaIOError(-1073807360)
                return 100.0

            def close(self) -> None:
                actions.append(("close", self.open_number))

        reader = PowerMeterReaderThread(self._settings())
        readings = []
        failures: list[str] = []
        statuses: list[str] = []
        reader.reading.connect(lambda reading: (readings.append(reading), reader.stop()))
        reader.failed.connect(failures.append)
        reader.status.connect(statuses.append)

        with (
            patch.object(power_meter_mvp, "CaihuangPowerMeter", FakeCaihuangPowerMeter),
            patch("combined_test.devices.POWER_METER_RECONNECT_DELAY_S", 0.0),
        ):
            reader.run()

        self.assertEqual(open_count, 2)
        self.assertEqual([reading.power_w for reading in readings], [93.5])
        self.assertEqual(failures, [])
        self.assertTrue(any("自动重连成功" in status for status in statuses))
        self.assertIn(("close", 1), actions)
        self.assertIn(("close", 2), actions)

    def test_system_error_stops_after_three_failed_reconnects(self) -> None:
        open_count = 0

        class FakeCaihuangPowerMeter:
            def __init__(self, _resource: str) -> None:
                nonlocal open_count
                open_count += 1
                if open_count > 1:
                    raise RuntimeError("COM7 尚未恢复")
                self.probe_serial_number = "PROBE-224050702"

            def test(self) -> str:
                return "OK"

            def set_wavelength(self, _wavelength_nm: float) -> None:
                return None

            def read_power_w(self) -> float:
                raise power_meter_mvp.pyvisa.errors.VisaIOError(-1073807360)

            def close(self) -> None:
                return None

        reader = PowerMeterReaderThread(self._settings())
        failures: list[str] = []
        reader.failed.connect(failures.append)

        with (
            patch.object(power_meter_mvp, "CaihuangPowerMeter", FakeCaihuangPowerMeter),
            patch("combined_test.devices.POWER_METER_RECONNECT_DELAY_S", 0.0),
        ):
            reader.run()

        self.assertEqual(open_count, 4)
        self.assertEqual(len(failures), 1)
        self.assertIn("自动重连 3 次失败", failures[0])
        self.assertIn("COM7 尚未恢复", failures[0])


if __name__ == "__main__":
    unittest.main()
