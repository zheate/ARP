from __future__ import annotations

import unittest

from combined_test.tdk_power_supply import TdkLambdaPowerSupply, list_tdk_serial_resources


class FakeInstrument:
    def __init__(self) -> None:
        self.timeout = 0
        self.read_termination = ""
        self.baud_rate = 0
        self.writes: list[str] = []
        self.queries: list[str] = []
        self.closed = False
        self.responses = {
            "MV?": "MV 12.34",
            "MC?": "5.67 A",
        }

    def query(self, command: str) -> str:
        self.queries.append(command)
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
        return ("USB0::1::INSTR", "ASRL9::INSTR", "PXI0::1", "ASRL3::INSTR")

    def open_resource(self, resource: str) -> FakeInstrument:
        self.opened_resource = resource
        return self.instrument

    def close(self) -> None:
        self.closed = True


class TdkLambdaPowerSupplyTests(unittest.TestCase):
    def test_lists_only_serial_resources(self) -> None:
        manager = FakeResourceManager()

        resources = list_tdk_serial_resources(lambda: manager)

        self.assertEqual(resources, ["ASRL3::INSTR", "ASRL9::INSTR"])
        self.assertTrue(manager.closed)

    def test_connect_uses_scripts_runner_serial_initialization_and_commands(self) -> None:
        instrument = FakeInstrument()
        manager = FakeResourceManager(instrument)
        controller = TdkLambdaPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: manager)

        connected, detail = controller.connect_device()
        controller.set_output_voltage(24.5)
        controller.set_output_current(5.25)
        controller.set_output_enabled(True)

        self.assertTrue(connected)
        self.assertIn("TDK-Lambda RS-232", detail)
        self.assertEqual(instrument.timeout, 100)
        self.assertEqual(instrument.read_termination, "\r")
        self.assertEqual(instrument.baud_rate, 115200)
        self.assertEqual(
            instrument.writes,
            ["ADR 6", "RMT 1", "PV 024.50", "PC 005.25", "OUT 1"],
        )
        self.assertEqual(controller.read_output_voltage(), 12.34)
        self.assertEqual(controller.read_output_current(), 5.67)
        self.assertEqual(instrument.queries, ["MV?", "MC?"])
        self.assertTrue(controller.output_enabled)

        controller.disconnect_device()
        self.assertFalse(controller.is_connected)
        self.assertTrue(instrument.closed)
        self.assertTrue(manager.closed)

    def test_rejects_non_serial_visa_resource(self) -> None:
        controller = TdkLambdaPowerSupply("USB0::1::INSTR", resource_manager_factory=FakeResourceManager)

        connected, detail = controller.connect_device()

        self.assertFalse(connected)
        self.assertIn("RS-232", detail)

    def test_arp_compatibility_translates_current_and_measurement_commands(self) -> None:
        instrument = FakeInstrument()
        manager = FakeResourceManager(instrument)
        controller = TdkLambdaPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: manager)
        self.assertTrue(controller.connect_device()[0])

        self.assertEqual(controller.i2c_write(0x41, [0xB4, 0xFF, 5, 25]), (True, "写入成功"))
        self.assertEqual(instrument.writes[-1], "PC 005.25")
        self.assertEqual(controller.i2c_write_read(0x41, [0xB4, 0x8B, 0, 0], 4), (True, [0, 0, 12, 34]))
        self.assertEqual(controller.i2c_write_read(0x41, [0xB4, 0x8C, 0, 0], 4), (True, [0, 0, 5, 67]))

    def test_invalid_or_unsupported_commands_fail_without_writing(self) -> None:
        instrument = FakeInstrument()
        manager = FakeResourceManager(instrument)
        controller = TdkLambdaPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: manager)
        controller.connect_device()
        initialization_writes = list(instrument.writes)

        success, message = controller.i2c_write(0x41, [0x00])
        self.assertFalse(success)
        self.assertIn("不支持", message)
        success, message = controller.i2c_write_read(0x41, [0xB4, 0x8D, 0, 0], 4)
        self.assertFalse(success)
        self.assertIn("温度", message)
        self.assertEqual(instrument.writes, initialization_writes)


if __name__ == "__main__":
    unittest.main()
