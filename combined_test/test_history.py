"""Read-only lookup for completed tests of the same shell SN."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .excel_export import sanitize_sn
from .shipping_report import WorkbookInspection, inspect_shipping_workbook


@dataclass(frozen=True)
class TestHistoryRecord:
    inspection: WorkbookInspection
    modified_time_ns: int

    @property
    def station(self) -> str:
        return self.inspection.session.get("测试站别", "").strip()

    @property
    def started_at_utc(self) -> str:
        return self.inspection.session.get("开始时间 UTC", "").strip()

    def summary_payload(self) -> dict[str, Any]:
        currents = [point.current_a for point in self.inspection.points]
        return {
            "sourcePath": str(self.inspection.source_path),
            "station": self.station,
            "startedAtUtc": self.started_at_utc,
            "modifiedAtMs": self.modified_time_ns / 1_000_000,
            "pointCount": len(currents),
            "minimumCurrentA": min(currents),
            "maximumCurrentA": max(currents),
        }

    def reference_payload(self) -> dict[str, Any]:
        return {
            **self.summary_payload(),
            "points": [
                {
                    "currentA": point.current_a,
                    "powerW": point.power_w,
                }
                for point in self.inspection.points
            ],
        }


def _record_sort_key(record: TestHistoryRecord) -> tuple[int, int, str]:
    try:
        started_time_ns = int(datetime.fromisoformat(record.started_at_utc).timestamp() * 1_000_000_000)
    except (TypeError, ValueError, OverflowError):
        started_time_ns = 0
    return started_time_ns, record.modified_time_ns, str(record.inspection.source_path)


def find_test_history(output_dir: Path | str, sn: str) -> tuple[TestHistoryRecord, ...]:
    requested_sn = str(sn).strip()
    if not requested_sn:
        return ()
    root = Path(output_dir).expanduser().resolve()
    sn_directory = (root / sanitize_sn(requested_sn)).resolve()
    if not sn_directory.is_dir():
        return ()

    records: list[TestHistoryRecord] = []
    for path in sn_directory.rglob("*.xlsx"):
        if path.is_symlink() or path.name.startswith("~$"):
            continue
        source = path.resolve()
        try:
            source.relative_to(sn_directory)
            modified_time_ns = source.stat().st_mtime_ns
            inspection = inspect_shipping_workbook(source)
        except (OSError, ValueError):
            continue
        if inspection.sn.strip() != requested_sn:
            continue
        if inspection.compatibility.kind != "verified_success":
            continue
        records.append(TestHistoryRecord(inspection, modified_time_ns))

    return tuple(sorted(records, key=_record_sort_key, reverse=True))
