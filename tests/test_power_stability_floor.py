"""Noise-floor acceptance must not conceal drift or change raw measurements."""

import math
import itertools
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from combined_test.core import PowerStabilityDetector
from combined_test.devices import PowerMeterReaderThread
from combined_test.models import PowerMeterSettings
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings


class PowerStabilityFloorTests(unittest.TestCase):
    def evaluate(self, values, *, floor=0.2, span=None, times=None):
        detector = PowerStabilityDetector(5.0, 0.2, span, absolute_floor_w=floor)
        times = times if times is not None else [i * 0.1 for i in range(len(values))]
        for timestamp, power in zip(times, values):
            result = detector.add_sample(timestamp, power)
        return detector, result

    def test_quantized_noise_passes_without_changing_raw_statistics(self):
        values = [32.448, 32.552] * 25 + [32.448]
        detector, result = self.evaluate(values)
        self.assertTrue(result.stable)
        self.assertGreater(result.stability_percent, 0.2)
        self.assertAlmostEqual(result.effective_limit_w, 0.2)
        self.assertEqual([power for _, power in detector._samples], values)
        self.assertEqual(result.median_w, 32.448)
        self.assertAlmostEqual(result.span_w, 0.104)

    def test_excessive_random_noise_stays_unstable(self):
        _, result = self.evaluate([32.2, 32.8] * 25 + [32.5])
        self.assertFalse(result.stable)

    def test_floor_never_widens_drift_limit_in_either_direction(self):
        for direction in (-1, 1):
            with self.subTest(direction=direction):
                _, result = self.evaluate([32.5 + direction * 0.003 * i for i in range(51)])
                self.assertFalse(result.stable)
                self.assertTrue(result.drift_exceeded)
                self.assertAlmostEqual(result.drift_w, 0.15)
                self.assertLess(result.drift_limit_w, 0.066)
                self.assertLess(result.stability_percent / 100 * 32.5, 0.2)
                self.assertLess(result.stable_duration_s, 5.0)

    def test_drift_uses_actual_timestamps_and_accepts_a_small_trend(self):
        times = [i / 10 for i in range(46)] + [4.7, 4.8, 4.9, 5.0]
        _, result = self.evaluate([32.5 + 0.01 * t for t in times], times=times)
        self.assertTrue(result.stable)
        self.assertAlmostEqual(result.drift_w, 0.05)

    def test_high_power_retains_relative_allowance(self):
        _, result = self.evaluate([300.0, 300.4] * 25 + [300.0])
        self.assertTrue(result.stable)
        self.assertGreater(result.effective_limit_w, 0.6)

    def test_zero_floor_restores_original_rule(self):
        _, result = self.evaluate([32.448, 32.552] * 25 + [32.448], floor=0)
        self.assertFalse(result.stable)
        _, result = self.evaluate([32.5 + 0.002 * i for i in range(51)], floor=0)
        self.assertTrue(result.stable)
        self.assertFalse(result.drift_exceeded)

    def test_explicit_span_rule_takes_precedence(self):
        _, result = self.evaluate([1.0, 1.2] * 25 + [1.0], span=0.2)
        self.assertTrue(result.stable)
        self.assertFalse(result.drift_exceeded)
        _, result = self.evaluate([1.0, 1.201] * 25 + [1.0], span=0.2)
        self.assertFalse(result.stable)

    def test_floor_cannot_accept_invalid_zero_or_incomplete_samples(self):
        for values in ([0.0] * 51, [math.nan] * 51, [32.5] * 40):
            with self.subTest(value=values[0], count=len(values)):
                _, result = self.evaluate(values)
                self.assertFalse(result.stable)
        _, result = self.evaluate([32.5] * 5, times=[0, 0.1, 0.2, 4.9, 5.0])
        self.assertFalse(result.stable)

    def test_floor_must_be_finite_and_nonnegative(self):
        for floor in (-0.1, math.nan, math.inf):
            with self.subTest(floor=floor), self.assertRaises(ValueError):
                PowerStabilityDetector(5, 0.2, absolute_floor_w=floor)

    def test_reader_updates_floor_and_resets_the_measurement_generation(self):
        reader = PowerMeterReaderThread(PowerMeterSettings("ASRL1::INSTR", 974, 1, 100, 5, 0.2, driver_kind="laserpoint", absolute_floor_w=0.2))
        generation = reader._stability_generation
        reader.update_stability_settings(5, 0.2, absolute_floor_w=0.3)
        self.assertEqual(reader._absolute_floor_w, 0.3)
        self.assertGreater(reader._stability_generation, generation)
        for value in (-1, math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                reader.update_stability_settings(5, 0.2, absolute_floor_w=value)

    def test_reader_preserves_raw_samples_and_floor_after_reconnecting(self):
        import tools.power_meter_mvp as drivers

        raw_values = [31.2, 31.3] * 25 + [31.2]
        meter = MagicMock()
        meter.test.return_value = "OK"
        before_disconnect = [31.2] * 3
        meter.read_power_w.side_effect = before_disconnect + [drivers.pyvisa.errors.VisaIOError(-1073807360)] + raw_values
        reader = PowerMeterReaderThread(PowerMeterSettings("ASRL1::INSTR", 974, 1.04, 100, 5, 0.2, driver_kind="laserpoint", absolute_floor_w=0.2))
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
        self.assertAlmostEqual(readings[-1].stability_limit_w, 0.2)
        self.assertFalse(readings[-1].stability_drift_exceeded)
        self.assertTrue(math.isfinite(readings[-1].stability_drift_w))
        self.assertEqual(meter.close.call_count, 2)


class PowerStabilityFloorRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = HeadlessBackend(JsonSettings(Path(self.temp.name) / "settings.json"))

    def tearDown(self):
        self.backend.export_worker.graceful_stop(timeout_s=5.0)
        self.temp.cleanup()

    def test_floor_is_persisted_validated_and_passed_to_laserpoint_reader(self):
        self.backend.dispatch("app.configure", {"powerStabilityAbsoluteFloorW": 0.25})
        station = self.backend.runtime
        self.assertEqual(station.configuration["powerStabilityAbsoluteFloorW"], 0.25)
        restored = HeadlessBackend(JsonSettings(Path(self.temp.name) / "settings.json"))
        try:
            self.assertEqual(restored.configuration["powerStabilityAbsoluteFloorW"], 0.25)
        finally:
            restored.export_worker.graceful_stop(timeout_s=5.0)
        for bad in (-1, math.nan, math.inf):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                station.configure({"powerStabilityAbsoluteFloorW": bad})
        station.configuration.update({"powerMeterDriverKind": "laserpoint", "powerMeterResource": "ASRL1::INSTR"})
        with patch.object(self.backend.power_meter_profiles, "require_ready"), patch("tauri_bridge.station_runtime.PowerMeterReaderThread") as reader:
            station.start_power_meter()
        self.assertEqual(reader.call_args.args[0].absolute_floor_w, 0.25)

    def test_snapshot_reports_actual_noise_floor_and_rejected_trend(self):
        from combined_test.automation import AutomaticTestState
        from combined_test.models import PowerMeterReading

        station = self.backend.runtime
        station.configuration["powerMeterDriverKind"] = "laserpoint"
        station.power_meter_reader = MagicMock(is_ready=True)
        station.automatic.state = AutomaticTestState.WAITING_STABLE
        station.automatic.currents = (3.0,)
        station.automatic.current_index = 0
        station.automatic_stability_generation = 0
        reading = PowerMeterReading(5, 32.65, False, 0.15, 5, 32.575, stability_percent=0.28, stability_limit_w=0.2, stability_drift_w=0.15, stability_drift_limit_w=0.06515, stability_drift_exceeded=True, stability_two_sigma_w=0.091)
        station._on_power_reading(reading)
        progress = self.backend.snapshot()["automaticTest"]["stability"]
        self.assertFalse(progress["powerStable"])
        self.assertTrue(progress["driftExceeded"])
        self.assertEqual(progress["absoluteFloorW"], 0.2)
        self.assertEqual(progress["effectiveLimitW"], 0.2)
        self.assertEqual(progress["twoSigmaW"], 0.091)
        self.assertEqual(progress["driftW"], 0.15)
        self.assertEqual(progress["holdElapsedS"], 0)

    def test_raw_realtime_values_and_median_liv_power_are_preserved(self):
        from combined_test.models import PowerMeterReading

        reading = PowerMeterReading(5.0, 32.552, True, 0.104, 5.0, 32.448)
        station = self.backend.runtime
        station._on_power_reading(reading)
        self.assertIs(station.latest_power, reading)
        self.assertEqual(station.power_points[-1]["powerW"], 32.552)
        self.assertEqual(station._stable_power_w(reading), 32.448)


if __name__ == "__main__":
    unittest.main()
