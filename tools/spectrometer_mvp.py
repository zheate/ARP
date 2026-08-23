from __future__ import annotations

import argparse
import csv
import io
import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from combined_test.concurrency import Signal, WorkerThread
from combined_test.ocean_direct_adapter import OceanDirectControl
from combined_test.spectrum_math import SpectrumStats, calculate_stats


DEFAULT_INTEGRATION_TIME_US = 10000
DEFAULT_INTERVAL_MS = 100


def spectrum_to_csv(wavelength: NDArray[np.float64], intensity: NDArray[np.float64]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["wavelength_nm", "intensity"])
    for x, y in zip(wavelength, intensity):
        writer.writerow([f"{float(x):.6f}", f"{float(y):.6f}"])
    return output.getvalue()


def slice_spectrum(
    wavelength: NDArray[np.float64],
    intensity: NDArray[np.float64],
    x_range: tuple[float, float] | None,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    if x_range is None:
        return wavelength, intensity
    mask = (wavelength >= x_range[0]) & (wavelength <= x_range[1])
    visible_wavelength = wavelength[mask]
    visible_intensity = intensity[mask]
    if visible_wavelength.size == 0:
        return wavelength, intensity
    return visible_wavelength, visible_intensity


def integrate_area(y: NDArray[np.float64], x: NDArray[np.float64]) -> float:
    if hasattr(np, "trapezoid"):
        return float(np.trapezoid(y, x))
    return float(np.sum((y[1:] + y[:-1]) * np.diff(x) / 2.0))


class OceanSpectrometer:
    def __init__(self) -> None:
        self.control = OceanDirectControl()
        self.device_id: int | None = None

    @staticmethod
    def detect() -> list[int]:
        return [item["deviceId"] for item in OceanSpectrometer.detect_devices()]

    @staticmethod
    def detect_devices() -> list[dict[str, int | str]]:
        control = OceanDirectControl()
        try:
            state = control.find_usb_devices()
            if state == -1:
                return []
            return [
                {
                    "deviceId": int(device_id),
                    "serialNumber": control.get_serial_number(int(device_id)),
                }
                for device_id in control.get_device_ids()
            ]
        finally:
            try:
                control.close_device()
            except Exception:
                pass
            control.shutdown()

    def open_first(self) -> int:
        self.control.find_usb_devices()
        device_ids = self.control.get_device_ids()
        if not device_ids:
            raise RuntimeError(
                "OceanDirect 未检测到光谱仪。请确认 OceanHR4 已连接、未被其他程序占用，"
                "然后重新插拔 USB 后重试。"
            )
        self.device_id = int(device_ids[0])
        self.control.open_device(self.device_id)
        return self.device_id

    def set_integration_time(self, integration_time_us: int) -> None:
        self.control.set_integration_time(integration_time_us)

    def get_minimum_integration_time(self) -> int:
        return self.control.get_minimum_integration_time()

    def get_maximum_integration_time(self) -> int:
        return self.control.get_maximum_integration_time()

    def get_maximum_intensity(self) -> float:
        return float(self.control.get_maximum_intensity())

    def read_spectrum(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        wavelength = self.control.get_wavelength()
        intensity = self.control.get_intensity()
        return wavelength, intensity

    def close(self) -> None:
        try:
            self.control.close_device()
        finally:
            self.control.shutdown()


class SpectrumReaderThread(WorkerThread):
    """Toolkit-free compatibility reader for standalone diagnostics."""

    spectrum = Signal(object, object, object)
    status = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        integration_time_us: int,
        interval_ms: int,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)
        self.integration_time_us = integration_time_us
        self.interval_ms = interval_ms
        self._running = False

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        spectrometer: OceanSpectrometer | None = None
        try:
            spectrometer = OceanSpectrometer()
            device_id = spectrometer.open_first()
            spectrometer.set_integration_time(self.integration_time_us)
            self.status.emit(f"Connected Ocean Insight spectrometer, device id {device_id}")

            self._running = True
            while self._running:
                wavelength, intensity = spectrometer.read_spectrum()
                stats = calculate_stats(wavelength, intensity)
                self.spectrum.emit(wavelength, intensity, stats)
                self.msleep(self.interval_ms)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if spectrometer is not None:
                try:
                    spectrometer.close()
                except Exception:
                    pass


def diagnose(
    *,
    integration_time_us: int = DEFAULT_INTEGRATION_TIME_US,
    sample_count: int = 1,
    interval_ms: int = DEFAULT_INTERVAL_MS,
    csv_path: Path | None = None,
) -> dict[str, object]:
    """Open the first spectrometer, acquire samples, and optionally save the latest CSV."""

    if integration_time_us <= 0:
        raise ValueError("积分时间必须大于 0")
    if sample_count <= 0:
        raise ValueError("样本数量必须大于 0")
    if interval_ms < 0:
        raise ValueError("样本间隔必须大于或等于 0")

    spectrometer = OceanSpectrometer()
    latest: tuple[NDArray[np.float64], NDArray[np.float64]] | None = None
    try:
        device_id = spectrometer.open_first()
        minimum = spectrometer.get_minimum_integration_time()
        maximum = spectrometer.get_maximum_integration_time()
        if not minimum <= integration_time_us <= maximum:
            raise ValueError(f"积分时间必须在 {minimum} 到 {maximum} 微秒之间")
        spectrometer.set_integration_time(integration_time_us)
        samples: list[dict[str, object]] = []
        for index in range(sample_count):
            latest = spectrometer.read_spectrum()
            wavelength, intensity = latest
            samples.append(asdict(calculate_stats(wavelength, intensity)))
            if index + 1 < sample_count:
                time.sleep(interval_ms / 1000.0)
        if csv_path is not None and latest is not None:
            csv_path = csv_path.expanduser().resolve()
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_path.write_text(spectrum_to_csv(*latest), encoding="utf-8-sig")
        return {
            "device_id": device_id,
            "integration_time_us": integration_time_us,
            "maximum_intensity": spectrometer.get_maximum_intensity(),
            "samples": samples,
            "csv_path": str(csv_path) if csv_path is not None else None,
        }
    finally:
        spectrometer.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="无界面 Ocean Insight 光谱仪诊断工具")
    parser.add_argument("--list", action="store_true", help="列出检测到的设备 ID")
    parser.add_argument("--samples", type=int, default=0, help="采集样本数量")
    parser.add_argument("--integration-us", type=int, default=DEFAULT_INTEGRATION_TIME_US)
    parser.add_argument("--interval-ms", type=int, default=DEFAULT_INTERVAL_MS)
    parser.add_argument("--csv", type=Path, help="将最后一个光谱保存为 CSV")
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps({"device_ids": OceanSpectrometer.detect()}, ensure_ascii=False, indent=2))
    if args.samples:
        print(
            json.dumps(
                diagnose(
                    integration_time_us=args.integration_us,
                    sample_count=args.samples,
                    interval_ms=args.interval_ms,
                    csv_path=args.csv,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif not args.list:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
