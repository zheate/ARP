from __future__ import annotations

import sys
import textwrap
import threading
import time
import unittest

from tools.usb2xxx_control import USB2XXXProcessController


def fake_worker_command(*, hang_method: str | None = None) -> list[str]:
    script = textwrap.dedent(
        f"""
        import json
        import sys
        import time

        connected = False
        hang_method = {hang_method!r}
        for line in sys.stdin:
            request = json.loads(line)
            request_id = request["id"]
            method = request["method"]
            if method == hang_method:
                time.sleep(60)
            if method == "ping":
                result = {{"protocol": 1, "pid": 123}}
            elif method == "set_i2c_speed":
                result = True
            elif method == "connect_device":
                connected = True
                result = [True, {{"controller": "USB2XXX", "serial": "FAKE0001"}}]
            elif method == "disconnect_device":
                connected = False
                result = True
            elif method == "i2c_write":
                result = [True, "写入成功"]
            elif method == "i2c_write_read":
                result = [True, [0x5A, 0x4D, 0x19, 0x43]]
            else:
                result = True
            response = {{
                "id": request_id,
                "ok": True,
                "result": result,
                "state": {{"is_connected": connected, "output_enabled": True}},
            }}
            sys.stdout.write(json.dumps(response) + "\\n")
            sys.stdout.flush()
        """
    )
    return [sys.executable, "-u", "-c", script]


class USB2XXXProcessControllerTests(unittest.TestCase):
    def test_default_worker_command_starts_without_project_package_import_errors(self) -> None:
        controller = USB2XXXProcessController(
            call_timeout_s=1.0,
            start_timeout_s=2.0,
        )
        self.addCleanup(controller.close_worker)

        response = controller._request("ping", {})
        self.assertEqual(response["protocol"], controller.PROTOCOL_VERSION)
        process = controller._process
        self.assertIsNotNone(process)
        assert process is not None
        self.assertIsNone(process.poll())
        self.assertNotIn("No module named 'combined_test'", "\n".join(controller._stderr_tail))

    def test_process_proxy_round_trip_and_clean_disconnect(self) -> None:
        controller = USB2XXXProcessController(
            call_timeout_s=1.0,
            start_timeout_s=1.0,
            worker_command=fake_worker_command(),
        )
        self.addCleanup(controller.close_worker)

        self.assertTrue(controller.set_i2c_speed(0))
        connected, detail = controller.connect_device(0)
        self.assertTrue(connected)
        self.assertEqual(detail["serial"], "FAKE0001")
        self.assertEqual(detail["isolation"], "subprocess")

        self.assertEqual(
            controller.i2c_write(0x41, [0xB4, 0xFF, 0x01, 0x00]),
            (True, "写入成功"),
        )
        self.assertEqual(
            controller.i2c_write_read(0x41, [0xB4, 0x8B, 0x00, 0x00], 4),
            (True, [0x5A, 0x4D, 0x19, 0x43]),
        )
        process = controller._process
        self.assertIsNotNone(process)

        self.assertTrue(controller.disconnect_device())
        self.assertFalse(controller.is_connected)
        self.assertIsNone(controller._process)
        assert process is not None
        self.assertIsNotNone(process.poll())

    def test_hung_dll_call_is_cut_off_by_killing_worker(self) -> None:
        controller = USB2XXXProcessController(
            call_timeout_s=0.2,
            start_timeout_s=1.0,
            worker_command=fake_worker_command(hang_method="i2c_write_read"),
        )
        self.addCleanup(controller.close_worker)
        self.assertTrue(controller.connect_device(0)[0])
        process = controller._process
        self.assertIsNotNone(process)

        started = time.monotonic()
        success, detail = controller.i2c_write_read(
            0x41,
            [0xB4, 0x8B, 0x00, 0x00],
            4,
        )

        self.assertFalse(success)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertIn("超过 0.2 秒", detail)
        self.assertIn("已终止", detail)
        self.assertFalse(controller.is_connected)
        self.assertIsNone(controller._process)
        self.assertEqual(controller.fault_reason, detail)
        assert process is not None
        self.assertIsNotNone(process.poll())

    def test_explicit_reconnect_starts_a_fresh_worker_after_timeout(self) -> None:
        controller = USB2XXXProcessController(
            call_timeout_s=0.2,
            start_timeout_s=1.0,
            worker_command=fake_worker_command(hang_method="i2c_write_read"),
        )
        self.addCleanup(controller.close_worker)
        self.assertTrue(controller.connect_device(0)[0])
        first_process = controller._process

        self.assertFalse(
            controller.i2c_write_read(0x41, [0xB4, 0x8B, 0x00, 0x00], 4)[0]
        )
        self.assertTrue(controller.connect_device(0)[0])
        second_process = controller._process

        self.assertIsNotNone(second_process)
        self.assertIsNot(first_process, second_process)
        self.assertTrue(controller.is_connected)

    def test_emergency_abort_wakes_blocked_request_and_allows_reconnect(self) -> None:
        controller = USB2XXXProcessController(
            call_timeout_s=10.0,
            start_timeout_s=1.0,
            worker_command=fake_worker_command(hang_method="i2c_write_read"),
        )
        self.addCleanup(controller.close_worker)
        self.assertTrue(controller.connect_device(0)[0])
        first_process = controller._process
        results: list[tuple[bool, object]] = []
        read_thread = threading.Thread(
            target=lambda: results.append(
                controller.i2c_write_read(0x41, [0xB4, 0x8B, 0x00, 0x00], 4)
            )
        )
        read_thread.start()
        time.sleep(0.1)

        reason = "功率保护：中止后台输出电压读取"
        self.assertTrue(controller.abort_in_flight(reason))
        read_thread.join(2.0)

        self.assertFalse(read_thread.is_alive())
        self.assertEqual(results, [(False, reason)])
        self.assertFalse(controller.is_connected)
        assert first_process is not None
        self.assertIsNotNone(first_process.poll())
        self.assertTrue(controller.connect_device(0)[0])
        self.assertTrue(controller.is_connected)


if __name__ == "__main__":
    unittest.main()
