"""Pure planning helpers for automatic current tests."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum


MAX_CURRENT_CENTIAMPS = 2000
MIN_POWER_SUPPLY_COMMAND_INTERVAL_S = 1.1
MAX_RAMP_UP_STEP_CENTIAMPS = 100
MAX_AUTOMATIC_SEQUENCE_POINTS = 10_000
POWER_DROP_PROTECTION_RELATIVE_FRACTION = 0.30
POWER_DROP_PROTECTION_WINDOW_S = 5.0


class AutomaticTestState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    SETTING_CURRENT = "setting_current"
    WAITING_STABLE = "waiting_stable"
    WAITING_VOLTAGE = "waiting_voltage"
    SAVING_POINT = "saving_point"
    RAMPING_DOWN = "ramping_down"
    PAUSED = "paused"
    COMPLETED = "completed"


@dataclass(frozen=True)
class AutomaticTestSettings:
    initial_current_a: float = 1.0
    target_current_a: float = 20.0
    current_step_a: float = 1.0
    point_timeout_s: float = 120.0
    ramp_down_step_a: float = 5.0
    ramp_down_interval_s: float = MIN_POWER_SUPPLY_COMMAND_INTERVAL_S
    pause_ramp_down_timeout_s: float = 30.0
    use_spectrometer: bool = True
    maximum_current_a: float | None = MAX_CURRENT_CENTIAMPS / 100.0


@dataclass(frozen=True)
class PowerDropProtectionResult:
    """Result of checking one automatic-test power sample."""

    triggered: bool
    reference_power_w: float | None
    observed_power_w: float
    drop_w: float = 0.0
    threshold_w: float = 0.0


class PowerDropProtectionDetector:
    """Latch when power falls by more than the limit over a rolling window."""

    def __init__(
        self,
        relative_fraction: float = POWER_DROP_PROTECTION_RELATIVE_FRACTION,
        window_s: float = POWER_DROP_PROTECTION_WINDOW_S,
    ) -> None:
        relative = float(relative_fraction)
        if not math.isfinite(relative) or not 0.0 < relative < 1.0:
            raise ValueError("功率下降保护比例必须在 0 和 1 之间")
        window = float(window_s)
        if not math.isfinite(window) or window <= 0.0:
            raise ValueError("功率下降保护窗口必须大于 0 秒")
        self.relative_fraction = relative
        self.window_s = window
        self.reference_power_w: float | None = None
        self.tripped = False
        self._samples: deque[tuple[float, float]] = deque()

    def reset(self) -> None:
        self.reference_power_w = None
        self.tripped = False
        self._samples.clear()

    def observe(
        self,
        power_w: float,
        elapsed_s: float | None = None,
    ) -> PowerDropProtectionResult:
        power = float(power_w)
        observed_at_s = time.monotonic() if elapsed_s is None else float(elapsed_s)
        reference = self.reference_power_w
        if self.tripped or not math.isfinite(power) or not math.isfinite(observed_at_s):
            return PowerDropProtectionResult(False, reference, power)

        if self._samples and observed_at_s < self._samples[-1][0]:
            self._samples.clear()
            self.reference_power_w = None

        self._samples.append((observed_at_s, power))
        cutoff_s = observed_at_s - self.window_s
        while len(self._samples) >= 2 and self._samples[1][0] <= cutoff_s:
            self._samples.popleft()

        if not self._samples or self._samples[0][0] > cutoff_s:
            self.reference_power_w = None
            return PowerDropProtectionResult(False, None, power)

        reference = self._samples[0][1]
        self.reference_power_w = reference
        if reference <= 0.0:
            return PowerDropProtectionResult(False, reference, power)

        threshold_w = reference * self.relative_fraction
        drop_w = reference - power
        comparison_margin = max(1e-12, threshold_w * 1e-12)
        if drop_w > threshold_w + comparison_margin:
            self.tripped = True
            return PowerDropProtectionResult(
                True,
                reference,
                power,
                drop_w,
                threshold_w,
            )

        return PowerDropProtectionResult(
            False,
            reference,
            power,
            max(0.0, drop_w),
            threshold_w,
        )


class AutomaticTestOrchestrator:
    """Own the device-independent state and sequencing of an automatic test."""

    def __init__(self) -> None:
        self.state = AutomaticTestState.IDLE
        self.settings: AutomaticTestSettings | None = None
        self.currents: tuple[float, ...] = ()
        self.current_index = -1
        self.power_meter_ready = False
        self.spectrum_meter_ready = False
        self.pause_reason = ""
        self.paused_from_state = AutomaticTestState.IDLE

    @property
    def acquisition_ready(self) -> bool:
        spectrum_ready = (
            bool(self.settings is not None and not self.settings.use_spectrometer)
            or self.spectrum_meter_ready
        )
        return self.power_meter_ready and spectrum_ready

    @property
    def current_a(self) -> float | None:
        if 0 <= self.current_index < len(self.currents):
            return self.currents[self.current_index]
        return None

    def start(
        self,
        settings: AutomaticTestSettings,
        *,
        power_meter_ready: bool,
        spectrum_meter_ready: bool,
    ) -> None:
        self.settings = settings
        self.currents = build_test_currents(settings)
        self.current_index = 0
        self.power_meter_ready = bool(power_meter_ready)
        self.spectrum_meter_ready = bool(spectrum_meter_ready)
        self.pause_reason = ""
        self.state = AutomaticTestState.STARTING

    def set_state(self, state: AutomaticTestState) -> None:
        self.state = state

    def mark_power_meter_ready(self) -> None:
        self.power_meter_ready = True

    def mark_spectrum_meter_ready(self) -> None:
        self.spectrum_meter_ready = True

    def pause(self, reason: str) -> None:
        if self.state != AutomaticTestState.PAUSED:
            self.paused_from_state = self.state
        self.pause_reason = str(reason)
        self.state = AutomaticTestState.PAUSED

    def advance(self) -> bool:
        if self.current_index + 1 >= len(self.currents):
            return False
        self.current_index += 1
        return True

    def begin_ramp_down(self, start_current_a: float) -> tuple[float, ...]:
        if self.settings is None:
            raise ValueError("自动下电参数不可用")
        currents = build_ramp_down_currents(
            start_current_a,
            self.settings.ramp_down_step_a,
            maximum_current_a=self.settings.maximum_current_a,
        )
        self.state = AutomaticTestState.RAMPING_DOWN
        return currents

    def complete(self) -> None:
        self.state = AutomaticTestState.COMPLETED


def _to_centiampere(value: float, name: str) -> int:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name}必须是有限数值")
    return round(number * 100.0)


def _maximum_centiampere(maximum_current_a: float | None) -> int | None:
    if maximum_current_a is None:
        return None
    maximum = _to_centiampere(maximum_current_a, "最大电流")
    if maximum <= 0:
        raise ValueError("最大电流必须大于 0 A")
    return maximum


def _validate_sequence_size(point_count: int) -> None:
    if point_count > MAX_AUTOMATIC_SEQUENCE_POINTS:
        raise ValueError(
            f"自动测试点数不能超过 {MAX_AUTOMATIC_SEQUENCE_POINTS}；请增大电流步长"
        )


def build_test_currents(settings: AutomaticTestSettings) -> tuple[float, ...]:
    """Build an increasing current sequence that always includes the target."""
    initial = _to_centiampere(settings.initial_current_a, "初始电流")
    target = _to_centiampere(settings.target_current_a, "目标电流")
    step = _to_centiampere(settings.current_step_a, "电流间隔")
    maximum = _maximum_centiampere(settings.maximum_current_a)
    if initial <= 0 or target <= 0 or initial > target:
        raise ValueError("电流必须满足 0 < 初始电流 <= 目标电流")
    if maximum is not None and target > maximum:
        raise ValueError(f"目标电流不能超过 {maximum / 100.0:g} A")
    if step <= 0:
        raise ValueError("电流间隔必须大于 0 A")

    step_count = (target - initial + step - 1) // step
    _validate_sequence_size(step_count + 1)
    currents = [min(target, initial + index * step) for index in range(step_count + 1)]
    return tuple(value / 100.0 for value in currents)


def build_ramp_down_currents(
    start_current_a: float,
    step_a: float,
    *,
    maximum_current_a: float | None = MAX_CURRENT_CENTIAMPS / 100.0,
) -> tuple[float, ...]:
    """Build descending setpoints after a completed or aborted test."""
    current = _to_centiampere(start_current_a, "当前电流")
    step = _to_centiampere(step_a, "下电步长")
    maximum = _maximum_centiampere(maximum_current_a)
    if current < 0:
        raise ValueError("当前电流必须大于或等于 0 A")
    if maximum is not None and current > maximum:
        raise ValueError(f"当前电流不能超过 {maximum / 100.0:g} A")
    if step <= 0:
        raise ValueError("下电步长必须大于 0 A")

    step_count = (current + step - 1) // step
    _validate_sequence_size(step_count)
    currents = [max(0, current - index * step) for index in range(1, step_count + 1)]
    return tuple(value / 100.0 for value in currents)


def build_ramp_up_currents(
    start_current_a: float,
    target_current_a: float,
    max_step_a: float = MAX_RAMP_UP_STEP_CENTIAMPS / 100.0,
    *,
    maximum_current_a: float | None = MAX_CURRENT_CENTIAMPS / 100.0,
) -> tuple[float, ...]:
    """Build safe increasing setpoints, excluding start and including target."""
    start = _to_centiampere(start_current_a, "当前电流")
    target = _to_centiampere(target_current_a, "目标电流")
    step = _to_centiampere(max_step_a, "最大升流步长")
    maximum = _maximum_centiampere(maximum_current_a)
    if start < 0 or target < 0 or start > target:
        raise ValueError("升流电流必须满足 0 <= 当前电流 <= 目标电流")
    if maximum is not None and target > maximum:
        raise ValueError(f"目标电流不能超过 {maximum / 100.0:g} A")
    if step <= 0:
        raise ValueError("最大升流步长必须大于 0 A")
    step_count = (target - start + step - 1) // step
    _validate_sequence_size(step_count)
    currents = [min(target, start + index * step) for index in range(1, step_count + 1)]
    return tuple(value / 100.0 for value in currents)


def validate_automatic_test_settings(
    settings: AutomaticTestSettings,
    *,
    stable_window_s: float,
    post_stable_delay_s: float,
) -> AutomaticTestSettings:
    """Validate settings shared by the UI and automatic runner."""
    build_test_currents(settings)
    build_ramp_down_currents(
        settings.target_current_a,
        settings.ramp_down_step_a,
        maximum_current_a=settings.maximum_current_a,
    )
    if (
        not math.isfinite(settings.ramp_down_interval_s)
        or settings.ramp_down_interval_s < MIN_POWER_SUPPLY_COMMAND_INTERVAL_S
    ):
        raise ValueError("下电间隔不能小于 1.1 s")
    stable_window = float(stable_window_s)
    post_stable_delay = float(post_stable_delay_s)
    if not math.isfinite(stable_window) or stable_window <= 0.0:
        raise ValueError("稳定窗口必须大于 0 s")
    if not math.isfinite(post_stable_delay) or post_stable_delay < 0.0:
        raise ValueError("稳定后等待时间必须大于或等于 0 s")
    minimum_timeout_s = stable_window + post_stable_delay
    if not math.isfinite(settings.point_timeout_s) or settings.point_timeout_s < minimum_timeout_s:
        raise ValueError(f"单点超时不能小于 {minimum_timeout_s:.1f} s")
    if not math.isfinite(settings.pause_ramp_down_timeout_s) or settings.pause_ramp_down_timeout_s < 0.0:
        raise ValueError("暂停安全下电等待时间必须大于或等于 0 s")
    return settings
