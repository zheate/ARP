from __future__ import annotations

import unittest

from combined_test.tdk_power_supply import (
    TdkLambdaPowerSupply,
    compensate_tdk_output_voltage,
    list_tdk_serial_resources,
)


class FakeInstrument:
    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.timeout = 0
        self.read_termination = ""
        self.write_termination = ""
        self.baud_rate = 0
        self.writes: list[str] = []
        self.reads: list[str] = []
        self.pending_responses: list[str] = []
        self.closed = False
        self.responses = responses or {
            "MV?": "MV 12.34",
            "MC?": "5.67 A",
        }

    def write(self, command: str) -> None:
        self.writes.append(command)
        self.pending_responses.append(self.responses.get(command, "OK"))

    def read(self) -> str:
        if not self.pending_responses:
            raise RuntimeError("no pending response")
        response = self.pending_responses.pop(0)
        self.reads.append(response)
        return response

    def close(self) -> None:
        self.closed = True


class FakeResourceManager:
    def __init__(self, instrument: FakeInstrument | None = None) -> None:
        self.instrument = instrument or FakeInstrument()
        self.opened_resource = ""
        self.list_query = ""
        self.closed = False

    def list_resources(self, query: str = "?*::INSTR") -> tuple[str, ...]:
        self.list_query = query
        if query == "ASRL?*::INSTR":
            return ("ASRL9::INSTR", "ASRL3::INSTR")
        return ("USB0::1::INSTR", "ASRL9::INSTR", "PXI0::1", "ASRL3::INSTR")

    def open_resource(self, resource: str) -> FakeInstrument:
        self.opened_resource = resource
        return self.instrument

    def close(self) -> None:
        self.closed = True


class TdkLambdaPowerSupplyTests(unittest.TestCase):
    def test_legacy_line_fit_converts_mv_reading_to_load_voltage(self) -> None:
        corrected_voltage = compensate_tdk_output_voltage(29.656, 10.0)

        self.assertAlmostEqual(corrected_voltage, 29.400957142857145)

    def test_lists_only_serial_resources(self) -> None:
        manager = FakeResourceManager()

        resources = list_tdk_serial_resources(lambda: manager)

        self.assertEqual(resources, ["ASRL3::INSTR", "ASRL9::INSTR"])
        self.assertEqual(manager.list_query, "ASRL?*::INSTR")
        self.assertTrue(manager.closed)

    def test_connect_uses_scripts_runner_serial_initialization_and_commands(self) -> None:
        instrument = FakeInstrument()
        manager = FakeResourceManager(instrument)
        controller = TdkLambdaPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: manager)

        connected, detail = controller.connect_device()
        controller.set_output_voltage(24.5)
        controller.set_output_current(5.25)
        controller.set_output_enabled(True)
        measured_voltage = controller.read_output_voltage()
        measured_current = controller.read_output_current()

        self.assertTrue(connected)
        self.assertIn("TDK-Lambda RS-232", detail)
        self.assertEqual(instrument.timeout, 1000)
        self.assertEqual(instrument.read_termination, "\r")
        self.assertEqual(instrument.write_termination, "\r")
        self.assertEqual(instrument.baud_rate, 9600)
        self.assertEqual(
            instrument.writes,
            ["ADR 6", "RMT 1", "PV 024.50", "PC 005.25", "OUT 1", "MV?", "MC?"],
        )
        self.assertEqual(instrument.reads, ["OK", "OK", "OK", "OK", "OK", "MV 12.34", "5.67 A"])
        self.assertEqual(measured_voltage, 12.34)
        self.assertEqual(measured_current, 5.67)
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

    def test_connect_fails_when_power_supply_does_not_confirm_address(self) -> None:
        instrument = FakeInstrument({"ADR 6": "NOT OK"})
        manager = FakeResourceManager(instrument)
        controller = TdkLambdaPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: manager)

        connected, detail = controller.connect_device()

        self.assertFalse(connected)
        self.assertIn("ADR 6", detail)
        self.assertIn("NOT OK", detail)
        self.assertFalse(controller.is_connected)
        self.assertTrue(instrument.closed)
        self.assertTrue(manager.closed)

    def test_setting_fails_when_power_supply_does_not_return_ok(self) -> None:
        instrument = FakeInstrument({"PV 024.50": "E01"})
        manager = FakeResourceManager(instrument)
        controller = TdkLambdaPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: manager)
        self.assertTrue(controller.connect_device()[0])

        with self.assertRaisesRegex(RuntimeError, "PV 024.50.*E01"):
            controller.set_output_voltage(24.5)

    def test_query_rejects_error_text_containing_a_number(self) -> None:
        instrument = FakeInstrument({"MV?": "ERROR 12"})
        manager = FakeResourceManager(instrument)
        controller = TdkLambdaPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: manager)
        self.assertTrue(controller.connect_device()[0])

        with self.assertRaisesRegex(RuntimeError, "ERROR 12"):
            controller.read_output_voltage()

    def test_invalid_visa_session_marks_tdk_disconnected_and_requests_reconnect(self) -> None:
        class InvalidSessionInstrument(FakeInstrument):
            def write(self, command: str) -> None:
                if command == "MV?":
                    raise RuntimeError("Invalid session handle. The resource might be closed.")
                super().write(command)

        instrument = InvalidSessionInstrument()
        manager = FakeResourceManager(instrument)
        controller = TdkLambdaPowerSupply(
            "ASRL9::INSTR",
            resource_manager_factory=lambda: manager,
        )
        self.assertTrue(controller.connect_device()[0])

        with self.assertRaisesRegex(RuntimeError, "RS-232 会话已失效.*重新连接"):
            controller.read_output_voltage()

        self.assertFalse(controller.is_connected)
        self.assertTrue(instrument.closed)
        self.assertTrue(manager.closed)

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
