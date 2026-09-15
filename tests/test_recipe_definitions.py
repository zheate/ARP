"""Pure recipe compilation and dependency-direction tests (no instruments)."""
from __future__ import annotations

import ast
import math
import random
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

from combined_test.recipes.catalog import (
    AGING_CURRENT_PLAN, AGING_HOLD, BUILTIN_RECIPES, DEFAULT_POINT_STABILITY,
    HOLD_POINTS_RECIPE, STANDARD_RECIPE,
)
from combined_test.recipes.definitions import (
    Capability, CurrentBand, CurrentHold, CurrentPlan, ExecutionKind,
    PointRetryMode, SpectrumPolicy, StabilityOverride, TracePolicy,
)
from tauri_bridge.recipe_configuration import (
    execution_kind_for_legacy_id, recipe_from_configuration,
    stability_profile_for_legacy_id,
)


ROOT = Path(__file__).resolve().parents[1]


def configuration(script_id: str = "standard", mode: str = "range") -> dict:
    return {
        "selectedScriptId": script_id,
        "initialCurrentA": 1, "targetCurrentA": 7, "currentStepA": 3,
        "currentPlanMode": mode, "customCurrentPoints": [{"currentA": 2, "dwellS": 30}],
        "stableWindowS": 3,
    }


class RecipeDefinitionTests(unittest.TestCase):
    def test_three_builtins_have_explicit_definitions(self) -> None:
        self.assertEqual(set(BUILTIN_RECIPES), {"standard", "hold-points", "aging"})
        for identity in BUILTIN_RECIPES:
            with self.subTest(identity=identity):
                recipe = recipe_from_configuration(configuration(identity))
                self.assertEqual(recipe.id, identity)
                self.assertEqual(recipe.version, 1)
                self.assertEqual(recipe.current_plan, CurrentPlan(1, 7, 3, "range", (CurrentHold(2, 30),)))
                self.assertIsNone(recipe.specification_id)
                self.assertIn(Capability.CURRENT_SOURCE, recipe.capabilities)
                self.assertIn(Capability.OPTICAL_POWER, recipe.capabilities)

    def test_mutating_input_rows_cannot_change_compiled_recipe(self) -> None:
        source = configuration("hold-points", "custom")
        recipe = recipe_from_configuration(source)
        source["customCurrentPoints"][0]["dwellS"] = 999
        source["customCurrentPoints"].append({"currentA": 5, "dwellS": 20})
        source["targetCurrentA"] = 20
        self.assertEqual(recipe.current_plan.holds, (CurrentHold(2, 30),))
        self.assertEqual(recipe.current_plan.target_current_a, 7)
        with self.assertRaises(FrozenInstanceError):
            recipe.current_plan.target_current_a = 9
        with self.assertRaises(FrozenInstanceError):
            recipe.current_plan.holds[0].dwell_s = 1
        with self.assertRaises(TypeError):
            BUILTIN_RECIPES["extra"] = STANDARD_RECIPE
        self.assertIsInstance(hash(recipe), int)

    def test_aging_intent_preserves_exact_fixed_profile_without_device_brands(self) -> None:
        recipe = BUILTIN_RECIPES["aging"].instantiate(
            current_plan=AGING_CURRENT_PLAN, stability=stability_profile_for_legacy_id("aging", 3),
        )
        self.assertEqual(recipe.execution_kind, ExecutionKind.TIMED_POWER_HOLD)
        self.assertEqual(recipe.current_plan, CurrentPlan(2, 20, 2, "custom", (CurrentHold(20, 7200),)))
        self.assertEqual((AGING_HOLD.requested_ramp_interval_s, AGING_HOLD.duration_s, AGING_HOLD.sample_interval_s), (1, 7200, 300))
        self.assertFalse(recipe.measurement_plan.requires_stability)
        self.assertEqual(recipe.measurement_plan.spectrum_policy, SpectrumPolicy.NEVER)
        self.assertEqual(recipe.measurement_plan.trace_policy, TracePolicy.NONE)
        self.assertEqual(recipe.retry_policy.point_retry_mode, PointRetryMode.NOT_APPLICABLE)
        self.assertEqual(recipe.capabilities, (Capability.CURRENT_SOURCE, Capability.OPTICAL_POWER))

    def test_spectrum_and_trace_are_conditional_intents_not_device_authorization(self) -> None:
        for identity in ("standard", "hold-points"):
            plan = recipe_from_configuration(configuration(identity, "custom")).measurement_plan
            self.assertFalse(plan.uses_spectrum(ready_at_start=False))
            self.assertTrue(plan.uses_spectrum(ready_at_start=True))
            self.assertTrue(plan.requests_second_trace(current_plan_mode="custom"))
            self.assertFalse(plan.requests_second_trace(current_plan_mode="range"))
            # Legacy trace creation is case-sensitive, unlike the plan validator.
            self.assertFalse(plan.requests_second_trace(current_plan_mode="CUSTOM"))
            self.assertTrue(plan.requires_stability)
        aging = recipe_from_configuration(configuration("aging", "custom")).measurement_plan
        self.assertFalse(aging.uses_spectrum(ready_at_start=True))
        self.assertFalse(aging.requests_second_trace(current_plan_mode="custom"))

    def test_no_automatic_measurement_retry_or_unsafe_control_is_added(self) -> None:
        for template in (STANDARD_RECIPE, HOLD_POINTS_RECIPE):
            self.assertEqual(template.retry_policy.point_retry_mode, PointRetryMode.OPERATOR_MEDIATED)
            self.assertEqual(template.retry_policy.automatic_point_retries, 0)
        all_fields = {field.name for value in (
            recipe_from_configuration(configuration()), AGING_CURRENT_PLAN, AGING_HOLD,
            STANDARD_RECIPE.measurement_plan, STANDARD_RECIPE.retry_policy,
        ) for field in fields(value)}
        self.assertFalse(all_fields & {"maximum_current_a", "ramp_down_step_a", "disable_journal", "skip_shutdown", "driver"})

    def test_profile_matches_literal_legacy_oracle_over_seeded_inputs(self) -> None:
        rng = random.Random(976)
        currents = [None, -1.0, 0.0, 1.99, 2.0 - 5e-10, 2.0, 2.0 + 5e-10, 2.0 + 2e-9, math.nan, math.inf]
        currents += [rng.uniform(-1, 25) for _ in range(250)]
        for identity in ("standard", "hold-points", "aging", "custom-unknown", " standard "):
            for window in (3.0, 5.0, 10.0):
                profile = stability_profile_for_legacy_id(identity, window)
                base = 5.0 if identity.strip() == "standard" else window
                for current in currents:
                    below = current is not None and 0 <= current < 2
                    at_two = current is not None and math.isclose(current, 2.0, rel_tol=0, abs_tol=1e-9)
                    actual = profile.for_current(current)
                    with self.subTest(identity=identity, window=window, current=current):
                        self.assertEqual(actual.power_window_s, 10.0 if below else base)
                        self.assertEqual(actual.wavelength_window_s, base)
                        self.assertEqual(actual.absolute_power_span_w, 0.1 if below else 0.2 if at_two else None)
                        self.assertEqual(actual.wavelength_tolerance_nm, 0.5 if at_two else 0.2)

    def test_profiles_can_express_another_recipe_without_id_branches(self) -> None:
        template = replace(HOLD_POINTS_RECIPE, id="lab-reference", version=2)
        profile = replace(DEFAULT_POINT_STABILITY, detector_window_s=7.0, overrides=(
            StabilityOverride(CurrentBand(5, 8), power_window_s=12, absolute_power_span_w=0.3),
        ))
        definition = template.instantiate(current_plan=CurrentPlan(5, 7, 1, "range"), stability=profile)
        self.assertEqual(definition.id, "lab-reference")
        self.assertEqual(definition.stability.for_current(6).power_window_s, 12)
        self.assertEqual(definition.stability.for_current(9).power_window_s, 7)
        self.assertEqual(definition.execution_kind, ExecutionKind.MEASURED_POINTS)

    def test_legacy_compiler_is_not_a_new_selection_or_validation_gate(self) -> None:
        for identity in ("CUSTOM", " aging ", ""):
            recipe = recipe_from_configuration(configuration(identity))
            self.assertEqual(recipe.id, identity)
            self.assertEqual(execution_kind_for_legacy_id(identity), ExecutionKind.MEASURED_POINTS)
        self.assertEqual(execution_kind_for_legacy_id("aging"), ExecutionKind.TIMED_POWER_HOLD)
        bad = configuration()
        bad["currentPlanMode"] = "not-a-plan"
        # Existing execution validator, not this compiler, owns the rejection.
        self.assertEqual(recipe_from_configuration(bad).current_plan.mode, "not-a-plan")
        bad["customCurrentPoints"] = [{"dwellS": 30}]
        with self.assertRaises(KeyError):
            recipe_from_configuration(bad)

    def test_recipe_import_and_compilation_work_without_site_packages(self) -> None:
        code = f"""
import sys
sys.path.insert(0, {str(ROOT)!r})
from combined_test.recipes.catalog import AGING_RECIPE, AGING_CURRENT_PLAN, DEFAULT_POINT_STABILITY
from tauri_bridge.recipe_configuration import recipe_from_configuration
recipe = AGING_RECIPE.instantiate(current_plan=AGING_CURRENT_PLAN, stability=DEFAULT_POINT_STABILITY)
assert recipe.timed_hold.duration_s == 7200
recipe_from_configuration({configuration()!r})
for forbidden in ('numpy', 'pyvisa', 'combined_test.devices', 'combined_test.record_store',
                  'tauri_bridge.station_runtime', 'tauri_bridge.station_automatic'):
    assert forbidden not in sys.modules, forbidden
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-c", code], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_domain_imports_are_closed_and_have_no_io_or_dynamic_code(self) -> None:
        allowed = {"__future__", "dataclasses", "enum", "math", "types", "typing"}
        for path in (ROOT / "combined_test" / "recipes").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertTrue(all(alias.name in allowed for alias in node.names), (path.name, ast.unparse(node)))
                elif isinstance(node, ast.ImportFrom):
                    self.assertTrue(node.level == 1 and node.module == "definitions" or node.module in allowed, (path.name, ast.unparse(node)))
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "exec", "eval", "__import__", "compile"})


if __name__ == "__main__":
    unittest.main()
