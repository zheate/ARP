"""One 20-second jump measurement per probe SN; all meters share deviation and drift limits."""
from __future__ import annotations

import copy
import math
import threading
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any

from combined_test.power_stability import BackgroundModel, NoiseLearningSettings, learn_background


class PowerStabilityRuntime:
    def __init__(self, station: Any) -> None:
        self.station = station
        self.model: BackgroundModel | None = None
        self.job: str | None = None
        self.job_epoch = 0
        self.samples: list[tuple[float, float]] = []
        self.state = "尚未学习跳动量"
        self.accept_after = math.inf
        self.learning_sn = ""
        self.learning_reader = None
        self.configure()

    def identity(self) -> str:
        return str(self.station.configuration.get("powerMeterSerialNumber", "")).strip().upper()

    def policy(self) -> NoiseLearningSettings:
        return NoiseLearningSettings(0, gap_s=max(1.0, float(self.station.configuration["powerMeterIntervalMs"]) * 0.003))

    def noise_jump_w(self) -> float:
        return self.model.recurrent_jump_w if self.model and self.model.identity == self.identity() else 0.0

    def configure(self) -> None:
        sn = self.identity()
        saved = self.station.backend.settings.get("runtime/powerBackgrounds", {})
        record = saved.get(sn) if isinstance(saved, dict) and sn else None
        # Reuse an already learned profile from the superseded configuration-keyed UI.
        if record is None and sn and isinstance(saved, dict):
            for candidate in saved.values():
                if not isinstance(candidate, dict):
                    continue
                instrument = candidate.get("instrument", {})
                if not isinstance(instrument, dict):
                    continue
                if str(instrument.get("powerMeterSerialNumber", "")).strip().upper() != sn:
                    continue
                try:
                    learned_gain = float(instrument["softwareGain"])
                    model = learn_background(candidate["samples"], identity=sn, model_id=str(uuid.uuid4()),
                                             created_at=datetime.now(timezone.utc).isoformat(), policy=self.policy())
                    record = {**candidate, "model": asdict(model), "rawJumpW": model.recurrent_jump_w / learned_gain,
                              "learnedGain": learned_gain}
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    continue
                saved[sn] = record
                self.station.backend.settings.set("runtime/powerBackgrounds", saved)
                break
        self.model = None
        if isinstance(record, dict):
            try:
                stored = BackgroundModel.from_dict(record["model"])
                jump = float(record["rawJumpW"]) * float(self.station.configuration["softwareGain"])
                if not math.isfinite(jump) or jump <= 0 or stored.identity != sn:
                    raise ValueError("invalid jump profile")
                self.model = replace(stored, quantization_w=jump, recurrent_jump_w=jump,
                                     shift_guard_w=jump, slope_guard_w_s=jump / 20)
            except (KeyError, TypeError, ValueError):
                pass
        self.state = f"SN {sn}：已记住反复跳动量" if self.model else "尚未学习跳动量"
        reader = getattr(self.station, "power_meter_reader", None)
        if reader is not None:
            reader.update_noise_jump(self.noise_jump_w())

    def start_background(self, params: dict) -> None:
        st = self.station
        with st._lock:
            if self.job:
                raise ValueError("跳动量学习已在进行")
            if not st.manual_controls_allowed() or st.manual_current_request_a is not None:
                raise ValueError("请先结束测试并将电流归零")
            if params.get("confirmedDark") is not True or abs(float(st.active_current_a or 0)) > 0.005:
                raise ValueError("请先确认 0 A、无激光输出")
            if st.record_store.unsaved_records():
                raise ValueError("请先保存未落盘的测试点")
            if st.power_meter_reader is None or not st.power_meter_reader.is_ready or st.latest_power is None:
                raise ValueError("请先启动功率采集并等待读数")
            if not self.identity():
                raise ValueError("请先识别功率计探头 SN")
            if st.power_supply_kind in {"tdk", "ru"} and st.power_supply_controller is not None:
                if (not st._refresh_live_power_supply_current(force=True)
                        or st.latest_power_supply_output_current_a is None
                        or abs(st.latest_power_supply_output_current_a) > 0.005):
                    raise ValueError("电源实际电流尚未确认归零")
            self.job_epoch += 1
            self.job = "learn"
            self.samples = []
            self.learning_sn = self.identity()
            self.learning_reader = st.power_meter_reader
            self.accept_after = time.monotonic()
            self.state = "学习跳动量 0.0 / 20 秒"

    def cancel_background(self) -> None:
        with self.station._lock:
            if self.job is not None:
                self.job_epoch += 1
                self.job = None
                self.samples = []
                self.state = "学习已取消；已记住的跳动量保留"

    def observe(self, reading: Any, received_at: float) -> None:
        if self.job != "learn":
            return
        st = self.station
        if (self.identity() != self.learning_sn or st.power_meter_reader is not self.learning_reader
                or abs(float(st.active_current_a or 0)) > 0.005 or not st.manual_controls_allowed()):
            self.cancel_background()
            return
        if reading.transaction_started_monotonic_s < self.accept_after:
            return
        gap = max(1.0, float(st.configuration["powerMeterIntervalMs"]) * 0.003)
        if (not math.isfinite(reading.elapsed_s) or not math.isfinite(reading.power_w)
                or (self.samples and not 0 < reading.elapsed_s - self.samples[-1][0] <= gap)):
            self.cancel_background()
            self.state = "学习采样中断，请重新学习 20 秒"
            return
        self.samples.append((reading.elapsed_s, reading.power_w))
        duration = self.samples[-1][0] - self.samples[0][0]
        self.state = f"学习跳动量 {min(duration, 20):.1f} / 20 秒"
        if duration >= 20:
            self.job = "saving"
            self.state = "正在记住跳动量"
            threading.Thread(target=self._finish_learning,
                             args=(self.job_epoch, list(self.samples), self.learning_sn, self.policy()),
                             name="power-meter-noise-learning", daemon=True).start()

    def _finish_learning(self, epoch: int, samples: list, identity: str, policy: NoiseLearningSettings) -> None:
        try:
            model = learn_background(samples, identity=identity, model_id=str(uuid.uuid4()),
                                     created_at=datetime.now(timezone.utc).isoformat(), policy=policy)
            gain = float(self.station.configuration["softwareGain"])
            if model.recurrent_jump_w <= 0:
                raise ValueError("未观察到可靠的反复跳动量")
            with self.station._lock:
                if epoch != self.job_epoch or identity != self.identity():
                    return
                saved = self.station.backend.settings.get("runtime/powerBackgrounds", {})
                saved = dict(saved) if isinstance(saved, dict) else {}
                saved[identity] = {"model": asdict(model), "samples": samples,
                                   "rawJumpW": model.recurrent_jump_w / gain, "learnedGain": gain,
                                   "instrument": {"powerMeterSerialNumber": identity, "softwareGain": gain}}
                self.station.backend.settings.set("runtime/powerBackgrounds", saved)
                self.job = None
                self.samples = []
                self.configure()
        except Exception as exc:
            with self.station._lock:
                if epoch == self.job_epoch:
                    self.job = None
                    self.samples = []
                    self.state = f"跳动量学习失败：{exc}"

    def snapshot(self) -> dict:
        return {"mode": "legacy", "algorithm": "relative-sigma-drift-with-sn-noise-v1", "state": self.state, "job": self.job,
                "parametersConfirmed": True, "responseParametersConfirmed": True,
                "model": asdict(self.model) if self.model else None,
                "decision": {"stable": False, "reason": "统一使用标准差与斜率判稳", "metrics": None,
                             "tolerance_w": None, "noise_limit_w": None,
                             "guarded_shift_w": None, "guarded_drift_w": None}}

    def session_metadata(self) -> dict:
        saved = self.station.backend.settings.get("runtime/powerBackgrounds", {})
        return {"mode": "legacy", "algorithm": "relative-sigma-drift-with-sn-noise-v1",
                "background": asdict(self.model) if self.model else None,
                "backgroundLearning": copy.deepcopy(saved.get(self.identity())) if isinstance(saved, dict) else None}
