"""Unit tests for multi-CH341 location path binding, dynamic index resolution and concurrency."""

from __future__ import annotations

import ctypes
import builtins
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import MagicMock, patch

from combined_test.ch341_ports import Ch341DeviceDescriptor
from combined_test.device_interfaces import ControllerPowerSupply
from combined_test.devices import (
    create_ch341_controller,
    load_legacy_ch341_controller_class,
    scan_ch341_devices,
)
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings


class MockFunc:
    def __init__(self, fn):
        self.fn = fn
        self.argtypes = []
        self.restype = None

    def __call__(self, *args, **kwargs):
        return self.fn(*args, **kwargs)


class FakeDll:
    """Mock CH341DLL simulating multiple physical devices with interface path queries."""

    def __init__(self, available_indices: list[int] | None = None, index_to_path: dict[int, str] | None = None):
        self.available_indices = set(available_indices if available_indices is not None else [0, 1, 2])
        self.index_to_path = dict(index_to_path or {
            0: "\\\\?\\USB#VID_1A86&PID_5512#PORT1#{...}",
            1: "\\\\?\\USB#VID_1A86&PID_5512#PORT2#{...}",
            2: "\\\\?\\USB#VID_1A86&PID_5512#PORT3#{...}",
        })
        self._path_cstrings = {
            idx: ctypes.create_string_buffer(path.encode("utf-8"))
            for idx, path in self.index_to_path.items()
        }
        self.opened_indices: set[int] = set()
        self.call_log: list[tuple[str, int, tuple]] = []
        self._lock = threading.Lock()
        self.CH341OpenDevice = MockFunc(self._open_device)
        self.CH341CloseDevice = MockFunc(self._close_device)
        self.CH341SetStream = MockFunc(self._set_stream)
        self.CH341StreamI2C = MockFunc(self._stream_i2c)
        self.CH341GetVersion = MockFunc(self._get_version)
        self.CH341FlushBuffer = MockFunc(self._flush_buffer)
        self.CH341GetDeviceName = MockFunc(self._get_device_name)

    def _open_device(self, index: int):
        with self._lock:
            self.call_log.append(("CH341OpenDevice", int(index), ()))
            if int(index) in self.available_indices:
                self.opened_indices.add(int(index))
                return 1000 + int(index)
            return 0

    def _close_device(self, index: int):
        with self._lock:
            self.call_log.append(("CH341CloseDevice", int(index), ()))
            self.opened_indices.discard(int(index))
            return None

    def _set_stream(self, index: int, mode: int):
        with self._lock:
            self.call_log.append(("CH341SetStream", int(index), (mode,)))
            return int(index) in self.opened_indices

    def _stream_i2c(self, index: int, write_len: int, write_buf: Any, read_len: int, read_buf: Any):
        with self._lock:
            self.call_log.append(("CH341StreamI2C", int(index), (write_len, read_len)))
            if int(index) not in self.opened_indices:
                return False
            if read_len > 0 and read_buf:
                ctypes.memmove(read_buf, b"\x00\x00\x00\x00", min(4, read_len))
            return True

    def _get_version(self):
        return 0x20

    def _flush_buffer(self, index: int):
        with self._lock:
            self.call_log.append(("CH341FlushBuffer", int(index), ()))
            return True

    def _get_device_name(self, index: int):
        with self._lock:
            self.call_log.append(("CH341GetDeviceName", int(index), ()))
            if int(index) in self._path_cstrings:
                return ctypes.cast(self._path_cstrings[int(index)], ctypes.c_void_p).value
            return 0


class HeadlessCH341ImportTest(unittest.TestCase):
    def test_scan_without_tkinter_uses_only_controller_and_mocked_dll(self):
        """Reproduce the frozen sidecar where the optional Tk toolkit is absent."""
        original_import = builtins.__import__
        toolkit_imports = []

        def without_tk(name, *args, **kwargs):
            if name.split('.')[0] in {'tkinter', '_tkinter'}:
                toolkit_imports.append(name)
                raise ModuleNotFoundError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        fake_dll = FakeDll(available_indices=[1])
        load_legacy_ch341_controller_class.cache_clear()
        try:
            with (
                patch('builtins.__import__', side_effect=without_tk),
                patch('ctypes.WinDLL', return_value=fake_dll, create=True),
                patch('combined_test.devices.get_location_path_from_interface_path', return_value='MOCK_PORT_1'),
            ):
                descriptors = scan_ch341_devices(max_devices=3)
            self.assertEqual(toolkit_imports, [])
            self.assertEqual([item.device_index for item in descriptors], [1])
            self.assertEqual([item.location_path for item in descriptors], ['MOCK_PORT_1'])
            self.assertEqual(fake_dll.opened_indices, set())
        finally:
            load_legacy_ch341_controller_class.cache_clear()


class MultiCH341Test(unittest.TestCase):
    def setUp(self):
        self.controller_cls = load_legacy_ch341_controller_class()

    def test_multi_device_initialization_and_connect(self):
        fake_dll = FakeDll(available_indices=[0, 1, 2])

        dev0 = self.controller_cls(device_index=0)
        dev0.ch341_dll = fake_dll
        dev0.init_dll_functions()

        dev1 = self.controller_cls(device_index=1)
        dev1.ch341_dll = fake_dll
        dev1.init_dll_functions()

        self.assertEqual(dev0.device_index, 0)
        self.assertEqual(dev1.device_index, 1)

        ok0, info0 = dev0.connect_device()
        self.assertTrue(ok0)
        self.assertEqual(info0["device_index"], 0)
        self.assertTrue(dev0.is_connected)

        ok1, info1 = dev1.connect_device()
        self.assertTrue(ok1)
        self.assertEqual(info1["device_index"], 1)
        self.assertTrue(dev1.is_connected)

        # Disconnect dev0, dev1 remains connected
        dev0.disconnect_device()
        self.assertFalse(dev0.is_connected)
        self.assertTrue(dev1.is_connected)

        dev1.disconnect_device()
        self.assertFalse(dev1.is_connected)

    def test_independent_concurrent_communication(self):
        fake_dll = FakeDll(available_indices=[0, 1, 2, 3])
        controllers = []
        for idx in range(4):
            ctrl = self.controller_cls(device_index=idx)
            ctrl.ch341_dll = fake_dll
            ctrl.init_dll_functions()
            ok, _ = ctrl.connect_device()
            self.assertTrue(ok)
            controllers.append(ctrl)

        results = []
        errors = []

        def worker(ctrl, dev_id):
            try:
                for step in range(10):
                    # Write
                    ok, _ = ctrl.i2c_write(0x41, [0x01, step])
                    if not ok:
                        errors.append(f"Write failed on dev {dev_id}")
                    # Write-Read
                    ok, data = ctrl.i2c_write_read(0x41, [0xB4, 0x8B, 0x00, 0x00], 4)
                    if not ok or len(data) != 4:
                        errors.append(f"Write-read failed on dev {dev_id}")
                results.append(dev_id)
            except Exception as exc:
                errors.append(f"Exception on dev {dev_id}: {exc}")

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker, ctrl, i) for i, ctrl in enumerate(controllers)]
            for f in futures:
                f.result()

        self.assertEqual(len(errors), 0, f"Encountered errors: {errors}")
        self.assertEqual(len(results), 4)

        for ctrl in controllers:
            ctrl.disconnect_device()

    def test_controller_power_supply_channel_isolation(self):
        fake_dll = FakeDll(available_indices=[0, 1])
        ctrl0 = self.controller_cls(device_index=0)
        ctrl0.ch341_dll = fake_dll
        ctrl0.init_dll_functions()
        ctrl0.connect_device()

        ctrl1 = self.controller_cls(device_index=1)
        ctrl1.ch341_dll = fake_dll
        ctrl1.init_dll_functions()
        ctrl1.connect_device()

        ps0 = ControllerPowerSupply(ctrl0, address=0x41)
        ps1 = ControllerPowerSupply(ctrl1, address=0x42)

        self.assertTrue(ps0.connected)
        self.assertTrue(ps1.connected)

        ps0.set_current(5.0)
        ps1.set_current(10.0)

        # Verify calls went to respective device indices
        set_stream_dev0 = [c for c in fake_dll.call_log if c[0] == "CH341StreamI2C" and c[1] == 0]
        set_stream_dev1 = [c for c in fake_dll.call_log if c[0] == "CH341StreamI2C" and c[1] == 1]
        self.assertGreater(len(set_stream_dev0), 0)
        self.assertGreater(len(set_stream_dev1), 0)

        ps0.disconnect()
        self.assertFalse(ps0.connected)
        self.assertTrue(ps1.connected)

        ps1.disconnect()
        self.assertFalse(ps1.connected)

    def test_disconnect_device_failure(self):
        fake_dll = FakeDll(available_indices=[0])
        fake_dll.CH341CloseDevice = MockFunc(lambda idx: (_ for _ in ()).throw(RuntimeError("I/O error")))

        ctrl = self.controller_cls(device_index=0)
        ctrl.ch341_dll = fake_dll
        ctrl.init_dll_functions()
        ctrl.connect_device()
        self.assertTrue(ctrl.is_connected)

        ok = ctrl.disconnect_device()
        self.assertFalse(ok)
        self.assertTrue(ctrl.is_connected)

    def test_location_paths_binding_and_re_enumeration(self):
        """Test: Two LocationPaths bound to station names. When Windows re-enumerates and indices swap,
        connecting to station name dynamically follows the physical LocationPath!"""
        backend = HeadlessBackend()
        loc1 = "PCIROOT(0)#USB(1)"
        loc2 = "PCIROOT(0)#USB(2)"

        # Selection is runtime-only; the physical path resolves the live DLL index.
        backend.dispatcher.configure({
            "ch341SelectedLocationPath": loc1,
            "powerSupplyKind": "ch341",
        })

        # Scan A: loc1 is Device 0, loc2 is Device 1
        desc_scan_a = [
            Ch341DeviceDescriptor(device_index=0, interface_path="iface0", location_path=loc1),
            Ch341DeviceDescriptor(device_index=1, interface_path="iface1", location_path=loc2),
        ]

        with patch("tauri_bridge.device_discovery.scan_ch341_devices", return_value=desc_scan_a):
            backend.discovery.refresh_device({"device": "powerSupply"})
            snap = backend.snapshot()
            ports = snap["devices"]["powerSupply"]["ch341"]["ports"]
            self.assertEqual(len(ports), 2)
            p1 = next(p for p in ports if p["locationPath"] == loc1)
            self.assertIsNone(p1["name"])
            self.assertEqual(p1["deviceIndex"], 0)
            self.assertTrue(p1["online"])

        # Scan B: Windows re-enumeration swaps indices! loc1 becomes Device 1, loc2 becomes Device 0
        desc_scan_b = [
            Ch341DeviceDescriptor(device_index=0, interface_path="iface1", location_path=loc2),
            Ch341DeviceDescriptor(device_index=1, interface_path="iface0", location_path=loc1),
        ]

        # Mock controller creation to verify the selected physical path now opens Device 1.
        mock_ctrl = MagicMock()
        mock_ctrl.is_connected = False
        mock_ctrl.connect_device.return_value = (True, "CH341 Device 1 Connected")
        mock_ctrl.get_device_interface_path.return_value = "iface0"

        with patch("tauri_bridge.device_discovery.scan_ch341_devices", return_value=desc_scan_b):
            with patch("tauri_bridge.station_runtime.create_ch341_controller", return_value=mock_ctrl) as mock_factory:
                with patch("tauri_bridge.station_runtime.get_location_path_from_interface_path", return_value=loc1):
                    backend._connect_power_supply()
                    mock_factory.assert_called_once_with(device_index=1)
                    mock_ctrl.connect_device.assert_called_once_with(1)
                    self.assertIn("Device 1", backend.status_message)

    def test_offline_port_does_not_call_connect(self):
        backend = HeadlessBackend()
        backend.dispatcher.configure({
            "ch341PortBindings": [{"locationPath": "PCIROOT(0)#USB(1)", "name": "1号工位"}],
            "ch341SelectedLocationPath": "PCIROOT(0)#USB(1)",
            "powerSupplyKind": "ch341",
        })

        # Scan only returns USB(2), USB(1) is offline
        desc_list = [Ch341DeviceDescriptor(device_index=0, interface_path="iface2", location_path="PCIROOT(0)#USB(2)")]

        with patch("tauri_bridge.device_discovery.scan_ch341_devices", return_value=desc_list):
            with patch("tauri_bridge.station_runtime.create_ch341_controller") as mock_factory:
                with self.assertRaises(RuntimeError) as ctx:
                    backend._connect_power_supply()
                self.assertIn("离线", str(ctx.exception))
                mock_factory.assert_not_called()

    def test_unscanned_empty_or_error_cannot_connect(self):
        backend = HeadlessBackend()
        backend.dispatcher.configure({
            "ch341PortBindings": [{"locationPath": "LOC1", "name": "工位1"}],
            "ch341SelectedLocationPath": "LOC1",
            "powerSupplyKind": "ch341",
        })

        # 1. Empty scan
        with patch("tauri_bridge.device_discovery.scan_ch341_devices", return_value=[]):
            with self.assertRaises(RuntimeError):
                backend._connect_power_supply()

        # 2. Error scan
        with patch("tauri_bridge.device_discovery.scan_ch341_devices", side_effect=RuntimeError("DLL error")):
            with self.assertRaises(RuntimeError) as ctx:
                backend._connect_power_supply()
            self.assertIn("DLL error", str(ctx.exception))

    def test_scan_failure_pushes_notice(self):
        backend = HeadlessBackend()
        backend.dispatcher.configure(
            {"powerSupplyKind": "ch341", "selectedScriptId": "standard"}
        )
        with patch("tauri_bridge.device_discovery.scan_ch341_devices", side_effect=RuntimeError("驱动缺失")):
            backend.discovery.refresh_device({"device": "powerSupply"})
            deadline = time.monotonic() + 1.0
            while (
                backend.power_supply_detect_thread is not None
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertEqual(backend.ch341_discovery_state, "error")
            self.assertIn("驱动缺失", backend.ch341_scan_error)
            self.assertTrue(any(n["title"] == "CH341 扫描失败" for n in backend.notices))

    def test_snapshot_does_not_include_historical_offline_bindings(self):
        backend = HeadlessBackend()
        backend.configuration["ch341PortBindings"] = [
            {"locationPath": "OLD_OFFLINE_LOC", "name": "旧名称"},
        ]
        backend.ch341_discovery_state = "ready"
        backend.ch341_descriptors = [
            Ch341DeviceDescriptor(0, "iface-current", "CURRENT_LOC"),
        ]

        ports = backend.snapshot()["devices"]["powerSupply"]["ch341"]["ports"]
        self.assertEqual(
            ports,
            [{"locationPath": "CURRENT_LOC", "name": None, "online": True, "deviceIndex": 0}],
        )

    def test_selected_port_connects_without_name_binding(self):
        backend = HeadlessBackend()
        backend.dispatcher.configure({
            "ch341PortBindings": [],  # No bindings!
            "ch341SelectedLocationPath": "LOC_UNNAMED",
            "powerSupplyKind": "ch341",
        })
        descriptor = Ch341DeviceDescriptor(
            device_index=0,
            interface_path="iface0",
            location_path="LOC_UNNAMED",
        )
        controller = MagicMock()
        controller.connect_device.return_value = (True, "connected")
        controller.get_device_interface_path.return_value = "iface0"

        with patch("tauri_bridge.device_discovery.scan_ch341_devices", return_value=[descriptor]):
            with patch("tauri_bridge.station_runtime.create_ch341_controller", return_value=controller):
                with patch("tauri_bridge.station_runtime.get_location_path_from_interface_path", return_value="LOC_UNNAMED"):
                    backend._connect_power_supply()

        self.assertEqual(backend.connected_ch341_port["name"], "Device 0")
        self.assertEqual(backend.configuration["ch341PortBindings"], [])

    def test_duplicate_name_or_duplicate_path_rejected(self):
        backend = HeadlessBackend()
        # Duplicate path
        with self.assertRaises(ValueError) as ctx:
            backend.dispatcher.configure({
                "ch341PortBindings": [
                    {"locationPath": "LOC1", "name": "工位1"},
                    {"locationPath": "LOC1", "name": "工位2"},
                ]
            })
        self.assertIn("重复的 locationPath", str(ctx.exception))

        # Duplicate name
        with self.assertRaises(ValueError) as ctx:
            backend.dispatcher.configure({
                "ch341PortBindings": [
                    {"locationPath": "LOC1", "name": "工位1"},
                    {"locationPath": "LOC2", "name": "工位1"},
                ]
            })
        self.assertIn("重复的工位名称", str(ctx.exception))

    def test_cannot_modify_bindings_or_selected_while_connected(self):
        backend = HeadlessBackend()
        fake_ctrl = MagicMock()
        fake_ctrl.is_connected = True
        backend.power_supply_controller = fake_ctrl
        backend.power_supply_kind = "ch341"
        backend.configuration["ch341SelectedLocationPath"] = "LOC1"
        backend.configuration["ch341PortBindings"] = [{"locationPath": "LOC1", "name": "工位1"}]

        # Mutating bindings while connected
        with self.assertRaises(ValueError) as ctx:
            backend.dispatcher.configure({
                "ch341PortBindings": [{"locationPath": "LOC1", "name": "工位1新名称"}]
            })
        self.assertIn("请先断开电源", str(ctx.exception))

        # Changing selected location path while connected
        with self.assertRaises(ValueError) as ctx:
            backend.dispatcher.configure({"ch341SelectedLocationPath": "LOC2"})
        self.assertIn("请先断开电源", str(ctx.exception))

    def test_post_open_verification_mismatch_disconnects(self):
        backend = HeadlessBackend()
        loc_target = "LOC_TARGET"
        backend.dispatcher.configure({
            "ch341PortBindings": [{"locationPath": loc_target, "name": "1号工位"}],
            "ch341SelectedLocationPath": loc_target,
            "powerSupplyKind": "ch341",
        })

        desc_list = [Ch341DeviceDescriptor(device_index=0, interface_path="iface0", location_path=loc_target)]
        mock_ctrl = MagicMock()
        mock_ctrl.is_connected = True
        mock_ctrl.connect_device.return_value = (True, "Connected")
        mock_ctrl.get_device_interface_path.return_value = "iface_OTHER"
        mock_ctrl.disconnect_device.return_value = True

        with patch("tauri_bridge.device_discovery.scan_ch341_devices", return_value=desc_list):
            with patch("tauri_bridge.station_runtime.create_ch341_controller", return_value=mock_ctrl):
                with patch("tauri_bridge.station_runtime.get_location_path_from_interface_path", return_value="LOC_WRONG"):
                    with self.assertRaises(RuntimeError) as ctx:
                        backend._connect_power_supply()
                    self.assertIn("端口复核不一致", str(ctx.exception))
                    mock_ctrl.disconnect_device.assert_called_once()
                    self.assertIsNone(backend.power_supply_controller)

    def test_post_open_verification_close_failure_retains_controller(self):
        backend = HeadlessBackend()
        backend.dispatcher.configure({
            "ch341PortBindings": [{"locationPath": "LOC_TARGET", "name": "1号工位"}],
            "ch341SelectedLocationPath": "LOC_TARGET",
            "powerSupplyKind": "ch341",
        })
        descriptors = [Ch341DeviceDescriptor(0, "iface0", "LOC_TARGET")]
        mock_ctrl = MagicMock()
        mock_ctrl.connect_device.return_value = (True, "Connected")
        mock_ctrl.get_device_interface_path.return_value = "iface_wrong"
        mock_ctrl.disconnect_device.return_value = False

        with patch("tauri_bridge.device_discovery.scan_ch341_devices", return_value=descriptors):
            with patch("tauri_bridge.station_runtime.create_ch341_controller", return_value=mock_ctrl):
                with patch("tauri_bridge.station_runtime.get_location_path_from_interface_path", return_value="LOC_WRONG"):
                    with self.assertRaisesRegex(RuntimeError, "设备关闭失败"):
                        backend._connect_power_supply()

        self.assertIs(backend.power_supply_controller, mock_ctrl)
        self.assertTrue(backend.power_supply_identity_error)
        snapshot = backend.snapshot()
        self.assertEqual(snapshot["devices"]["powerSupply"]["state"], "error")

        mock_ctrl.disconnect_device.return_value = True
        backend._disconnect_power_supply()
        self.assertIsNone(backend.power_supply_controller)
        self.assertEqual(backend.power_supply_identity_error, "")

    def test_connected_snapshot_displays_live_device_index(self):
        backend = HeadlessBackend()
        controller = MagicMock()
        controller.is_connected = True
        backend.power_supply_controller = controller
        backend.power_supply_kind = "ch341"
        backend.connected_ch341_port = {
            "locationPath": "LOC1",
            "name": "Device 2",
            "deviceIndex": 2,
        }

        detail = backend.snapshot()["devices"]["powerSupply"]["detail"]
        self.assertEqual(detail, "Device 2 · 已连接")

    def test_ch341_selection_and_bindings_are_not_persisted(self):
        with TemporaryDirectory() as directory:
            settings = JsonSettings(Path(directory) / "settings.json")
            settings.set(
                "runtime/stations",
                {
                    "1": {
                        "ch341SelectedLocationPath": "LOC1",
                        "ch341PortBindings": [{"locationPath": "LOC1", "name": "旧名称"}],
                    },
                    "2": {
                        "ch341SelectedLocationPath": "LOC2",
                        "ch341PortBindings": [{"locationPath": "LOC2", "name": "旧名称2"}],
                    },
                },
            )

            backend = HeadlessBackend(settings)
            try:
                self.assertEqual(backend.runtime.configuration["ch341SelectedLocationPath"], "")
                self.assertEqual(backend.runtime.configuration["ch341PortBindings"], [])

                backend.runtime.configuration["ch341SelectedLocationPath"] = "CURRENT_LOC"
                backend._persist_configuration()
                persisted = settings.get("runtime/configuration")
                self.assertEqual(persisted["ch341SelectedLocationPath"], "")
                self.assertEqual(persisted["ch341PortBindings"], [])
                self.assertIsNone(settings.get("runtime/stations"))
            finally:
                backend.export_worker.graceful_stop(timeout_s=5.0)

    def test_legacy_device_index_is_not_loaded_or_persisted(self):
        with TemporaryDirectory() as directory:
            settings = JsonSettings(Path(directory) / "settings.json")
            settings.set(
                "runtime/configuration",
                {"ch341DeviceIndex": 7, "powerSupplyKind": "ch341"},
            )
            reloaded = HeadlessBackend(settings)

            self.assertNotIn("ch341DeviceIndex", reloaded.configuration)
            reloaded._persist_configuration()
            stored = settings.get("runtime/configuration", {})
            self.assertNotIn("ch341DeviceIndex", stored)

    def test_ch341_tdk_switch_clears_opposite_scan(self):
        backend = HeadlessBackend()
        backend.power_supply_kind = "ch341"
        backend.ch341_discovery_state = "ready"
        backend.ch341_descriptors = [Ch341DeviceDescriptor(0, "iface", "loc")]

        # Switch to TDK
        backend.dispatcher.configure(
            {"powerSupplyKind": "tdk", "selectedScriptId": "standard"}
        )
        self.assertEqual(backend.power_supply_kind, "tdk")
        self.assertEqual(backend.ch341_discovery_state, "not_scanned")
        self.assertEqual(backend.ch341_descriptors, [])


if __name__ == "__main__":
    unittest.main()
