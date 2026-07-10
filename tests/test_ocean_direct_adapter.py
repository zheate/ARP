import unittest
from ctypes import c_double, c_long

from combined_test.ocean_direct_adapter import OceanDirectControl


class FakeOceanDirectApi:
    def __init__(self) -> None:
        self.initialized = False
        self.shutdown_called = False
        self.opened_device_id = None
        self.closed_device_id = None
        self.integration_time = 10000
        self.device_ids = [101, 202]
        self.wavelength = [975.0, 976.0, 977.0]
        self.intensity = [1.0, 5.0, 2.0]

    def odapi_initialize(self) -> None:
        self.initialized = True

    def odapi_shutdown(self) -> None:
        self.shutdown_called = True

    def odapi_probe_devices(self) -> int:
        return len(self.device_ids)

    def odapi_get_number_of_device_ids(self) -> int:
        return len(self.device_ids)

    def odapi_get_device_ids(self, ids, err) -> int:
        err[0] = 0
        for index, device_id in enumerate(self.device_ids):
            ids[index] = device_id
        return len(self.device_ids)

    def odapi_open_device(self, device_id: int, err) -> None:
        err[0] = 0
        self.opened_device_id = int(device_id)

    def odapi_close_device(self, device_id: int, err) -> None:
        err[0] = 0
        self.closed_device_id = int(device_id)

    def odapi_get_formatted_spectrum_length(self, device_id: int, err) -> int:
        err[0] = 0
        return len(self.wavelength)

    def odapi_set_integration_time_micros(self, device_id: int, err, value) -> None:
        err[0] = 0
        self.integration_time = int(value.value)

    def odapi_get_integration_time_micros(self, device_id: int, err) -> int:
        err[0] = 0
        return self.integration_time

    def odapi_get_minimum_integration_time_micros(self, device_id: int, err) -> int:
        err[0] = 0
        return 1

    def odapi_get_maximum_integration_time_micros(self, device_id: int, err) -> int:
        err[0] = 0
        return 10_000_000

    def odapi_get_maximum_intensity(self, device_id: int, err) -> float:
        err[0] = 0
        return 65535.0

    def odapi_get_wavelengths(self, device_id: int, err, buffer, length: int) -> None:
        err[0] = 0
        for index, value in enumerate(self.wavelength[:length]):
            buffer[index] = value

    def odapi_get_formatted_spectrum(self, device_id: int, err, buffer, length: int) -> None:
        err[0] = 0
        for index, value in enumerate(self.intensity[:length]):
            buffer[index] = value

    def odapi_get_error_string_length(self, errno: int) -> int:
        return 16

    def odapi_get_error_string(self, errno: int, buffer, length: int) -> None:
        buffer.value = b"fake error"


class OceanDirectControlTests(unittest.TestCase):
    def test_uses_injected_api_to_find_open_read_and_close_device(self) -> None:
        api = FakeOceanDirectApi()
        control = OceanDirectControl(api=api)

        self.assertTrue(api.initialized)
        self.assertEqual(control.find_usb_devices(), 2)
        self.assertEqual(control.get_device_ids(), [101, 202])
        self.assertEqual(control.open_device(202), 0)
        self.assertEqual(api.opened_device_id, 202)

        self.assertEqual(control.set_integration_time(25000), 0)
        self.assertEqual(control.get_integration_time(), 25000)
        self.assertEqual(control.get_minimum_integration_time(), 1)
        self.assertEqual(control.get_maximum_integration_time(), 10_000_000)
        self.assertEqual(control.get_maximum_intensity(), 65535.0)
        self.assertEqual(list(control.get_wavelength()), [975.0, 976.0, 977.0])
        self.assertEqual(list(control.get_intensity()), [1.0, 5.0, 2.0])

        control.close_device()
        self.assertEqual(api.closed_device_id, 202)


if __name__ == "__main__":
    unittest.main()
