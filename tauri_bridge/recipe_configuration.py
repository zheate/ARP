"""Compatibility boundary from existing station configuration to pure recipes.

This is a compiler, not a validator or device capability registry. Keep the
existing configuration/readiness/planner validation and hardware guards in
charge. No additional setting is persisted or sent over the bridge.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from combined_test.recipes.catalog import (
    BUILTIN_RECIPES, HOLD_POINTS_RECIPE,
)
from combined_test.recipes.definitions import (
    CurrentHold, CurrentPlan, ExecutionKind, PointStability,
    RecipeDefinition, RecipeTemplate, StabilityProfile,
)


def template_for_legacy_id(script_id: Any) -> RecipeTemplate:
    identity = str(script_id)
    # Unknown IDs retain the old parameterized helper behavior. They are NOT
    # made selectable: automatic_readiness still rejects unknown script IDs.
    return BUILTIN_RECIPES.get(identity) or replace(HOLD_POINTS_RECIPE, id=identity)


def execution_kind_for_legacy_id(script_id: Any) -> ExecutionKind:
    # Execution historically compares exact IDs; do not strip/casefold here.
    return template_for_legacy_id(script_id).execution_kind


def stability_profile_for_legacy_id(script_id: Any, configured_window_s: Any) -> StabilityProfile:
    # Window selection historically strips whitespace, unlike cycle dispatch.
    template = template_for_legacy_id(str(script_id).strip())
    window = template.fixed_stability_window_s
    if window is None:
        window = float(configured_window_s)
    return replace(template.stability_profile, detector_window_s=window)


def recipe_from_configuration(configuration: Mapping[str, Any]) -> RecipeDefinition:
    # Deeply copy mutable point rows into immutable, typed values. Parsing
    # retains the old order and conversions; planner validation is unchanged.
    holds = tuple(
        CurrentHold(float(point["currentA"]), float(point["dwellS"]))
        for point in configuration["customCurrentPoints"]
    )
    plan = CurrentPlan(
        initial_current_a=float(configuration["initialCurrentA"]),
        target_current_a=float(configuration["targetCurrentA"]),
        current_step_a=float(configuration["currentStepA"]),
        mode=str(configuration["currentPlanMode"]),
        holds=holds,
    )
    script_id = configuration.get("selectedScriptId", "standard")
    return template_for_legacy_id(script_id).instantiate(
        current_plan=plan,
        stability=stability_profile_for_legacy_id(script_id, configuration["stableWindowS"]),
    )


def point_stability_from_configuration(
    configuration: Mapping[str, Any], current_a: float | None,
) -> PointStability:
    """Resolve shared runtime/snapshot parameters without compiling long plans."""
    return stability_profile_for_legacy_id(
        configuration.get("selectedScriptId", "standard"), configuration["stableWindowS"],
    ).for_current(current_a)
