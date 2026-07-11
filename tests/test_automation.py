import math
import unittest

from combined_test.automation import (
    AutomaticTestSettings,
    build_ramp_down_currents,
    build_test_currents,
    validate_automatic_test_settings,
)


class AutomaticCurrentSequenceTests(unittest.TestCase):
    def test_exact_step_sequence_and_single_target_sequence_are_preserved(self) -> None:
        exact = AutomaticTestSettings(initial_current_a=1.0, target_current_a=3.0, current_step_a=1.0)
        single = AutomaticTestSettings(initial_current_a=20.0, target_current_a=20.0, current_step_a=1.0)

        self.assertEqual(build_test_currents(exact), (1.0, 2.0, 3.0))
        self.assertEqual(build_test_currents(single), (20.0,))

    def test_target_current_is_always_included_when_step_does_not_land_on_it(self) -> None:
        settings = AutomaticTestSettings(
            initial_current_a=2.0,
            target_current_a=10.0,
            current_step_a=3.0,
            point_timeout_s=120.0,
            ramp_down_step_a=5.0,
            ramp_down_interval_s=1.1,
        )

        self.assertEqual(build_test_currents(settings), (2.0, 5.0, 8.0, 10.0))

    def test_ramp_down_sequence_reaches_zero_without_repeating_start_current(self) -> None:
        self.assertEqual(build_ramp_down_currents(20.0, 5.0), (15.0, 10.0, 5.0, 0.0))
        self.assertEqual(build_ramp_down_currents(12.0, 5.0), (7.0, 2.0, 0.0))

    def test_ramp_down_interval_cannot_bypass_power_supply_command_guard(self) -> None:
        settings = AutomaticTestSettings(ramp_down_interval_s=1.0)

        with self.assertRaisesRegex(ValueError, "1.1"):
            validate_automatic_test_settings(settings, stable_window_s=3.0, post_stable_delay_s=5.0)

    def test_point_timeout_must_allow_stability_and_voltage_delay(self) -> None:
        settings = AutomaticTestSettings(point_timeout_s=7.9)

        with self.assertRaisesRegex(ValueError, "8.0"):
            validate_automatic_test_settings(settings, stable_window_s=3.0, post_stable_delay_s=5.0)

    def test_non_finite_timing_values_are_rejected(self) -> None:
        settings = AutomaticTestSettings(ramp_down_interval_s=math.nan)

        with self.assertRaises(ValueError):
            validate_automatic_test_settings(settings, stable_window_s=3.0, post_stable_delay_s=5.0)


if __name__ == "__main__":
    unittest.main()
