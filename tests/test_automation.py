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


if __name__ == "__main__":
    unittest.main()
