"""Probe-bound background lifecycle fed exclusively by the existing reader."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any

from combined_test.power_stability import (
    ALGORITHM_VERSION, LEARNING_DURATION_S, LONG_WINDOW_S, BackgroundModel,
    PowerWindow, StabilityDecision, StabilityPolicy, evaluate_stability,
    learn_background, verify_background,
)


IDENTITY_KEYS = (
    "powerMeterDriverKind", "powerMeterSerialNumber", "softwareGain",
    "powerMeterWavelengthNm", "powerMeterProfileId", "powerMeterProfileVersion",
    "powerMeterIntervalMs", "powerStabilityQuantizationW", "powerStabilityInstrumentSettings",
)
POLICY_KEYS = (
    "powerStabilityMode", "powerStabilityAbsoluteDriftW", "powerStabilityRelativeDriftPercent",
    "powerStabilityReferenceS", "powerStabilityExtraNoisePercent", "powerStabilityResponseS",
    "powerStabilityParametersConfirmed", "powerStabilitySettleS",
    "powerStabilityCurrentToleranceA",
    "powerSupplyKind",
    "powerStabilityTolerancePreset",
    "powerStabilityResponseConfirmationRevision",
)
RESPONSE_KEYS = (
    "powerStabilityResponseS", "powerStabilitySettleS", "powerStabilityCurrentToleranceA",
    "powerSupplyKind",
)
STANDARD_TOLERANCE = {
    "absolute_drift_w": 0.0, "relative_drift_percent": 0.2,
    "reference_s": 20.0, "extra_noise_percent": 0.0,
}


def tolerance_policy(config: dict) -> dict[str, float]:
    """Resolve acceptance limits without overwriting the saved custom preset."""
    preset = config.get("powerStabilityTolerancePreset", "standard")
    if preset == "standard":
        return dict(STANDARD_TOLERANCE)
    if preset != "custom":
        raise ValueError("未知功率判稳容差预设")
    return {
        "absolute_drift_w": float(config["powerStabilityAbsoluteDriftW"]),
        "relative_drift_percent": float(config["powerStabilityRelativeDriftPercent"]),
        "reference_s": float(config["powerStabilityReferenceS"]),
        "extra_noise_percent": float(config["powerStabilityExtraNoisePercent"]),
    }


class PowerStabilityRuntime:
    def __init__(self, station: Any) -> None:
        self.station = station
        self.model: BackgroundModel | None = None
        self.frozen_model: BackgroundModel | None = None
        self.job: str | None = None
        self.job_epoch = 0
        self.samples: list[tuple[float, float]] = []
        self.job_connection: tuple | None = None
        self.connection: tuple | None = None
        self.instrument_token = ""
        self.verified_connection: tuple | None = None
        self.verified_at: float | None = None
        self.last_received: float | None = None
        self.last_sample: float | None = None
        self.window = PowerWindow(1.0)
        self.expected_generation: int | None = None
        self.accept_after = math.inf
        self.state = "尚未学习本底"
        self.decision = StabilityDecision(False, self.state)
        self.last_journal_at = -math.inf
        self.trace_revision = 0
        self.failure_revision = 0
        self._load_model()

    def identity(self, config: dict | None = None) -> str:
        config = self.station.configuration if config is None else config
        value = {key: config.get(key) for key in IDENTITY_KEYS}
        value["algorithm"] = ALGORITHM_VERSION
        value["instrumentToken"] = self.instrument_token
        zero_versions = self.station.backend.settings.get("runtime/powerBackgroundZeroVersions", {})
        value["zeroVersion"] = zero_versions.get(self._probe_key(config), 0) if isinstance(zero_versions, dict) else 0
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _probe_key(config: dict) -> str:
        return f"{config.get('powerMeterDriverKind', '')}:{config.get('powerMeterSerialNumber', '')}"

    def before_zero_command(self) -> None:
        """Durably retire old models before a possibly partially applied zero command."""
        versions = self.station.backend.settings.get("runtime/powerBackgroundZeroVersions", {})
        if not isinstance(versions, dict):
            versions = {}
        key = self._probe_key(self.station.configuration)
        versions[key] = int(versions.get(key, 0)) + 1
        self.station.backend.settings.set("runtime/powerBackgroundZeroVersions", versions)
        self.invalidate("已开始调零操作，旧本底失效，请重新学习")
        self.model = None

    def policy(self) -> StabilityPolicy:
        c = self.station.configuration
        quantization = float(c["powerStabilityQuantizationW"])
        if quantization == 0 and self.model is not None and self.model.identity == self.identity():
            quantization = self.model.quantization_w
        return StabilityPolicy(
            quantization_w=quantization,
            **tolerance_policy(c),
            response_s=float(c["powerStabilityResponseS"]),
            parameters_confirmed=self.response_parameters_confirmed(),
            gap_s=min(5.0, max(1.0, float(c["powerMeterIntervalMs"]) * 0.003)),
        )

    def confirmation_signature(self, config: dict | None = None) -> str:
        config = self.station.configuration if config is None else config
        values = {key: config.get(key) for key in RESPONSE_KEYS}
        values["identity"] = self.identity(config)
        values["version"] = "device-response-v2"
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def _legacy_confirmation_signature(self, config: dict) -> str:
        values = {key: config.get(key) for key in POLICY_KEYS
                  if key not in {"powerStabilityMode", "powerStabilityParametersConfirmed",
                                 "powerStabilityTolerancePreset", "powerStabilityResponseConfirmationRevision"}}
        values["identity"] = self.identity(config)
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def response_parameters_confirmed(self, config: dict | None = None) -> bool:
        config = self.station.configuration if config is None else config
        if not config["powerStabilityParametersConfirmed"] or any(
            float(config[key]) <= 0 for key in RESPONSE_KEYS if key != "powerSupplyKind"
        ):
            return False
        signature = config.get("powerStabilityConfirmedSignature")
        return signature in (self.confirmation_signature(config), self._legacy_confirmation_signature(config))

    @property
    def mode(self) -> str:
        return str(self.station.configuration["powerStabilityMode"])

    @property
    def controls(self) -> bool:
        return self.mode == "dual" and not self.station._aging_test_selected()

    def _load_model(self) -> None:
        saved = self.station.backend.settings.get("runtime/powerBackgrounds", {})
        raw = saved.get(self.identity()) if isinstance(saved, dict) else None
        self.model = None
        if isinstance(raw, dict):
            try:
                self.model = BackgroundModel.from_dict(raw["model"])
            except (KeyError, TypeError, ValueError):
                self.state = "已保存本底无效，请重新学习"
            else:
                self.state = "本底已保存，连接恢复后请复核 20 秒；通过后可连续复用"

    def invalidate(self, reason: str) -> None:
        self.verified_at = None
        self.verified_connection = None
        self.window = PowerWindow(self.policy().gap_s)
        self.decision = StabilityDecision(False, reason)
        self.state = reason
        self.job_epoch += 1
        self.job = None
        self.samples = []

    def configure(self, old: dict, new: dict) -> None:
        if self.identity(old) != self.identity(new):
            self.invalidate("判稳配置已改变，请重新复核本底")
            self._load_model()
        elif any(old.get(k) != new.get(k) for k in POLICY_KEYS):
            # Changing a decision policy does not alter measured background.
            # Parameter confirmation remains bound separately to the new policy.
            self.window = PowerWindow(self.policy().gap_s)
            self.decision = StabilityDecision(False, "判稳策略已更新，等待新窗口")

    def start_background(self, params: dict, *, verify: bool = False) -> None:
        st = self.station
        with st._lock:
            if not st.manual_controls_allowed() or st.manual_current_request_a is not None:
                raise ValueError("请先结束测试并将输出电流归零")
            if self.job is not None:
                raise ValueError("本底任务已在运行")
            if params.get("confirmedDark") is not True:
                raise ValueError("请确认 0 A、无光输出且探头已冷却")
            if abs(float(st.active_current_a or 0)) > 0.005:
                raise ValueError("学习或复核本底前必须先将输出电流归零")
            if (st.record_store.current_session is not None
                    and st.record_store.current_session.mode == "manual"
                    and st.record_store.unsaved_records()):
                raise ValueError("请先保存未落盘的手动测试点")
            if st.power_meter_reader is None or not st.power_meter_reader.is_ready:
                raise ValueError("请先启动功率采集")
            if self.connection is None or st.latest_power is None:
                raise ValueError("请等待首个有效功率读数后再开始本底任务")
            if st.power_supply_kind in {"tdk", "ru"} and st.power_supply_controller is not None:
                if (not st._refresh_live_power_supply_current(force=True)
                        or st.latest_power_supply_output_current_a is None
                        or abs(st.latest_power_supply_output_current_a) > 0.005):
                    raise ValueError("电源实际电流尚未确认归零")
            if not str(st.configuration["powerMeterSerialNumber"]).strip():
                raise ValueError("本底必须绑定已验证的探头 SN")
            policy = self.policy()
            if verify and (self.model is None or self.model.identity != self.identity()):
                raise ValueError("没有匹配本底，请先学习本底")
            self.invalidate("正在复核本底" if verify else "正在学习 0 A 本底")
            self.job = "verify" if verify else "learn"
            self.job_connection = self.connection
            self.accept_after = time.monotonic()
            self.window = PowerWindow(policy.gap_s)

    def cancel_background(self) -> None:
        with self.station._lock:
            self.invalidate("本底任务已取消；已保存模型保留，使用前须复核")

    def blocker(self) -> str | None:
        if self.job:
            return "本底任务进行中，请完成或取消后再开始测试"
        if not self.controls:
            return None
        return self.control_blocker()

    def control_blocker(self) -> str | None:
        """Expose setup readiness even while the legacy algorithm still controls."""
        if not self.response_parameters_confirmed():
            return "设备响应参数尚未确认，请在一次确认中保存响应时间、电流建立时间和回读容差"
        policy = self.policy()
        if max(policy.absolute_drift_w, policy.relative_drift_percent) <= 0:
            return "请选择有效的测试容差预设"
        if self.model is None or self.model.identity != self.identity():
            return "请学习 0 A 本底，自动获取步进与噪声保护量"
        if (self.verified_at is None or self.verified_connection is None
                or self.verified_connection != self.connection):
            return "连接或本底状态已改变，请在 0 A 复核 20 秒；通过后可连续复用"
        if self.last_received is None or not 0 <= time.monotonic() - self.last_received <= self.policy().gap_s:
            return "功率采样中断，请恢复采集后在 0 A 复核本底"
        return None

    def freeze(self) -> None:
        error = self.blocker()
        if error:
            raise ValueError(error)
        self.frozen_model = self.model
        # Freezing a run must not consume trust in an unchanged connection.
        # Reconnects, configuration changes and acquisition failures invalidate it.
        self.last_journal_at = -math.inf

    def begin_point(self, generation: int, *, response_start: float | None = None) -> None:
        self.expected_generation = generation
        self.window = PowerWindow(self.policy().gap_s)
        self.accept_after = (time.monotonic() if response_start is None else response_start) + self.policy().response_s
        self.decision = StabilityDecision(False, "等待探头响应")

    def _finish_learning(self, epoch: int, samples: list, identity: str, policy: StabilityPolicy) -> None:
        try:
            model = learn_background(samples, identity=identity, model_id=str(uuid.uuid4()),
                                     created_at=datetime.now(timezone.utc).isoformat(), policy=policy)
            with self.station._lock:
                if epoch != self.job_epoch or identity != self.identity():
                    return
                saved = self.station.backend.settings.get("runtime/powerBackgrounds", {})
                if not isinstance(saved, dict):
                    saved = {}
                saved[identity] = {"model": asdict(model), "samples": samples,
                                   "detectedSettings": self.detected_settings(),
                                   "instrument": {key: self.station.configuration.get(key) for key in IDENTITY_KEYS}}
                self.station.backend.settings.set("runtime/powerBackgrounds", saved)
                self.model = model
                self.job = None
                self.samples = []
                if self.connection is not None and self.job_connection == self.connection:
                    self.verified_connection = self.connection
                    self.verified_at = self.last_received
                    self.state = "本底学习完成，当前连接可直接复用；重连或异常后需复核"
                else:
                    self.state = "本底已保存，使用前请在当前连接复核 20 秒"
        except Exception as exc:
            with self.station._lock:
                if epoch == self.job_epoch and identity == self.identity():
                    # Retain the last failed trace for analysis, without qualifying it.
                    attempts = self.station.backend.settings.get("runtime/powerBackgroundLearningAttempts", {})
                    if not isinstance(attempts, dict):
                        attempts = {}
                    attempts[identity] = {"samples": samples, "error": str(exc),
                                          "detectedSettings": self.detected_settings()}
                    try:
                        self.station.backend.settings.set("runtime/powerBackgroundLearningAttempts", attempts)
                    except Exception as save_error:
                        self.invalidate(f"本底学习失败：{exc}；诊断数据保存失败：{save_error}")
                        return
                    self.invalidate(f"本底学习失败：{exc}")

    def observe(self, reading: Any, received_at: float) -> Any:
        st = self.station
        connection = (id(st.power_meter_reader), reading.connection_generation, reading.instrument_configuration_token)
        if connection != self.connection:
            self.connection = connection
            self.instrument_token = reading.instrument_configuration_token
            self.invalidate("功率计连接或量程已改变，请重新复核本底")
            self._load_model()
        policy = self.policy()
        gap = self.last_received is not None and received_at - self.last_received > policy.gap_s
        self.last_received = received_at
        # Only a completed hardware transaction can enter this method. Identical
        # values from different transactions remain valid quantized observations.
        if self.last_sample is not None and reading.elapsed_s == self.last_sample:
            self.failure_revision += 1
            self.decision = StabilityDecision(False, "重复采样事务，等待新样本")
            return replace(reading, stable=False) if self.controls else reading
        self.last_sample = reading.elapsed_s
        if gap or not math.isfinite(reading.power_w) or not math.isfinite(reading.elapsed_s):
            self.invalidate("功率采样中断或无效，请重新建立窗口和本底复核")
        if self.job in {"learn", "verify"}:
            if (connection != self.job_connection or abs(float(st.active_current_a or 0)) > 0.005
                    or not st.manual_controls_allowed()):
                self.invalidate("本底条件改变，任务已取消")
            elif reading.transaction_started_monotonic_s >= self.accept_after:
                if self.samples and not 0 < reading.elapsed_s - self.samples[-1][0] <= policy.gap_s:
                    self.invalidate("本底采样中断，请重新开始")
                else:
                    self.samples.append((reading.elapsed_s, reading.power_w))
                    metrics = self.window.add(reading.elapsed_s, reading.power_w)
                    duration = self.samples[-1][0] - self.samples[0][0]
                    required = LEARNING_DURATION_S if self.job == "learn" else LONG_WINDOW_S
                    self.state = f"{'学习' if self.job == 'learn' else '复核'}本底 {min(duration, required):.1f} / {required:g} 秒"
                    if len(self.samples) > 200_000:
                        self.invalidate("本底样本过多，请检查采样设置")
                    elif duration >= required:
                        if self.job == "verify":
                            if verify_background(metrics, self.model):
                                self.verified_connection = connection
                                self.verified_at = received_at
                                self.job = None
                                self.samples = []
                                self.state = "本底复核通过，当前连接可连续复用；重连或异常后需复核"
                            else:
                                self.invalidate("本底复核未通过，请检查冷却、零点或重新学习")
                        else:
                            self.job = "saving"
                            self.state = "正在校验并保存本底"
                            threading.Thread(target=self._finish_learning,
                                             args=(self.job_epoch, list(self.samples), self.identity(),
                                                   replace(policy, quantization_w=float(st.configuration["powerStabilityQuantizationW"]))),
                                             name="power-background-learning", daemon=True).start()
        active = not st.manual_controls_allowed() and not st._aging_test_selected()
        if not active or self.mode == "legacy":
            return reading
        if reading.stability_generation != self.expected_generation:
            return replace(reading, stable=False) if self.controls else reading
        # Elapsed time in the source is the read's START, before any blocking IO.
        # The monotonic transaction boundary excludes in-flight, pre-delay reads.
        if reading.transaction_started_monotonic_s < self.accept_after:
            self.decision = StabilityDecision(False, "等待探头响应")
        elif self.connection != self.verified_connection:
            self.decision = StabilityDecision(False, "连接或采样已中断，必须结束测试并在 0 A 复核本底", code="background_invalid")
        else:
            metrics = self.window.add(reading.elapsed_s, reading.power_w)
            self.decision = evaluate_stability(metrics, self.frozen_model, policy, identity=self.identity())
        self.trace_revision += 1
        if not self.decision.stable:
            self.failure_revision += 1
        if not self.controls:
            return reading
        result = self.decision
        metrics = result.metrics
        return replace(reading, stable=result.stable,
                       stable_window_s=0.0 if metrics is None else metrics.covered_s,
                       stable_duration_s=0.0 if metrics is None else metrics.covered_s,
                       stable_median_w=math.nan if metrics is None else metrics.median_w,
                       stable_span_w=math.nan if metrics is None else metrics.span_w)

    def detected_settings(self) -> dict:
        # Reuse the driver's existing range token. This never queries hardware.
        full_scale = None
        parts = self.instrument_token.split(":")
        if len(parts) == 4 and parts[0] == "laserpoint":
            try:
                value = float(parts[2])
                if math.isfinite(value) and value > 0:
                    full_scale = value
            except ValueError:
                pass
        c = self.station.configuration
        return {"fullScaleW": full_scale, "filter": "unavailable",
                "softwareGain": float(c["softwareGain"]),
                "intervalMs": float(c["powerMeterIntervalMs"])}

    def snapshot(self) -> dict:
        return {"mode": self.mode, "algorithm": ALGORITHM_VERSION,
                "state": self.state, "job": self.job,
                "parametersConfirmed": self.policy().ready,
                "responseParametersConfirmed": self.response_parameters_confirmed(),
                "controlBlocker": self.control_blocker(),
                "tolerancePreset": self.station.configuration["powerStabilityTolerancePreset"],
                "tolerance": tolerance_policy(self.station.configuration),
                "detectedSettings": self.detected_settings(),
                "model": None if self.model is None else asdict(self.model),
                "decision": asdict(self.decision)}

    def session_metadata(self) -> dict:
        learning = None
        if self.frozen_model is not None:
            saved = self.station.backend.settings.get("runtime/powerBackgrounds", {})
            candidate = saved.get(self.frozen_model.identity) if isinstance(saved, dict) else None
            candidate_model = candidate.get("model") if isinstance(candidate, dict) else None
            if isinstance(candidate_model, dict) and candidate_model.get("model_id") == self.frozen_model.model_id:
                learning = candidate
        return {"mode": self.mode, "algorithm": ALGORITHM_VERSION,
                "legacyAlgorithm": "relative-or-span-v1",
                "tolerancePreset": self.station.configuration["powerStabilityTolerancePreset"],
                "policy": asdict(self.policy()),
                "identity": {key: self.station.configuration.get(key) for key in IDENTITY_KEYS},
                "instrumentToken": self.instrument_token,
                "backgroundLearning": learning,
                "background": None if self.frozen_model is None else asdict(self.frozen_model)}

    def journal_diagnostic(self, current_a: float, *, force: bool = False) -> None:
        if self.mode == "legacy" or self.station._aging_test_selected():
            return
        now = time.monotonic()
        if force or now - self.last_journal_at >= 1.0:
            with self.station._lock:
                diagnostic = self.snapshot()
                reading = self.station.latest_power
                diagnostic["rawPowerW"] = None if reading is None else reading.power_w
                diagnostic["sampleS"] = None if reading is None else reading.elapsed_s
                diagnostic["connection"] = None if reading is None else reading.connection_generation
            self.station.record_store.record_stability_diagnostic(current_a, diagnostic)
            self.last_journal_at = now
