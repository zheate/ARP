from __future__ import annotations

import unittest
from typing import Any, Callable

from tools.usb2xxx_control import USB2XXXI2CController


class FakeFunction:
    def __init__(self, implementation: Callable[..., Any]) -> None:
        self.implementation = implementation
        self.argtypes: list[Any] | None = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> Any:
        return self.implementation(*args)


class FakeUSB2XXXDll:
    HANDLE = 0x310027B4

    def __init__(
        self,
        *,
        init_status: int = 0,
        write_status: int = 0,
        read_status: int = 0,
        response: list[int] | None = None,
    ) -> None:
        self.events: list[Any] = []
        self.config = None
        self.init_status = init_status
        self.write_status = write_status
        self.read_status = read_status
        self.response = [0x5A, 0x4D, 0x19, 0x43] if response is None else response
        self.USB_ScanDevice = FakeFunction(self._scan)
        self.USB_OpenDevice = FakeFunction(self._open)
        self.USB_CloseDevice = FakeFunction(self._close)
        self.DEV_SetPowerLevel = FakeFunction(self._set_power_level)
        self.IIC_Init = FakeFunction(self._init)
        self.IIC_WriteBytes = FakeFunction(self._write)
        self.IIC_ReadBytes = FakeFunction(self._read)
        self.IIC_WriteReadBytes = FakeFunction(self._combined_write_read)

    def _scan(self, handles: Any) -> int:
        self.events.append("scan")
        handles[0] = self.HANDLE
        return 1

    def _open(self, handle: int) -> int:
        self.events.append(("open", handle))
        return 1

    def _close(self, handle: int) -> int:
        self.events.append(("close", handle))
        return 1

    def _set_power_level(self, handle: int, level: int) -> int:
        self.events.append(("power", handle, level))
        return 1

    def _init(self, handle: int, channel: int, config_pointer: Any) -> int:
        self.events.append(("init", handle, channel))
        self.config = config_pointer._obj
        return self.init_status

    def _write(
        self,
        handle: int,
        channel: int,
        address: int,
        buffer: Any,
        length: int,
        timeout_ms: int,
    ) -> int:
        self.events.append(
            ("write", handle, channel, address, list(buffer[:length]), timeout_ms)
        )
        return self.write_status

    def _read(
        self,
        handle: int,
        channel: int,
        address: int,
        buffer: Any,
        length: int,
        timeout_ms: int,
    ) -> int:
        self.events.append(("read", handle, channel, address, length, timeout_ms))
        for index in range(length):
            buffer[index] = self.response[index]
        return self.read_status

    def _combined_write_read(self, *_args: Any) -> int:
        raise AssertionError("不得调用 IIC_WriteReadBytes")


class USB2XXXControllerTests(unittest.TestCase):
    def make_controller(self, dll: FakeUSB2XXXDll, sleeps: list[float] | None = None):
        captured_sleeps = [] if sleeps is None else sleeps
        return USB2XXXI2CController(
            dll=dll,
            sleep_fn=captured_sleeps.append,
        )

    def test_connect_initializes_channel_zero_for_known_safe_configuration(self) -> None:
        dll = FakeUSB2XXXDll()
        controller = self.make_controller(dll)

        self.assertTrue(controller.set_i2c_speed(0))
        connected, detail = controller.connect_device(0)

        self.assertTrue(connected)
        self.assertEqual(detail["serial"], "310027B4")
        self.assertEqual(detail["channel"], 0)
        self.assertEqual(detail["address"], "0x41")
        self.assertEqual(detail["clock_hz"], 10_000)
        self.assertEqual(detail["interface_voltage_v"], 3.3)
        self.assertEqual(dll.config.ClockSpeedHz, 10_000)
        self.assertEqual(dll.config.Master, 1)
        self.assertEqual(dll.config.AddrBits, 7)
        self.assertEqual(dll.config.EnablePu, 1)
        self.assertEqual(dll.config.ByteIntervalUs, 0)
        self.assertIn(("power", dll.HANDLE, 3), dll.events)

    def test_read_uses_stop_separated_write_wait_and_read(self) -> None:
        dll = FakeUSB2XXXDll()
        sleeps: list[float] = []
        controller = self.make_controller(dll, sleeps)
        self.assertTrue(controller.connect_device(0)[0])
        dll.events.clear()

        success, data = controller.i2c_write_read(0x41, [0xB4, 0x8B, 0x00, 0x00], 4)

        self.assertTrue(success)
        self.assertEqual(data, [0x5A, 0x4D, 0x19, 0x43])
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(dll.events[0][0], "write")
        self.assertEqual(dll.events[0][3], 0x41)
        self.assertEqual(dll.events[0][4], [0xB4, 0x8B, 0x00, 0x00])
        self.assertEqual(dll.events[0][5], 3_000)
        self.assertEqual(dll.events[1][0], "read")

    def test_rejects_shifted_frame_instead_of_displaying_false_voltage(self) -> None:
        dll = FakeUSB2XXXDll(response=[0x43, 0x5A, 0x4D, 0x1A])
        controller = self.make_controller(dll)
        self.assertTrue(controller.connect_device(0)[0])

        success, detail = controller.i2c_write_read(0x41, [0xB4, 0x8B, 0x00, 0x00], 4)

        self.assertFalse(success)
        self.assertIn("响应帧错位", detail)
        self.assertIn("43 5A 4D 1A", detail)
        self.assertIn("拒绝使用该数值", detail)

    def test_accepts_temperature_frame_with_command_specific_second_byte(self) -> None:
        dll = FakeUSB2XXXDll(response=[0x5A, 0x06, 0x21, 0x20])
        controller = self.make_controller(dll)
        self.assertTrue(controller.connect_device(0)[0])

        success, data = controller.i2c_write_read(0x41, [0xB4, 0x8D, 0x00, 0x00], 4)

        self.assertTrue(success)
        self.assertEqual(data, [0x5A, 0x06, 0x21, 0x20])

    def test_rejects_invalid_decimal_byte(self) -> None:
        dll = FakeUSB2XXXDll(response=[0x5A, 0x4D, 0x19, 0xFF])
        controller = self.make_controller(dll)
        self.assertTrue(controller.connect_device(0)[0])

        success, detail = controller.i2c_write_read(0x41, [0xB4, 0x8C, 0x00, 0x00], 4)

        self.assertFalse(success)
        self.assertIn("响应小数位异常", detail)
        self.assertIn("5A 4D 19 FF", detail)

    def test_rejects_other_i2c_addresses_without_touching_bus(self) -> None:
        dll = FakeUSB2XXXDll()
        controller = self.make_controller(dll)
        self.assertTrue(controller.connect_device(0)[0])
        dll.events.clear()

        success, detail = controller.i2c_write(0x42, [0xB4, 0xFF, 0x01, 0x00])

        self.assertFalse(success)
        self.assertIn("仅允许访问固定 7 位地址 0x41", detail)
        self.assertEqual(dll.events, [])

    def test_reports_bus_busy_with_recovery_hint(self) -> None:
        dll = FakeUSB2XXXDll(write_status=2)
        controller = self.make_controller(dll)
        self.assertTrue(controller.connect_device(0)[0])

        success, detail = controller.i2c_write(0x41, [0xB4, 0xFF, 0x01, 0x00])

        self.assertFalse(success)
        self.assertIn("总线忙", detail)
        self.assertIn("断电重启", detail)

    def test_init_failure_closes_opened_adapter(self) -> None:
        dll = FakeUSB2XXXDll(init_status=4)
        controller = self.make_controller(dll)

        connected, detail = controller.connect_device(0)

        self.assertFalse(connected)
        self.assertIn("超时", detail)
        self.assertIn(("close", dll.HANDLE), dll.events)
        self.assertIn(("power", dll.HANDLE, 0), dll.events)
        self.assertFalse(controller.is_connected)
        self.assertIsNone(controller.device_handle)


if __name__ == "__main__":
    unittest.main()
