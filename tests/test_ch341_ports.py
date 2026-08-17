from __future__ import annotations

import ctypes
import unittest
from unittest.mock import patch

import combined_test.ch341_ports as ch341_ports


class _FakeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class _FakeSetupApi:
    def __init__(self, location_paths: list[str]):
        raw = ("\x00".join(location_paths) + "\x00\x00").encode("utf-16-le")

        self.SetupDiCreateDeviceInfoList = _FakeFunction(lambda _guid, _window: 100)
        self.SetupDiOpenDeviceInterfaceW = _FakeFunction(
            lambda _info_set, _path, _flags, _interface_data: True
        )

        def get_detail(_info_set, _interface_data, _detail, _detail_size, required_size, _device_info):
            ctypes.cast(required_size, ctypes.POINTER(ctypes.c_ulong)).contents.value = 64
            return False

        def get_property(
            _info_set,
            _device_info,
            property_key,
            property_type,
            property_buffer,
            _property_buffer_size,
            required_size,
            _flags,
        ):
            actual_key = ctypes.cast(
                property_key, ctypes.POINTER(ch341_ports.DEVPROPKEY)
            ).contents
            if actual_key.pid != 37:
                raise AssertionError(
                    f"LocationPaths property id must be 37, got {actual_key.pid}"
                )
            ctypes.cast(required_size, ctypes.POINTER(ctypes.c_ulong)).contents.value = len(raw)
            if not property_buffer:
                return False
            ctypes.cast(property_type, ctypes.POINTER(ctypes.c_ulong)).contents.value = (
                ch341_ports.DEVPROP_TYPE_STRING_LIST
            )
            ctypes.memmove(property_buffer, raw, len(raw))
            return True

        self.SetupDiGetDeviceInterfaceDetailW = _FakeFunction(get_detail)
        self.SetupDiGetDevicePropertyW = _FakeFunction(get_property)
        self.SetupDiDestroyDeviceInfoList = _FakeFunction(lambda _info_set: True)


class Ch341PortResolverTest(unittest.TestCase):
    def test_resolves_device_instance_location_path_with_setupapi(self):
        setupapi = _FakeSetupApi([
            "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(3)",
            "ACPI(_SB_)#ACPI(PCI0)#USBROOT(0)#USB(3)",
        ])
        with patch.object(ch341_ports.platform, "system", return_value="Windows"):
            with patch.object(ch341_ports.ctypes, "WinDLL", return_value=setupapi, create=True):
                with patch.object(
                    ch341_ports,
                    "_last_error",
                    side_effect=[
                        ch341_ports.ERROR_INSUFFICIENT_BUFFER,
                        ch341_ports.ERROR_INSUFFICIENT_BUFFER,
                    ],
                ):
                    location = ch341_ports.get_location_path_from_interface_path(
                        r"\\?\usb#vid_1a86&pid_5512#example#{guid}"
                    )

        self.assertEqual(location, "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(3)")

    def test_does_not_fall_back_to_interface_path_when_setupapi_fails(self):
        with patch.object(ch341_ports.platform, "system", return_value="Windows"):
            with patch.object(
                ch341_ports.ctypes,
                "WinDLL",
                side_effect=OSError("setupapi missing"),
                create=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "SetupAPI"):
                    ch341_ports.get_location_path_from_interface_path("interface-path")

    def test_non_windows_is_explicitly_unsupported(self):
        with patch.object(ch341_ports.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(RuntimeError, "仅支持 Windows"):
                ch341_ports.get_location_path_from_interface_path("interface-path")


if __name__ == "__main__":
    unittest.main()
