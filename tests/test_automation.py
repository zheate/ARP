import math
import unittest

from combined_test.automation import (
    AutomaticCurrentPoint,
    AutomaticTestOrchestrator,
    AutomaticTestSettings,
    AutomaticTestState,
    PowerDropProtectionDetector,
    StableHoldTimer,
    automatic_post_stable_delay_s,
    build_custom_hold_ramp_currents,
    build_ramp_down_currents,
    build_test_currents,
    build_test_dwells,
    validate_automatic_test_settings,
)


class PowerDropProtectionDetectorTests(unittest.TestCase):
    def test_protection_waits_until_five_second_window_is_covered(self) -> None:
        detector = PowerDropProtectionDetector()

        self.assertFalse(detector.observe(100.0, 0.0).triggered)
        self.assertFalse(detector.observe(60.0, 4.99).triggered)
        self.assertIsNone(detector.reference_power_w)

    def test_drop_must_exceed_thirty_percent_for_three_consecutive_samples(self) -> None:
        detector = PowerDropProtectionDetector()
        detector.observe(100.0, 0.0)

        self.assertFalse(detector.observe(70.0, 5.0).triggered)
        self.assertFalse(detector.observe(69.9, 5.1).triggered)
        self.assertFalse(detector.observe(69.8, 5.2).triggered)
        result = detector.observe(69.7, 5.3)

        self.assertTrue(result.triggered)
        self.assertEqual(result.reference_power_w, 100.0)
        self.assertAlmostEqual(result.drop_w, 30.3)
        self.assertAlmostEqual(result.threshold_w, 30.0)
        self.assertEqual(result.confirmation_count, 3)
        self.assertEqual(result.confirmation_samples_w, (69.9, 69.8, 69.7))
        self.assertFalse(detector.observe(60.0, 5.4).triggered)

    def test_single_low_sample_does_not_trip_and_normal_sample_resets_confirmation(self) -> None:
        detector = PowerDropProtectionDetector()
        detector.observe(100.0, 0.0)

        first_drop = detector.observe(60.0, 5.1)
        recovered = detector.observe(95.0, 5.2)
        next_drop = detector.observe(60.0, 5.3)

        self.assertFalse(first_drop.triggered)
        self.assertEqual(first_drop.confirmation_count, 1)
        self.assertFalse(recovered.triggered)
        self.assertEqual(recovered.confirmation_count, 0)
        self.assertFalse(next_drop.triggered)
        self.assertEqual(next_drop.confirmation_count, 1)

    def test_brief_high_sample_is_not_used_as_the_reference(self) -> None:
        detector = PowerDropProtectionDetector()
        detector.observe(100.0, 0.0)
        detector.observe(200.0, 2.0)

        result = detector.observe(90.0, 5.0)

        self.assertFalse(result.triggered)
        self.assertEqual(result.reference_power_w, 100.0)
        self.assertAlmostEqual(result.drop_w, 10.0)

    def test_rolling_window_advances_and_reset_clears_history(self) -> None:
        detector = PowerDropProtectionDetector()
        detector.observe(100.0, 0.0)
        detector.observe(110.0, 5.0)
        detector.observe(200.0, 6.0)

        result = detector.observe(150.0, 11.0)
        self.assertFalse(result.triggered)
        self.assertEqual(result.reference_power_w, 200.0)
        detector.reset()

        self.assertIsNone(detector.reference_power_w)
        self.assertFalse(detector.observe(0.0, 12.0).triggered)

    def test_drop_to_zero_is_protected_at_very_low_power(self) -> None:
        detector = PowerDropProtectionDetector()
        detector.observe(0.05, 0.0)

        self.assertFalse(detector.observe(0.0, 5.0).triggered)
        self.assertFalse(detector.observe(0.0, 5.1).triggered)
        result = detector.observe(0.0, 5.2)

        self.assertTrue(result.triggered)
        self.assertAlmostEqual(result.threshold_w, 0.015)


class StableHoldTimerTests(unittest.TestCase):
    def test_counts_only_fresh_stable_samples_after_acceptance(self) -> None:
        timer = StableHoldTimer(5.0, 1.0)
        for tick in range(11):
            elapsed = tick / 2
            self.assertEqual(timer.observe(elapsed, elapsed, elapsed, stable=False), 0.0)
        for tick in range(11):
            elapsed = 5.0 + tick / 2
            self.assertEqual(timer.observe(elapsed, elapsed, elapsed, stable=True), tick / 2)
            self.assertEqual(timer.observe(elapsed, elapsed, elapsed + 0.1, stable=True), tick / 2)

    def test_resets_for_instability_gaps_reversed_time_and_invalid_samples(self) -> None:
        for sample, received, now, stable in [(1.5, 1.5, 1.5, False), (4.0, 4.0, 4.0, True), (1.0, 1.0, 3.0, True), (0.0, 1.5, 1.5, True), (math.nan, 1.5, 1.5, True)]:
            timer = StableHoldTimer(5.0, 1.0)
            for elapsed in (0.0, 0.5, 1.0):
                timer.observe(elapsed, elapsed, elapsed, stable=True)
            self.assertEqual(timer.observe(sample, received, now, stable=stable), 0.0)

    def test_sample_clock_cannot_fast_forward_actual_hold(self) -> None:
        timer = StableHoldTimer(5.0, 1.0)
        for tick in range(11):
            self.assertEqual(timer.observe(tick / 2, 0.0, 0.0, stable=True), 0.0)


class AutomaticCurrentSequenceTests(unittest.TestCase):
    def test_final_point_requires_ten_seconds_and_other_points_require_five(self) -> None:
        for index in range(2):
            self.assertEqual(automatic_post_stable_delay_s(index, 3), 5.0)
        self.assertEqual(automatic_post_stable_delay_s(2, 3), 10.0)
        self.assertEqual(automatic_post_stable_delay_s(0, 1), 10.0)

    def test_custom_points_preserve_currents_and_individual_dwell_times(self) -> None:
        settings = AutomaticTestSettings(
            initial_current_a=1.0,
            target_current_a=5.0,
            current_step_a=3.0,
            current_plan_mode="custom",
            custom_current_points=(
                AutomaticCurrentPoint(3.0, 300.0),
                AutomaticCurrentPoint(5.0, 120.0),
            ),
            custom_hold_ramp_enabled=True,
        )

        self.assertEqual(build_test_currents(settings), (3.0, 5.0))
        self.assertEqual(build_test_dwells(settings), (300.0, 120.0))
        orchestrator = AutomaticTestOrchestrator()
        orchestrator.start(settings, power_meter_ready=True, spectrum_meter_ready=True)
        self.assertEqual(orchestrator.current_dwell_s, 300.0)
        self.assertTrue(orchestrator.advance())
        self.assertEqual(orchestrator.current_dwell_s, 120.0)

    def test_ch341_custom_mode_keeps_the_original_measurement_sequence(self) -> None:
        settings = AutomaticTestSettings(
            initial_current_a=1.0,
            target_current_a=5.0,
            current_step_a=3.0,
            current_plan_mode="custom",
            custom_current_points=(
                AutomaticCurrentPoint(3.0, 300.0),
                AutomaticCurrentPoint(5.0, 120.0),
            ),
        )

        self.assertEqual(build_test_currents(settings), (1.0, 3.0, 4.0, 5.0))
        self.assertEqual(build_test_dwells(settings), (0.0, 300.0, 0.0, 120.0))

    def test_custom_hold_ramp_uses_half_amp_steps_and_includes_target(self) -> None:
        self.assertEqual(
            build_custom_hold_ramp_currents(0.0, 3.0),
            (0.5, 1.0, 1.5, 2.0, 2.5, 3.0),
        )
        self.assertEqual(build_custom_hold_ramp_currents(3.0, 4.2), (3.5, 4.0, 4.2))
        self.assertEqual(build_custom_hold_ramp_currents(5.0, 5.0), ())

    def test_custom_points_must_be_strictly_increasing_with_positive_dwell(self) -> None:
        duplicate = AutomaticTestSettings(
            current_plan_mode="custom",
            custom_current_points=(
                AutomaticCurrentPoint(3.0, 300.0),
                AutomaticCurrentPoint(3.0, 120.0),
            ),
        )
        invalid_dwell = AutomaticTestSettings(
            current_plan_mode="custom",
            custom_current_points=(AutomaticCurrentPoint(3.0, 0.0),),
        )

        with self.assertRaisesRegex(ValueError, "严格递增"):
            build_test_currents(duplicate)
        with self.assertRaisesRegex(ValueError, "停留时间必须大于 0"):
            build_test_currents(invalid_dwell)

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

    def test_tdk_sequences_have_no_software_current_ceiling(self) -> None:
        settings = AutomaticTestSettings(
            initial_current_a=25.0,
            target_current_a=30.0,
            current_step_a=2.0,
            ramp_down_step_a=7.0,
            maximum_current_a=None,
        )

        self.assertEqual(build_test_currents(settings), (25.0, 27.0, 29.0, 30.0))
        self.assertEqual(
            build_ramp_down_currents(30.0, 7.0, maximum_current_a=None),
            (23.0, 16.0, 9.0, 2.0, 0.0),
        )
        self.assertIs(
            validate_automatic_test_settings(
                settings,
                stable_window_s=3.0,
                post_stable_delay_s=5.0,
            ),
            settings,
        )

    def test_legacy_sequences_still_reject_current_above_twenty_amps(self) -> None:
        settings = AutomaticTestSettings(initial_current_a=20.0, target_current_a=21.0)

        with self.assertRaisesRegex(ValueError, "20 A"):
            build_test_currents(settings)

    def test_unlimited_current_sequence_rejects_excessive_point_count(self) -> None:
        settings = AutomaticTestSettings(
            initial_current_a=1.0,
            target_current_a=20_000.0,
            current_step_a=0.1,
            maximum_current_a=None,
        )

        with self.assertRaisesRegex(ValueError, "测试点数"):
            build_test_currents(settings)

    def test_ramp_down_interval_cannot_bypass_power_supply_command_guard(self) -> None:
        settings = AutomaticTestSettings(ramp_down_interval_s=1.0)

        with self.assertRaisesRegex(ValueError, "1.1"):
            validate_automatic_test_settings(settings, stable_window_s=3.0, post_stable_delay_s=5.0)

    def test_ramp_down_interval_accepts_zero_and_short_intervals_without_command_guard(self) -> None:
        for interval_s in (0.0, 0.1, 0.5, 1.0):
            with self.subTest(interval_s=interval_s):
                settings = AutomaticTestSettings(ramp_down_interval_s=interval_s)
                self.assertIs(
                    validate_automatic_test_settings(
                        settings,
                        stable_window_s=3.0,
                        post_stable_delay_s=5.0,
                        minimum_ramp_down_interval_s=0.0,
                    ),
                    settings,
                )

    def test_ramp_down_interval_still_rejects_invalid_values_without_command_guard(self) -> None:
        for interval_s in (-0.1, math.nan, math.inf, -math.inf):
            with self.subTest(interval_s=interval_s):
                with self.assertRaisesRegex(ValueError, "下电间隔"):
                    validate_automatic_test_settings(
                        AutomaticTestSettings(ramp_down_interval_s=interval_s),
                        stable_window_s=3.0,
                        post_stable_delay_s=5.0,
                        minimum_ramp_down_interval_s=0.0,
                    )

    def test_point_timeout_must_allow_stability_and_voltage_delay(self) -> None:
        settings = AutomaticTestSettings(point_timeout_s=7.9)

        with self.assertRaisesRegex(ValueError, "8.0"):
            validate_automatic_test_settings(settings, stable_window_s=3.0, post_stable_delay_s=5.0)

    def test_non_finite_timing_values_are_rejected(self) -> None:
        settings = AutomaticTestSettings(ramp_down_interval_s=math.nan)

        with self.assertRaises(ValueError):
            validate_automatic_test_settings(settings, stable_window_s=3.0, post_stable_delay_s=5.0)


class AutomaticTestOrchestratorTests(unittest.TestCase):
    def test_start_waits_for_both_devices_and_exposes_current_point(self) -> None:
        orchestrator = AutomaticTestOrchestrator()
        settings = AutomaticTestSettings(initial_current_a=1.0, target_current_a=2.0)

        orchestrator.start(settings, power_meter_ready=False, spectrum_meter_ready=False)
        orchestrator.mark_power_meter_ready()

        self.assertEqual(orchestrator.state, AutomaticTestState.STARTING)
        self.assertFalse(orchestrator.acquisition_ready)
        orchestrator.mark_spectrum_meter_ready()
        self.assertTrue(orchestrator.acquisition_ready)
        self.assertEqual(orchestrator.current_a, 1.0)

    def test_pause_remembers_previous_state_and_ramp_down_reaches_zero(self) -> None:
        orchestrator = AutomaticTestOrchestrator()
        settings = AutomaticTestSettings(ramp_down_step_a=5.0)
        orchestrator.start(settings, power_meter_ready=True, spectrum_meter_ready=True)
        orchestrator.set_state(AutomaticTestState.WAITING_STABLE)

        orchestrator.pause("功率计失败")
        ramp_down = orchestrator.begin_ramp_down(12.0)

        self.assertEqual(orchestrator.paused_from_state, AutomaticTestState.WAITING_STABLE)
        self.assertEqual(ramp_down, (7.0, 2.0, 0.0))
        self.assertEqual(orchestrator.state, AutomaticTestState.RAMPING_DOWN)

    def test_spectrometer_can_be_optional_for_acquisition_readiness(self) -> None:
        orchestrator = AutomaticTestOrchestrator()
        settings = AutomaticTestSettings(use_spectrometer=False)

        orchestrator.start(settings, power_meter_ready=True, spectrum_meter_ready=False)

        self.assertTrue(orchestrator.acquisition_ready)


if __name__ == "__main__":
    unittest.main()
