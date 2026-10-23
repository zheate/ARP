"""Capture one finished run briefly under lock, then compress on the file worker."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from combined_test.automation import AutomaticTestState
from combined_test.diagnostics import export_diagnostic_bundle
from combined_test.record_store import SessionStatus


def export_current_session(station: Any, params: dict) -> dict:
    if not station._diagnostic_export_lock.acquire(blocking=False):
        raise ValueError("诊断包正在导出，请等待完成")
    try:
        with station._lock:
            session = station.record_store.current_session
            if (station.automatic.state != AutomaticTestState.COMPLETED or session is None
                    or session.mode != "automatic" or session.status == SessionStatus.RUNNING
                    or (station._automatic_thread is not None and station._automatic_thread.is_alive())
                    or station.export_status in {"recording", "exporting"}):
                raise ValueError("请等待本轮测试结束及 Excel 写入完成后再导出诊断包")
            if not isinstance(params.get("sessionId"), str) or params["sessionId"] != session.session_id:
                raise ValueError("当前测试轮次已改变，请从本轮结果区重新导出诊断包")
            journal = station.record_store.journal_path
            if journal is None or not journal.is_file():
                raise ValueError("本轮测试日志不存在，无法导出诊断包")
            # JSON-safe copy: paths become strings; Enum values already derive from str.
            payload = json.loads(json.dumps(asdict(session), ensure_ascii=False, default=str, allow_nan=False))
            background = copy.deepcopy(payload["settings"].get("powerStability", {}))
            model = background.get("background")
            # Older sessions may lack the embedded trace. Accept only the exact model ID.
            if model and not background.get("backgroundLearning"):
                saved = station.backend.settings.get("runtime/powerBackgrounds", {})
                candidate = saved.get(model.get("identity")) if isinstance(saved, dict) else None
                candidate_model = candidate.get("model") if isinstance(candidate, dict) else None
                if isinstance(candidate_model, dict) and candidate_model.get("model_id") == model.get("model_id"):
                    background["backgroundLearning"] = candidate
            excel = Path(station.export_path) if station.export_status == "completed" and station.export_path else None
            samples = Path(station.automatic_sample_path) if station.automatic_sample_path else None
            output_dir = station.backend.settings.path.parent / "diagnostics"
            export_status, export_error = station.export_status, station.export_error
        return export_diagnostic_bundle(session=payload, background=background, journal_path=journal,
                                        excel_path=excel, sample_path=samples, output_dir=output_dir,
                                        export_status=export_status, export_error=export_error)
    finally:
        station._diagnostic_export_lock.release()
