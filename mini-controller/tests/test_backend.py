import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import DEFAULT_ADDRESS, DEFAULT_SPEED, MIN_COMMAND_INTERVAL_S, ManualController, current_command, decode_value


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
    def clock(self):
        return self.now
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeDriver:
    def __init__(self):
        self.calls = []
        self.fail_write = self.fail_read = self.fail_close = False
        self.current = bytes([0xB4, 0x8C, 0, 0])

    def scan(self):
        self.calls.append(("scan",))
        return [0, 2]

    def open(self, index, speed):
        self.calls.append(("open", index, speed))

    def close(self, index):
        self.calls.append(("close", index))
        if self.fail_close:
            raise RuntimeError("close failure")

    def write(self, index, speed, address, data):
        self.calls.append(("write", index, speed, address, data))
        if self.fail_write:
            raise RuntimeError("write failure")

    def read(self, index, speed, address, register):
        self.calls.append(("read", register))
        if self.fail_read:
            raise RuntimeError("read failure")
        return self.current


class ManualControllerTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.clock = FakeClock()
        self.controller = ManualController(self.driver, sleeper=self.clock.sleep, clock=self.clock.clock)

    def connect(self):
        self.controller.connect(2)

    def test_protocol_matches_product_for_all_centiamperes(self):
        spec = importlib.util.spec_from_file_location("original_core", ROOT.parent / "combined_test/core.py")
        original = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = original
        spec.loader.exec_module(original)
        for value in range(2001):
            self.assertEqual(current_command(value / 100), bytes(original.build_set_current_command(value / 100)))
        self.assertEqual(decode_value(bytes([0, 0, 12, 34])), 12.34)

    def test_invalid_currents_cannot_reach_driver(self):
        self.connect()
        for value in [-0.01, 20.01, float("nan"), float("inf"), True, "5", None]:
            with self.assertRaises(ValueError):
                self.controller.set_current(value)
        self.assertEqual(self.driver.calls, [("open", 2, 0)])

    def test_initialization_and_connection_never_energize(self):
        self.assertEqual(self.driver.calls, [])
        self.connect()
        self.assertEqual(self.driver.calls, [("open", 2, 0)])
        self.assertIsNone(self.controller.status.set_current)
        self.assertFalse(self.controller.status.zero_sent)
        self.assertEqual((DEFAULT_ADDRESS, DEFAULT_SPEED), (0x41, 0))

    def test_duplicate_connection_and_scan_while_connected_blocked(self):
        self.connect()
        for action in (self.controller.scan, lambda: self.controller.connect(0)):
            with self.assertRaises(RuntimeError):
                action()
        self.assertEqual(self.driver.calls, [("open", 2, 0)])

    def test_invalid_connection_settings_blocked(self):
        for values in [(16, 0x41, 1), (0, 0x80, 1), (0, 0x41, 4), (True, 0x41, 1)]:
            with self.assertRaises(ValueError):
                self.controller.connect(*values)
        self.assertEqual(self.driver.calls, [])

    def test_set_requires_connection(self):
        with self.assertRaises(RuntimeError):
            self.controller.set_current(5)

    def test_manual_write_and_original_ramp_release_order(self):
        self.connect()
        self.controller.set_current(5.25)
        self.controller.disconnect()
        self.assertEqual(self.driver.calls, [("open", 2, 0), ("write", 2, 0, 0x41, bytes([0xB4, 0xFF, 5, 25])), ("write", 2, 0, 0x41, bytes([0xB4, 0xFF, 0, 25])), ("write", 2, 0, 0x41, bytes([0xB4, 0xFF, 0, 0])), ("close", 2)])
        self.assertEqual(self.clock.sleeps, [1.1, 1.1])
        self.assertFalse(self.controller.status.connected)

    def test_failed_write_invalidates_previous_zero_confirmation(self):
        self.connect()
        self.controller.stop()
        self.clock.sleep(1.1)
        self.driver.fail_write = True
        with self.assertRaises(RuntimeError):
            self.controller.set_current(5)
        self.assertFalse(self.controller.status.zero_sent)
        self.assertIsNone(self.controller.status.set_current)

    def test_read_failures_reported_without_automatic_read_retry(self):
        self.connect()
        for failure, response in [(True, bytes([0, 0, 0, 0])), (False, bytes([0, 0, 0, 255]))]:
            self.driver.fail_read, self.driver.current = failure, response
            with self.assertRaises(RuntimeError):
                self.controller.read_status("output_current")
            self.assertTrue(self.controller.status.connected)
            self.assertNotIn(("close", 2), self.driver.calls)
            self.clock.sleep(1.1)

    def test_shutdown_write_failure_keeps_connection(self):
        self.connect()
        self.controller.set_current(20)
        self.driver.fail_write = True
        with self.assertRaises(RuntimeError):
            self.controller.disconnect()
        self.assertTrue(self.controller.status.connected)
        self.assertFalse(self.controller.status.zero_sent)
        self.assertNotIn(("close", 2), self.driver.calls)

    def test_read_registers_and_close_failure(self):
        self.connect()
        for name in ("input_voltage", "output_voltage", "output_current", "temperature"):
            self.assertEqual(set(self.controller.read_status(name)), {name})
            self.clock.sleep(1.1)
        self.assertEqual([c[1] for c in self.driver.calls if c[0] == "read"], [0x88, 0x8B, 0x8C, 0x8D])
        self.driver.fail_close = True
        with self.assertRaises(RuntimeError):
            self.controller.disconnect()
        self.assertTrue(self.controller.status.connected)

    def test_force_disconnect_does_not_claim_zero_or_send_output(self):
        self.connect()
        self.controller.force_disconnect()
        self.assertEqual(self.driver.calls, [("open", 2, 0), ("close", 2)])
        self.assertFalse(self.controller.status.zero_sent)

    def test_writes_and_reads_share_original_1_1_second_guard(self):
        self.connect()
        self.controller.set_current(5)
        before = list(self.driver.calls)
        for action in (lambda: self.controller.set_current(6), lambda: self.controller.read_status("output_current")):
            with self.assertRaisesRegex(RuntimeError, "命令过快"):
                action()
        self.assertEqual(self.driver.calls, before)
        self.clock.sleep(1.09)
        with self.assertRaisesRegex(RuntimeError, "命令过快"):
            self.controller.read_status("output_current")
        self.clock.sleep(0.01)
        self.controller.read_status("output_current")
        with self.assertRaisesRegex(RuntimeError, "命令过快"):
            self.controller.set_current(6)
        self.clock.sleep(1.1)
        self.controller.set_current(6)
        self.assertEqual(MIN_COMMAND_INTERVAL_S, 1.1)

    def test_shutdown_matches_original_ramp_plan_for_every_centiamperes(self):
        spec = importlib.util.spec_from_file_location("original_automation", ROOT.parent / "combined_test/automation.py")
        original = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = original
        spec.loader.exec_module(original)
        self.assertEqual(MIN_COMMAND_INTERVAL_S, original.MIN_POWER_SUPPLY_COMMAND_INTERVAL_S)
        for cents in range(2001):
            driver, clock = FakeDriver(), FakeClock()
            controller = ManualController(driver, sleeper=clock.sleep, clock=clock.clock)
            controller.connect(0)
            controller.set_current(cents / 100)
            driver.calls.clear()
            controller.stop()
            sent = [call[4][2] * 100 + call[4][3] for call in driver.calls]
            expected = list(original.build_ramp_down_currents(cents / 100, 5.0)) or [0.0]
            self.assertEqual(sent, [round(value * 100) for value in expected])
            self.assertTrue(all(abs(delay - 1.1) < 1e-9 for delay in clock.sleeps))
            self.assertTrue(controller.status.zero_sent)


if __name__ == "__main__":
    unittest.main()
