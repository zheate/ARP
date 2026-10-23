"""Build a session-scoped diagnostic ZIP without changing source files."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from .excel_export import sanitize_sn


def export_diagnostic_bundle(*, session: dict[str, Any], background: dict[str, Any],
                             journal_path: Path, excel_path: Path | None,
                             sample_path: Path | None, output_dir: Path,
                             export_status: str, export_error: str) -> dict[str, Any]:
    """Keep exact file bytes and a hash inventory; publish only a finished ZIP."""
    with journal_path.open("rb") as source:
        first = json.loads(source.readline())
    if first.get("type") != "session_start" or first.get("session", {}).get("session_id") != session["session_id"]:
        raise ValueError("会话日志与本轮测试不匹配，诊断包未生成")

    warnings = []
    learning = background.get("backgroundLearning")
    samples = learning.get("samples") if isinstance(learning, dict) else None
    if not background.get("background"):
        warnings.append("本轮未使用已学习的本底模型")
    elif not isinstance(samples, list) or not samples:
        warnings.append("本轮本底原始样本缺失；已保留冻结模型，未混入其他学习结果")
    files = [("session.jsonl", journal_path)]
    if excel_path is not None and excel_path.is_file():
        files.append((f"result/{excel_path.name}", excel_path))
    else:
        reason = export_error or {"failed": "生成失败", "completed": "文件不存在"}.get(export_status, "未生成")
        warnings.append(f"本轮 Excel 不可用：{reason}")
    if sample_path is not None and sample_path.is_file():
        files.append((f"result/{sample_path.name}", sample_path))

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{sanitize_sn(session['sn'])[:40]}_{session['session_id']}_diagnostics_{uuid.uuid4().hex[:8]}.zip"
    target = output_dir / filename
    descriptor, temporary_name = tempfile.mkstemp(prefix=".diagnostic-", suffix=".tmp", dir=output_dir)
    os.close(descriptor)
    temporary = Path(temporary_name)
    inventory = []
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            def write_json(name: str, value: Any) -> None:
                data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
                archive.writestr(name, data)
                inventory.append({"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})

            write_json("session.json", session)
            write_json("background.json", background)
            for name, path in files:
                digest = hashlib.sha256()
                size = 0
                before = path.stat()
                with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as destination:
                    while block := source.read(1024 * 1024):
                        destination.write(block)
                        digest.update(block)
                        size += len(block)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("测试文件仍在写入，请等待完成后重新导出诊断包")
                inventory.append({"name": name, "size": size, "sha256": digest.hexdigest()})
            manifest = {"formatVersion": 1, "sessionId": session["session_id"], "sn": session["sn"],
                        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
                        "startedAtUtc": session["started_at_utc"], "status": session["status"],
                        "softwareVersion": session["software_version"],
                        "exportStatus": export_status, "exportError": export_error,
                        "complete": not warnings, "warnings": warnings, "files": inventory}
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        # A random destination keeps previous packages intact. No partial ZIP is exposed.
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(target), "sessionId": session["session_id"], "warnings": warnings}
