from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from combined_test.device_interfaces import ControllerPowerSupply, PowerSupply
from combined_test.devices import SpectrometerReaderThread
from combined_test.excel_export import ExcelTestRecord
from combined_test.models import SpectrometerSettings
from combined_test.record_store import RecordStore, SessionRecordStore


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
        value = 12.34 if command[1] == 0x8B else 4.56
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
    def test_spectrometer_mailbox_keeps_only_the_latest_frame(self) -> None:
        reader = SpectrometerReaderThread(SpectrometerSettings(10_000, 50))
        first = ([975.0], [10.0])
        latest = ([976.0], [20.0])

        reader._publish_latest_spectrum(*first)
        reader._publish_latest_spectrum(*latest)

        self.assertEqual(reader.take_latest_spectrum(), latest)
        self.assertIsNone(reader.take_latest_spectrum())

    def test_controller_adapter_exposes_semantic_power_supply_operations(self) -> None:
        controller = FakeController()
        supply = ControllerPowerSupply(controller)

        self.assertIsInstance(supply, PowerSupply)
        supply.set_current(3.25)

        self.assertEqual(controller.writes, [(0x41, [0xB4, 0xFF, 3, 25])])
        self.assertAlmostEqual(supply.read_output_voltage(), 12.34)
        self.assertAlmostEqual(supply.read_output_current(), 4.56)
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
            self.assertEqual(path.name, "result.xlsx")
            self.assertEqual(path.parent.parent.name, "2026-07-12")
            self.assertEqual(path.parent.parent.parent.name, "老化站 1")
            self.assertEqual(path.parent.parent.parent.parent.name, "SN-1")
            self.assertTrue(path.parent.is_dir())
            self.assertTrue((output_dir / "index.sqlite3").is_file())

            record = ExcelTestRecord(1, 2, 3, 0.5, 976, 976, 1, 0.8, [975, 976], [1, 2])

            store.queue(record)

            self.assertIsInstance(store, RecordStore)
            self.assertEqual(store.unsaved_records(), (record,))
            store.mark_saved((record,))
            self.assertEqual(store.unsaved_records(), ())

    def test_session_record_store_waits_for_explicit_database_commit(self) -> None:
        store = SessionRecordStore()
        with tempfile.TemporaryDirectory() as temp_dir:
            store.begin_session(
                Path(temp_dir) / "records",
                "SN-DB",
                datetime(2026, 7, 12, 9, 30, 0),
                test_station="老化站 1",
            )
            record = ExcelTestRecord(1, 2, 3, 0.5, 976, 976, 1, 0.8, [975, 976], [1, 2])
            store.queue(record)
            session_id = store.current_session.session_id  # type: ignore[union-attr]

            self.assertEqual(store.pending_database_count(), 1)
            self.assertEqual(store.archive.list_attempts(session_id), ())  # type: ignore[union-attr]
            self.assertEqual(store.commit_pending_records(), 1)
            self.assertEqual(store.pending_database_count(), 0)
            self.assertEqual(len(store.archive.list_attempts(session_id)), 1)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
