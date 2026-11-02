"""All meters/currents share relative 2-sigma and fitted drift limits."""
import itertools
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from combined_test.core import PowerStabilityDetector
from combined_test.devices import PowerMeterReaderThread
from combined_test.models import PowerMeterSettings, PowerMeterReading
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings

DRIVERS = ("caihuang", "coherent", "laserpoint", "ophir")

class UnifiedPowerStabilityTests(unittest.TestCase):
    def evaluate(self, values, *, times=None, jump=0, legacy_floor=0, legacy_span=None):
        detector = PowerStabilityDetector(5, 0.2, legacy_span, absolute_floor_w=legacy_floor, noise_jump_w=jump)
        for t, p in zip(times or [i / 10 for i in range(len(values))], values):
            result = detector.add_sample(t, p)
        return detector, result

    def test_standard_deviation_is_sample_sigma_and_raw_values_are_not_changed(self):
        values = [32.5 + (i % 2) * 0.01 for i in range(51)]
        detector, result = self.evaluate(values)
        mean = math.fsum(values) / len(values)
        sigma = math.sqrt(math.fsum((p - mean) ** 2 for p in values) / (len(values) - 1))
        self.assertTrue(result.stable)
        self.assertEqual([p for _, p in detector._samples], values)
        self.assertEqual(result.median_w, 32.5)
        self.assertAlmostEqual(result.two_sigma_w, 2 * sigma)
        self.assertAlmostEqual(result.effective_limit_w, mean * 0.002)
        self.assertAlmostEqual(result.drift_limit_w, mean * 0.002)

    def test_positive_and_negative_drift_fail_even_while_sigma_passes(self):
        for direction in (-1, 1):
            with self.subTest(direction=direction):
                _, result = self.evaluate([32.5 + direction * 0.002 * i for i in range(51)])
                self.assertLess(result.two_sigma_w, result.effective_limit_w)
                self.assertAlmostEqual(result.drift_w, 0.1)
                self.assertTrue(result.drift_exceeded)
                self.assertFalse(result.stable)

    def test_actual_time_and_inclusive_drift_boundary(self):
        times = [i / 10 for i in range(46)] + [4.7, 4.8, 4.9, 5]
        _, result = self.evaluate([32.5 + 0.01 * t for t in times], times=times)
        self.assertTrue(result.stable)
        self.assertAlmostEqual(result.drift_w, 0.05)
        _, boundary = self.evaluate([32.5 + 0.013 * (i / 10 - 2.5) for i in range(51)])
        self.assertTrue(boundary.stable)
        _, outside = self.evaluate([32.5 + 0.0131 * (i / 10 - 2.5) for i in range(51)])
        self.assertFalse(outside.stable)
        self.assertTrue(outside.drift_exceeded)

    def test_historical_floor_and_span_cannot_widen_acceptance(self):
        values = [32.448, 32.552] * 25 + [32.448]
        for floor, span in ((0, None), (0.2, None), (100, 1.0)):
            with self.subTest(floor=floor, span=span):
                _, result = self.evaluate(values, legacy_floor=floor, legacy_span=span)
                self.assertFalse(result.stable)
                self.assertLess(result.effective_limit_w, 0.066)
        detector, noise = self.evaluate(values, jump=0.104)
        self.assertTrue(noise.stable)
        self.assertEqual([p for _, p in detector._samples], values)

    def test_zero_invalid_incomplete_and_gapped_windows_never_qualify(self):
        for values in ([0.] * 51, [math.nan] * 51, [32.5] * 40):
            self.assertFalse(self.evaluate(values)[1].stable)
        self.assertFalse(self.evaluate([32.5] * 5, times=[0, 0.1, 0.2, 4.9, 5])[1].stable)

    def test_all_driver_readers_ignore_legacy_limits(self):
        for driver in DRIVERS:
            with self.subTest(driver=driver):
                reader = PowerMeterReaderThread(PowerMeterSettings("TEST", 976, 1, 100, 5, 0.2, driver_kind=driver, absolute_floor_w=100))
                reader.update_stability_settings(5, 0.2, 1.0, absolute_floor_w=100)
                self.assertIsNone(reader._absolute_span_limit_w)
                self.assertEqual(reader._absolute_floor_w, 0)
                self.assertEqual(reader._stable_window_s, 5)

    def test_reader_preserves_raw_samples_and_sn_noise_after_reconnecting(self):
        import tools.power_meter_mvp as drivers

        raw_values = [31.2, 31.3] * 25 + [31.2]
        meter = MagicMock()
        meter.test.return_value = "OK"
        before_disconnect = [31.2] * 3
        meter.read_power_w.side_effect = before_disconnect + [drivers.pyvisa.errors.VisaIOError(-1073807360)] + raw_values
        reader = PowerMeterReaderThread(PowerMeterSettings("ASRL1::INSTR", 974, 1.04, 100, 5, 0.2, driver_kind="laserpoint", absolute_floor_w=0.2, noise_jump_w=0.104))
        readings, failures = [], []

        def receive(reading):
            readings.append(reading)
            if len(readings) == len(before_disconnect) + len(raw_values):
                reader.stop()

        reader.reading.connect(receive)
        reader.failed.connect(failures.append)
        ticks = itertools.count(0, 0.05)
        with patch.object(drivers, "LaserPointPowerMeter", return_value=meter), patch("combined_test.devices.time", SimpleNamespace(monotonic=lambda: next(ticks))), patch.object(reader._stop_requested, "wait", return_value=False):
            reader.run()
        self.assertEqual(failures, [])
        self.assertEqual([reading.power_w for reading in readings], [value * 1.04 for value in before_disconnect + raw_values])
        self.assertFalse(readings[len(before_disconnect)].stable)
        self.assertEqual(readings[len(before_disconnect)].stable_window_s, 0)
        self.assertTrue(readings[-1].stable)
        self.assertGreater(readings[-1].stability_percent, 0.2)
        self.assertAlmostEqual(readings[-1].stability_limit_w, readings[-1].stability_drift_limit_w)
        self.assertFalse(readings[-1].stability_drift_exceeded)
        self.assertTrue(math.isfinite(readings[-1].stability_drift_w))
        self.assertEqual(meter.close.call_count, 2)

class UnifiedPowerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.backend = HeadlessBackend(JsonSettings(Path(self.directory.name) / "settings.json"))
        self.station = self.backend.runtime

    def tearDown(self):
        self.backend.export_worker.graceful_stop(timeout_s=2)
        self.directory.cleanup()

    def test_all_brands_start_with_the_same_parameters_and_no_legacy_floor(self):
        for driver in DRIVERS:
            with self.subTest(driver=driver):
                self.station.power_meter_reader = None
                self.station.configuration.update(powerMeterDriverKind=driver, powerMeterResource="TEST")
                with patch.object(self.backend.power_meter_profiles, "require_ready"), patch("tauri_bridge.station_runtime.PowerMeterReaderThread") as factory:
                    self.station.start_power_meter()
                settings = factory.call_args.args[0]
                self.assertEqual(settings.absolute_floor_w, 0)
                self.assertEqual(settings.stability_limit_percent, 0.2)
        self.station.power_meter_reader = None
        self.backend.dispatch("app.configure", {"powerStabilityAbsoluteFloorW": 100})
        self.assertNotIn("powerStabilityAbsoluteFloorW", self.station.configuration)

    def test_same_trace_produces_identical_results_at_every_brand_and_current(self):
        from tauri_bridge.station_automatic import automatic_power_stability_window_s, automatic_power_stability_span_limit_w
        traces = (
            [32.5 + (i % 2) * 0.01 for i in range(51)],
            [32.5 + i * 0.002 for i in range(51)],
            [32.5 - i * 0.002 for i in range(51)],
        )
        baseline = None
        for driver in DRIVERS:
            for current in (0.1, 0.6, 2, 2.01, 20):
                with self.subTest(driver=driver, current=current):
                    self.assertIsNone(automatic_power_stability_span_limit_w(current, driver))
                    window = automatic_power_stability_window_s("standard", 3, current)
                    self.assertEqual(window, 5)
                    results = []
                    for trace in traces:
                        detector = PowerStabilityDetector(window, 0.2)
                        for i, value in enumerate(trace):
                            result = detector.add_sample(i / 10, value)
                        results.append(result)
                    if baseline is None:
                        baseline = results
                    else:
                        self.assertEqual(results, baseline)

    def test_snapshot_uses_the_same_criterion_and_raw_liv_median(self):
        station = self.station
        reading = PowerMeterReading(5, 32.552, True, 0.104, 5, 32.448)
        station._on_power_reading(reading)
        self.assertIs(station.latest_power, reading)
        self.assertEqual(station.power_points[-1]["powerW"], 32.552)
        self.assertEqual(station._stable_power_w(reading), 32.448)
        for driver in DRIVERS:
            station.configuration["powerMeterDriverKind"] = driver
            snapshot = self.backend.snapshot()["automaticTest"]["stability"]
            self.assertIsNone(snapshot["spanLimitW"])
            self.assertEqual(snapshot["absoluteFloorW"], 0)
