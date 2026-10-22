"""Replayable background learning and dual-window power stability, in W and s.

The empirical guards are engineering bounds, not confidence intervals. Neither
quantization nor correlated noise is divided by sqrt(sample count).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from statistics import median


ALGORITHM_VERSION = "background-dual-window-v1"
SHORT_WINDOW_S = 5.0
LONG_WINDOW_S = 20.0
LEARNING_DURATION_S = 180.0


@dataclass(frozen=True)
class StabilityPolicy:
    quantization_w: float
    absolute_drift_w: float = 0.0
    relative_drift_percent: float = 0.0
    reference_s: float = 20.0
    extra_noise_percent: float = 0.0
    response_s: float = 0.0
    parameters_confirmed: bool = False
    gap_s: float = 1.0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name == "parameters_confirmed":
                if not isinstance(value, bool):
                    raise ValueError("判稳参数确认必须是布尔值")
                continue
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} 必须是非负有限数值")
        if self.reference_s <= 0 or not 0 < self.gap_s <= SHORT_WINDOW_S:
            raise ValueError("参考时间必须大于零，最大采样间隔不得超过短窗口")

    @property
    def ready(self) -> bool:
        return (self.parameters_confirmed and self.quantization_w > 0
                and self.response_s > 0
                and max(self.absolute_drift_w, self.relative_drift_percent) > 0)


@dataclass(frozen=True)
class WindowMetrics:
    mean_w: float
    median_w: float
    span_w: float
    residual_sigma_w: float
    block_shift_w: float
    slope_w_s: float
    covered_s: float
    sample_count: int


def fitted_line(samples: list[tuple[float, float]]) -> tuple[float, float]:
    center_t = math.fsum(t for t, _ in samples) / len(samples)
    center_p = math.fsum(p for _, p in samples) / len(samples)
    denominator = math.fsum((t - center_t) ** 2 for t, _ in samples)
    slope = (math.fsum((t - center_t) * (p - center_p) for t, p in samples)
             / denominator if denominator else 0.0)
    sigma = math.sqrt(math.fsum((p - center_p - slope * (t - center_t)) ** 2
                               for t, p in samples) / len(samples))
    return slope, sigma


def window_metrics(samples: list[tuple[float, float]], gap_s: float) -> WindowMetrics | None:
    if len(samples) < 10 or samples[-1][0] - samples[0][0] < LONG_WINDOW_S:
        return None
    if any(not math.isfinite(t) or not math.isfinite(p) for t, p in samples):
        return None
    if any(not 0 < b[0] - a[0] <= gap_s for a, b in zip(samples, samples[1:])):
        return None
    end = samples[-1][0]
    # A boundary observation is retained for coverage only. No observation is
    # shared between the four non-overlapping time bins.
    blocks = [[p for t, p in samples
               if end - LONG_WINDOW_S + i * SHORT_WINDOW_S < t
               <= end - LONG_WINDOW_S + (i + 1) * SHORT_WINDOW_S]
              for i in range(4)]
    if any(len(block) < 2 for block in blocks):
        return None
    short = [(t, p) for t, p in samples if t > end - SHORT_WINDOW_S]
    _, sigma = fitted_line(short)
    slope, _ = fitted_line(samples)
    powers = [p for _, p in samples]
    means = [math.fsum(block) / len(block) for block in blocks]
    return WindowMetrics(math.fsum(powers) / len(powers), median(powers),
                         max(powers) - min(powers), sigma, max(means) - min(means),
                         slope, samples[-1][0] - samples[0][0], len(samples))


class PowerWindow:
    """Keep a bounded, continuous window; duplicate transactions never add time."""

    def __init__(self, gap_s: float) -> None:
        self.gap_s = gap_s
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, timestamp: float, power_w: float) -> WindowMetrics | None:
        if not math.isfinite(timestamp) or not math.isfinite(power_w):
            self.samples.clear()
            return None
        if self.samples:
            difference = timestamp - self.samples[-1][0]
            if difference == 0:
                return None
            if difference < 0 or difference > self.gap_s:
                self.samples.clear()
        self.samples.append((timestamp, power_w))
        while len(self.samples) > 1 and self.samples[1][0] <= timestamp - LONG_WINDOW_S:
            self.samples.popleft()
        # Bound memory even for a faulty source that floods timestamps.
        if len(self.samples) > 20_000:
            self.samples.clear()
            return None
        return window_metrics(list(self.samples), self.gap_s)


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

    @classmethod
    def from_dict(cls, value: dict) -> BackgroundModel:
        model = cls(**value)
        if model.algorithm != ALGORITHM_VERSION:
            raise ValueError("本底算法版本不匹配")
        if not all(isinstance(item, str) and item for item in
                   (model.model_id, model.identity, model.created_at)):
            raise ValueError("本底身份不完整")
        for name in ("quantization_w", "center_w", "noise_sigma_w", "shift_guard_w",
                     "slope_guard_w_s", "duration_s", "sample_count"):
            number = getattr(model, name)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                raise ValueError("本底指标无效")
            if name != "center_w" and number < 0:
                raise ValueError("本底指标不得为负")
        if model.quantization_w <= 0 or model.duration_s < LEARNING_DURATION_S or model.sample_count < 10:
            raise ValueError("本底数据不足")
        if model.shift_guard_w < model.quantization_w or model.slope_guard_w_s < model.quantization_w / LONG_WINDOW_S:
            raise ValueError("本底量化保护量不完整")
        return model


def percentile95(values: list[float]) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)]


def learn_background(samples: list[tuple[float, float]], *, identity: str,
                     model_id: str, created_at: str, policy: StabilityPolicy) -> BackgroundModel:
    if policy.quantization_w <= 0:
        raise ValueError("请填写当前探头经增益换算后的量化步进 (W)")
    if not samples or samples[-1][0] - samples[0][0] < LEARNING_DURATION_S:
        raise ValueError("本底采集至少需要 180 秒")
    if any(not math.isfinite(t) or not math.isfinite(p) for t, p in samples):
        raise ValueError("本底包含无效读数")
    if any(not 0 < b[0] - a[0] <= policy.gap_s for a, b in zip(samples, samples[1:])):
        raise ValueError("本底采样中断或时间戳重复，请重新学习")
    duration = samples[-1][0] - samples[0][0]
    slope, _ = fitted_line(samples)
    # A fixed quantization-based gate precedes noise estimation. A cooling
    # trend must never train itself into a larger permissible noise envelope.
    quarter_means = []
    for index in range(4):
        values = [p for t, p in samples
                  if samples[0][0] + duration * index / 4 <= t
                  <= samples[0][0] + duration * (index + 1) / 4]
        quarter_means.append(math.fsum(values) / len(values))
    if (abs(slope) * duration > policy.quantization_w
            or max(quarter_means) - min(quarter_means) > policy.quantization_w):
        raise ValueError("本底尚未稳定：存在持续漂移，请等待探头冷却后重新学习")
    window = PowerWindow(policy.gap_s)
    metrics = []
    last_replay = -math.inf
    for timestamp, power in samples:
        result = window.add(timestamp, power)
        if result is not None and timestamp - last_replay >= SHORT_WINDOW_S:
            metrics.append(result)
            last_replay = timestamp
    if len(metrics) < 20:
        raise ValueError("本底有效窗口不足")
    return BackgroundModel(
        model_id, identity, created_at, policy.quantization_w,
        median(p for _, p in samples),
        max(policy.quantization_w / 2, percentile95([m.residual_sigma_w for m in metrics])),
        max(policy.quantization_w, percentile95([m.block_shift_w for m in metrics])),
        max(policy.quantization_w / LONG_WINDOW_S, percentile95([abs(m.slope_w_s) for m in metrics])),
        len(samples), duration,
    )


@dataclass(frozen=True)
class StabilityDecision:
    stable: bool
    reason: str
    metrics: WindowMetrics | None = None
    tolerance_w: float | None = None
    noise_limit_w: float | None = None
    guarded_shift_w: float | None = None
    guarded_drift_w: float | None = None
    code: str = "waiting"


def evaluate_stability(metrics: WindowMetrics | None, model: BackgroundModel | None,
                       policy: StabilityPolicy, *, identity: str) -> StabilityDecision:
    if model is None or model.identity != identity or model.quantization_w != policy.quantization_w:
        return StabilityDecision(False, "本底无效，请学习或重新复核", metrics, code="background_invalid")
    if metrics is None:
        return StabilityDecision(False, "正在建立 20 秒连续功率窗口")
    tolerance = max(policy.absolute_drift_w, abs(metrics.mean_w) * policy.relative_drift_percent / 100)
    noise_limit = model.noise_sigma_w + abs(metrics.mean_w) * policy.extra_noise_percent / 200
    shift = metrics.block_shift_w + model.shift_guard_w
    drift = (abs(metrics.slope_w_s) + model.slope_guard_w_s) * policy.reference_s
    args = (metrics, tolerance, noise_limit, shift, drift)
    if not policy.ready:
        return StabilityDecision(False, "响应时间与漂移容差待确认（仅诊断）", *args, code="unconfigured")
    if max(model.shift_guard_w, model.slope_guard_w_s * policy.reference_s) > tolerance:
        return StabilityDecision(False, "测量能力不足：本底保护量超过目标容差", *args, code="capability_insufficient")
    if metrics.residual_sigma_w > noise_limit + 1e-12:
        return StabilityDecision(False, "波动超出本底范围", *args, code="noise_exceeded")
    if max(shift, drift) > tolerance + 1e-12:
        direction = "上升" if metrics.slope_w_s > 0 else "下降"
        return StabilityDecision(False, f"功率仍在变化（趋势{direction}或窗口均值移动超限）", *args, code="drift_exceeded")
    return StabilityDecision(True, "功率满足本底、噪声及漂移条件", *args, code="stable")


def verify_background(metrics: WindowMetrics | None, model: BackgroundModel) -> bool:
    return bool(metrics and abs(metrics.mean_w - model.center_w) <= model.shift_guard_w
                and metrics.block_shift_w <= model.shift_guard_w + 1e-12
                and abs(metrics.slope_w_s) <= model.slope_guard_w_s + 1e-12
                and metrics.residual_sigma_w <= model.noise_sigma_w + 1e-12)
