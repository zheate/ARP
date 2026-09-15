"""Built-in intent profiles, without brand names or device execution policy."""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from .definitions import (
    Capability, CurrentBand, CurrentHold, CurrentMatch, CurrentPlan, ExecutionKind,
    MeasurementPlan, PointRetryMode, RecipeTemplate, RetryPolicy, SpectrumPolicy,
    StabilityOverride, StabilityProfile, TimedPowerHold, TracePolicy,
)


STANDARD_WAVELENGTH_STABILITY_TOLERANCE_NM = 0.2
STANDARD_TEST_STABILITY_WINDOW_S = 5.0
RELAXED_STABILITY_CURRENT_A = 2.0
RELAXED_WAVELENGTH_STABILITY_TOLERANCE_NM = 0.5
RELAXED_POWER_STABILITY_SPAN_W = 0.2
LOW_POWER_STABILITY_SPAN_W = 0.1
LOW_POWER_STABILITY_WINDOW_S = 10.0

MEASURED_POINT_OVERRIDES = (
    StabilityOverride(
        CurrentBand(0.0, RELAXED_STABILITY_CURRENT_A),
        power_window_s=LOW_POWER_STABILITY_WINDOW_S,
        absolute_power_span_w=LOW_POWER_STABILITY_SPAN_W,
    ),
    StabilityOverride(
        CurrentMatch(RELAXED_STABILITY_CURRENT_A),
        absolute_power_span_w=RELAXED_POWER_STABILITY_SPAN_W,
        wavelength_tolerance_nm=RELAXED_WAVELENGTH_STABILITY_TOLERANCE_NM,
    ),
)
DEFAULT_POINT_STABILITY = StabilityProfile(
    STANDARD_TEST_STABILITY_WINDOW_S,
    STANDARD_WAVELENGTH_STABILITY_TOLERANCE_NM,
    MEASURED_POINT_OVERRIDES,
)
MEASURED_POINT_MEASUREMENTS = MeasurementPlan(
    SpectrumPolicy.WHEN_READY_AT_START, TracePolicy.SUPPORTED_CUSTOM_HOLD, True,
)
MEASURED_POINT_CAPABILITIES = (
    Capability.CURRENT_SOURCE, Capability.OPTICAL_POWER, Capability.VOLTAGE_READBACK,
)
OPERATOR_POINT_RETRY = RetryPolicy(PointRetryMode.OPERATOR_MEDIATED)

STANDARD_RECIPE = RecipeTemplate(
    id="standard", execution_kind=ExecutionKind.MEASURED_POINTS,
    capabilities=MEASURED_POINT_CAPABILITIES, measurement_plan=MEASURED_POINT_MEASUREMENTS,
    retry_policy=OPERATOR_POINT_RETRY, stability_profile=DEFAULT_POINT_STABILITY,
    fixed_stability_window_s=STANDARD_TEST_STABILITY_WINDOW_S,
)
HOLD_POINTS_RECIPE = RecipeTemplate(
    id="hold-points", execution_kind=ExecutionKind.MEASURED_POINTS,
    capabilities=MEASURED_POINT_CAPABILITIES, measurement_plan=MEASURED_POINT_MEASUREMENTS,
    retry_policy=OPERATOR_POINT_RETRY, stability_profile=DEFAULT_POINT_STABILITY,
)
AGING_HOLD = TimedPowerHold(duration_s=7_200.0, sample_interval_s=300.0, requested_ramp_interval_s=1.0)
AGING_CURRENT_PLAN = CurrentPlan(
    initial_current_a=2.0, target_current_a=20.0, current_step_a=2.0, mode="custom",
    holds=(CurrentHold(20.0, AGING_HOLD.duration_s),),
)
AGING_RECIPE = RecipeTemplate(
    id="aging", execution_kind=ExecutionKind.TIMED_POWER_HOLD,
    capabilities=(Capability.CURRENT_SOURCE, Capability.OPTICAL_POWER),
    measurement_plan=MeasurementPlan(SpectrumPolicy.NEVER, TracePolicy.NONE, False),
    retry_policy=RetryPolicy(PointRetryMode.NOT_APPLICABLE), stability_profile=DEFAULT_POINT_STABILITY,
    timed_hold=AGING_HOLD, default_current_plan=AGING_CURRENT_PLAN,
)
BUILTIN_RECIPES: Mapping[str, RecipeTemplate] = MappingProxyType({
    template.id: template for template in (STANDARD_RECIPE, HOLD_POINTS_RECIPE, AGING_RECIPE)
})
