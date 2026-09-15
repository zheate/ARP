"""Small, closed set of recipe values; these objects never execute a test.

Device constraints, numerical current-plan validation, recovery actions and
persistence acknowledgements remain owned by the existing execution layer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):
    CURRENT_SOURCE = "controllable_current_source"
    OPTICAL_POWER = "optical_power_measurement"
    SPECTRUM = "spectrum_acquisition"
    CURRENT_READBACK = "verified_current_readback"
    VOLTAGE_READBACK = "voltage_readback"


class ExecutionKind(str, Enum):
    MEASURED_POINTS = "measured_points"
    TIMED_POWER_HOLD = "timed_power_hold"


class SpectrumPolicy(str, Enum):
    WHEN_READY_AT_START = "when_ready_at_start"
    NEVER = "never"


class TracePolicy(str, Enum):
    SUPPORTED_CUSTOM_HOLD = "supported_custom_hold_one_second"
    NONE = "none"


class PointRetryMode(str, Enum):
    OPERATOR_MEDIATED = "operator_mediated"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class CurrentHold:
    current_a: float
    dwell_s: float


@dataclass(frozen=True)
class CurrentPlan:
    initial_current_a: float
    target_current_a: float
    current_step_a: float
    # Keep the legacy spelling until the existing planner validates it. A new
    # enum conversion here would change accepted input/error and ramp semantics.
    mode: str
    holds: tuple[CurrentHold, ...] = ()


@dataclass(frozen=True)
class CurrentBand:
    minimum_a: float
    maximum_a: float

    def matches(self, current_a: float) -> bool:
        return self.minimum_a <= current_a < self.maximum_a


@dataclass(frozen=True)
class CurrentMatch:
    current_a: float
    tolerance_a: float = 1e-9

    def matches(self, current_a: float) -> bool:
        return math.isclose(current_a, self.current_a, rel_tol=0.0, abs_tol=self.tolerance_a)


@dataclass(frozen=True)
class StabilityOverride:
    condition: CurrentBand | CurrentMatch
    power_window_s: float | None = None
    absolute_power_span_w: float | None = None
    wavelength_tolerance_nm: float | None = None


@dataclass(frozen=True)
class PointStability:
    power_window_s: float
    wavelength_window_s: float
    absolute_power_span_w: float | None
    wavelength_tolerance_nm: float


@dataclass(frozen=True)
class StabilityProfile:
    detector_window_s: float
    wavelength_tolerance_nm: float
    overrides: tuple[StabilityOverride, ...] = ()

    def matching_overrides(self, current_a: float | None) -> tuple[StabilityOverride, ...]:
        if current_a is None:
            return ()
        current = float(current_a)
        return tuple(rule for rule in self.overrides if rule.condition.matches(current))

    def power_window_override(self, current_a: float | None) -> float | None:
        return next((rule.power_window_s for rule in self.matching_overrides(current_a)
                     if rule.power_window_s is not None), None)

    def for_current(self, current_a: float | None) -> PointStability:
        # First matching override wins PER FIELD. Just below 2 A the power
        # band and the wavelength's 1e-9 A exact-match tolerance both apply.
        rules = self.matching_overrides(current_a)
        return PointStability(
            power_window_s=next((rule.power_window_s for rule in rules
                                 if rule.power_window_s is not None), self.detector_window_s),
            wavelength_window_s=self.detector_window_s,
            absolute_power_span_w=next((rule.absolute_power_span_w for rule in rules
                                       if rule.absolute_power_span_w is not None), None),
            wavelength_tolerance_nm=next((rule.wavelength_tolerance_nm for rule in rules
                                         if rule.wavelength_tolerance_nm is not None), self.wavelength_tolerance_nm),
        )


@dataclass(frozen=True)
class MeasurementPlan:
    spectrum_policy: SpectrumPolicy
    trace_policy: TracePolicy
    requires_stability: bool

    def uses_spectrum(self, *, ready_at_start: bool) -> bool:
        return self.spectrum_policy is SpectrumPolicy.WHEN_READY_AT_START and ready_at_start

    def requests_second_trace(self, *, current_plan_mode: str) -> bool:
        # Request only: the platform must still check whether its device path
        # supports this trace. This method does not authorize extra polling.
        return self.trace_policy is TracePolicy.SUPPORTED_CUSTOM_HOLD and current_plan_mode == "custom"


@dataclass(frozen=True)
class RetryPolicy:
    point_retry_mode: PointRetryMode
    # Point retries are operator-mediated today. Device transport retries and
    # safety timeouts are NOT part of this policy and are deliberately absent.
    automatic_point_retries: int = 0


@dataclass(frozen=True)
class TimedPowerHold:
    duration_s: float
    sample_interval_s: float
    requested_ramp_interval_s: float


@dataclass(frozen=True)
class RecipeDefinition:
    id: str
    version: int
    execution_kind: ExecutionKind
    capabilities: tuple[Capability, ...]
    current_plan: CurrentPlan
    stability: StabilityProfile
    measurement_plan: MeasurementPlan
    retry_policy: RetryPolicy
    timed_hold: TimedPowerHold | None = None
    specification_id: str | None = None


@dataclass(frozen=True)
class RecipeTemplate:
    id: str
    execution_kind: ExecutionKind
    capabilities: tuple[Capability, ...]
    measurement_plan: MeasurementPlan
    retry_policy: RetryPolicy
    stability_profile: StabilityProfile
    fixed_stability_window_s: float | None = None
    timed_hold: TimedPowerHold | None = None
    default_current_plan: CurrentPlan | None = None
    version: int = 1

    def instantiate(self, *, current_plan: CurrentPlan, stability: StabilityProfile) -> RecipeDefinition:
        return RecipeDefinition(
            id=self.id, version=self.version, execution_kind=self.execution_kind,
            capabilities=self.capabilities, current_plan=current_plan, stability=stability,
            measurement_plan=self.measurement_plan, retry_policy=self.retry_policy,
            timed_hold=self.timed_hold,
        )
