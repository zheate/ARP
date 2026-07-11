from __future__ import annotations

import unittest

from combined_test.tdk_power_supply import TdkLambdaPowerSupply, list_tdk_visa_resources


class FakeInstrument:
    def __init__(self) -> None:
        self.timeout = 0
        self.write_termination = ""
        self.read_termination = ""
        self.writes: list[str] = []
        self.closed = False
        self.responses = {
            "*IDN?": "TDK-LAMBDA,Z20-20,1234,1.0",
            "OUTP?": "0",
            "MEAS:VOLT?": "12.34",
            "MEAS:CURR?": "5.67",
        }

    def query(self, command: str) -> str:
        if command not in self.responses:
            raise RuntimeError(f"unsupported query: {command}")
        return self.responses[command]

    def write(self, command: str) -> None:
        self.writes.append(command)

    def close(self) -> None:
        self.closed = True


class FakeResourceManager:
    def __init__(self, instrument: FakeInstrument | None = None) -> None:
        self.instrument = instrument or FakeInstrument()
        self.opened_resource = ""
        self.closed = False

    def list_resources(self) -> tuple[str, ...]:
        return ("USB0::1::INSTR", "ASRL9::INSTR", "PXI0::1", "TCPIP0::10.0.0.2::INSTR")

    def open_resource(self, resource: str) -> FakeInstrument:
        self.opened_resource = resource
        return self.instrument

    def close(self) -> None:
        self.closed = True


class TdkLambdaPowerSupplyTests(unittest.TestCase):
    def test_lists_supported_visa_resources(self) -> None:
        manager = FakeResourceManager()

        resources = list_tdk_visa_resources(lambda: manager)

        self.assertEqual(
            resources,
            ["ASRL9::INSTR", "TCPIP0::10.0.0.2::INSTR", "USB0::1::INSTR"],
        )
        self.assertTrue(manager.closed)

    def test_connect_set_read_output_and_disconnect(self) -> None:
        instrument = FakeInstrument()
        manager = FakeResourceManager(instrument)
        controller = TdkLambdaPowerSupply("USB0::1::INSTR", resource_manager_factory=lambda: manager)

        connected, detail = controller.connect_device()
        controller.set_output_voltage(12.5)
        controller.set_output_current(6.25)
        controller.set_output_enabled(True)

        self.assertTrue(connected)
        self.assertIn("TDK-LAMBDA", detail)
        self.assertEqual(instrument.timeout, 2000)
        self.assertEqual(instrument.writes, ["VOLT 12.500", "CURR 6.250", "OUTP ON"])
        self.assertEqual(controller.read_output_voltage(), 12.34)
        self.assertEqual(controller.read_output_current(), 5.67)
        self.assertTrue(controller.output_enabled)

        controller.disconnect_device()
        self.assertFalse(controller.is_connected)
        self.assertTrue(instrument.closed)
        self.assertTrue(manager.closed)

    def test_arp_compatibility_translates_current_and_measurement_commands(self) -> None:
        instrument = FakeInstrument()
        manager = FakeResourceManager(instrument)
        controller = TdkLambdaPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: manager)
        self.assertTrue(controller.connect_device()[0])

        self.assertEqual(controller.i2c_write(0x41, [0xB4, 0xFF, 5, 25]), (True, "写入成功"))
        self.assertEqual(instrument.writes, ["CURR 5.250"])
        self.assertEqual(controller.i2c_write_read(0x41, [0xB4, 0x8B, 0, 0], 4), (True, [0, 0, 12, 34]))
        self.assertEqual(controller.i2c_write_read(0x41, [0xB4, 0x8C, 0, 0], 4), (True, [0, 0, 5, 67]))

    def test_invalid_or_unsupported_commands_fail_without_writing(self) -> None:
        instrument = FakeInstrument()
        manager = FakeResourceManager(instrument)
        controller = TdkLambdaPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: manager)
        controller.connect_device()

        success, message = controller.i2c_write(0x41, [0x00])
        self.assertFalse(success)
        self.assertIn("不支持", message)
        success, message = controller.i2c_write_read(0x41, [0xB4, 0x8D, 0, 0], 4)
        self.assertFalse(success)
        self.assertIn("温度", message)
        self.assertEqual(instrument.writes, [])


if __name__ == "__main__":
    unittest.main()
