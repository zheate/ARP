"""RU protocol and product safety checks. All serial traffic is simulated."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from combined_test.ru_power_supply import RuPowerSupply, probe_ru_serial_resource, rtu_frame
from combined_test.models import PowerMeterReading
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings


class RuPort:
    def __init__(self):
        self.writes = []
        self.pending = b""
        self.closed = False
        self.output = False
        self.current = 0
        self.voltage = 643
        self.decimals = (1, 2)
        self.rated = (1000, 1000)
        self.corrupt = None
        self.fail_register = None
        self.stuck_current = False
        self.stuck_output = False
        self.output_readbacks = []
        self.current_readbacks = []

    def flush(self, _mask):
        self.pending = b""

    def write_raw(self, frame):
        self.writes.append(frame)
        address, function = frame[:2]
        register = int.from_bytes(frame[2:4], "big")
        value = int.from_bytes(frame[4:6], "big")
        if self.fail_register == (function, register):
            response = bytes([address, function | 0x80, 2])
        elif function == 4:
            values = {106: self.decimals, 103: self.rated, 100: (self.voltage,), 101: (self.current,)}[register]
            if register == 101 and self.current_readbacks:
                values = (self.current_readbacks.pop(0),)
            payload = b"".join(n.to_bytes(2, "big") for n in values[:value])
            response = bytes([address, function, len(payload)]) + payload
        elif function == 1:
            state = self.output_readbacks.pop(0) if self.output_readbacks else self.output
            response = bytes([address, function, 1, int(state)])
        else:
            response = frame[:-2]
            if function == 5:
                if not self.stuck_output:
                    self.output = bool(value)
                if not self.output and not self.stuck_current:
                    self.current = 0
            if function == 6 and register == 150 and not self.stuck_current:
                self.current = value
        self.pending = rtu_frame(response)
        if self.corrupt:
            self.pending = self.corrupt(self.pending)

    def read_bytes(self, count):
        if len(self.pending) < count:
            raise TimeoutError("simulated timeout / truncated frame")
        result, self.pending = self.pending[:count], self.pending[count:]
        return result

    def close(self):
        self.closed = True


class RuManager:
    def __init__(self, port):
        self.port = port
        self.closed = False

    def open_resource(self, _resource):
        return self.port

    def close(self):
        self.closed = True


class RuProtocolTests(unittest.TestCase):
    def connected(self):
        port = RuPort()
        manager = RuManager(port)
        supply = RuPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: manager)
        self.assertTrue(supply.connect_device()[0])
        self.addCleanup(supply.disconnect_device)
        return supply, port, manager

    def test_manual_golden_requests_and_crc(self):
        self.assertEqual(rtu_frame(bytes.fromhex("01050085FF00")).hex(), "01050085ff009dd3")
        self.assertEqual(rtu_frame(bytes.fromhex("0106009501F4")).hex(), "0106009501f499f1")
        supply, port, _ = self.connected()
        supply.set_output_voltage(50)
        supply.set_output_current(0.01)
        self.assertEqual(port.writes[-2].hex(), "0106009501f499f1")
        self.assertEqual(port.writes[-1][2:6].hex(), "00960001")
        self.assertEqual(supply.read_output_voltage(), 64.3)
        self.assertEqual(supply.read_output_current(), 0.01)
        self.assertEqual(supply.maximum_current_a, 10)
        self.assertEqual(supply.maximum_voltage_v, 100)

    def test_connect_and_probe_read_only_8n1_and_release(self):
        supply, port, _ = self.connected()
        self.assertTrue(all(frame[1] in (1, 4) for frame in port.writes))
        self.assertEqual(port.baud_rate, 9600)
        self.assertEqual(port.data_bits, 8)
        self.assertIsNone(port.read_termination)
        self.assertFalse(supply.output_enabled)
        port2 = RuPort()
        manager2 = RuManager(port2)
        self.assertTrue(probe_ru_serial_resource("ASRL4::INSTR", resource_manager_factory=lambda: manager2))
        self.assertTrue(all(frame[1] in (1, 4) for frame in port2.writes))
        self.assertTrue(port2.closed and manager2.closed)

    def test_connect_reports_existing_output_without_changing_it(self):
        port = RuPort()
        port.output = True
        supply = RuPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: RuManager(port))
        self.addCleanup(supply.disconnect_device)
        self.assertTrue(supply.connect_device()[0])
        self.assertTrue(supply.output_enabled)
        self.assertTrue(all(frame[1] in (1, 4) for frame in port.writes))

    def test_dynamic_precision_and_limits_do_not_round(self):
        supply, port, _ = self.connected()
        before = len(port.writes)
        for value in (-1, 10.01, float("nan"), float("inf"), 0.001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                supply.set_output_current(value)
        for value in (100.1, 0.01):
            with self.assertRaises(ValueError):
                supply.set_output_voltage(value)
        self.assertEqual(len(port.writes), before)

    def test_rejects_broadcast_invalid_address_and_transport(self):
        for kwargs in ({"address": 0}, {"address": 256}, {"address": 1.5}, {"address": True}, {"baud_rate": 1234}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RuPowerSupply("ASRL9::INSTR", **kwargs)
        with self.assertRaises(ValueError):
            RuPowerSupply("TCPIP::INSTR")

    def test_crc_address_function_count_echo_exception_and_timeout_rejected_without_retry(self):
        supply, port, _ = self.connected()
        cases = [lambda f: f[:-1] + bytes([f[-1] ^ 1]),
                 lambda f: rtu_frame(bytes([2]) + f[1:-2]),
                 lambda f: rtu_frame(f[:1] + bytes([3]) + f[2:-2]),
                 lambda f: rtu_frame(f[:2] + bytes([4]) + f[3:-2]),
                 lambda f: f[:-2]]
        for corrupt in cases:
            port.corrupt = corrupt
            before = len(port.writes)
            with self.assertRaises((RuntimeError, TimeoutError)):
                supply.read_output_voltage()
            self.assertEqual(len(port.writes), before + 1)
        port.corrupt = lambda f: rtu_frame(f[:5] + bytes([f[5] ^ 1]))
        with self.assertRaisesRegex(RuntimeError, "回显"):
            supply.set_output_current(1)
        port.corrupt = None
        port.fail_register = (6, 150)
        with self.assertRaisesRegex(RuntimeError, "异常响应"):
            supply.set_output_current(1)

    def test_failed_connect_closes_handles(self):
        port = RuPort()
        port.decimals = (65535, 2)
        manager = RuManager(port)
        supply = RuPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: manager)
        self.assertFalse(supply.connect_device()[0])
        self.assertTrue(port.closed and manager.closed)
        self.assertFalse(supply.is_connected)

    def test_output_requires_echo_and_physical_coil_confirmation(self):
        supply, port, _ = self.connected()
        supply.set_output_enabled(True)
        port.stuck_output = True
        with self.assertRaisesRegex(RuntimeError, "输出状态"):
            supply.set_output_enabled(False)
        self.assertTrue(supply.output_enabled)
        port.stuck_output = False
        supply.set_output_enabled(False)
        self.assertFalse(supply.output_enabled)

    def test_transactions_serialized_between_threads(self):
        supply, port, _ = self.connected()
        failures = []
        def read():
            try:
                for _ in range(10):
                    self.assertEqual(supply.read_output_voltage(), 64.3)
            except Exception as exc:
                failures.append(exc)
        workers = [threading.Thread(target=read) for _ in range(3)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])

    def test_output_confirmation_waits_for_delayed_coil_without_replaying_write(self):
        supply, port, _ = self.connected()
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                port.output_readbacks = [not enabled, not enabled, enabled]
                before = len(port.writes)
                supply.set_output_enabled(enabled)
                self.assertEqual(supply.output_enabled, enabled)
                frames = port.writes[before:]
                self.assertEqual([frame[1] for frame in frames], [5, 1, 1, 1])
                self.assertEqual(port.timeout, 1000)

    def test_persistent_opposite_coil_times_out_with_diagnostics_and_conservative_state(self):
        supply, port, _ = self.connected()
        supply.set_output_enabled(True)
        port.stuck_output = True
        before = len(port.writes)
        with (patch("combined_test.ru_power_supply.RU_OUTPUT_CONFIRM_TIMEOUT_S", 0.03),
              patch("combined_test.ru_power_supply.RU_OUTPUT_CONFIRM_INTERVAL_S", 0.005)):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "请求关闭，回读开启.*ASRL9::INSTR"):
                supply.set_output_enabled(False)
            self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(supply.output_enabled)
        self.assertEqual(sum(frame[1] == 5 for frame in port.writes[before:]), 1)
        self.assertGreater(sum(frame[1] == 1 for frame in port.writes[before:]), 1)
        self.assertEqual(port.timeout, 1000)

    def test_invalid_confirmation_packet_is_not_retried_or_accepted(self):
        supply, port, _ = self.connected()
        port.corrupt = lambda frame: frame[:-1] + bytes([frame[-1] ^ 1]) if frame[1] == 1 else frame
        before = len(port.writes)
        with self.assertRaisesRegex(RuntimeError, "CRC"):
            supply.set_output_enabled(True)
        self.assertEqual([frame[1] for frame in port.writes[before:]], [5, 1])
        self.assertTrue(supply.output_enabled)
        self.assertEqual(port.timeout, 1000)


class RuProductTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings = JsonSettings(Path(temp.name) / "settings.json")
        self.backend = HeadlessBackend(self.settings)
        self.addCleanup(self.backend.export_worker.graceful_stop, 2)
        self.station = self.backend.runtime
        self.station.configure({"powerSupplyKind": "ru", "ruResource": "ASRL9::INSTR", "rampDownIntervalS": 0})
        self.port = RuPort()
        self.supply = RuPowerSupply("ASRL9::INSTR", resource_manager_factory=lambda: RuManager(self.port))
        self.addCleanup(self.supply.disconnect_device)
        factory = patch("tauri_bridge.station_runtime.RuPowerSupply", return_value=self.supply)
        factory.start()
        self.addCleanup(factory.stop)
        self.backend.dispatch("powerSupply.connect", {})
        self.addCleanup(self.stop_burst)

    def stop_burst(self):
        self.station._tdk_voltage_burst_stop.set()
        if self.station._tdk_voltage_burst_thread:
            self.station._tdk_voltage_burst_thread.join(2)

    def test_configuration_saved_and_connection_parameters_locked(self):
        self.backend.dispatch("powerSupply.setVoltage", {"voltageV": 50})
        self.assertEqual(self.settings.get("runtime/configuration")["ruVoltageV"], 50)
        self.assertEqual(self.station._device_snapshots()[0].resource, "ASRL9::INSTR")
        for change in ({"ruAddress": 2}, {"ruBaudRate": 19200}, {"ruResource": "ASRL8::INSTR"}, {"powerSupplyKind": "tdk"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "断开"):
                self.station.configure(change)

    def test_manual_enable_zeroes_current_and_shutdown_confirms_off(self):
        self.port.current = 400
        self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.assertTrue(self.port.output)
        self.assertEqual(self.port.current, 0)
        self.backend.dispatch("powerSupply.setCurrent", {"currentA": 4.5})
        self.assertEqual(self.port.current, 450)
        self.assertEqual(self.backend.snapshot({})["devices"]["powerSupply"]["maximumCurrentA"], 10)
        result = self.backend.dispatch("app.shutdown", {})
        self.assertTrue(result["shutdownConfirmed"])
        self.assertFalse(self.port.output)
        self.assertEqual(self.port.current, 0)

    def test_failed_current_write_during_ramp_still_switches_off(self):
        self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.backend.dispatch("powerSupply.setCurrent", {"currentA": 4})
        self.port.fail_register = (6, 150)
        self.station.disconnect_power_supply()
        self.assertFalse(self.port.output)
        self.assertTrue(self.port.closed)
        self.assertIsNone(self.station.power_supply_controller)

    def test_failed_off_or_nonzero_readback_keeps_handle_for_retry(self):
        self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.port.fail_register = (5, 133)
        with self.assertRaises(RuntimeError):
            self.station.disconnect_power_supply()
        self.assertIs(self.station.power_supply_controller, self.supply)
        self.assertFalse(self.port.closed)
        self.assertFalse(self.station.shutdown_confirmed)
        self.port.fail_register = None
        self.port.stuck_current = True
        self.port.current = 100
        with self.assertRaisesRegex(RuntimeError, "归零"):
            self.station.disconnect_power_supply()
        self.assertFalse(self.port.closed)
        self.port.stuck_current = False
        self.station.disconnect_power_supply()
        self.assertTrue(self.port.closed)

    def test_automatic_readiness_output_gate_plan_limits_and_no_tdk_compensation(self):
        readiness = self.station.automatic_readiness()
        self.assertIn("ru_output_disabled", [item["code"] for item in readiness["blockers"]])
        self.station.configure({"targetCurrentA": 10, "initialCurrentA": 2})
        self.assertEqual(self.station._automatic_settings().maximum_current_a, 10)
        self.station.configure({"targetCurrentA": 11})
        with self.assertRaises(ValueError):
            self.station._automatic_settings()
        with self.assertRaisesRegex(ValueError, "仅支持 CH341"):
            self.station.configure({"selectedScriptId": "aging"})

    def test_manual_and_automatic_records_keep_raw_ru_voltage(self):
        st = self.station
        reading = PowerMeterReading(elapsed_s=5, power_w=4, stable=True,
                                   stable_span_w=0.02, stable_window_s=3,
                                   stable_median_w=4, stability_generation=1)
        st.configuration.update({"sn": "RU-MOCK", "outputDir": str(self.settings.path.parent),
                                 "targetCurrentA": 2, "initialCurrentA": 2})
        st.active_current_a = st.recorded_manual_stable_current_a = 2
        st.recorded_manual_stability_generation = 1
        st.latest_power = reading
        st.latest_power_received_monotonic_s = time.monotonic()
        st.power_meter_reader = SimpleNamespace(is_ready=True)
        st._record_manual_voltage_point(64.3)
        record = st.record_store.snapshot()[0]
        self.assertEqual(record.voltage_v, 64.3)
        self.assertAlmostEqual(record.efficiency, 4 / 2 / 64.3)
        settings = st._automatic_settings()
        with (patch.object(st, "_wait_for_automatic_acquisition"),
              patch.object(st, "_set_automatic_current"),
              patch.object(st, "_wait_for_automatic_stability", return_value=(reading, None, 1, 0)),
              patch.object(st, "_wait_for_automatic_stable_hold", return_value=(reading, None)),
              patch.object(st.record_store, "queue") as queue):
            st._run_automatic_point(0, 2, settings)
        automatic_record = queue.call_args.args[0]
        self.assertEqual(automatic_record.voltage_v, 64.3)
        self.assertAlmostEqual(automatic_record.efficiency, 4 / 2 / 64.3)

    def test_identify_uses_ru_parameters_excludes_power_meter_and_selects_first(self):
        self.station.disconnect_power_supply()
        self.station.configure({"ruResource": "", "ruBaudRate": 19200, "ruAddress": 3})
        self.station.configuration["powerMeterResource"] = "ASRL3::INSTR"
        with patch("tauri_bridge.device_discovery.list_tdk_serial_resources", return_value=["ASRL3::INSTR", "ASRL9::INSTR"]), patch("tauri_bridge.device_discovery.probe_ru_serial_resource", return_value=True) as probe:
            self.backend.discovery._refresh_power_supply(self.station)
            worker = self.backend.power_supply_detect_thread
            if worker:
                self.assertTrue(worker.wait(2000))
            probe.assert_called_once_with("ASRL9::INSTR", baud_rate=19200, address=3)
        self.assertEqual(self.station.configuration["ruResource"], "ASRL9::INSTR")

    def test_unconfirmed_shutdown_blocks_energizing_until_verified_recovery(self):
        self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.port.stuck_output = True
        with patch("combined_test.ru_power_supply.RU_OUTPUT_CONFIRM_TIMEOUT_S", 0.02):
            with self.assertRaises(RuntimeError):
                self.backend.dispatch("powerSupply.safeRampDown", {})
        self.assertFalse(self.station.shutdown_confirmed)
        self.assertIn("shutdown_unconfirmed", [item["code"] for item in self.station.automatic_readiness()["blockers"]])
        for method, params in (("powerSupply.setCurrent", {"currentA": 1}),
                               ("powerSupply.setVoltage", {"voltageV": 12}),
                               ("powerSupply.setOutput", {"enabled": True})):
            before = len(self.port.writes)
            with self.subTest(method=method), self.assertRaisesRegex(RuntimeError, "安全下电尚未确认"):
                self.backend.dispatch(method, params)
            self.assertEqual(len(self.port.writes), before)
        before = len(self.port.writes)
        with self.assertRaisesRegex(RuntimeError, "手动配置正在使用设备"):
            self.backend.dispatch("automatic.start", {})
        # Check the automatic workflow's shutdown guard independently of the
        # earlier ownership check, without weakening either production guard.
        with patch.object(self.station.workflow_access, "owner", "automatic"):
            with self.assertRaisesRegex(RuntimeError, "安全下电尚未确认"):
                self.backend.dispatch("automatic.start", {})
        self.assertEqual(len(self.port.writes), before)
        self.backend.dispatch("powerSupply.setCurrent", {"currentA": 0})
        self.assertTrue(self.station.shutdown_requested)
        self.assertFalse(self.station.shutdown_confirmed)
        self.stop_burst()
        self.port.stuck_output = False
        self.backend.dispatch("powerSupply.safeRampDown", {})
        self.assertTrue(self.station.shutdown_confirmed)
        self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.backend.dispatch("powerSupply.setCurrent", {"currentA": 1})
        self.assertEqual(self.port.current, 100)

    def test_failed_enable_is_reported_as_unconfirmed_and_off_retry_recovers(self):
        self.port.stuck_output = True
        with patch("combined_test.ru_power_supply.RU_OUTPUT_CONFIRM_TIMEOUT_S", 0.02):
            with self.assertRaises(RuntimeError):
                self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.assertTrue(self.station.shutdown_requested)
        self.assertFalse(self.station.shutdown_confirmed)
        self.backend.dispatch("powerSupply.setOutput", {"enabled": False})
        self.assertTrue(self.station.shutdown_confirmed)

    def test_confirmed_coil_off_with_unconfirmed_current_still_blocks_energizing(self):
        self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.port.stuck_current = True
        self.port.current = 100
        with self.assertRaisesRegex(RuntimeError, "无法确认电流归零"):
            self.backend.dispatch("powerSupply.setOutput", {"enabled": False})
        self.assertFalse(self.port.output)
        self.assertTrue(self.station.shutdown_requested)
        self.assertFalse(self.station.shutdown_confirmed)
        self.assertTrue(self.station.shutdown_output_off_confirmed)
        with self.assertRaisesRegex(RuntimeError, "安全下电尚未确认"):
            self.backend.dispatch("powerSupply.setOutput", {"enabled": True})

    def test_direct_close_sends_off_before_clearing_the_current_setpoint(self):
        self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.backend.dispatch("powerSupply.setCurrent", {"currentA": 4})
        self.stop_burst()
        before = len(self.port.writes)
        self.backend.dispatch("powerSupply.setOutput", {"enabled": False})
        frames = self.port.writes[before:]
        self.assertEqual([frame[1] for frame in frames], [5, 1, 6, 4])
        self.assertEqual(frames[0][2:6].hex(), "00850000")
        self.assertEqual(frames[2][2:6].hex(), "00960000")
        self.assertFalse(self.port.output)
        self.assertTrue(self.station.shutdown_confirmed)
        self.assertEqual(self.station.latest_power_supply_output_current_a, 0)

    def test_direct_close_invalidates_previously_queued_enable(self):
        generation = self.station.command_safety.capture()
        self.backend.dispatch("powerSupply.setOutput", {"enabled": False})
        before = len(self.port.writes)
        with self.assertRaisesRegex(RuntimeError, "安全停止失效"):
            self.backend.dispatch("powerSupply.setOutput", {"enabled": True}, command_generation=generation)
        self.assertEqual(len(self.port.writes), before)
        self.assertFalse(self.port.output)

    def test_direct_close_waits_for_current_readback_to_settle_after_one_off_write(self):
        self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.backend.dispatch("powerSupply.setCurrent", {"currentA": 4})
        self.stop_burst()
        self.port.current_readbacks = [400, 120, 0]
        before = len(self.port.writes)
        result = self.backend.dispatch("powerSupply.setOutput", {"enabled": False})
        frames = self.port.writes[before:]
        self.assertEqual([frame[1] for frame in frames], [5, 1, 6, 4, 4, 4])
        self.assertEqual(frames[0][2:6].hex(), "00850000")
        self.assertFalse(self.port.output)
        self.assertTrue(result["safety"]["shutdown"]["confirmed"])
        self.assertFalse(result["devices"]["powerSupply"]["outputEnabled"])
        self.assertEqual(result["devices"]["powerSupply"]["outputCurrentA"], 0)
        self.assertEqual(self.station.configuration["setCurrentA"], 0)

    def test_direct_close_current_read_failure_stays_unconfirmed_without_replaying_off(self):
        self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.stop_burst()
        self.port.fail_register = (4, 101)
        before = len(self.port.writes)
        with self.assertRaises(RuntimeError):
            self.backend.dispatch("powerSupply.setOutput", {"enabled": False})
        self.assertEqual([frame[1] for frame in self.port.writes[before:]], [5, 1, 6, 4])
        self.assertFalse(self.port.output)
        self.assertTrue(self.station.shutdown_output_off_confirmed)
        self.assertFalse(self.station.shutdown_confirmed)

    def test_direct_close_zero_write_failure_does_not_report_confirmed_shutdown(self):
        self.backend.dispatch("powerSupply.setOutput", {"enabled": True})
        self.backend.dispatch("powerSupply.setCurrent", {"currentA": 4})
        self.stop_burst()
        self.port.fail_register = (6, 150)
        before = len(self.port.writes)
        with self.assertRaises(RuntimeError):
            self.backend.dispatch("powerSupply.setOutput", {"enabled": False})
        self.assertEqual([frame[1] for frame in self.port.writes[before:]], [5, 1, 6])
        self.assertFalse(self.port.output)
        self.assertTrue(self.station.shutdown_output_off_confirmed)
        self.assertFalse(self.station.shutdown_confirmed)


if __name__ == "__main__":
    unittest.main()
