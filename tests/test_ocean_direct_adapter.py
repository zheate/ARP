import tempfile
import unittest
from ctypes import c_double, c_long
from pathlib import Path
from unittest.mock import patch

import numpy as np

from combined_test.ocean_direct_adapter import (
    OceanDirectControl,
    OceanDirectError,
    _GLOBAL_SESSIONS,
    _GLOBAL_SESSIONS_LOCK,
)
from tools import spectrometer_mvp


class FakeOceanDirectApi:
    def __init__(self) -> None:
        self.initialized = False
        self.initialize_count = 0
        self.shutdown_called = False
        self.shutdown_count = 0
        self.opened_device_ids: list[int] = []
        self.closed_device_ids: list[int] = []
        self.open_error = 0
        self.integration_error = 0
        self.serial_errors: dict[int, int] = {}
        self.integration_times = {101: 10000, 202: 20000}
        self.device_ids = [101, 202]
        self.wavelength = [975.0, 976.0, 977.0]
        self.intensity = [1.0, 5.0, 2.0]

    @property
    def opened_device_id(self) -> int | None:
        return self.opened_device_ids[-1] if self.opened_device_ids else None

    @property
    def closed_device_id(self) -> int | None:
        return self.closed_device_ids[-1] if self.closed_device_ids else None

    def odapi_initialize(self) -> None:
        self.initialized = True
        self.initialize_count += 1

    def odapi_shutdown(self) -> None:
        self.shutdown_called = True
        self.shutdown_count += 1

    def odapi_probe_devices(self) -> int:
        return len(self.device_ids)

    def odapi_get_number_of_device_ids(self) -> int:
        return len(self.device_ids)

    def odapi_get_device_ids(self, ids, err) -> int:
        err[0] = 0
        for index, device_id in enumerate(self.device_ids):
            ids[index] = device_id
        return len(self.device_ids)

    def odapi_get_serial_number(self, device_id: int, err, buffer, _length: int) -> int:
        err[0] = self.serial_errors.get(int(device_id), 0)
        if err[0] != 0:
            return 0
        value = f"OCEAN-{int(device_id)}".encode("utf-8")
        buffer.value = value
        return len(value)

    def odapi_open_device(self, device_id: int, err) -> None:
        err[0] = self.open_error
        self.opened_device_ids.append(int(device_id))

    def odapi_close_device(self, device_id: int, err) -> None:
        err[0] = 0
        self.closed_device_ids.append(int(device_id))

    def odapi_get_formatted_spectrum_length(self, device_id: int, err) -> int:
        err[0] = 0
        return len(self.wavelength)

    def odapi_set_integration_time_micros(self, device_id: int, err, value) -> None:
        err[0] = self.integration_error
        self.integration_times[int(device_id)] = int(value.value)

    def odapi_get_integration_time_micros(self, device_id: int, err) -> int:
        err[0] = 0
        return self.integration_times.get(int(device_id), 10000)

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
    def test_reads_stable_device_serial_number(self) -> None:
        api = FakeOceanDirectApi()
        control = OceanDirectControl(api=api)
        try:
            self.assertEqual(control.get_serial_number(101), "OCEAN-101")
        finally:
            control.shutdown()

    def test_missing_serial_feature_returns_empty_serial_number(self) -> None:
        api = FakeOceanDirectApi()
        api.serial_errors[3] = 5
        control = OceanDirectControl(api=api)
        try:
            self.assertEqual(control.get_serial_number(3), "")
        finally:
            control.shutdown()

    def test_other_serial_errors_remain_fatal(self) -> None:
        api = FakeOceanDirectApi()
        api.serial_errors[3] = 7
        control = OceanDirectControl(api=api)
        try:
            with self.assertRaisesRegex(
                OceanDirectError,
                r"OceanDirect get_serial_number\(3\) 失败，错误码 7：fake error",
            ):
                control.get_serial_number(3)
        finally:
            control.shutdown()

    def test_detection_keeps_device_without_serial_feature(self) -> None:
        api = FakeOceanDirectApi()
        api.device_ids = [3]
        api.serial_errors[3] = 5
        control = OceanDirectControl(api=api)

        with patch.object(spectrometer_mvp, "OceanDirectControl", return_value=control):
            self.assertEqual(
                spectrometer_mvp.OceanSpectrometer.detect_devices(),
                [{"deviceId": 3, "serialNumber": ""}],
            )

    def tearDown(self) -> None:
        with _GLOBAL_SESSIONS_LOCK:
            _GLOBAL_SESSIONS.clear()

    def test_toolkit_free_reader_preserves_the_standalone_thread_contract(self) -> None:
        actions: list[object] = []

        class FakeSpectrometer:
            def open_first(self) -> int:
                actions.append("open")
                return 101

            def set_integration_time(self, integration_time_us: int) -> None:
                actions.append(("integration", integration_time_us))

            def read_spectrum(self):
                return np.array([975.0, 976.0, 977.0]), np.array([1.0, 5.0, 2.0])

            def close(self) -> None:
                actions.append("close")

        reader = spectrometer_mvp.SpectrumReaderThread(10_000, 0)
        peaks: list[float] = []

        def on_spectrum(_wavelength, _intensity, stats) -> None:
            peaks.append(stats.peak_intensity)
            reader.stop()

        reader.spectrum.connect(on_spectrum)
        with patch.object(spectrometer_mvp, "OceanSpectrometer", FakeSpectrometer):
            reader.start()
            self.assertTrue(reader.wait(1_000))

        self.assertEqual(peaks, [5.0])
        self.assertEqual(actions, ["open", ("integration", 10_000), "close"])

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
        control.shutdown()
        control.shutdown()
        self.assertTrue(api.shutdown_called)

    def test_real_dll_controllers_scope_runtime_to_each_session(self) -> None:
        api = FakeOceanDirectApi()
        with tempfile.TemporaryDirectory() as temp_dir:
            dll_path = Path(temp_dir) / "OceanDirect.dll"
            dll_path.touch()
            with patch(
                "combined_test.ocean_direct_adapter.cdll.LoadLibrary",
                return_value=api,
            ):
                first = OceanDirectControl(dll_path=dll_path)
                first.shutdown()
                second = OceanDirectControl(dll_path=dll_path)
                second.shutdown()

        self.assertEqual(api.initialize_count, 2)
        self.assertEqual(api.shutdown_count, 2)

    def test_dual_spectrometers_share_session_and_operate_independently(self) -> None:
        api = FakeOceanDirectApi()
        first = OceanDirectControl(api=api)
        second = OceanDirectControl(api=api)

        # Single initialization for shared SDK session
        self.assertEqual(api.initialize_count, 1)

        first.open_device(101)
        second.open_device(202)
        self.assertEqual(api.opened_device_ids, [101, 202])

        first.set_integration_time(15000)
        second.set_integration_time(35000)
        self.assertEqual(first.get_integration_time(), 15000)
        self.assertEqual(second.get_integration_time(), 35000)

        # Reading from first does not interfere with second
        self.assertEqual(list(first.get_wavelength()), [975.0, 976.0, 977.0])
        self.assertEqual(list(second.get_intensity()), [1.0, 5.0, 2.0])

        # Closing first device does not shutdown session for second device
        first.shutdown()
        self.assertEqual(api.closed_device_ids, [101])
        self.assertFalse(api.shutdown_called)

        # Second device can still read
        self.assertEqual(second.get_integration_time(), 35000)

        # Closing second device triggers session shutdown
        second.shutdown()
        self.assertEqual(api.closed_device_ids, [101, 202])
        self.assertTrue(api.shutdown_called)
        self.assertEqual(api.shutdown_count, 1)

    def test_open_device_error_preserves_sdk_code_and_message(self) -> None:
        api = FakeOceanDirectApi()
        api.open_error = 7
        control = OceanDirectControl(api=api)

        with self.assertRaisesRegex(
            OceanDirectError,
            r"OceanDirect open_device\(202\) 失败，错误码 7：fake error",
        ):
            control.open_device(202)

    def test_integration_error_preserves_sdk_code_and_message(self) -> None:
        api = FakeOceanDirectApi()
        control = OceanDirectControl(api=api)
        control.open_device(202)
        api.integration_error = 9

        with self.assertRaisesRegex(
            OceanDirectError,
            r"OceanDirect set_integration_time\(3800 us\) 失败，错误码 9：fake error",
        ):
            control.set_integration_time(3800)


if __name__ == "__main__":
    unittest.main()
