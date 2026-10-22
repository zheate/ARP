"""Offline CSV replay; imports no device drivers and never opens hardware."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from combined_test.power_stability import PowerWindow, StabilityPolicy, evaluate_stability, learn_background


def read_samples(path: Path) -> list[tuple[float, float]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"elapsed_s", "power_w"}.issubset(reader.fieldnames or ()):
            raise ValueError(f"{path}: CSV 需要 elapsed_s,power_w 列；功率必须已换算为 W 并应用同一软件增益")
        return [(float(row["elapsed_s"]), float(row["power_w"])) for row in reader]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", type=Path, required=True, help="至少 180 秒的 0 A CSV")
    parser.add_argument("--samples", type=Path, required=True, help="同一探头、量程、滤波和增益的功率 CSV")
    parser.add_argument("--quantization-w", type=float, default=0, help="已含增益的步进 W；0（默认）从本底估计，仅诊断")
    parser.add_argument("--absolute-drift-w", type=float, default=0)
    parser.add_argument("--relative-drift-percent", type=float, default=0)
    parser.add_argument("--reference-s", type=float, default=20)
    parser.add_argument("--extra-noise-percent", type=float, default=0)
    parser.add_argument("--response-s", type=float, default=0)
    parser.add_argument("--gap-s", type=float, default=1)
    parser.add_argument("--confirmed", action="store_true", help="仅在响应实测与测试规范已确认后设置")
    parser.add_argument("--output", type=Path, help="新建 JSONL；已有文件不会覆盖")
    args = parser.parse_args()
    policy = StabilityPolicy(args.quantization_w, args.absolute_drift_w, args.relative_drift_percent,
                             args.reference_s, args.extra_noise_percent, args.response_s, args.confirmed, args.gap_s)
    model = learn_background(read_samples(args.background), identity="offline-same-configuration",
                            model_id="offline-replay", created_at=datetime.now(timezone.utc).isoformat(), policy=policy)
    if args.quantization_w == 0:
        policy = replace(policy, quantization_w=model.quantization_w, parameters_confirmed=False)
    window = PowerWindow(policy.gap_s)
    samples = read_samples(args.samples)
    started = samples[0][0] if samples else 0
    stream = args.output.open("x", encoding="utf-8") if args.output else sys.stdout
    try:
        stream.write(json.dumps({"background": asdict(model), "policy": asdict(policy)}, ensure_ascii=False) + "\n")
        for timestamp, power in samples:
            if timestamp < started + policy.response_s:
                continue
            decision = evaluate_stability(window.add(timestamp, power), model, policy, identity=model.identity)
            stream.write(json.dumps({"elapsed_s": timestamp, "power_w": power, "decision": asdict(decision)}, ensure_ascii=False, allow_nan=False) + "\n")
    finally:
        if args.output:
            stream.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
