from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from combined_test.automation import AutomaticTestSettings, AutomaticTestState
from combined_test.devices import SpectrometerReaderThread
from combined_test.models import SpectrometerSettings
from combined_test.record_store import AttemptValidity
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
from tauri_bridge.station_support import AutomaticPointQualityError


class SpectrumBackendRegressions(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.backend = HeadlessBackend(JsonSettings(Path(self.directory.name) / "settings.json"))
        self.station = self.backend.stations["1"]

    def tearDown(self) -> None:
        self.backend.export_worker.graceful_stop()
        self.directory.cleanup()

    def test_visible_spectrum_reader_publishes_peak_markers_in_snapshot(self) -> None:
        reader = SpectrometerReaderThread(SpectrometerSettings(10_000, 100, average_frames=1))
        self.station.spectrometer_reader = reader
        reader.reading.connect(self.station._on_spectrometer_reading)
        wavelength = [420.0 + index * 0.1 for index in range(501)]
        intensity = [float((index % 5) - 2) for index in range(501)]
        intensity[299:302] = [360.0, 600.0, 360.0]
        reader._publish_averaged_spectrum(wavelength, intensity)

        measurements = self.backend.snapshot({"stationId": "1", "view": "manual"})["measurements"]
        self.assertEqual(measurements["spectrum"]["wavelengthNm"], wavelength)
        self.assertEqual(measurements["spectrum"]["intensity"], intensity)
        self.assertEqual(measurements["spectrumPeaks"], [
            {"label": "P1", "peakWavelengthNm": 450.0, "peakIntensity": 600.0},
        ])
        # Restoring the display annotation does not alter fixed-band SMSR.
        self.assertTrue(math.isnan(self.station.latest_spectrum.smsr.smsr_db))

    def test_clipped_raw_frame_pauses_automatic_test_even_when_average_is_below_full_scale(self) -> None:
        station = self.station
        reader = SpectrometerReaderThread(SpectrometerSettings(10_000, 50, average_frames=3))
        reader.full_scale_counts = 1_000.0
        station.spectrometer_reader = reader
        station.automatic.start(
            AutomaticTestSettings(use_spectrometer=True),
            power_meter_ready=True,
            spectrum_meter_ready=True,
        )
        station.automatic.set_state(AutomaticTestState.WAITING_STABLE)
        reader.reading.connect(station._on_spectrometer_reading)
        wavelength = [974.0, 975.0, 976.0, 977.0, 978.0]
        with patch.object(station, "_request_automatic_point_retry") as retry:
            for intensity in (
                [0.0, 200.0, 300.0, 200.0, 0.0],
                [0.0, 1_000.0, 1_000.0, 1_000.0, 0.0],
                [0.0, 200.0, 300.0, 200.0, 0.0],
            ):
                reader._publish_averaged_spectrum(wavelength, intensity)

        retry.assert_called_once()
        self.assertEqual(retry.call_args.args[0], AttemptValidity.SATURATED)
        self.assertTrue(retry.call_args.kwargs["hold_current"])
        self.assertEqual(station.spectrum_revision, 1)
        self.assertLess(station.latest_spectrum.peak_intensity, 995.0)
        self.assertTrue(station.latest_spectrum.saturation.saturated)
        self.assertTrue(math.isnan(station.latest_spectrum.pib))
        with self.assertRaises(AutomaticPointQualityError) as error:
            station._validate_automatic_spectrum(station.latest_spectrum)
        self.assertEqual(error.exception.validity, AttemptValidity.SATURATED)

    def test_auto_integration_limit_and_failure_keep_live_updates_and_fixed_time_recovery(self) -> None:
        station = self.station
        station.configuration.update({"spectrometerDeviceId": 7, "autoIntegration": True})
        # Wire the production callbacks without opening hardware or spawning a thread.
        with patch.object(SpectrometerReaderThread, "start"):
            station.start_spectrometer()
        reader = station.spectrometer_reader
        failures = []
        live_frames = []
        integration_times = []
        read_count = 0
        frames_after_failure = 0

        class FakeSpectrometer:
            def __init__(self):
                self.control = SimpleNamespace(
                    find_usb_devices=lambda: None,
                    get_device_ids=lambda: [7],
                    open_device=lambda _device_id: None,
                )

            def get_minimum_integration_time(self):
                return 3_800

            def get_maximum_integration_time(self):
                return 300_000

            def get_maximum_intensity(self):
                return 100.0

            def set_integration_time(self, value):
                integration_times.append(value)

            def read_spectrum(self):
                nonlocal read_count, frames_after_failure
                read_count += 1
                if read_count > 25:
                    reader.stop()
                if failures:
                    frames_after_failure += 1
                peak = 100.0 if not failures else 60.0 + frames_after_failure
                return [975.0, 976.0, 977.0], [peak, peak, peak]

            def close(self):
                return

        def after_reading(_reading):
            live_frames.append((station.spectrum_revision, station.latest_spectrum.peak_intensity))
            if frames_after_failure == 2:
                reader.use_fixed_integration_time(25_000)
            elif frames_after_failure == 3:
                reader.stop()

        reader.auto_integration_failed.connect(failures.append)
        reader.reading.connect(after_reading)
        reader_errors = []
        reader.failed.connect(reader_errors.append)
        with (
            patch(
                "combined_test.devices.load_spectrometer_components",
                return_value=(FakeSpectrometer, lambda _x, _y: SimpleNamespace(
                    peak_wavelength_nm=976.0, centroid_nm=976.0, fwhm_nm=0.2,
                )),
            ),
            patch.object(reader._stop_requested, "wait", return_value=False),
        ):
            reader.run()

        self.assertEqual(reader_errors, [])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["adjustment_count"], 10)
        self.assertEqual(frames_after_failure, 3)
        self.assertEqual(len(live_frames), read_count)
        self.assertEqual([revision for revision, _ in live_frames], list(range(1, read_count + 1)))
        self.assertEqual([peak for _, peak in live_frames[-3:]], [61.0, 62.0, 63.0])
        self.assertEqual(integration_times, [3_800, 25_000])
        self.assertEqual(station.configuration["integrationTimeUs"], 25_000)
        self.assertIsNotNone(station.latest_spectrum_sampled_at_utc)
        self.assertFalse(reader._auto_integration_enabled.is_set())
        notices = [notice for notice in self.backend.notices if notice.get("code") == "spectrum_auto_integration_failed"]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["level"], "warning")
        self.assertIn("仍持续采集", notices[0]["message"])
        self.assertIn("重新启动采集", notices[0]["message"])


if __name__ == "__main__":
    unittest.main()
