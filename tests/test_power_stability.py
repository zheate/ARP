"""Offline acceptance for the background/dual-window engineering criterion."""

import math
import random
import unittest
from dataclasses import asdict, replace

from combined_test.power_stability import (
    BackgroundModel, PowerWindow, StabilityPolicy, evaluate_stability,
    learn_background, verify_background,
)


def quantized_trace(function, duration=180, interval=0.1, seed=17):
    rng = random.Random(seed)
    return [(index * interval, round((function(index * interval) + rng.uniform(-0.09, 0.09)) / 0.15) * 0.15)
            for index in range(round(duration / interval) + 1)]


def metrics_for(samples):
    window = PowerWindow(1.0)
    for timestamp, power in samples:
        result = window.add(timestamp, power)
    return result


class BackgroundDualWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = StabilityPolicy(0.15, absolute_drift_w=0.35, reference_s=20,
                                     response_s=2, parameters_confirmed=True)
        cls.samples = quantized_trace(lambda t: 0.0)
        cls.model = learn_background(cls.samples, identity="probe-A", model_id="test-model",
                                    created_at="2026-10-22", policy=cls.policy)

    def evaluate(self, function, **kwargs):
        metrics = metrics_for(quantized_trace(function, duration=20, **kwargs))
        return evaluate_stability(metrics, self.model, self.policy, identity="probe-A")

    def test_quantization_before_measurement_allows_constant_power_without_offset_subtraction(self):
        result = self.evaluate(lambda t: 32.5)
        self.assertTrue(result.stable, result)
        self.assertGreaterEqual(result.metrics.median_w, 32.4)
        self.assertGreater(result.metrics.span_w, 0)
        self.assertLessEqual(result.metrics.residual_sigma_w, result.noise_limit_w)

    def test_positive_and_negative_drift_fail_even_with_small_short_window_noise(self):
        for direction in (-1, 1):
            with self.subTest(direction=direction):
                result = self.evaluate(lambda t: 32.5 + direction * 0.04 * t)
                self.assertFalse(result.stable)
                self.assertGreater(max(result.guarded_shift_w, result.guarded_drift_w), result.tolerance_w)

    def test_symmetric_bump_and_oscillation_do_not_hide_behind_zero_net_slope(self):
        for function in (lambda t: 32.5 + 0.8 * math.sin(math.pi * t / 20),
                         lambda t: 32.5 + 0.8 * math.cos(2 * math.pi * t / 20),
                         lambda t: 32.5 + 0.8 * math.sin(2 * math.pi * t)):
            with self.subTest(function=function):
                self.assertFalse(self.evaluate(function).stable)

    def test_unresolved_tolerance_is_explicit_measurement_capability_failure(self):
        metrics = metrics_for(quantized_trace(lambda t: 32.5, duration=20))
        result = evaluate_stability(metrics, self.model, replace(self.policy, absolute_drift_w=0.1), identity="probe-A")
        self.assertFalse(result.stable)
        self.assertIn("测量能力不足", result.reason)

    def test_no_confirmation_no_model_or_wrong_probe_never_passes(self):
        metrics = metrics_for(quantized_trace(lambda t: 32.5, duration=20))
        for model, policy, identity in (
            (None, self.policy, "probe-A"), (self.model, self.policy, "probe-B"),
            (self.model, replace(self.policy, parameters_confirmed=False), "probe-A"),
        ):
            self.assertFalse(evaluate_stability(metrics, model, policy, identity=identity).stable)

    def test_drifting_background_cannot_train_itself_into_loose_thresholds(self):
        for function in (lambda t: 0.005 * t, lambda t: -0.005 * t,
                         lambda t: 0 if t < 60 else 0.5):
            with self.subTest(function=function), self.assertRaisesRegex(ValueError, "本底尚未稳定"):
                learn_background(quantized_trace(function), identity="a", model_id="a", created_at="now", policy=self.policy)

    def test_short_plateau_and_thermal_tail_need_the_full_window(self):
        samples = quantized_trace(lambda t: 32.5 + 3 * math.exp(-t / 50), duration=100)
        window = PowerWindow(1)
        for timestamp, power in samples:
            metrics = window.add(timestamp, power)
            decision = evaluate_stability(metrics, self.model, self.policy, identity="probe-A")
            if timestamp < 20:
                self.assertFalse(decision.stable)
            if 20 <= timestamp <= 40:
                self.assertFalse(decision.stable)

    def test_gaps_invalid_values_and_clock_restarts_reset_coverage(self):
        for timestamp, power in ((23, 32.5), (20.1, math.nan), (-1, 32.5)):
            window = PowerWindow(1)
            for t, p in quantized_trace(lambda t: 32.5, duration=20):
                result = window.add(t, p)
            self.assertIsNotNone(result)
            self.assertIsNone(window.add(timestamp, power))

    def test_same_value_is_valid_but_same_transaction_does_not_add_coverage(self):
        window = PowerWindow(1)
        for index in range(201):
            result = window.add(index / 10, 32.5)
        self.assertIsNotNone(result)
        count = len(window.samples)
        self.assertIsNone(window.add(20, 32.5))
        self.assertEqual(len(window.samples), count)

    def test_recheck_rejects_center_shift_or_drift(self):
        self.assertTrue(verify_background(metrics_for(self.samples[-201:]), self.model))
        self.assertFalse(verify_background(metrics_for(quantized_trace(lambda t: 0.5, duration=20)), self.model))

    def test_model_roundtrip_and_guards_never_shrink_with_sample_count(self):
        self.assertEqual(BackgroundModel.from_dict(asdict(self.model)), self.model)
        self.assertGreaterEqual(self.model.shift_guard_w, 0.15)
        self.assertGreaterEqual(self.model.slope_guard_w_s, 0.15 / 20)
        damaged = asdict(self.model)
        damaged["shift_guard_w"] = 0.001
        with self.assertRaises(ValueError):
            BackgroundModel.from_dict(damaged)

    def test_background_rejects_missing_data_and_invalid_policy(self):
        for samples in (self.samples[:100], self.samples[:50] + self.samples[80:], [(0, math.nan)] * 1801):
            with self.assertRaises(ValueError):
                learn_background(samples, identity="a", model_id="a", created_at="now", policy=self.policy)
        for kwargs in ({"quantization_w": math.nan}, {"reference_s": 0}, {"response_s": -1}):
            with self.assertRaises(ValueError):
                StabilityPolicy(**{"quantization_w": 0.15, **kwargs})

    def test_auto_step_uses_observed_units_once_and_preserves_manual_models(self):
        for gain in (1, 1.04):
            samples = [(i * 0.5, (-0.3 + (i % 3) * 0.15) * gain) for i in range(361)]
            model = learn_background(samples, identity="a", model_id="auto", created_at="now",
                                     policy=replace(self.policy, quantization_w=0))
            self.assertAlmostEqual(model.quantization_w, 0.15 * gain)
            self.assertEqual(model.quantization_source, "observed-grid-v1")
            self.assertEqual(BackgroundModel.from_dict(asdict(model)), model)
        legacy = asdict(self.model)
        del legacy["quantization_source"]
        self.assertEqual(BackgroundModel.from_dict(legacy).quantization_source, "manual")

    def test_auto_step_refuses_flat_continuous_nonuniform_or_one_way_data(self):
        rng = random.Random(42)
        functions = (lambda i: 0, lambda i: rng.random() * 0.15,
                     lambda i: (0, 0.15, 0.37)[i % 3], lambda i: i // 120 * 0.15,
                     lambda i: 0.15 if i == 180 else 0)
        for function in functions:
            with self.subTest(function=function), self.assertRaisesRegex(ValueError, "无法可靠估计"):
                learn_background([(i * 0.5, function(i)) for i in range(361)],
                                 identity="a", model_id="auto", created_at="now",
                                 policy=replace(self.policy, quantization_w=0))

    def test_auto_step_still_rejects_drifting_background(self):
        samples = [(i * 0.5, (i // 120) * 0.15 + (i % 2) * 0.15) for i in range(361)]
        with self.assertRaisesRegex(ValueError, "本底尚未稳定"):
            learn_background(samples, identity="a", model_id="auto", created_at="now",
                             policy=replace(self.policy, quantization_w=0))


if __name__ == "__main__":
    unittest.main()
