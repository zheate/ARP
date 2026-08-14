"""Durable one-second samples produced by the automatic test workflow."""

from __future__ import annotations

import csv
import math
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence, TextIO

from .runtime_logging import get_runtime_logger


_LOGGER = get_runtime_logger("automatic_samples")


AUTOMATIC_SAMPLE_INTERVAL_S = 1.0

CSV_HEADER = (
    "sample_index",
    "timestamp",
    "point_index",
    "point_elapsed_s",
    "target_current_a",
    "output_current_a",
    "output_voltage_raw_v",
    "output_voltage_v",
    "optical_power_w",
    "electrical_power_w",
    "efficiency_percent",
    "peak_wavelength_nm",
    "centroid_nm",
    "fwhm_nm",
    "pib",
    "spectrum_point_count",
    "spectrum_wavelengths_nm",
    "spectrum_intensities",
    "error",
)


def _format_number(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return ""
    return f"{float(value):.12g}"


def _format_spectrum(values: Sequence[float]) -> str:
    return ";".join(_format_number(value) for value in values)


@dataclass(frozen=True)
class AutomaticSecondSample:
    point_index: int
    point_elapsed_s: float
    target_current_a: float
    output_current_a: float | None
    output_voltage_raw_v: float | None
    output_voltage_v: float | None
    optical_power_w: float | None
    peak_wavelength_nm: float | None = None
    centroid_nm: float | None = None
    fwhm_nm: float | None = None
    pib: float | None = None
    spectrum_wavelengths_nm: tuple[float, ...] = ()
    spectrum_intensities: tuple[float, ...] = ()
    error: str = ""


def automatic_sample_path(workbook_path: Path) -> Path:
    target = Path(workbook_path)
    return target.with_name(f"{target.stem}_1s_samples.csv")


class AutomaticSampleRecorder:
    """Line-buffered recorder.

    Path mode (``file=None``) appends rows through a background writer
    thread fed by a queue, so the automatic test GUI thread never waits on
    disk I/O. Injected-file mode stays synchronous so tests can assert row
    contents immediately. Row order is preserved in both modes.
    """

    def __init__(self, path: Path, file: TextIO | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = file
        self._sample_index = 0
        self._queue: queue.Queue[tuple[Any, ...] | None] | None = None
        self._queue_lock = threading.Lock()
        self._writer_thread: threading.Thread | None = None
        self._writer_failed = False
        self._write_error: Exception | None = None
        if self._file is None:
            with self.path.open("w", newline="", encoding="utf-8-sig") as output:
                csv.writer(output).writerow(CSV_HEADER)
            self._queue = queue.Queue()
            self._writer_thread = threading.Thread(
                target=self._write_rows_loop,
                name="automatic-sample-csv-writer",
                daemon=True,
            )
            self._writer_thread.start()
        else:
            csv.writer(self._file).writerow(CSV_HEADER)
            self._file.flush()

    def _mark_writer_failed(self, exc: Exception) -> None:
        """Record a fatal writer failure and account for every queued row.

        Must run under ``_queue_lock`` so no row can be queued after the
        drain and leave ``queue.join()`` in ``close()`` waiting forever.
        """
        self._write_error = exc
        self._writer_failed = True
        while True:
            try:
                row = self._queue.get_nowait()
            except queue.Empty:
                break
            if row is not None:
                try:
                    self._queue.task_done()
                except ValueError:
                    # The queue was already drained; nothing left to account.
                    pass

    def _write_rows_loop(self) -> None:
        try:
            # Probe append access when the worker starts so startup failures
            # are reported before callers can enqueue rows. Do not retain the
            # handle: on Windows flush_now() must also release the file lock.
            with self.path.open("a", newline="", encoding="utf-8"):
                pass
        except Exception as exc:
            _LOGGER.exception("逐秒 CSV 追加文件打开失败")
            with self._queue_lock:
                self._mark_writer_failed(exc)
            return
        try:
            while True:
                row = self._queue.get()
                if row is None:
                    break
                try:
                    # Sampling is one row per second. Opening in the worker is
                    # cheap relative to that cadence and guarantees readers,
                    # exporters, and cleanup can access the file after a flush.
                    with self.path.open("a", newline="", encoding="utf-8") as output:
                        writer = csv.writer(output)
                        writer.writerow(row)
                        output.flush()
                except Exception as exc:
                    _LOGGER.exception("逐秒 CSV 后台写入失败")
                    with self._queue_lock:
                        self._write_error = exc
                finally:
                    self._queue.task_done()
        except Exception as exc:
            _LOGGER.exception("逐秒 CSV 后台写线程异常")
            with self._queue_lock:
                self._mark_writer_failed(exc)

    def _build_row(
        self,
        sample: AutomaticSecondSample,
        timestamp: str | None = None,
    ) -> tuple[Any, ...]:
        electrical_power_w = None
        efficiency_percent = None
        if sample.output_current_a is not None and sample.output_voltage_v is not None:
            electrical_power_w = sample.output_current_a * sample.output_voltage_v
            if electrical_power_w > 0.0 and sample.optical_power_w is not None:
                efficiency_percent = sample.optical_power_w / electrical_power_w * 100.0
        return (
            self._sample_index,
            timestamp or datetime.now().astimezone().isoformat(timespec="milliseconds"),
            sample.point_index,
            f"{sample.point_elapsed_s:.6f}",
            _format_number(sample.target_current_a),
            _format_number(sample.output_current_a),
            _format_number(sample.output_voltage_raw_v),
            _format_number(sample.output_voltage_v),
            _format_number(sample.optical_power_w),
            _format_number(electrical_power_w),
            _format_number(efficiency_percent),
            _format_number(sample.peak_wavelength_nm),
            _format_number(sample.centroid_nm),
            _format_number(sample.fwhm_nm),
            _format_number(sample.pib),
            len(sample.spectrum_wavelengths_nm),
            _format_spectrum(sample.spectrum_wavelengths_nm),
            _format_spectrum(sample.spectrum_intensities),
            sample.error,
        )

    def write(self, sample: AutomaticSecondSample, *, timestamp: str | None = None) -> None:
        row = self._build_row(sample, timestamp)
        if self._queue is not None:
            with self._queue_lock:
                if self._writer_failed:
                    error = self._write_error
                    raise RuntimeError(
                        "逐秒 CSV 后台写线程已退出，样本未记录"
                    ) from error
                self._queue.put(row)
        elif self._file is not None:
            csv.writer(self._file).writerow(row)
            self._file.flush()
        self._sample_index += 1

    def flush_now(self) -> None:
        """Block until every queued row has reached the file.

        Raises the first background write error, mirroring the synchronous
        failure surface of the previous implementation.
        """
        if self._queue is None:
            return
        self._queue.join()
        if self._write_error is not None:
            raise self._write_error

    def close(self) -> None:
        """Drain queued rows, stop the writer thread, then surface failures.

        The writer thread is always stopped before any stored write error is
        raised, so callers that catch the error are never left with a leaked
        background thread holding the file open.
        """
        if self._queue is not None:
            self._queue.join()
            self._queue.put(None)
            if self._writer_thread is not None:
                self._writer_thread.join(timeout=30)
            self._queue = None
            self._writer_thread = None
        elif self._file is not None:
            self._file.flush()
        if self._write_error is not None:
            raise self._write_error
