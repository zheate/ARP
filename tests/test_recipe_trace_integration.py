"""A selected trace intent is not permission to bypass station device guards."""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from combined_test.automation import AutomaticTestSettings
from combined_test.recipes.catalog import BUILTIN_RECIPES, HOLD_POINTS_RECIPE
from combined_test.recipes.definitions import TracePolicy
from tauri_bridge.headless_backend import HeadlessBackend
from tauri_bridge.settings import JsonSettings
from tests.test_station_runtime import _FakeController


class RecipeTraceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.backend = HeadlessBackend(JsonSettings(Path(temporary.name) / "settings.json"))
        self.addCleanup(self.backend.export_worker.graceful_stop, 2.0)
        self.station = self.backend.runtime
        self.station.configuration.update({
            "sn": "RECIPE-TRACE", "outputDir": str(Path(temporary.name) / "output"),
            "powerMeterResource": "ASRL3::INSTR",
        })
        self.station.power_supply_controller = _FakeController()
        self.station.power_supply_kind = "tdk"

    def no_trace_registry(self):
        template = replace(
            HOLD_POINTS_RECIPE,
            id="test-only-no-trace",
            measurement_plan=replace(HOLD_POINTS_RECIPE.measurement_plan, trace_policy=TracePolicy.NONE),
        )
        self.station.configuration["selectedScriptId"] = template.id
        return dict(BUILTIN_RECIPES, **{template.id: template})

    def test_selected_no_trace_intent_is_honoured_without_disabling_journal(self) -> None:
        with (
            patch("tauri_bridge.recipe_configuration.BUILTIN_RECIPES", self.no_trace_registry()),
            patch("tauri_bridge.station_automatic.AutomaticSampleRecorder") as recorder,
        ):
            self.station._begin_recording_session(AutomaticTestSettings(current_plan_mode="custom"))
            recorder.assert_not_called()
            # Registering a template in this test does not approve it in readiness.
            blockers = self.station.automatic_readiness()["blockers"]
            self.assertIn("invalid_script", {block["code"] for block in blockers})
        self.assertEqual(self.station.automatic_sample_path, "")
        self.assertIsNone(self.station.automatic_sample_recorder)
        self.assertIsNotNone(self.station.record_store.current_session)
        events = [json.loads(line) for line in self.station.record_store.journal_path.read_text().splitlines()]
        self.assertEqual([event["type"] for event in events], ["session_start"])
        self.assertEqual(self.station.power_supply_controller.writes, [])

    def test_trace_request_does_not_authorize_ch341_polling(self) -> None:
        self.station.power_supply_kind = "ch341"
        self.station.configuration["selectedScriptId"] = "hold-points"
        with patch("tauri_bridge.station_automatic.AutomaticSampleRecorder") as recorder:
            self.station._begin_recording_session(AutomaticTestSettings(current_plan_mode="custom"))
        recorder.assert_not_called()
        self.assertIsNotNone(self.station.record_store.journal_path)
        self.assertEqual(self.station.power_supply_controller.writes, [])

    def test_builtin_trace_keeps_resolved_mode_and_exact_case_semantics(self) -> None:
        for script in ("standard", "hold-points"):
            self.station.configuration["selectedScriptId"] = script
            for mode, expected in (("custom", True), ("range", False), ("CUSTOM", False), (" custom ", False)):
                with self.subTest(script=script, mode=mode):
                    # Deliberately conflict with the settings passed to this callable.
                    self.station.configuration["currentPlanMode"] = "range" if mode == "custom" else "custom"
                    with patch("tauri_bridge.station_automatic.AutomaticSampleRecorder") as recorder:
                        self.station._begin_recording_session(AutomaticTestSettings(current_plan_mode=mode))
                        self.assertEqual(recorder.call_count, int(expected))
                    self.station._close_automatic_sample_recorder()

    def test_no_trace_intent_closes_old_recorder_and_clears_stale_path(self) -> None:
        old_recorder = MagicMock()
        self.station.automatic_sample_recorder = old_recorder
        self.station.automatic_sample_path = "previous-session.csv"
        with (
            patch("tauri_bridge.recipe_configuration.BUILTIN_RECIPES", self.no_trace_registry()),
            patch("tauri_bridge.station_automatic.AutomaticSampleRecorder") as recorder,
        ):
            self.station._begin_recording_session(AutomaticTestSettings(current_plan_mode="custom"))
        old_recorder.close.assert_called_once_with()
        recorder.assert_not_called()
        self.assertIsNone(self.station.automatic_sample_recorder)
        self.assertEqual(self.station.automatic_sample_path, "")

    def test_absent_legacy_script_id_keeps_standard_trace_default(self) -> None:
        self.station.configuration.pop("selectedScriptId", None)
        with patch("tauri_bridge.station_automatic.AutomaticSampleRecorder") as recorder:
            self.station._begin_recording_session(AutomaticTestSettings(current_plan_mode="custom"))
        recorder.assert_called_once()
        self.station._close_automatic_sample_recorder()


if __name__ == "__main__":
    unittest.main()
