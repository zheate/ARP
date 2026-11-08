"""Device ownership shared by navigation, commands and chart snapshots."""

from contextlib import contextmanager
from typing import Any, Iterator

from .errors import CommandRejected


class WorkflowAccess:
    def __init__(self, station: Any) -> None:
        self.station = station
        self.owner: str | None = None
        self._pending = 0
        self._series_start: dict[str, int] = {}

    def busy(self) -> bool:
        st = self.station
        return bool(
            self._pending or not st.manual_controls_allowed()
            or st.power_supply_controller is not None
            or st.power_meter_reader is not None or st.spectrometer_reader is not None
            or st.manual_power_stopping or st.manual_current_request_a is not None
        )

    def allows_series(self, name: str, view: str) -> bool:
        if self.owner is None:
            return True  # Compatibility for internal callers without a page.
        if view not in {self.owner, "full"}:
            return False
        return self._series_start.get(name) != self.station.series_revisions[name]

    @contextmanager
    def command(self, method: str, params: dict[str, Any]) -> Iterator[None]:
        activation = method in {
            "powerSupply.connect", "powerMeter.start", "spectrometer.start", "automatic.start",
        } or (method == "powerSupply.setOutput" and params.get("enabled") is True)
        requested = "automatic" if method.startswith("automatic.") else params.get("workflow")
        if activation and requested is None:
            requested = "manual"
        if requested not in {None, "manual", "automatic"}:
            raise ValueError("未知测试模式")
        safe = method in {
            "app.stopAll", "app.shutdown", "powerSupply.safeRampDown", "powerSupply.disconnect",
            "powerMeter.stop", "spectrometer.stop", "automatic.end",
        } or (method == "powerSupply.setOutput" and params.get("enabled") is False)
        with self.station._lock:
            if not safe and requested and self.owner not in {None, requested} and self.busy():
                label = "手动配置" if self.owner == "manual" else "自动测试"
                raise CommandRejected("control_conflict", f"{label}正在使用设备，请先停止采集并断开电源")
            previous = self.owner, self._series_start
            if activation:
                if requested != self.owner:
                    self.owner = requested
                    # Old histories stay intact; each chart becomes visible only
                    # after its own series changes in the new workflow.
                    self._series_start = dict(self.station.series_revisions)
                self._pending += 1
        try:
            yield
        except Exception:
            with self.station._lock:
                if activation and self._pending == 1:
                    self._pending -= 1
                    if not self.busy():
                        self.owner, self._series_start = previous
                    self._pending += 1
            raise
        finally:
            if activation:
                with self.station._lock:
                    self._pending -= 1
