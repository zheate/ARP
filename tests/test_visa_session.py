from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.visa_session import VisaResourceManagerPool, _create_default_resource_manager


class FakeResourceManager:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class VisaResourceManagerPoolTests(unittest.TestCase):
    def test_missing_native_library_uses_bundled_serial_transport(self) -> None:
        sentinel = object()
        with patch.dict('os.environ', {}, clear=True), patch('pyvisa.highlevel.open_visa_library', side_effect=OSError('no VISA DLL')), patch('tools.serial_visa.create_serial_resource_manager', return_value=sentinel):
            self.assertIs(_create_default_resource_manager(), sentinel)

    def test_present_native_library_is_preserved(self) -> None:
        library, manager = object(), object()
        with patch.dict('os.environ', {}, clear=True), patch('pyvisa.highlevel.open_visa_library', return_value=library), patch('pyvisa.ResourceManager', return_value=manager) as create:
            self.assertIs(_create_default_resource_manager(), manager)
            create.assert_called_once_with(library)

    def test_native_runtime_failure_does_not_change_transport(self) -> None:
        import pyvisa
        failure = pyvisa.errors.VisaIOError(-1073807360)
        with patch.dict('os.environ', {}, clear=True), patch('pyvisa.highlevel.open_visa_library', return_value=object()), patch('pyvisa.ResourceManager', side_effect=failure), patch('tools.serial_visa.create_serial_resource_manager') as fallback:
            with self.assertRaises(pyvisa.errors.VisaIOError):
                _create_default_resource_manager()
            fallback.assert_not_called()

    def test_explicit_library_selection_is_not_overridden(self) -> None:
        with patch.dict('os.environ', {'PYVISA_LIBRARY': 'selected.dll'}), patch('pyvisa.ResourceManager', side_effect=OSError('selected DLL absent')), patch('tools.serial_visa.create_serial_resource_manager') as fallback:
            with self.assertRaises(OSError):
                _create_default_resource_manager()
            fallback.assert_not_called()

    def test_manager_stays_open_until_last_device_releases_it(self) -> None:
        managers: list[FakeResourceManager] = []

        def make_manager() -> FakeResourceManager:
            manager = FakeResourceManager()
            managers.append(manager)
            return manager

        pool = VisaResourceManagerPool(make_manager)
        tdk_manager = pool.acquire()
        power_meter_manager = pool.acquire()

        self.assertIs(tdk_manager, power_meter_manager)
        pool.release(power_meter_manager)
        self.assertEqual(tdk_manager.close_count, 0)

        pool.release(tdk_manager)
        self.assertEqual(tdk_manager.close_count, 1)

    def test_new_manager_is_created_after_all_users_release_previous_one(self) -> None:
        managers: list[FakeResourceManager] = []

        def make_manager() -> FakeResourceManager:
            manager = FakeResourceManager()
            managers.append(manager)
            return manager

        pool = VisaResourceManagerPool(make_manager)
        first = pool.acquire()
        pool.release(first)
        second = pool.acquire()

        self.assertIsNot(first, second)
        self.assertEqual(len(managers), 2)
        pool.release(second)


if __name__ == "__main__":
    unittest.main()
