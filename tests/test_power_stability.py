"""20-second repeated-jump learning; no device access."""
import json
import math
import unittest
from pathlib import Path
from combined_test.power_stability import BackgroundModel, NoiseLearningSettings, learn_background
from combined_test.core import PowerStabilityDetector


class PowerMeterNoiseTests(unittest.TestCase):
    def test_learns_the_measured_jump_from_only_20_seconds(self):
        samples = json.loads((Path(__file__).parent / "fixtures/laserpoint-background-180s.json").read_text(encoding="utf-8"))
        end = next(i for i, row in enumerate(samples) if row[0] - samples[0][0] >= 20)
        model = learn_background(samples[:end + 1], identity="PROBE-A", model_id="measured", created_at="now", policy=NoiseLearningSettings(0))
        self.assertLess(model.duration_s, 20.2)
        self.assertAlmostEqual(model.recurrent_jump_w, 0.104)
        self.assertEqual(BackgroundModel.from_dict(__import__("dataclasses").asdict(model)), model)

    def evaluate(self, values, step=0.104, absolute=None, times=None):
        detector = PowerStabilityDetector(2, 0.01, absolute, noise_jump_w=step)
        for t, p in zip(times or [i / 10 for i in range(len(values))], values):
            result = detector.add_sample(t, p)
        return detector, result

    def test_same_repeated_jump_is_ignored_and_raw_data_stays_exact(self):
        values = [32.5 + (i % 2) * 0.104 for i in range(21)]
        detector, result = self.evaluate(values)
        self.assertTrue(result.stable)
        self.assertFalse(self.evaluate(values, step=0)[1].stable)
        self.assertEqual([p for _, p in detector._samples], values)
        self.assertAlmostEqual(result.span_w, 0.104)
        self.assertEqual(result.median_w, 32.5)

    def test_window_cutting_a_jump_pair_does_not_flicker_between_stable_states(self):
        for count in (21, 22, 51, 52):
            with self.subTest(count=count):
                values = [32.5 + (i % 2) * 0.104 for i in range(count)]
                self.assertTrue(self.evaluate(values)[1].stable)

    def test_one_way_drift_other_jumps_and_mixed_drift_still_use_original_rules(self):
        for values in ([32.5 + i * 0.104 for i in range(21)],
                       [32.5 + (i % 2) * 0.208 for i in range(21)],
                       [32.5 + i * 0.002 + (i % 2) * 0.104 for i in range(21)]):
            with self.subTest(values=values):
                self.assertFalse(self.evaluate(values)[1].stable)

    def test_noise_does_not_bypass_coverage_gaps_or_invalid_values(self):
        self.assertFalse(self.evaluate([32.5, 32.604, 32.5])[1].stable)
        self.assertFalse(self.evaluate([32.5, 32.604, 32.5, 32.604, 32.5], times=[0, 0.1, 1.8, 1.9, 2])[1].stable)
        self.assertFalse(self.evaluate([32.5, math.nan])[1].stable)

    def test_noise_exception_is_shared_with_absolute_span_mode(self):
        values = [32.5 + (i % 2) * 0.104 for i in range(21)]
        self.assertTrue(self.evaluate(values, absolute=0.01)[1].stable)
        self.assertFalse(self.evaluate(values, step=0, absolute=0.01)[1].stable)

    def test_learning_rejects_short_gapped_one_way_or_nonrecurrent_data(self):
        for samples in ([(i / 10, (i % 2) * 0.104) for i in range(200)],
                        [(0, 0), (20, 0.104)],
                        [(i / 10, i * 0.104) for i in range(201)],
                        [(i / 10, 0) for i in range(201)]):
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                learn_background(samples, identity="PROBE-A", model_id="test", created_at="now", policy=NoiseLearningSettings(0))
