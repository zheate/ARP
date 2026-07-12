"""Pure planning helpers for automatic current tests."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


MAX_CURRENT_CENTIAMPS = 2000
MIN_POWER_SUPPLY_COMMAND_INTERVAL_S = 1.1
MAX_RAMP_UP_STEP_CENTIAMPS = 100


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
        return self.power_meter_ready and self.spectrum_meter_ready

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
        currents = build_ramp_down_currents(start_current_a, self.settings.ramp_down_step_a)
        self.state = AutomaticTestState.RAMPING_DOWN
        return currents

    def complete(self) -> None:
        self.state = AutomaticTestState.COMPLETED


def _to_centiampere(value: float, name: str) -> int:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name}必须是有限数值")
    return round(number * 100.0)


def build_test_currents(settings: AutomaticTestSettings) -> tuple[float, ...]:
    """Build an increasing current sequence that always includes the target."""
    initial = _to_centiampere(settings.initial_current_a, "初始电流")
    target = _to_centiampere(settings.target_current_a, "目标电流")
    step = _to_centiampere(settings.current_step_a, "电流间隔")
    if initial <= 0 or target <= 0 or initial > target or target > MAX_CURRENT_CENTIAMPS:
        raise ValueError("电流必须满足 0 < 初始电流 <= 目标电流 <= 20 A")
    if step <= 0:
        raise ValueError("电流间隔必须大于 0 A")

    currents = [initial]
    while currents[-1] < target:
        currents.append(min(target, currents[-1] + step))
    return tuple(value / 100.0 for value in currents)


def build_ramp_down_currents(start_current_a: float, step_a: float) -> tuple[float, ...]:
    """Build descending setpoints after a completed or aborted test."""
    current = _to_centiampere(start_current_a, "当前电流")
    step = _to_centiampere(step_a, "下电步长")
    if current < 0 or current > MAX_CURRENT_CENTIAMPS:
        raise ValueError("当前电流必须在 0 至 20 A 范围内")
    if step <= 0:
        raise ValueError("下电步长必须大于 0 A")

    currents: list[int] = []
    while current > 0:
        current = max(0, current - step)
        currents.append(current)
    return tuple(value / 100.0 for value in currents)


def build_ramp_up_currents(
    start_current_a: float,
    target_current_a: float,
    max_step_a: float = MAX_RAMP_UP_STEP_CENTIAMPS / 100.0,
) -> tuple[float, ...]:
    """Build safe increasing setpoints, excluding start and including target."""
    start = _to_centiampere(start_current_a, "当前电流")
    target = _to_centiampere(target_current_a, "目标电流")
    step = _to_centiampere(max_step_a, "最大升流步长")
    if start < 0 or target < 0 or start > target or target > MAX_CURRENT_CENTIAMPS:
        raise ValueError("升流电流必须满足 0 <= 当前电流 <= 目标电流 <= 20 A")
    if step <= 0:
        raise ValueError("最大升流步长必须大于 0 A")
    currents: list[int] = []
    current = start
    while current < target:
        current = min(target, current + step)
        currents.append(current)
    return tuple(value / 100.0 for value in currents)


def validate_automatic_test_settings(
    settings: AutomaticTestSettings,
    *,
    stable_window_s: float,
    post_stable_delay_s: float,
) -> AutomaticTestSettings:
    """Validate settings shared by the UI and automatic runner."""
    build_test_currents(settings)
    build_ramp_down_currents(settings.target_current_a, settings.ramp_down_step_a)
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
