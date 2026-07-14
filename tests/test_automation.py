import math
import unittest

from combined_test.automation import (
    AutomaticTestOrchestrator,
    AutomaticTestSettings,
    AutomaticTestState,
    build_ramp_down_currents,
    build_ramp_up_currents,
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

    def test_ramp_up_inserts_one_amp_safety_steps_and_includes_target(self) -> None:
        self.assertEqual(build_ramp_up_currents(0.0, 3.5), (1.0, 2.0, 3.0, 3.5))
        self.assertEqual(build_ramp_up_currents(3.0, 3.0), ())

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
            build_ramp_up_currents(20.0, 23.0, maximum_current_a=None),
            (21.0, 22.0, 23.0),
        )
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
