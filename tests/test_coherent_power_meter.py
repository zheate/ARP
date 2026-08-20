from __future__ import annotations

import math
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from combined_test.devices import PowerMeterDetectThread, PowerMeterReaderThread
from combined_test.models import PowerMeterSettings
from tools import power_meter_mvp


class FakeVisaInstrument:
    def __init__(self) -> None:
        self.timeout = 0
        self.write_termination = ""
        self.read_termination = ""
        self.baud_rate = 0
        self.data_bits = 0
        self.parity = None
        self.stop_bits = None
        self.flow_control = None
        self.writes: list[str] = []
        self.closed = False

    def query(self, command: str) -> str:
        replies = {
            "*IDN?": "Coherent, Inc - PowerMax USB - V1.1",
            "SYSTem:INFormation:MODel?": '"PM150-50C"',
            "SYSTem:INFormation:SNUMber?": '"123456"',
            "CONFigure:MEASure?": "W",
            "CONFigure:WAVElength?": "976.5",
            "READ?": "1.23450E+01,0,1234",
        }
        return replies[command]

    def write(self, command: str) -> None:
        self.writes.append(command)

    def close(self) -> None:
        self.closed = True


class FakeVisaManager:
    def __init__(self, instrument: FakeVisaInstrument) -> None:
        self.instrument = instrument
        self.opened_resources: list[str] = []

    def open_resource(self, resource: str) -> FakeVisaInstrument:
        self.opened_resources.append(resource)
        return self.instrument


class CoherentPowerMeterTests(unittest.TestCase):
    def test_toolkit_free_reader_preserves_the_standalone_thread_contract(self) -> None:
        actions: list[object] = []

        class FakeMeter:
            device_type = "Fake meter"

            def __init__(self, resource: str) -> None:
                actions.append(("open", resource))

            def test(self) -> str:
                return "OK"

            def set_wavelength(self, wavelength_nm: float) -> None:
                actions.append(("wavelength", wavelength_nm))

            def read_power_w(self) -> float:
                return 1.25

            def close(self) -> None:
                actions.append("close")

        reader = power_meter_mvp.PowerReaderThread(
            resource="ASRL3::INSTR",
            wavelength_nm=976.0,
            software_gain=2.0,
            interval_ms=0,
        )
        samples: list[float] = []

        def on_sample(_elapsed_s: float, power_w: float) -> None:
            samples.append(power_w)
            reader.stop()

        reader.sample.connect(on_sample)
        with patch.object(power_meter_mvp, "CaihuangPowerMeter", FakeMeter):
            reader.start()
            self.assertTrue(reader.wait(1_000))

        self.assertEqual(samples, [2.5])
        self.assertEqual(actions, [("open", "ASRL3::INSTR"), ("wavelength", 976.0), "close"])

    def test_probe_identifies_pm150_and_configures_documented_serial_port(self) -> None:
        instrument = FakeVisaInstrument()
        manager = FakeVisaManager(instrument)
        with (
            patch.object(power_meter_mvp, "acquire_visa_resource_manager", return_value=manager),
            patch.object(power_meter_mvp, "release_visa_resource_manager"),
        ):
            result = power_meter_mvp.CoherentPowerMeter.probe("COM7", timeout_ms=250)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.resource, "ASRL7::INSTR")
        self.assertEqual(result.device_type, "Coherent PowerMax")
        self.assertEqual(result.driver_kind, "coherent")
        self.assertIn("PM150-50C", result.detail)
        self.assertIn("SN 123456", result.detail)
        self.assertEqual(instrument.timeout, 250)
        self.assertEqual(instrument.baud_rate, 9600)
        self.assertEqual(instrument.data_bits, 8)
        self.assertEqual(instrument.write_termination, "\r")
        self.assertEqual(instrument.read_termination, "\r\n")
        self.assertTrue(instrument.closed)

    def test_adapter_sets_watts_and_wavelength_reads_power_and_controls_zero(self) -> None:
        instrument = FakeVisaInstrument()
        manager = FakeVisaManager(instrument)
        with (
            patch.object(power_meter_mvp, "acquire_visa_resource_manager", return_value=manager),
            patch.object(power_meter_mvp, "release_visa_resource_manager") as release,
        ):
            meter = power_meter_mvp.CoherentPowerMeter("ASRL7::INSTR")
            self.assertEqual(meter.test(), "OK")
            self.assertEqual(meter.device_type, "Coherent PM150-50C")
            meter.set_power_mode()
            meter.set_wavelength(976.5)
            self.assertTrue(math.isclose(meter.read_power_w(), 12.345))
            meter.set_relative_zero(True)
            meter.set_relative_zero(False)
            meter.close()

        self.assertEqual(
            instrument.writes,
            [
                "CONFigure:MEASure W",
                "CONFigure:WAVElength 976.5",
                "CONFigure:ZERO",
                "*RST",
            ],
        )
        self.assertTrue(instrument.closed)
        release.assert_called_once_with(manager)

    def test_read_record_rejects_overrange_and_overtemperature(self) -> None:
        self.assertEqual(power_meter_mvp.parse_coherent_power_w("-1.0E-3,N,42"), -0.001)
        with self.assertRaisesRegex(RuntimeError, "量程溢出"):
            power_meter_mvp.parse_coherent_power_w("1.0E+2,R,43")
        with self.assertRaisesRegex(RuntimeError, "过温"):
            power_meter_mvp.parse_coherent_power_w("1.0E+1,T,44")

    def test_combined_detection_includes_coherent_serial_meter(self) -> None:
        probe_result = power_meter_mvp.ProbeResult(
            "ASRL7::INSTR",
            "Coherent PowerMax",
            "PM150-50C | SN 123456",
            "coherent",
        )

        class ResourceManager:
            def list_resources(self) -> tuple[str, ...]:
                return ("ASRL7::INSTR",)

        @contextmanager
        def fake_visa_resource_manager():
            yield ResourceManager()

        detected: list[object] = []
        statuses: list[str] = []
        thread = PowerMeterDetectThread()
        thread.detected.connect(detected.extend)
        thread.status.connect(statuses.append)
        with (
            patch("combined_test.devices.visa_resource_manager", fake_visa_resource_manager),
            patch.object(power_meter_mvp.CaihuangPowerMeter, "probe", return_value=None),
            patch.object(power_meter_mvp.CoherentPowerMeter, "probe", return_value=probe_result),
            patch.object(power_meter_mvp.LaserPointPowerMeter, "probe", return_value=None),
            patch.object(power_meter_mvp.OphirPowerMeter, "probe_all", return_value=[]),
        ):
            thread.run()

        self.assertEqual(len(detected), 1)
        self.assertEqual(detected[0].driver_kind, "coherent")
        self.assertIn("PM150-50C", detected[0].detail)
        self.assertTrue(any(status.startswith("正在检测串口 1/1：ASRL7::INSTR") for status in statuses))
        self.assertEqual(statuses[-1], "识别完成：发现 1 台兼容功率计")

    def test_ophir_detection_recovers_when_saved_serial_is_no_longer_present(self) -> None:
        replacement = power_meter_mvp.ProbeResult(
            "OPHIR::USB::963826",
            "Ophir USB",
            "USB SN 963826",
            "ophir",
        )

        class ResourceManager:
            def list_resources(self) -> tuple[str, ...]:
                return ()

        @contextmanager
        def fake_visa_resource_manager():
            yield ResourceManager()

        def probe_all(resource: str = "", timeout_ms: int = 1000):
            del timeout_ms
            return [replacement] if resource == "" else []

        detected: list[object] = []
        thread = PowerMeterDetectThread(preferred_resource="OPHIR::USB::3103403")
        thread.detected.connect(detected.extend)
        with (
            patch("combined_test.devices.visa_resource_manager", fake_visa_resource_manager),
            patch.object(power_meter_mvp.OphirPowerMeter, "probe_all", side_effect=probe_all),
        ):
            thread.run()

        self.assertEqual([option.resource for option in detected], ["OPHIR::USB::963826"])

    def test_combined_reader_configures_selected_coherent_driver(self) -> None:
        actions: list[object] = []

        class FakeCoherentPowerMeter:
            device_type = "Coherent PM150-50C"

            def __init__(self, resource: str) -> None:
                actions.append(("open", resource))

            def test(self) -> str:
                actions.append("test")
                return "OK"

            def set_power_mode(self) -> None:
                actions.append("power_mode")

            def set_wavelength(self, wavelength_nm: float) -> None:
                actions.append(("wavelength", wavelength_nm))

            def close(self) -> None:
                actions.append("close")

        reader = PowerMeterReaderThread(
            PowerMeterSettings(
                resource="ASRL8::INSTR",
                wavelength_nm=976.5,
                software_gain=1.0,
                interval_ms=300,
                stable_window_s=1.0,
                stable_tolerance_w=0.1,
                driver_kind="coherent",
            )
        )
        reader.stop()
        with patch.object(power_meter_mvp, "CoherentPowerMeter", FakeCoherentPowerMeter):
            reader.run()

        self.assertEqual(
            actions,
            [
                ("open", "ASRL8::INSTR"),
                "test",
                "power_mode",
                ("wavelength", 976.5),
                "close",
            ],
        )


if __name__ == "__main__":
    unittest.main()
