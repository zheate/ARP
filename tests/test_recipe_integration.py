"""Phase-1 integration with real runtime/planner and fake device boundaries."""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from combined_test.automation import AutomaticTestSettings, AutomaticTestState
from combined_test.recipes.catalog import AGING_HOLD, BUILTIN_RECIPES, HOLD_POINTS_RECIPE
from combined_test.recipes.definitions import CurrentBand, StabilityOverride
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.recipe_configuration import recipe_from_configuration
from tauri_bridge.settings import JsonSettings
from tauri_bridge.station_automatic import automatic_power_stability_window_s
from tests.test_station_runtime import _FakeController


class RecipeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.backend = HeadlessBackend(JsonSettings(Path(temporary.name) / "settings.json"))
        self.addCleanup(self.backend.export_worker.graceful_stop, 2.0)
        self.station = self.backend.runtime
        self.station.configuration.update({
            "sn": "RECIPE-INTEGRATION", "outputDir": str(Path(temporary.name) / "output"),
            "powerMeterResource": "ASRL3::INSTR",
        })
        self.station.power_supply_controller = _FakeController()

    def test_compiled_recipe_cannot_replace_station_current_ceiling(self) -> None:
        recipe = recipe_from_configuration(self.station.configuration)
        recipe = replace(recipe, current_plan=replace(recipe.current_plan, target_current_a=25))
        with patch("tauri_bridge.station_automatic.recipe_from_configuration", return_value=recipe):
            with self.assertRaisesRegex(ValueError, "20 A"):
                self.station._automatic_settings()
            # Preserve the existing TDK policy instead of inventing a 20 A limit.
            self.station.power_supply_kind = "tdk"
            settings = self.station._automatic_settings()
            self.assertIsNone(settings.maximum_current_a)
            self.assertEqual(settings.target_current_a, 25)
        self.assertEqual(self.station.power_supply_controller.writes, [])

    def test_compilation_does_not_relax_ch341_command_interval_validation(self) -> None:
        self.station.configuration["rampDownIntervalS"] = 0.1
        with self.assertRaisesRegex(ValueError, "1.1 s"):
            self.station._automatic_settings()
        self.station.power_supply_kind = "tdk"
        self.assertEqual(self.station._automatic_settings().ramp_down_interval_s, 0.1)
        self.assertEqual(self.station.power_supply_controller.writes, [])

    def test_platform_timing_and_spectrum_readiness_are_not_owned_by_recipe(self) -> None:
        self.station.power_supply_kind = "tdk"
        self.station.spectrometer_reader = SimpleNamespace(is_ready=True)
        self.station.configuration.update({
            "selectedScriptId": "hold-points", "currentPlanMode": "range",
            "initialCurrentA": 3, "targetCurrentA": 9, "currentStepA": 3,
            "customCurrentPoints": [], "pointTimeoutS": 85,
            "rampDownStepA": 0.5, "rampDownIntervalS": 0,
            "pauseRampDownTimeoutS": 11,
        })
        self.assertEqual(self.station._automatic_settings(), AutomaticTestSettings(
            initial_current_a=3, target_current_a=9, current_step_a=3,
            point_timeout_s=85, ramp_down_step_a=0.5, ramp_down_interval_s=0,
            pause_ramp_down_timeout_s=11, use_spectrometer=True, maximum_current_a=None,
        ))

    def test_one_profile_drives_both_detector_configuration_and_snapshot(self) -> None:
        profile = replace(HOLD_POINTS_RECIPE.stability_profile, detector_window_s=7.0, overrides=(
            StabilityOverride(CurrentBand(5, 8), power_window_s=12.0, absolute_power_span_w=0.3),
        ))
        template = replace(HOLD_POINTS_RECIPE, id="test-only-reference", stability_profile=profile)
        registry = dict(BUILTIN_RECIPES, **{template.id: template})
        self.station.configuration["selectedScriptId"] = template.id
        self.station.configuration["stableWindowS"] = 7.0
        self.station.automatic.currents = (6.0,)
        self.station.automatic.current_index = 0
        self.station.automatic.state = AutomaticTestState.WAITING_STABLE
        reader = MagicMock(is_ready=True)
        reader.reset_stability_window.return_value = 7
        self.station.power_meter_reader = reader
        with patch("tauri_bridge.recipe_configuration.BUILTIN_RECIPES", registry):
            self.station._begin_automatic_measurement_window(6.0)
            progress = self.backend.snapshot({"view": "automatic"})["automaticTest"]["stability"]
            self.assertEqual(progress["requiredS"], 12.0)
            self.assertEqual(progress["spanLimitW"], 0.3)
            # A domain template is not automatically approved for production use.
            blockers = self.station.automatic_readiness()["blockers"]
            self.assertIn("invalid_script", {block["code"] for block in blockers})
        reader.update_stability_settings.assert_called_once_with(
            12.0, self.station.configuration["stabilityLimitPercent"], 0.3,
        )
        self.assertEqual(self.station.automatic_stability_generation, 7)

    def test_aging_dispatch_supplies_typed_hold_and_still_calls_original_shutdown(self) -> None:
        self.station.configure({"selectedScriptId": "aging"})
        self.station.automatic.settings = self.station._automatic_settings()
        with (
            patch.object(self.station, "_run_aging_cycle") as cycle,
            patch.object(self.station, "safe_ramp_down") as shutdown,
            patch.object(self.station, "_queue_automatic_export") as export,
        ):
            self.station._run_automatic()
        cycle.assert_called_once_with(hold=AGING_HOLD)
        shutdown.assert_called_once_with()
        export.assert_not_called()
        self.assertEqual(self.station.automatic_terminal_outcome, "succeeded")
        self.assertEqual(self.station.automatic.state, AutomaticTestState.COMPLETED)

    def test_aging_shutdown_error_is_not_hidden_by_recipe_dispatch(self) -> None:
        self.station.configure({"selectedScriptId": "aging"})
        self.station.automatic.settings = self.station._automatic_settings()
        with (
            patch.object(self.station, "_run_aging_cycle"),
            patch.object(self.station, "safe_ramp_down", side_effect=RuntimeError("unconfirmed output")),
        ):
            self.station._run_automatic()
        self.assertEqual(self.station.automatic_terminal_outcome, "shutdown_unconfirmed")
        self.assertFalse(self.station.shutdown_confirmed)

    def test_legacy_low_current_helper_retains_lazy_configured_window_conversion(self) -> None:
        self.assertEqual(automatic_power_stability_window_s("hold-points", "not-numeric", 1.0), 10.0)
        with self.assertRaises(ValueError):
            automatic_power_stability_window_s("hold-points", "not-numeric", 3.0)

    def test_compilation_does_not_require_or_create_io_or_persistence_permissions(self) -> None:
        before = dict(self.station.configuration)
        self.station._automatic_settings()
        self.assertEqual(self.station.configuration, before)
        self.assertIsNone(self.station.record_store.current_session)
        self.assertIsNone(self.station.record_store.journal_path)
        self.assertEqual(self.station.power_supply_controller.writes, [])
        self.assertFalse(self.station.shutdown_requested)


if __name__ == "__main__":
    unittest.main()
