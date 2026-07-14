from __future__ import annotations

import unittest

from tools.visa_session import VisaResourceManagerPool


class FakeResourceManager:
    def __init__(self) -> None:
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class VisaResourceManagerPoolTests(unittest.TestCase):
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
