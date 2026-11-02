"""Learn a probe's repeated jump from 20 seconds of existing 0 A samples.

Legacy model fields remain readable for archived session/export compatibility.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from statistics import median


ALGORITHM_VERSION = "power-meter-jump-20s-v1"
LONG_WINDOW_S = 20.0
LEARNING_DURATION_S = 20.0


@dataclass(frozen=True)
class NoiseLearningSettings:
    quantization_w: float = 0.0
    gap_s: float = 1.0

    def __post_init__(self) -> None:
        if (not math.isfinite(self.quantization_w) or self.quantization_w < 0
                or not math.isfinite(self.gap_s) or self.gap_s <= 0):
            raise ValueError("跳动量和采样间隔无效")


@dataclass(frozen=True)
class BackgroundModel:
    model_id: str
    identity: str
    created_at: str
    quantization_w: float
    center_w: float
    noise_sigma_w: float
    shift_guard_w: float
    slope_guard_w_s: float
    sample_count: int
    duration_s: float
    algorithm: str = ALGORITHM_VERSION
    quantization_source: str = "manual"
    recurrent_jump_w: float = 0.0
    learning_method: str = "stationary-v1"

    @classmethod
    def from_dict(cls, value: dict) -> BackgroundModel:
        value = dict(value)
        # Compatibility with the unshipped range-based prototype.
        value.pop("noise_span_w", None)
        value.pop("recurrent_span_w", None)
        if value.get("learning_method") == "recurrent-noise-v2":
            value["learning_method"] = "repeated-jump-20s-v3"
        model = cls(**value)
        if model.algorithm not in {ALGORITHM_VERSION, "background-dual-window-v1"}:
            raise ValueError("本底算法版本不匹配")
        if model.quantization_source not in {"manual", "observed-grid-v1"}:
            raise ValueError("本底步进来源无效")
        if model.learning_method not in {"stationary-v1", "repeated-jump-20s-v3"}:
            raise ValueError("本底学习方法无效")
        if not all(isinstance(item, str) and item for item in
                   (model.model_id, model.identity, model.created_at)):
            raise ValueError("本底身份不完整")
        for name in ("quantization_w", "center_w", "noise_sigma_w", "shift_guard_w",
                     "slope_guard_w_s", "duration_s", "sample_count", "recurrent_jump_w"):
            number = getattr(model, name)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                raise ValueError("本底指标无效")
            if name != "center_w" and number < 0:
                raise ValueError("本底指标不得为负")
        if model.quantization_w <= 0 or model.duration_s + 1e-9 < LEARNING_DURATION_S or model.sample_count < 10:
            raise ValueError("本底数据不足")
        if model.shift_guard_w < model.quantization_w or model.slope_guard_w_s < model.quantization_w / LONG_WINDOW_S:
            raise ValueError("本底量化保护量不完整")
        return model


def estimate_observed_step(samples: list[tuple[float, float]]) -> float:
    """Find the dominant jump repeated at least twice in each direction."""
    changes = sorted((abs(b[1] - a[1]), b[1] > a[1])
                     for a, b in zip(samples, samples[1:]) if b[1] != a[1])
    groups: list[list[float | int]] = []
    for size, rising in changes:
        if not groups or abs(size - groups[-1][0]) > max(1e-12, size * 1e-6):
            groups.append([size, 0, 0])
        groups[-1][1 if rising else 2] += 1
    candidates = [g for g in groups if min(g[1], g[2]) >= 2
                  and g[1] + g[2] >= len(changes) / 2]
    if not candidates:
        raise ValueError("20 秒内未观察到可靠的反复跳动量，请保持 0 A 后重试")
    return float(max(candidates, key=lambda g: g[1] + g[2])[0])


def learn_background(samples: list[tuple[float, float]], *, identity: str,
                     model_id: str, created_at: str, policy: NoiseLearningSettings) -> BackgroundModel:
    if not samples or samples[-1][0] - samples[0][0] + 1e-9 < LEARNING_DURATION_S:
        raise ValueError("本底采集至少需要 20 秒")
    if any(not math.isfinite(t) or not math.isfinite(p) for t, p in samples):
        raise ValueError("本底包含无效读数")
    if any(not 0 < b[0] - a[0] <= policy.gap_s for a, b in zip(samples, samples[1:])):
        raise ValueError("本底采样中断或时间戳重复，请重新学习")
    source = "manual"
    if policy.quantization_w == 0:
        policy = replace(policy, quantization_w=estimate_observed_step(samples))
        source = "observed-grid-v1"
    duration = samples[-1][0] - samples[0][0]
    # Only the repeated jump is learned. No trajectory, range or noise fit.
    powers = [p for _, p in samples]
    step = policy.quantization_w
    epsilon = max(1e-12, step * 1e-6)
    changes = [b - a for a, b in zip(powers, powers[1:])]
    upward = sum(abs(change - step) <= epsilon for change in changes)
    downward = sum(abs(change + step) <= epsilon for change in changes)
    return BackgroundModel(
        model_id, identity, created_at, step, median(powers), step / 2,
        step, step / LONG_WINDOW_S, len(samples), duration,
        quantization_source=source,
        recurrent_jump_w=step if min(upward, downward) >= 2 else 0.0,
        learning_method="repeated-jump-20s-v3",
    )
