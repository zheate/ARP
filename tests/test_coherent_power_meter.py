from __future__ import annotations

import math
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from combined_test.devices import PowerMeterDetectThread, PowerMeterReaderThread
from combined_test.models import PowerMeterReading, PowerMeterSettings
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
            "SYSTem:INFormation:INSTrument:SNUMber?": '"METER123"',
            "SYSTem:INFormation:PROBe:SNUMber?": '"PROBE456"',
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
    def test_caihuang_probe_reads_head_and_probe_identity_with_hin(self) -> None:
        class CaihuangInstrument(FakeVisaInstrument):
            def query(self, command: str) -> str:
                return {
                    "$TES": "OK",
                    "$VER": "V2.1",
                    "$HIN": "$HIN=SN:HEAD001,PN:PROBE002",
                }[command]

        instrument = CaihuangInstrument()
        manager = FakeVisaManager(instrument)
        with (
            patch.object(power_meter_mvp, "acquire_visa_resource_manager", return_value=manager),
            patch.object(power_meter_mvp, "release_visa_resource_manager"),
        ):
            result = power_meter_mvp.CaihuangPowerMeter.probe("COM3", timeout_ms=250)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.serial_number, "PROBE002")
        self.assertIn("HIN SN:HEAD001,PN:PROBE002", result.detail)
        self.assertIn("增益身份：探头 SN PROBE002", result.detail)
        self.assertTrue(instrument.closed)

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
        self.assertIn("表头 SN METER123", result.detail)
        self.assertIn("探头 SN PROBE456", result.detail)
        self.assertEqual(result.serial_number, "PROBE456")
        self.assertEqual(instrument.timeout, 250)
        self.assertEqual(instrument.baud_rate, 9600)
        self.assertEqual(instrument.data_bits, 8)
        self.assertEqual(instrument.write_termination, "\r")
        self.assertEqual(instrument.read_termination, "\r\n")
        self.assertTrue(instrument.closed)

    def test_laserpoint_probe_uses_head_serial_for_gain_identity(self) -> None:
        class LaserPointInstrument(FakeVisaInstrument):
            def query(self, command: str) -> str:
                self.asserted_command = command
                return "#SERNU 654321;"

        instrument = LaserPointInstrument()
        manager = FakeVisaManager(instrument)
        with (
            patch.object(power_meter_mvp, "acquire_visa_resource_manager", return_value=manager),
            patch.object(power_meter_mvp, "release_visa_resource_manager"),
        ):
            result = power_meter_mvp.LaserPointPowerMeter.probe("COM9", timeout_ms=250)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.serial_number, "654321")
        self.assertEqual(result.detail, "探头 SN 654321")
        self.assertEqual(instrument.asserted_command, "*SERNU")
        self.assertEqual(instrument.baud_rate, 38400)
        self.assertTrue(instrument.closed)

    def test_laserpoint_normalizes_active_milliwatt_scale_to_watts(self) -> None:
        class LaserPointInstrument(FakeVisaInstrument):
            def __init__(self) -> None:
                super().__init__()
                self.queries: list[str] = []

            def query(self, command: str) -> str:
                self.queries.append(command)
                return {
                    "*OUTPM": "860.0;",
                    "*X1D": "#5;",
                    "*FSWX1 2": "#1000.00_mW;",
                }[command]

        instrument = LaserPointInstrument()
        manager = FakeVisaManager(instrument)
        with (
            patch.object(power_meter_mvp, "acquire_visa_resource_manager", return_value=manager),
            patch.object(power_meter_mvp, "release_visa_resource_manager"),
        ):
            meter = power_meter_mvp.LaserPointPowerMeter("COM9")
            self.assertTrue(math.isclose(meter.read_power_w(), 0.86))
            self.assertTrue(math.isclose(meter.read_power_w(), 0.86))
            meter.close()

        self.assertEqual(
            instrument.queries,
            [
                "*X1D", "*OUTPM", "*X1D", "*FSWX1 2",
                "*X1D", "*OUTPM", "*X1D",
            ],
        )

    def test_laserpoint_preserves_active_watt_scale_readings(self) -> None:
        class LaserPointInstrument(FakeVisaInstrument):
            def query(self, command: str) -> str:
                return {
                    "*OUTPM": "860.0;",
                    "*X1D": "#4;",
                    "*FSWX1 1": "#1000.0_W;",
                }[command]

        instrument = LaserPointInstrument()
        manager = FakeVisaManager(instrument)
        with (
            patch.object(power_meter_mvp, "acquire_visa_resource_manager", return_value=manager),
            patch.object(power_meter_mvp, "release_visa_resource_manager"),
        ):
            meter = power_meter_mvp.LaserPointPowerMeter("COM9")
            self.assertTrue(math.isclose(meter.read_power_w(), 860.0))
            meter.close()

    def test_laserpoint_normalizes_active_kilowatt_scale_to_watts(self) -> None:
        class LaserPointInstrument(FakeVisaInstrument):
            def query(self, command: str) -> str:
                return {
                    "*OUTPM": "0.5;",
                    "*X1D": "#3;",
                    "*FSWX1 0": "#4.5000_kW;",
                }[command]

        instrument = LaserPointInstrument()
        manager = FakeVisaManager(instrument)
        with (
            patch.object(power_meter_mvp, "acquire_visa_resource_manager", return_value=manager),
            patch.object(power_meter_mvp, "release_visa_resource_manager"),
        ):
            meter = power_meter_mvp.LaserPointPowerMeter("COM9")
            self.assertTrue(math.isclose(meter.read_power_w(), 500.0))
            meter.close()

    def test_laserpoint_retries_when_auto_gain_changes_during_read(self) -> None:
        class LaserPointInstrument(FakeVisaInstrument):
            def __init__(self) -> None:
                super().__init__()
                self.gain_replies = iter(("#5;", "#4;", "#4;", "#4;"))
                self.power_replies = iter(("420.0;", "0.4784;"))

            def query(self, command: str) -> str:
                if command == "*X1D":
                    return next(self.gain_replies)
                if command == "*OUTPM":
                    return next(self.power_replies)
                if command == "*FSWX1 1":
                    return "#3.0000_kW;"
                raise AssertionError(command)

        instrument = LaserPointInstrument()
        manager = FakeVisaManager(instrument)
        with (
            patch.object(power_meter_mvp, "acquire_visa_resource_manager", return_value=manager),
            patch.object(power_meter_mvp, "release_visa_resource_manager"),
        ):
            meter = power_meter_mvp.LaserPointPowerMeter("COM9")
            self.assertTrue(math.isclose(meter.read_power_w(), 478.4))
            meter.close()

    def test_laserpoint_rejects_value_impossible_for_reported_scale(self) -> None:
        class LaserPointInstrument(FakeVisaInstrument):
            def query(self, command: str) -> str:
                return {
                    "*OUTPM": "420.0;",
                    "*X1D": "#4;",
                    "*FSWX1 1": "#3.0000_kW;",
                }[command]

        instrument = LaserPointInstrument()
        manager = FakeVisaManager(instrument)
        with (
            patch.object(power_meter_mvp, "acquire_visa_resource_manager", return_value=manager),
            patch.object(power_meter_mvp, "release_visa_resource_manager"),
        ):
            meter = power_meter_mvp.LaserPointPowerMeter("COM9")
            with self.assertRaisesRegex(RuntimeError, "自动量程切换"):
                meter.read_power_w()
            meter.close()

    def test_laserpoint_rejects_unknown_full_scale_unit(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "功率量程单位不受支持"):
            power_meter_mvp.parse_laserpoint_full_scale_power_to_w("#4.5_MW;")

    def test_laserpoint_rejects_fractional_wavelength_without_sending_a_command(self) -> None:
        instrument = FakeVisaInstrument()
        meter = power_meter_mvp.LaserPointPowerMeter.__new__(
            power_meter_mvp.LaserPointPowerMeter
        )
        meter.inst = instrument
        meter._power_ranges_to_w = {0: 1.0}

        with self.assertRaisesRegex(RuntimeError, "波长必须是整数 nm"):
            meter.set_wavelength(976.5)

        self.assertEqual(instrument.writes, [])
        self.assertEqual(meter._power_ranges_to_w, {0: 1.0})

    def test_laserpoint_sends_supported_integer_wavelength_unchanged(self) -> None:
        instrument = FakeVisaInstrument()
        meter = power_meter_mvp.LaserPointPowerMeter.__new__(
            power_meter_mvp.LaserPointPowerMeter
        )
        meter.inst = instrument
        meter._power_ranges_to_w = {0: 1.0}

        meter.set_wavelength(976.0)

        self.assertEqual(instrument.writes, ["*SETLAM00976"])
        self.assertEqual(meter._power_ranges_to_w, {})

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
                stability_limit_percent=0.2,
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

    def test_combined_reader_rejects_a_different_probe_before_acquisition(self) -> None:
        class FakeCoherentPowerMeter:
            device_type = "Coherent PM150-50C"

            def __init__(self, _resource: str) -> None:
                self.probe_serial_number = "PROBE-NEW"

            def test(self) -> str:
                return "OK"

            def close(self) -> None:
                return None

        reader = PowerMeterReaderThread(
            PowerMeterSettings(
                resource="ASRL8::INSTR",
                wavelength_nm=976.5,
                software_gain=1.0,
                interval_ms=300,
                stable_window_s=1.0,
                stability_limit_percent=0.2,
                driver_kind="coherent",
                serial_number="PROBE-SAVED",
            )
        )
        failures: list[str] = []
        reader.failed.connect(failures.append)

        with patch.object(power_meter_mvp, "CoherentPowerMeter", FakeCoherentPowerMeter):
            reader.run()

        self.assertEqual(len(failures), 1)
        self.assertIn("已记住 PROBE-SAVED，当前 PROBE-NEW", failures[0])

    def test_combined_reader_uses_configured_stability_limit(self) -> None:
        class FakeCoherentPowerMeter:
            device_type = "Coherent PM150-50C"

            def __init__(self, _resource: str) -> None:
                pass

            def test(self) -> str:
                return "OK"

            def set_power_mode(self) -> None:
                pass

            def set_wavelength(self, _wavelength_nm: float) -> None:
                pass

            def read_power_w(self) -> float:
                return 100.0

            def close(self) -> None:
                pass

        reader = PowerMeterReaderThread(
            PowerMeterSettings(
                resource="ASRL8::INSTR",
                wavelength_nm=976.5,
                software_gain=1.0,
                interval_ms=300,
                stable_window_s=3.0,
                stability_limit_percent=0.2,
                driver_kind="coherent",
            )
        )
        readings: list[PowerMeterReading] = []

        def on_reading(reading: PowerMeterReading) -> None:
            readings.append(reading)
            reader.stop()

        reader.reading.connect(on_reading)
        with patch.object(power_meter_mvp, "CoherentPowerMeter", FakeCoherentPowerMeter):
            reader.run()

        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].stability_limit_percent, 0.2)


if __name__ == "__main__":
    unittest.main()
