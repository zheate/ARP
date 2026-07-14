"""Qt main window for the combined optical test application."""

from __future__ import annotations

import math
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from PySide6.QtCore import QEvent, QSettings, QTimer, Qt
from PySide6.QtGui import QCloseEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .automatic_controller import AutomaticTestController
from .automation import (
    AutomaticTestOrchestrator,
    AutomaticTestSettings,
    AutomaticTestState,
    MIN_POWER_SUPPLY_COMMAND_INTERVAL_S,
    validate_automatic_test_settings,
)
from .core import (
    CombinedMeasurement,
    WavelengthStabilityDetector,
    spectrum_curve_to_rows,
    stability_tolerance_for_power,
)
from .devices import (
    POWER_METER_PROBE_TIMEOUT_MS,
    PowerMeterDetectThread,
    PowerMeterReaderThread,
    SpectrometerReaderThread,
    extract_power_resource_name,
    load_legacy_ch341_controller_class,
    load_spectrometer_components,
    normalize_power_resource_name,
    open_spectrometer_device,
    parse_i2c_address,
    read_power_status_value,
)
from .device_interfaces import ControllerPowerSupply, PowerMeter, PowerSupply, SpectrumMeter
from .models import (
    CombinedTestSettings,
    LiveReading,
    PowerMeterOption,
    PowerMeterReading,
    PowerMeterSettings,
    SpectrometerOption,
    SpectrometerReading,
    SpectrometerSettings,
)
from .persistence import (
    ExcelSaveThread,
    append_csv_record,
    build_spectrum_csv_path,
    save_spectrum_curve,
)
from .record_store import RecordStore, SessionRecordStore
from .plots import LivePlots
from .spectrum import (
    SPECTRUM_CENTER_LOCK_HALF_RANGE_NM,
    SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES,
    SPECTRUM_CENTER_LOCK_TOLERANCE_NM,
    detect_spectrum_saturation,
)
from .excel_export import ExcelTestRecord, sanitize_sn
from .spectrum_math import calculate_pib, calculate_smsr, calculate_stats
from .tdk_power_supply import TdkLambdaPowerSupply, list_tdk_serial_resources
from .theme import apply_application_theme


DEFAULT_POWER_RESOURCE = "ASRL3::INSTR"
DEFAULT_OUTPUT_DIR = "test_records"
DEFAULT_I2C_ADDRESS = 0x41
DEFAULT_I2C_SPEED = 0  # 20 KHz
AUTO_VOUT_AFTER_STABLE_S = 5.0
MIN_VOUT_READ_INTERVAL_S = 5.0
POWER_SUPPLY_COMMAND_MIN_INTERVAL_S = MIN_POWER_SUPPLY_COMMAND_INTERVAL_S
DEFAULT_SPECTROMETER_INTEGRATION_US = 10000
WAVELENGTH_STABILITY_TOLERANCE_NM = 0.2
MIN_SPECTRUM_PEAK_INTENSITY = 500.0
AUTOMATIC_DEVICE_START_TIMEOUT_S = 15.0
LEFT_PANEL_MIN_WIDTH = 440
LEFT_PANEL_MAX_WIDTH = 460


def user_facing_error_message(error: BaseException | str) -> str:
    """Translate common driver errors into actionable operator-facing Chinese."""
    message = str(error).strip()
    normalized = message.lower()

    if "vi_error_rsrc_nfound" in normalized or "requested device or resource is not present" in normalized:
        return (
            "未找到指定的设备或通信资源。\n"
            "请检查设备是否已连接、端口选择是否正确，然后刷新端口重试。\n"
            "错误代码：VI_ERROR_RSRC_NFOUND（-1073807343）"
        )
    if "vi_error_tmo" in normalized or "timeout expired" in normalized or "timed out" in normalized:
        return "设备通信超时。请检查设备连接和通信参数，然后重新连接并重试。"
    if "vi_error_rsrc_busy" in normalized or "resource is busy" in normalized:
        return "设备正在被其他程序占用。请关闭占用该设备的程序后重试。"
    if "vi_error_inv_rsrc_name" in normalized or "invalid resource reference" in normalized:
        return "设备资源名称无效。请刷新端口并重新选择设备。"
    if "access is denied" in normalized or "permission denied" in normalized:
        return "无法访问设备端口。该端口可能被其他程序占用，或当前用户没有访问权限。"
    if "could not open port" in normalized:
        return "无法打开串口。请确认端口存在、设备已连接且未被其他程序占用。"
    if "no module named" in normalized or "modulenotfounderror" in normalized:
        return "缺少设备驱动依赖。请使用项目指定的运行环境，并确认相关驱动已安装。"
    if "dll load failed" in normalized or "cannot load library" in normalized:
        return "设备驱动库加载失败。请确认设备驱动和所需 DLL 已正确安装。"
    if any("\u4e00" <= character <= "\u9fff" for character in message):
        return message
    return "设备操作失败。请检查设备连接、端口选择和驱动状态后重试。"


class MainWindow(QMainWindow):
    def __init__(self, input_settings: QSettings | None = None) -> None:
        super().__init__()
        self.input_settings = input_settings or QSettings("Changguang Huaxin", "Pump Driver Integrated Test")
        self.setWindowTitle("电源 / 功率计 / 波长综合测试")
        self.resize(1450, 980)
        self.power_meter_detect_thread: PowerMeterDetectThread | None = None
        self.power_meter_reader: PowerMeter | None = None
        self.spectrometer_reader: SpectrumMeter | None = None
        self.manual_ch341_controller: Any | None = None
        self.power_supply_controller_kind = "ch341"
        self.latest_spectrum_wavelength: Any | None = None
        self.latest_spectrum_intensity: Any | None = None
        self.stable_power_points: dict[float, float] = {}
        self.efficiency_points: dict[float, float] = {}
        self.efficiency_voltage_points: dict[float, float] = {}
        self.active_output_current_a: float | None = None
        self.pending_stable_point_current_a: float | None = None
        self.pending_stable_point_generation: int | None = None
        self.recorded_stable_point_current_a: float | None = None
        self.recorded_stable_point_generation: int | None = None
        self.latest_power_meter_reading: PowerMeterReading | None = None
        self.pending_auto_vout_current_a: float | None = None
        self.pending_auto_vout_generation: int | None = None
        self.last_vout_read_monotonic_s: float | None = None
        self.last_power_supply_command_monotonic_s: float | None = None
        self.auto_vout_timer = QTimer(self)
        self.auto_vout_timer.setSingleShot(True)
        self.auto_vout_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.auto_vout_timer.timeout.connect(self.on_auto_vout_timer_timeout)
        self.spectrum_center_candidate_nm: float | None = None
        self.spectrum_center_candidate_count = 0
        self.spectrum_center_locked_nm: float | None = None
        self.centroid_display_samples: deque[float] = deque(maxlen=5)
        self.latest_spectrum_saturated = False
        self.latest_spectrum_peak_intensity = 0.0
        self.wavelength_stability_detector = WavelengthStabilityDetector(3.0, WAVELENGTH_STABILITY_TOLERANCE_NM)
        self.latest_wavelength_stable = False
        self.latest_wavelength_span_nm = math.inf
        self.test_session_started_at: datetime | None = None
        self.record_store: RecordStore = SessionRecordStore()
        self.excel_save_thread: ExcelSaveThread | None = None
        self.automatic_orchestrator = AutomaticTestOrchestrator()
        self.automatic_controller = AutomaticTestController(
            self,
            power_supply_provider=self.get_power_supply,
            power_meter_provider=lambda: self.power_meter_reader,
            spectrum_meter_provider=lambda: self.spectrometer_reader,
            record_store=self.record_store,
            error_formatter=user_facing_error_message,
        )
        self.automatic_controller.bind_to_host()
        self.automatic_test_state = AutomaticTestState.IDLE
        self.automatic_test_settings: AutomaticTestSettings | None = None
        self.automatic_test_currents: tuple[float, ...] = ()
        self.automatic_test_current_index = -1
        self.automatic_power_meter_ready = False
        self.automatic_spectrometer_ready = False
        self.automatic_pause_reason = ""
        self.automatic_paused_from_state = AutomaticTestState.IDLE
        self.close_after_automatic_ramp_down = False
        self.close_after_background_tasks = False
        self.automatic_completion_record: ExcelTestRecord | None = None
        self.last_point_record_error = ""
        self.automatic_device_start_timer = QTimer(self)
        self.automatic_device_start_timer.setSingleShot(True)
        self.automatic_device_start_timer.timeout.connect(self.on_automatic_device_start_timeout)
        self.automatic_point_timer = QTimer(self)
        self.automatic_point_timer.setSingleShot(True)
        self.automatic_point_timer.timeout.connect(self.on_automatic_point_timeout)
        self.automatic_command_timer = QTimer(self)
        self.automatic_command_timer.setSingleShot(True)
        self.automatic_command_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.automatic_command_timer.timeout.connect(self.on_automatic_command_timer_timeout)
        self.pending_automatic_current_a: float | None = None
        self.pending_automatic_command_kind: str | None = None
        self.automatic_ramp_down_currents: deque[float] = deque()
        self.automatic_ramp_up_currents: deque[float] = deque()
        self.automatic_ramp_down_timer = QTimer(self)
        self.automatic_ramp_down_timer.setSingleShot(True)
        self.automatic_ramp_down_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.automatic_ramp_down_timer.timeout.connect(self.schedule_next_automatic_ramp_down_current)
        self.automatic_pause_safety_timer = QTimer(self)
        self.automatic_pause_safety_timer.setSingleShot(True)
        self.automatic_pause_safety_timer.timeout.connect(self.on_automatic_pause_safety_timeout)

        self.content_widget = QWidget(self)
        self.setCentralWidget(self.content_widget)

        root = self.content_widget
        main = QVBoxLayout(root)
        main.setContentsMargins(16, 14, 16, 12)
        main.setSpacing(10)

        self._build_global_status_bar(main)

        body = QHBoxLayout()
        body.setSpacing(10)
        main.addLayout(body, stretch=1)

        self.left_control_panel = QScrollArea(self)
        self.left_control_panel.setMinimumWidth(LEFT_PANEL_MIN_WIDTH)
        self.left_control_panel.setMaximumWidth(LEFT_PANEL_MAX_WIDTH)
        self.left_control_panel.setWidgetResizable(True)
        self.left_control_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_control_panel.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.left_control_panel.setFrameShape(QScrollArea.Shape.NoFrame)
        self.left_control_panel.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self.left_control_content = QWidget(self.left_control_panel)
        self.left_control_panel.setWidget(self.left_control_content)
        left = QVBoxLayout(self.left_control_content)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        body.addWidget(self.left_control_panel)

        self._build_session_group(left)
        self._build_automatic_test_group(left)
        self._build_power_supply_group(left)
        self._build_power_meter_group(left)
        self._build_spectrometer_group(left)
        self._build_record_group(left)
        left.addStretch(1)

        self.monitor_panel = QWidget(self)
        monitor = QVBoxLayout(self.monitor_panel)
        monitor.setContentsMargins(0, 0, 0, 0)
        monitor.setSpacing(10)
        body.addWidget(self.monitor_panel, stretch=1)

        self._build_curve_panel(monitor)

        self._build_log_panel(main)
        self._configure_button_semantics()
        self._disable_wheel_input_changes()
        self._restore_input_settings()

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("就绪")
        self.update_global_status()

    def _restore_input_settings(self) -> None:
        """Restore only operator-entered configuration, never live acquisition state."""
        settings = self.input_settings
        prefix = "input/"
        saved_controller = str(settings.value(prefix + "power_supply_controller", "ch341"))
        controller_index = self.power_supply_controller_combo.findData(saved_controller)
        if controller_index >= 0:
            self.power_supply_controller_combo.setCurrentIndex(controller_index)
        saved_tdk_resource = str(settings.value(prefix + "tdk_resource", ""))
        if saved_tdk_resource:
            self.tdk_resource_combo.setEditText(saved_tdk_resource)
        self.set_current_spin.setValue(settings.value(prefix + "set_current_a", self.set_current_spin.value(), type=float))
        self.tdk_voltage_spin.setValue(
            settings.value(prefix + "tdk_voltage_v", self.tdk_voltage_spin.value(), type=float)
        )

        saved_resource = extract_power_resource_name(
            str(settings.value(prefix + "power_resource", self.power_meter_combo.currentText()))
        )
        resource_index = self.power_meter_combo.findText(saved_resource)
        if resource_index < 0 and saved_resource:
            self.power_meter_combo.addItem(saved_resource, None)
            resource_index = self.power_meter_combo.count() - 1
        if resource_index >= 0:
            self.power_meter_combo.setCurrentIndex(resource_index)
        self.power_wavelength_spin.setValue(
            settings.value(prefix + "power_wavelength_nm", self.power_wavelength_spin.value(), type=float)
        )
        self.software_gain_spin.setValue(settings.value(prefix + "software_gain", self.software_gain_spin.value(), type=float))
        self.power_meter_interval_spin.setValue(
            settings.value(prefix + "power_meter_interval_ms", self.power_meter_interval_spin.value(), type=int)
        )

        self.integration_spin.setValue(settings.value(prefix + "integration_time_us", self.integration_spin.value(), type=int))
        self.auto_integration_check.setChecked(
            settings.value(prefix + "auto_integration_enabled", self.auto_integration_check.isChecked(), type=bool)
        )
        self.interval_spin.setValue(settings.value(prefix + "spectrometer_interval_ms", self.interval_spin.value(), type=int))
        self.stable_window_spin.setValue(settings.value(prefix + "stable_window_s", self.stable_window_spin.value(), type=float))
        self.stable_tolerance_spin.setValue(0.15)
        self.auto_initial_current_spin.setValue(
            settings.value(prefix + "auto_initial_current_a", self.auto_initial_current_spin.value(), type=float)
        )
        self.auto_target_current_spin.setValue(
            settings.value(prefix + "auto_target_current_a", self.auto_target_current_spin.value(), type=float)
        )
        self.auto_current_step_spin.setValue(
            settings.value(prefix + "auto_current_step_a", self.auto_current_step_spin.value(), type=float)
        )
        self.auto_point_timeout_spin.setValue(
            settings.value(prefix + "auto_point_timeout_s", self.auto_point_timeout_spin.value(), type=float)
        )
        self.auto_ramp_down_step_spin.setValue(
            settings.value(prefix + "auto_ramp_down_step_a", self.auto_ramp_down_step_spin.value(), type=float)
        )
        self.auto_ramp_down_interval_spin.setValue(
            settings.value(
                prefix + "auto_ramp_down_interval_s",
                self.auto_ramp_down_interval_spin.value(),
                type=float,
            )
        )
        self.auto_pause_ramp_down_timeout_spin.setValue(
            settings.value(
                prefix + "auto_pause_ramp_down_timeout_s",
                self.auto_pause_ramp_down_timeout_spin.value(),
                type=float,
            )
        )
        self.sn_field.setText(str(settings.value(prefix + "sn", self.sn_field.text())))
        saved_output_dir = settings.value(
            prefix + "output_dir",
            settings.value(prefix + "csv_path", self.output_dir_field.text()),
        )
        saved_output_path = Path(str(saved_output_dir)).expanduser()
        if saved_output_path.suffix.lower() == ".csv":
            saved_output_path = saved_output_path.parent
        self.output_dir_field.setText(str(saved_output_path))
        self.output_dir_field.setCursorPosition(0)

    @property
    def excel_workbook_path(self) -> Path | None:
        return self.record_store.workbook_path

    @excel_workbook_path.setter
    def excel_workbook_path(self, value: Path | None) -> None:
        self.record_store.workbook_path = value

    @property
    def excel_recorded_currents(self) -> set[float]:
        return self.record_store.recorded_currents

    @property
    def pending_excel_records(self) -> dict[float, ExcelTestRecord]:
        return self.record_store.pending_records

    def get_power_supply(self) -> PowerSupply | None:
        if self.manual_ch341_controller is None:
            return None
        return ControllerPowerSupply(self.manual_ch341_controller, DEFAULT_I2C_ADDRESS)

    @property
    def automatic_test_state(self) -> AutomaticTestState:
        return self.automatic_orchestrator.state

    @automatic_test_state.setter
    def automatic_test_state(self, value: AutomaticTestState) -> None:
        self.automatic_orchestrator.state = value

    @property
    def automatic_test_settings(self) -> AutomaticTestSettings | None:
        return self.automatic_orchestrator.settings

    @automatic_test_settings.setter
    def automatic_test_settings(self, value: AutomaticTestSettings | None) -> None:
        self.automatic_orchestrator.settings = value

    @property
    def automatic_test_currents(self) -> tuple[float, ...]:
        return self.automatic_orchestrator.currents

    @automatic_test_currents.setter
    def automatic_test_currents(self, value: tuple[float, ...]) -> None:
        self.automatic_orchestrator.currents = value

    @property
    def automatic_test_current_index(self) -> int:
        return self.automatic_orchestrator.current_index

    @automatic_test_current_index.setter
    def automatic_test_current_index(self, value: int) -> None:
        self.automatic_orchestrator.current_index = int(value)

    @property
    def automatic_power_meter_ready(self) -> bool:
        return self.automatic_orchestrator.power_meter_ready

    @automatic_power_meter_ready.setter
    def automatic_power_meter_ready(self, value: bool) -> None:
        self.automatic_orchestrator.power_meter_ready = bool(value)

    @property
    def automatic_spectrometer_ready(self) -> bool:
        return self.automatic_orchestrator.spectrum_meter_ready

    @automatic_spectrometer_ready.setter
    def automatic_spectrometer_ready(self, value: bool) -> None:
        self.automatic_orchestrator.spectrum_meter_ready = bool(value)

    @property
    def automatic_pause_reason(self) -> str:
        return self.automatic_orchestrator.pause_reason

    @automatic_pause_reason.setter
    def automatic_pause_reason(self, value: str) -> None:
        self.automatic_orchestrator.pause_reason = str(value)

    @property
    def automatic_paused_from_state(self) -> AutomaticTestState:
        return self.automatic_orchestrator.paused_from_state

    @automatic_paused_from_state.setter
    def automatic_paused_from_state(self, value: AutomaticTestState) -> None:
        self.automatic_orchestrator.paused_from_state = value

    def save_input_settings(self) -> None:
        settings = self.input_settings
        prefix = "input/"
        settings.setValue(prefix + "power_supply_controller", self._selected_power_supply_kind())
        settings.setValue(prefix + "tdk_resource", self.tdk_resource_combo.currentText().strip())
        settings.setValue(prefix + "tdk_voltage_v", self.tdk_voltage_spin.value())
        settings.setValue(prefix + "set_current_a", self.set_current_spin.value())
        settings.setValue(prefix + "power_resource", self._selected_power_resource())
        settings.setValue(prefix + "power_wavelength_nm", self.power_wavelength_spin.value())
        settings.setValue(prefix + "software_gain", self.software_gain_spin.value())
        settings.setValue(prefix + "power_meter_interval_ms", self.power_meter_interval_spin.value())
        settings.setValue(prefix + "integration_time_us", self.integration_spin.value())
        settings.setValue(prefix + "auto_integration_enabled", self.auto_integration_check.isChecked())
        settings.setValue(prefix + "spectrometer_interval_ms", self.interval_spin.value())
        settings.setValue(prefix + "stable_window_s", self.stable_window_spin.value())
        settings.setValue(prefix + "auto_initial_current_a", self.auto_initial_current_spin.value())
        settings.setValue(prefix + "auto_target_current_a", self.auto_target_current_spin.value())
        settings.setValue(prefix + "auto_current_step_a", self.auto_current_step_spin.value())
        settings.setValue(prefix + "auto_point_timeout_s", self.auto_point_timeout_spin.value())
        settings.setValue(prefix + "auto_ramp_down_step_a", self.auto_ramp_down_step_spin.value())
        settings.setValue(prefix + "auto_ramp_down_interval_s", self.auto_ramp_down_interval_spin.value())
        settings.setValue(
            prefix + "auto_pause_ramp_down_timeout_s",
            self.auto_pause_ramp_down_timeout_spin.value(),
        )
        settings.setValue(prefix + "sn", self.sn_field.text().strip())
        settings.setValue(prefix + "output_dir", self.output_dir_field.text().strip())
        settings.sync()

    def _build_global_status_bar(self, parent: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.global_status_label = QLabel("测试待机", self)
        self.global_status_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        row.addWidget(self.global_status_label)
        row.addStretch(1)

        self.global_psu_status_label = QLabel("电源：未连接", self)
        self.global_power_meter_status_label = QLabel("功率计：已停止", self)
        self.global_spectrometer_status_label = QLabel("光谱仪：已停止", self)
        self.global_psu_status_indicator = QLabel(self)
        self.global_power_meter_status_indicator = QLabel(self)
        self.global_spectrometer_status_indicator = QLabel(self)
        for indicator, label in (
            (self.global_psu_status_indicator, self.global_psu_status_label),
            (self.global_power_meter_status_indicator, self.global_power_meter_status_label),
            (self.global_spectrometer_status_indicator, self.global_spectrometer_status_label),
        ):
            indicator.setFixedSize(12, 12)
            indicator.setAccessibleName(f"{label.text().split('：', 1)[0]}连接状态")
            status_widget = QWidget(self)
            status_layout = QHBoxLayout(status_widget)
            status_layout.setContentsMargins(0, 0, 0, 0)
            status_layout.setSpacing(7)
            status_layout.addWidget(indicator)
            status_layout.addWidget(label)
            status_widget.setMinimumWidth(135)
            row.addWidget(status_widget)

        self.start_all_button = QPushButton("开始采集", self)
        self.stop_all_button = QPushButton("全部停止", self)
        self.start_all_button.setMinimumSize(132, 32)
        self.stop_all_button.setMinimumSize(96, 32)
        self.start_all_button.setDefault(True)
        self.start_all_button.clicked.connect(self.start_all)
        self.stop_all_button.clicked.connect(self.stop_all)
        row.addWidget(self.start_all_button)
        row.addWidget(self.stop_all_button)

        parent.addLayout(row)

    @staticmethod
    def _set_status_indicator(indicator: QLabel, connected: bool) -> None:
        color = "#16a34a" if connected else "#dc2626"
        state = "已连接" if connected else "未连接"
        indicator.setStyleSheet(
            f"background-color: {color}; border: 1px solid {color}; border-radius: 6px;"
        )
        indicator.setToolTip(state)

    def _reserve_group_height(self, group: QGroupBox) -> None:
        group.setMinimumHeight(0)
        group.updateGeometry()
        group.setMinimumHeight(group.sizeHint().height())

    @staticmethod
    def _configure_left_form(form: QFormLayout) -> None:
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setContentsMargins(8, 10, 8, 10)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)

    @staticmethod
    def _configure_action_button(button: QPushButton, minimum_width: int = 88) -> None:
        button.setMinimumWidth(minimum_width)
        button.setMinimumHeight(28)

    def _configure_button_semantics(self) -> None:
        """Keep native controls and reserve color for destructive actions."""
        self.start_all_button.setStyleSheet("font-weight: 600;")
        self.start_automatic_test_button.setStyleSheet("font-weight: 600;")
        self.apply_current_button.setStyleSheet("font-weight: 600;")
        self.save_excel_button.setStyleSheet("font-weight: 600;")

        window_is_light = self.palette().color(QPalette.ColorRole.Window).lightness() >= 128
        danger_color = "#b42318" if window_is_light else "#ff7b72"
        destructive_style = (
            f"QPushButton {{ color: {danger_color}; font-weight: 600; }}"
            "QPushButton:disabled { color: palette(mid); }"
        )
        for button in (
            self.stop_all_button,
            self.stop_power_meter_button,
            self.stop_spectrometer_button,
            self.end_automatic_test_button,
        ):
            button.setStyleSheet(destructive_style)

    def _build_session_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("测试记录", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.sn_field = QLineEdit(self)
        self.sn_field.setPlaceholderText("开始采集前必填")
        form.addRow("SN", self.sn_field)

        self.output_dir_field = QLineEdit(str(Path(DEFAULT_OUTPUT_DIR).resolve()), self)
        self.output_dir_field.setPlaceholderText("Excel 输出文件夹")
        self.output_dir_field.setToolTip(self.output_dir_field.text())
        self.browse_button = QPushButton("浏览", self)
        self._configure_action_button(self.browse_button, 72)
        self.browse_button.clicked.connect(self.browse_output_dir)
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_row.addWidget(self.output_dir_field, stretch=1)
        path_row.addWidget(self.browse_button)
        form.addRow("文件夹", path_row)

        record_actions = QHBoxLayout()
        record_actions.setSpacing(8)
        self.save_excel_button = QPushButton("保存 Excel", self)
        self._configure_action_button(self.save_excel_button, 104)
        self.save_excel_button.setEnabled(False)
        self.save_excel_button.clicked.connect(self.save_pending_excel_records)
        record_actions.addStretch(1)
        record_actions.addWidget(self.save_excel_button)
        form.addRow("", record_actions)

        self.save_status_label = QLabel("暂无可保存的测试点", self)
        self.save_status_label.setWordWrap(True)
        form.addRow("状态", self.save_status_label)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_power_supply_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("电源", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.power_supply_controller_combo = QComboBox(self)
        self.power_supply_controller_combo.addItem("CH341 I²C", "ch341")
        self.power_supply_controller_combo.addItem("TDK RS232", "tdk")
        self.power_supply_controller_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.power_supply_controller_combo.setMinimumContentsLength(10)
        self.power_supply_controller_combo.currentIndexChanged.connect(self.on_power_supply_controller_changed)
        form.addRow("控制器", self.power_supply_controller_combo)

        self.tdk_resource_combo = QComboBox(self)
        self.tdk_resource_combo.setEditable(True)
        self.tdk_resource_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.tdk_resource_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.tdk_resource_combo.setMinimumContentsLength(8)
        self.tdk_resource_combo.setPlaceholderText("ASRL3::INSTR")
        self.refresh_tdk_resources_button = QPushButton("刷新", self)
        self._configure_action_button(self.refresh_tdk_resources_button, minimum_width=58)
        self.refresh_tdk_resources_button.clicked.connect(self.refresh_tdk_resources)
        tdk_resource_row = QHBoxLayout()
        tdk_resource_row.setSpacing(6)
        tdk_resource_row.addWidget(self.tdk_resource_combo, stretch=1)
        tdk_resource_row.addWidget(self.refresh_tdk_resources_button)
        form.addRow("TDK 串口", tdk_resource_row)
        self.tdk_resource_row = tdk_resource_row

        self.tdk_voltage_spin = QDoubleSpinBox(self)
        self.tdk_voltage_spin.setRange(0.0, 1000.0)
        self.tdk_voltage_spin.setDecimals(2)
        self.tdk_voltage_spin.setSingleStep(1.0)
        self.tdk_voltage_spin.setSuffix(" V")
        self.apply_tdk_voltage_button = QPushButton("设置电压", self)
        self._configure_action_button(self.apply_tdk_voltage_button)
        self.apply_tdk_voltage_button.clicked.connect(self.apply_tdk_output_voltage)
        tdk_voltage_row = QHBoxLayout()
        tdk_voltage_row.setSpacing(6)
        tdk_voltage_row.addWidget(self.tdk_voltage_spin, stretch=1)
        tdk_voltage_row.addWidget(self.apply_tdk_voltage_button)
        form.addRow("TDK 电压", tdk_voltage_row)
        self.tdk_voltage_row = tdk_voltage_row

        self.set_current_spin = QDoubleSpinBox(self)
        self.set_current_spin.setRange(0.0, 20.0)
        self.set_current_spin.setDecimals(1)
        self.set_current_spin.setSingleStep(1.0)
        self.set_current_spin.setValue(1.0)
        self.set_current_spin.setSuffix(" A")
        self.apply_current_button = QPushButton("设置电流", self)
        self._configure_action_button(self.apply_current_button)
        self.apply_current_button.clicked.connect(self.apply_output_current)
        current_row = QHBoxLayout()
        current_row.setSpacing(6)
        current_row.addWidget(self.set_current_spin, stretch=1)
        current_row.addWidget(self.apply_current_button)
        form.addRow("设定电流", current_row)

        self.connect_i2c_button = QPushButton("连接 CH341", self)
        self._configure_action_button(self.connect_i2c_button)
        self.connect_i2c_button.clicked.connect(self.connect_i2c_device)
        self.i2c_status_label = QLabel("未连接", self)
        connection_row = QHBoxLayout()
        connection_row.setSpacing(6)
        connection_row.addWidget(self.i2c_status_label, stretch=1)
        connection_row.addWidget(self.connect_i2c_button)
        form.addRow("连接", connection_row)

        self.tdk_output_button = QPushButton("开启输出", self)
        self._configure_action_button(self.tdk_output_button)
        self.tdk_output_button.clicked.connect(self.toggle_tdk_output)
        self.tdk_output_status_label = QLabel("输出关闭", self)
        output_row = QHBoxLayout()
        output_row.setSpacing(6)
        output_row.addWidget(self.tdk_output_status_label, stretch=1)
        output_row.addWidget(self.tdk_output_button)
        form.addRow("TDK 输出", output_row)

        read_grid = QGridLayout()
        self.read_input_voltage_button = QPushButton("输入电压", self)
        self.read_output_voltage_button = QPushButton("输出电压", self)
        self.read_output_current_button = QPushButton("输出电流", self)
        self.read_temperature_button = QPushButton("温度", self)
        self.read_input_voltage_button.clicked.connect(self.read_input_voltage)
        self.read_output_voltage_button.clicked.connect(self.read_output_voltage)
        self.read_output_current_button.clicked.connect(self.read_output_current)
        self.read_temperature_button.clicked.connect(self.read_temperature)
        for button in (
            self.read_input_voltage_button,
            self.read_output_voltage_button,
            self.read_output_current_button,
            self.read_temperature_button,
        ):
            button.setMinimumHeight(28)
        read_grid.setHorizontalSpacing(4)
        read_grid.addWidget(self.read_input_voltage_button, 0, 0)
        read_grid.addWidget(self.read_output_voltage_button, 0, 1)
        read_grid.addWidget(self.read_output_current_button, 1, 0)
        read_grid.addWidget(self.read_temperature_button, 1, 1)
        form.addRow("读取", read_grid)
        self.power_supply_form = form
        self.power_supply_read_row = read_grid

        parent.addWidget(group)
        self.on_power_supply_controller_changed()
        self._reserve_group_height(group)

    def _build_automatic_test_group(self, parent: QVBoxLayout) -> None:
        self.automatic_test_section = QWidget(self)
        section_layout = QVBoxLayout(self.automatic_test_section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(4)

        self.automatic_test_toggle = QToolButton(self)
        self.automatic_test_toggle.setText("自动测试")
        self.automatic_test_toggle.setCheckable(True)
        self.automatic_test_toggle.setChecked(False)
        self.automatic_test_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.automatic_test_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.automatic_test_toggle.setAutoRaise(True)
        self.automatic_test_toggle.setStyleSheet(
            "QToolButton { border: none; font-size: 16px; font-weight: 600; padding: 4px 2px; }"
        )
        self.automatic_test_toggle.toggled.connect(self._set_automatic_test_expanded)
        section_layout.addWidget(self.automatic_test_toggle)

        self.automatic_test_content = QGroupBox(self)
        form = QFormLayout(self.automatic_test_content)
        self._configure_left_form(form)

        self.auto_initial_current_spin = QDoubleSpinBox(self)
        self.auto_initial_current_spin.setRange(0.1, 20.0)
        self.auto_initial_current_spin.setDecimals(1)
        self.auto_initial_current_spin.setSingleStep(0.1)
        self.auto_initial_current_spin.setValue(1.0)
        self.auto_initial_current_spin.setSuffix(" A")

        self.auto_target_current_spin = QDoubleSpinBox(self)
        self.auto_target_current_spin.setRange(0.1, 20.0)
        self.auto_target_current_spin.setDecimals(1)
        self.auto_target_current_spin.setSingleStep(0.1)
        self.auto_target_current_spin.setValue(20.0)
        self.auto_target_current_spin.setSuffix(" A")

        self.auto_current_step_spin = QDoubleSpinBox(self)
        self.auto_current_step_spin.setRange(0.1, 20.0)
        self.auto_current_step_spin.setDecimals(1)
        self.auto_current_step_spin.setSingleStep(0.1)
        self.auto_current_step_spin.setValue(1.0)
        self.auto_current_step_spin.setSuffix(" A")

        self.auto_point_timeout_spin = QDoubleSpinBox(self)
        self.auto_point_timeout_spin.setRange(5.0, 3600.0)
        self.auto_point_timeout_spin.setDecimals(1)
        self.auto_point_timeout_spin.setSingleStep(10.0)
        self.auto_point_timeout_spin.setValue(120.0)
        self.auto_point_timeout_spin.setSuffix(" s")

        self.auto_ramp_down_step_spin = QDoubleSpinBox(self)
        self.auto_ramp_down_step_spin.setRange(0.1, 20.0)
        self.auto_ramp_down_step_spin.setDecimals(1)
        self.auto_ramp_down_step_spin.setSingleStep(0.1)
        self.auto_ramp_down_step_spin.setValue(5.0)
        self.auto_ramp_down_step_spin.setSuffix(" A")

        self.auto_ramp_down_interval_spin = QDoubleSpinBox(self)
        self.auto_ramp_down_interval_spin.setRange(POWER_SUPPLY_COMMAND_MIN_INTERVAL_S, 60.0)
        self.auto_ramp_down_interval_spin.setDecimals(1)
        self.auto_ramp_down_interval_spin.setSingleStep(0.1)
        self.auto_ramp_down_interval_spin.setValue(POWER_SUPPLY_COMMAND_MIN_INTERVAL_S)
        self.auto_ramp_down_interval_spin.setSuffix(" s")

        self.auto_pause_ramp_down_timeout_spin = QDoubleSpinBox(self)
        self.auto_pause_ramp_down_timeout_spin.setRange(0.0, 600.0)
        self.auto_pause_ramp_down_timeout_spin.setDecimals(1)
        self.auto_pause_ramp_down_timeout_spin.setSingleStep(5.0)
        self.auto_pause_ramp_down_timeout_spin.setValue(30.0)
        self.auto_pause_ramp_down_timeout_spin.setSuffix(" s")
        self.auto_pause_ramp_down_timeout_spin.setToolTip("暂停后超过此时间自动分段降至 0 A；设为 0 可关闭")

        parameter_grid = QGridLayout()
        # Keep the two parameter columns inside the scroll-area viewport. If
        # this grid is wider than the viewport, focusing a field can scroll it
        # horizontally even though the scrollbar is hidden and clip labels.
        parameter_grid.setHorizontalSpacing(6)
        parameter_grid.setVerticalSpacing(6)
        parameter_grid.setColumnStretch(1, 1)
        parameter_grid.setColumnStretch(3, 1)
        parameters = (
            ("初始电流", self.auto_initial_current_spin),
            ("目标电流", self.auto_target_current_spin),
            ("电流间隔", self.auto_current_step_spin),
            ("单点超时", self.auto_point_timeout_spin),
            ("下电步长", self.auto_ramp_down_step_spin),
            ("下电间隔", self.auto_ramp_down_interval_spin),
            ("暂停下电", self.auto_pause_ramp_down_timeout_spin),
        )
        for index, (label_text, spin_box) in enumerate(parameters):
            row = index // 2
            column = (index % 2) * 2
            label = QLabel(label_text, self)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            parameter_grid.addWidget(label, row, column)
            parameter_grid.addWidget(spin_box, row, column + 1)
        form.addRow(parameter_grid)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.start_automatic_test_button = QPushButton("开始自动测试", self)
        self.retry_automatic_test_button = QPushButton("重试当前点", self)
        self.end_automatic_test_button = QPushButton("结束并下电", self)
        self.start_automatic_test_button.clicked.connect(self.start_automatic_test)
        self.retry_automatic_test_button.clicked.connect(self.retry_automatic_test)
        self.end_automatic_test_button.clicked.connect(self.end_automatic_test)
        self.retry_automatic_test_button.setEnabled(False)
        self.end_automatic_test_button.setEnabled(False)
        actions.addWidget(self.start_automatic_test_button)
        actions.addWidget(self.retry_automatic_test_button)
        actions.addWidget(self.end_automatic_test_button)
        form.addRow(actions)

        self.automatic_test_status_label = QLabel("未开始", self)
        self.automatic_test_status_label.setWordWrap(True)
        form.addRow("状态", self.automatic_test_status_label)

        section_layout.addWidget(self.automatic_test_content)
        self.automatic_test_content.setVisible(False)
        parent.addWidget(self.automatic_test_section)

    def _set_automatic_test_expanded(self, expanded: bool) -> None:
        self.automatic_test_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.automatic_test_content.setVisible(expanded)
        self.automatic_test_section.updateGeometry()

    def _build_power_meter_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("功率计", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.power_meter_combo = QComboBox(self)
        self.power_meter_combo.setEditable(True)
        self.power_meter_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.power_meter_combo.setMinimumContentsLength(8)
        self.power_meter_combo.addItem(DEFAULT_POWER_RESOURCE, None)
        self.detect_power_meter_button = QPushButton("自动检测", self)
        self._configure_action_button(self.detect_power_meter_button)
        self.detect_power_meter_button.clicked.connect(self.auto_detect_power_meters)
        device_row = QHBoxLayout()
        device_row.setSpacing(6)
        device_row.addWidget(self.power_meter_combo, stretch=1)
        device_row.addWidget(self.detect_power_meter_button)
        form.addRow("设备", device_row)

        power_actions = QHBoxLayout()
        power_actions.setSpacing(8)
        self.refresh_power_meter_button = QPushButton("刷新端口", self)
        self._configure_action_button(self.refresh_power_meter_button)
        self.rel_zero_check = QCheckBox("相对调零", self)
        self.refresh_power_meter_button.clicked.connect(self.refresh_power_meter_resources)
        self.rel_zero_check.toggled.connect(self.set_power_meter_relative_zero)
        power_actions.addWidget(self.refresh_power_meter_button)
        power_actions.addWidget(self.rel_zero_check)
        form.addRow(power_actions)

        self.power_wavelength_spin = QDoubleSpinBox(self)
        self.power_wavelength_spin.setRange(190.0, 25000.0)
        self.power_wavelength_spin.setDecimals(3)
        self.power_wavelength_spin.setSingleStep(0.1)
        self.power_wavelength_spin.setValue(976.0)
        self.power_wavelength_spin.setSuffix(" nm")
        form.addRow("波长", self.power_wavelength_spin)

        self.software_gain_spin = QDoubleSpinBox(self)
        self.software_gain_spin.setRange(0.000001, 1000000.0)
        self.software_gain_spin.setDecimals(6)
        self.software_gain_spin.setValue(1.0)
        form.addRow("软件增益", self.software_gain_spin)

        self.power_meter_interval_spin = QSpinBox(self)
        self.power_meter_interval_spin.setRange(20, 5000)
        self.power_meter_interval_spin.setValue(300)
        self.power_meter_interval_spin.setSingleStep(50)
        self.power_meter_interval_spin.setSuffix(" ms")
        form.addRow("采样间隔", self.power_meter_interval_spin)

        self.power_meter_status_label = QLabel("已停止", self)
        power_run_actions = QHBoxLayout()
        power_run_actions.setSpacing(6)
        self.start_power_meter_button = QPushButton("启动", self)
        self.stop_power_meter_button = QPushButton("停止", self)
        self._configure_action_button(self.start_power_meter_button)
        self._configure_action_button(self.stop_power_meter_button)
        self.stop_power_meter_button.hide()
        self.start_power_meter_button.clicked.connect(self.start_power_meter)
        self.stop_power_meter_button.clicked.connect(self.stop_power_meter)
        power_run_actions.addWidget(self.power_meter_status_label, stretch=1)
        power_run_actions.addWidget(self.start_power_meter_button)
        power_run_actions.addWidget(self.stop_power_meter_button)
        form.addRow("状态", power_run_actions)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_spectrometer_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("光谱仪", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.spectrometer_combo = QComboBox(self)
        self.spectrometer_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.spectrometer_combo.setMinimumContentsLength(8)
        self.spectrometer_combo.addItem("自动选择第一台 Ocean Insight", None)
        self.detect_spectrometer_button = QPushButton("自动检测", self)
        self._configure_action_button(self.detect_spectrometer_button)
        self.detect_spectrometer_button.clicked.connect(self.auto_detect_spectrometers)
        device_row = QHBoxLayout()
        device_row.setSpacing(6)
        device_row.addWidget(self.spectrometer_combo, stretch=1)
        device_row.addWidget(self.detect_spectrometer_button)
        form.addRow("设备", device_row)

        self.integration_spin = QSpinBox(self)
        self.integration_spin.setRange(1, 10_000_000)
        self.integration_spin.setValue(DEFAULT_SPECTROMETER_INTEGRATION_US)
        self.integration_spin.setSingleStep(100)
        self.integration_spin.setSuffix(" us")
        form.addRow("积分时间", self.integration_spin)

        self.auto_integration_check = QCheckBox("启用（目标 8k–14k）", self)
        self.auto_integration_check.setToolTip("自动调整积分时间，使光谱峰值保持在 8000–14000 counts")
        self.auto_integration_check.setChecked(False)
        form.addRow("自动积分", self.auto_integration_check)

        self.interval_spin = QSpinBox(self)
        self.interval_spin.setRange(50, 5000)
        self.interval_spin.setValue(300)
        self.interval_spin.setSingleStep(50)
        self.interval_spin.setSuffix(" ms")
        form.addRow("采样间隔", self.interval_spin)

        self.spectrometer_status_label = QLabel("已停止", self)
        spectrometer_run_actions = QHBoxLayout()
        spectrometer_run_actions.setSpacing(6)
        self.start_spectrometer_button = QPushButton("启动", self)
        self.stop_spectrometer_button = QPushButton("停止", self)
        self._configure_action_button(self.start_spectrometer_button)
        self._configure_action_button(self.stop_spectrometer_button)
        self.stop_spectrometer_button.hide()
        self.start_spectrometer_button.clicked.connect(self.start_spectrometer)
        self.stop_spectrometer_button.clicked.connect(self.stop_spectrometer)
        spectrometer_run_actions.addWidget(self.spectrometer_status_label, stretch=1)
        spectrometer_run_actions.addWidget(self.start_spectrometer_button)
        spectrometer_run_actions.addWidget(self.stop_spectrometer_button)
        form.addRow("状态", spectrometer_run_actions)

        spectrum_actions = QHBoxLayout()
        spectrum_actions.setSpacing(6)
        self.copy_spectrum_button = QPushButton("复制 CSV", self)
        self.save_spectrum_button = QPushButton("保存 CSV", self)
        self._configure_action_button(self.copy_spectrum_button)
        self._configure_action_button(self.save_spectrum_button)
        self.copy_spectrum_button.setEnabled(False)
        self.save_spectrum_button.setEnabled(False)
        self.copy_spectrum_button.clicked.connect(self.copy_spectrum_csv)
        self.save_spectrum_button.clicked.connect(self.save_spectrum_csv)
        spectrum_actions.addWidget(self.copy_spectrum_button)
        spectrum_actions.addWidget(self.save_spectrum_button)
        form.addRow("光谱数据", spectrum_actions)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_record_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("稳定性", self)
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.stable_window_spin = QDoubleSpinBox(self)
        self.stable_window_spin.setRange(0.5, 300.0)
        self.stable_window_spin.setDecimals(1)
        self.stable_window_spin.setValue(3.0)
        self.stable_window_spin.setSuffix(" s")
        self.stable_window_spin.valueChanged.connect(self.on_stability_settings_changed)
        form.addRow("稳定窗口", self.stable_window_spin)

        self.stable_tolerance_spin = QDoubleSpinBox(self)
        self.stable_tolerance_spin.setRange(0.0, 100000.0)
        self.stable_tolerance_spin.setDecimals(4)
        self.stable_tolerance_spin.setValue(0.15)
        self.stable_tolerance_spin.setSuffix(" W")
        self.stable_tolerance_spin.setReadOnly(True)
        self.stable_tolerance_spin.setToolTip(
            "自动判定：<100 W = 0.15 W；100 至 <200 W = 0.25 W；>=200 W = 0.35 W"
        )
        form.addRow("允许波动", self.stable_tolerance_spin)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_curve_panel(self, parent: QVBoxLayout) -> None:
        self.live_plots = LivePlots(self)
        self.live_plots.expose_compatibility_attributes(self)
        parent.addWidget(self.live_plots.group, stretch=2)
        self.reset_curves()

    def _build_log_panel(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("日志", self)
        layout = QHBoxLayout(group)
        layout.setContentsMargins(10, 6, 10, 8)
        self.log_text = QLabel("就绪", self)
        self.log_text.setMinimumWidth(0)
        self.log_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.log_text, stretch=1)
        parent.addWidget(group)
        self._reserve_group_height(group)

    def _disable_wheel_input_changes(self) -> None:
        for widget in self.findChildren(QAbstractSpinBox):
            widget.installEventFilter(self)
        for widget in self.findChildren(QComboBox):
            widget.installEventFilter(self)

    def eventFilter(self, watched: Any, event: Any) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(watched, (QAbstractSpinBox, QComboBox)):
            return True
        return super().eventFilter(watched, event)

    def browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 Excel 输出文件夹", self.output_dir_field.text())
        if path:
            self.output_dir_field.setText(path)
            self.output_dir_field.setToolTip(path)

    def start_all(self) -> None:
        if self.excel_save_thread is not None:
            self.statusBar().showMessage("请等待当前 Excel 保存完成")
            return
        if self.power_meter_reader is None and self.spectrometer_reader is None:
            try:
                self.begin_test_session()
            except ValueError as exc:
                QMessageBox.warning(self, "测试记录", user_facing_error_message(exc))
                return
        self.start_power_meter()
        self.start_spectrometer()

    def begin_test_session(self, reset_records: bool = True) -> Path:
        sn = sanitize_sn(self.sn_field.text())
        output_dir_text = self.output_dir_field.text().strip()
        if not output_dir_text:
            raise ValueError("Excel 输出文件夹不能为空")
        self.test_session_started_at = datetime.now()
        self.excel_workbook_path = self.record_store.begin_session(
            Path(output_dir_text),
            sn,
            self.test_session_started_at,
            reset=reset_records,
        )
        if reset_records:
            self.save_status_label.setText("暂无可保存的测试点")
            self.save_excel_button.setEnabled(False)
        self.add_log(f"测试记录：{self.excel_workbook_path}")
        return self.excel_workbook_path

    def stop_all(self) -> None:
        if self.automatic_test_state not in (
            AutomaticTestState.IDLE,
            AutomaticTestState.COMPLETED,
            AutomaticTestState.RAMPING_DOWN,
        ):
            self.begin_automatic_ramp_down()
            return
        if self.automatic_test_state == AutomaticTestState.RAMPING_DOWN:
            return
        self.stop_power_meter()
        self.stop_spectrometer()

    def update_global_status(self) -> None:
        if not hasattr(self, "global_status_label"):
            return
        psu_connected = self._manual_i2c_connected()
        power_running = self.power_meter_reader is not None
        spectrometer_running = self.spectrometer_reader is not None
        power_connected = power_running and bool(getattr(self.power_meter_reader, "is_ready", False))
        spectrometer_connected = spectrometer_running and bool(getattr(self.spectrometer_reader, "is_ready", False))
        power_detecting = self.power_meter_detect_thread is not None
        automatic_active = self.automatic_test_state not in (
            AutomaticTestState.IDLE,
            AutomaticTestState.COMPLETED,
        )

        if self.automatic_test_state == AutomaticTestState.PAUSED:
            self.global_status_label.setText("自动测试已暂停")
        elif self.automatic_test_state == AutomaticTestState.RAMPING_DOWN:
            self.global_status_label.setText("自动测试下电中")
        elif automatic_active:
            self.global_status_label.setText("自动测试运行中")
        else:
            self.global_status_label.setText("测试运行中" if power_running or spectrometer_running else "测试待机")
        self.global_psu_status_label.setText("电源：已连接" if psu_connected else "电源：未连接")
        if power_detecting:
            self.global_power_meter_status_label.setText("功率计：检测中")
        else:
            self.global_power_meter_status_label.setText("功率计：运行中" if power_running else "功率计：已停止")
        self.global_spectrometer_status_label.setText("光谱仪：运行中" if spectrometer_running else "光谱仪：已停止")
        self._set_status_indicator(self.global_psu_status_indicator, psu_connected)
        self._set_status_indicator(self.global_power_meter_status_indicator, power_connected)
        self._set_status_indicator(self.global_spectrometer_status_indicator, spectrometer_connected)
        self.stop_all_button.setEnabled(power_running or spectrometer_running or automatic_active)

        if hasattr(self, "power_meter_status_label"):
            self.power_meter_status_label.setText("检测中" if power_detecting else ("运行中" if power_running else "已停止"))
        if hasattr(self, "spectrometer_status_label"):
            self.spectrometer_status_label.setText("运行中" if spectrometer_running else "已停止")

    def _manual_i2c_connected(self) -> bool:
        power_supply = self.get_power_supply()
        return power_supply is not None and power_supply.connected

    def _selected_power_supply_kind(self) -> str:
        return str(self.power_supply_controller_combo.currentData() or "ch341")

    def on_power_supply_controller_changed(self) -> None:
        selected_kind = self._selected_power_supply_kind()
        if self._manual_i2c_connected() and selected_kind != self.power_supply_controller_kind:
            try:
                if self.power_supply_controller_kind == "tdk" and bool(
                    getattr(self.manual_ch341_controller, "output_enabled", False)
                ):
                    self.manual_ch341_controller.set_output_enabled(False)
                self.manual_ch341_controller.disconnect_device()
            except Exception as exc:
                previous_index = self.power_supply_controller_combo.findData(self.power_supply_controller_kind)
                self.power_supply_controller_combo.blockSignals(True)
                self.power_supply_controller_combo.setCurrentIndex(previous_index)
                self.power_supply_controller_combo.blockSignals(False)
                QMessageBox.critical(
                    self,
                    "TDK 输出",
                    f"关闭 TDK 输出失败，已保持当前连接。\n{user_facing_error_message(exc)}",
                )
                return
            finally:
                if not self._manual_i2c_connected():
                    self.manual_ch341_controller = None
        self.power_supply_controller_kind = selected_kind
        is_tdk = selected_kind == "tdk"
        self.power_supply_form.setRowVisible(self.tdk_resource_row, is_tdk)
        self.power_supply_form.setRowVisible(self.tdk_voltage_row, is_tdk)
        self.power_supply_form.setRowVisible(self.power_supply_read_row, not is_tdk)
        if not is_tdk:
            self.set_current_spin.setMaximum(20.0)
            self.auto_initial_current_spin.setMaximum(20.0)
            self.auto_target_current_spin.setMaximum(20.0)
            self.tdk_voltage_spin.setMaximum(1000.0)
        for widget in (
            self.tdk_resource_combo,
            self.refresh_tdk_resources_button,
            self.tdk_voltage_spin,
            self.apply_tdk_voltage_button,
            self.tdk_output_button,
        ):
            widget.setEnabled(is_tdk)
        self.read_input_voltage_button.setEnabled(not is_tdk)
        self.read_temperature_button.setEnabled(not is_tdk)
        self.connect_i2c_button.setText("连接 TDK" if is_tdk else "连接 CH341")
        self.i2c_status_label.setText("未连接")
        self.tdk_output_status_label.setText("输出关闭")
        self.tdk_output_button.setText("开启输出")
        self.update_global_status()

    def refresh_tdk_resources(self) -> None:
        current = self.tdk_resource_combo.currentText().strip()
        try:
            resources = list_tdk_serial_resources()
        except Exception as exc:
            QMessageBox.critical(self, "TDK 电源", user_facing_error_message(exc))
            return
        self.tdk_resource_combo.clear()
        self.tdk_resource_combo.addItems(resources)
        if current and current not in resources:
            self.tdk_resource_combo.setEditText(current)
        elif current:
            self.tdk_resource_combo.setCurrentText(current)
        self.statusBar().showMessage(f"找到 {len(resources)} 个可用 RS-232 串口")

    def _get_manual_ch341_controller(self) -> Any:
        if self.manual_ch341_controller is None:
            if self._selected_power_supply_kind() == "tdk":
                resource = self.tdk_resource_combo.currentText().strip()
                self.manual_ch341_controller = TdkLambdaPowerSupply(resource)
                self.power_supply_controller_kind = "tdk"
            else:
                controller_class = load_legacy_ch341_controller_class()
                self.manual_ch341_controller = controller_class()
                self.power_supply_controller_kind = "ch341"
        return self.manual_ch341_controller

    def connect_i2c_device(self) -> None:
        controller = self._get_manual_ch341_controller()
        label = "TDK" if self.power_supply_controller_kind == "tdk" else "CH341"
        if self._manual_i2c_connected():
            try:
                if label == "TDK" and bool(getattr(controller, "output_enabled", False)):
                    controller.set_output_enabled(False)
                controller.disconnect_device()
            except Exception as exc:
                QMessageBox.critical(self, label, f"安全断开失败。\n{user_facing_error_message(exc)}")
                return
            if label == "TDK":
                self.manual_ch341_controller = None
            self.connect_i2c_button.setText(f"连接 {label}")
            self.i2c_status_label.setText("未连接")
            self.tdk_output_status_label.setText("输出关闭")
            self.tdk_output_button.setText("开启输出")
            self.add_log(f"{label} 已断开")
            self.update_global_status()
            return

        try:
            controller.set_i2c_speed(DEFAULT_I2C_SPEED)
            connected, detail = controller.connect_device(0)
            if not connected:
                raise RuntimeError(str(detail))
            self.connect_i2c_button.setText(f"断开 {label}")
            self.i2c_status_label.setText("已连接")
            if label == "TDK":
                output_enabled = bool(getattr(controller, "output_enabled", False))
                self.tdk_output_status_label.setText("输出开启" if output_enabled else "输出关闭")
                self.tdk_output_button.setText("关闭输出" if output_enabled else "开启输出")
                maximum_voltage = getattr(controller, "maximum_voltage_v", None)
                maximum_current = getattr(controller, "maximum_current_a", None)
                if maximum_voltage is not None:
                    self.tdk_voltage_spin.setMaximum(float(maximum_voltage))
                if maximum_current is not None:
                    current_limit = min(20.0, float(maximum_current))
                    self.set_current_spin.setMaximum(current_limit)
                    self.auto_initial_current_spin.setMaximum(current_limit)
                    self.auto_target_current_spin.setMaximum(current_limit)
            self.add_log(f"{label} 已连接：{detail}")
            self.update_global_status()
        except Exception as exc:
            if label == "TDK" and not bool(getattr(controller, "is_connected", False)):
                self.manual_ch341_controller = None
            QMessageBox.critical(self, label, user_facing_error_message(exc))

    def _require_manual_i2c_controller(self) -> Any | None:
        if not self._manual_i2c_connected():
            label = "TDK" if self._selected_power_supply_kind() == "tdk" else "CH341"
            QMessageBox.warning(self, label, f"请先连接 {label}。")
            return None
        return self.manual_ch341_controller

    def apply_tdk_output_voltage(self) -> None:
        controller = self._require_manual_i2c_controller()
        if controller is None:
            return
        if self.power_supply_controller_kind != "tdk":
            return
        if not self.begin_power_supply_command("设置 TDK 输出电压"):
            return
        try:
            power_supply = self.get_power_supply()
            if power_supply is None:
                raise RuntimeError("TDK 电源未连接")
            power_supply.set_voltage(self.tdk_voltage_spin.value())
            self.add_log(f"TDK 输出电压已设为 {self.tdk_voltage_spin.value():.2f} V")
            self.statusBar().showMessage(f"TDK 输出电压已设为 {self.tdk_voltage_spin.value():.2f} V")
        except Exception as exc:
            QMessageBox.critical(self, "TDK 电压", user_facing_error_message(exc))

    def toggle_tdk_output(self) -> None:
        controller = self._require_manual_i2c_controller()
        if controller is None:
            return
        if self.power_supply_controller_kind != "tdk":
            return
        if not self.begin_power_supply_command("切换 TDK 输出"):
            return
        try:
            power_supply = self.get_power_supply()
            if power_supply is None:
                raise RuntimeError("TDK 电源未连接")
            enabled = not power_supply.output_enabled
            power_supply.set_output_enabled(enabled)
            self.tdk_output_status_label.setText("输出开启" if enabled else "输出关闭")
            self.tdk_output_button.setText("关闭输出" if enabled else "开启输出")
            self.add_log(f"TDK 输出已{'开启' if enabled else '关闭'}")
            self.statusBar().showMessage(f"TDK 输出已{'开启' if enabled else '关闭'}")
        except Exception as exc:
            QMessageBox.critical(self, "TDK 输出", user_facing_error_message(exc))

    def begin_power_supply_command(self, command_name: str) -> bool:
        """Reserve the power-supply bus so I2C commands remain safely spaced."""
        now = time.monotonic()
        remaining_s = self.power_supply_command_interval_remaining_s(now)
        if remaining_s > 0.0:
            message = f"{command_name}被阻止；请等待 {remaining_s:.1f} 秒后再发送下一条电源命令"
            self.statusBar().showMessage(message)
            self.add_log(message)
            return False
        self.last_power_supply_command_monotonic_s = now
        return True

    def power_supply_command_interval_remaining_s(self, now: float | None = None) -> float:
        if self.last_power_supply_command_monotonic_s is None:
            return 0.0
        current_time = time.monotonic() if now is None else float(now)
        elapsed_s = current_time - self.last_power_supply_command_monotonic_s
        return max(0.0, POWER_SUPPLY_COMMAND_MIN_INTERVAL_S - elapsed_s)

    def read_input_voltage(self) -> None:
        self.execute_i2c_read([0xB4, 0x88, 0x00, 0x00], "输入电压", "V")

    def read_output_voltage(self, automatic: bool = False) -> None:
        remaining_s = self.vout_read_interval_remaining_s()
        if remaining_s > 0.0:
            message = f"输出电压读取受限；请等待 {remaining_s:.1f} 秒"
            self.statusBar().showMessage(message)
            self.add_log(message)
            if automatic:
                self.schedule_auto_vout_read(delay_s=remaining_s)
            return

        voltage_v = self.execute_i2c_read([0xB4, 0x8B, 0x00, 0x00], "输出电压", "V")
        if voltage_v is not None:
            self.last_vout_read_monotonic_s = time.monotonic()
            self.record_efficiency_from_vout(voltage_v)
        elif automatic and self.automatic_test_state == AutomaticTestState.WAITING_VOLTAGE:
            self.pause_automatic_test("输出电压读取失败")

    def vout_read_interval_remaining_s(self) -> float:
        if self.last_vout_read_monotonic_s is None:
            return 0.0
        elapsed_s = time.monotonic() - self.last_vout_read_monotonic_s
        return max(0.0, MIN_VOUT_READ_INTERVAL_S - elapsed_s)

    def cancel_auto_vout_read(self) -> None:
        self.auto_vout_timer.stop()
        self.pending_auto_vout_current_a = None
        self.pending_auto_vout_generation = None

    def schedule_auto_vout_read(self, delay_s: float = AUTO_VOUT_AFTER_STABLE_S) -> None:
        current_a = self.recorded_stable_point_current_a
        generation = self.recorded_stable_point_generation
        if (
            current_a is None
            or current_a <= 0.0
            or generation is None
            or current_a in self.efficiency_voltage_points
        ):
            return

        delay_s = max(float(delay_s), self.vout_read_interval_remaining_s())
        self.pending_auto_vout_current_a = current_a
        self.pending_auto_vout_generation = generation
        self.auto_vout_timer.start(max(1, math.ceil(delay_s * 1000.0)))
        self.statusBar().showMessage(f"功率已稳定；将在 {delay_s:.1f} 秒后自动读取输出电压")
        self.add_log(f"{current_a:.3f} A 时功率已稳定；将在 {delay_s:.1f} 秒后自动读取输出电压")

    def on_auto_vout_timer_timeout(self) -> None:
        current_a = self.pending_auto_vout_current_a
        generation = self.pending_auto_vout_generation
        self.pending_auto_vout_current_a = None
        self.pending_auto_vout_generation = None
        reading = self.latest_power_meter_reading
        if (
            current_a is None
            or generation is None
            or current_a != self.active_output_current_a
            or current_a != self.recorded_stable_point_current_a
            or generation != self.recorded_stable_point_generation
            or reading is None
            or not reading.stable
            or reading.stability_generation != generation
            or (
                self.automatic_test_state == AutomaticTestState.WAITING_VOLTAGE
                and not self.latest_wavelength_stable
            )
        ):
            self.add_log("当前测试点不再稳定，已取消自动读取输出电压")
            return
        self.read_output_voltage(automatic=True)

    def invalidate_automatic_stability(self, reason: str) -> None:
        if self.automatic_test_state != AutomaticTestState.WAITING_VOLTAGE:
            return
        self.cancel_auto_vout_read()
        self.recorded_stable_point_current_a = None
        self.recorded_stable_point_generation = None
        self.pending_stable_point_current_a = self.active_output_current_a
        if self.power_meter_reader is not None:
            self.pending_stable_point_generation = self.power_meter_reader.reset_stability_window()
        else:
            self.pending_stable_point_generation = None
        self.reset_wavelength_stability_window()
        self.set_automatic_test_state(AutomaticTestState.WAITING_STABLE, f"{reason}，重新判稳")
        self.add_log(f"{reason}，已取消读取输出电压并重新判稳")

    def read_output_current(self) -> None:
        self.execute_i2c_read([0xB4, 0x8C, 0x00, 0x00], "输出电流", "A")

    def read_temperature(self) -> None:
        self.execute_i2c_read([0xB4, 0x8D, 0x00, 0x00], "模块温度", "°C")

    def execute_i2c_read(self, command: list[int], name: str, unit: str) -> float | None:
        controller = self._require_manual_i2c_controller()
        if controller is None:
            return None
        if not self.begin_power_supply_command(name):
            return None
        try:
            power_supply = self.get_power_supply()
            if command[1] == 0x8B and power_supply is not None:
                value = power_supply.read_output_voltage()
            elif command[1] == 0x8C and power_supply is not None:
                value = power_supply.read_output_current()
            else:
                value = read_power_status_value(controller, DEFAULT_I2C_ADDRESS, command)
            raw_command = " ".join(f"{item:02X}" for item in command)
            self.add_log(f"{name}: {value:.2f} {unit} ({raw_command})")
            self.statusBar().showMessage(f"{name}: {value:.2f} {unit}")
            return value
        except Exception as exc:
            QMessageBox.critical(self, name, user_facing_error_message(exc))
            return None

    def record_efficiency_from_vout(self, voltage_v: float) -> None:
        current_a = self.active_output_current_a
        if current_a is None or current_a <= 0.0:
            self.statusBar().showMessage("仅记录电流大于 0 A 的效率")
            self.add_log("已读取输出电压；0 A 测试点不绘制效率")
            self.pause_automatic_point_if_waiting("仅记录电流大于 0 A 的效率")
            return
        if self.pending_stable_point_current_a == current_a:
            self.statusBar().showMessage("请等待新设定的电流点稳定后再读取输出电压")
            self.add_log("已读取输出电压；新电流点尚未稳定，未更新效率")
            self.pause_automatic_point_if_waiting("当前电流点尚未稳定")
            return
        if current_a not in self.stable_power_points:
            self.statusBar().showMessage("请等待当前测试点稳定后再读取输出电压")
            self.add_log("已读取输出电压；暂无稳定功率点，未绘制效率")
            self.pause_automatic_point_if_waiting("当前测试点缺少稳定功率数据")
            return
        if voltage_v <= 0.0:
            self.statusBar().showMessage("输出电压必须大于 0 才能计算效率")
            self.add_log("已读取输出电压；输出电压为 0，未绘制效率")
            self.pause_automatic_point_if_waiting("输出电压必须大于 0 才能计算效率")
            return

        power_w = self.stable_power_points[current_a]
        self.efficiency_voltage_points[current_a] = voltage_v
        efficiency_percent = self.update_efficiency_point(current_a)
        if current_a == self.pending_auto_vout_current_a:
            self.cancel_auto_vout_read()
        self.update_stable_power_curve()
        self.statusBar().showMessage(f"{current_a:.3f} A 时效率：{efficiency_percent:.2f}%")
        self.add_log(
            f"效率点：{current_a:.3f} A，{power_w:.3f} W / "
            f"({current_a:.3f} A × {voltage_v:.3f} V) = {efficiency_percent:.2f}%"
        )

        queued = self.queue_excel_test_point(current_a, voltage_v, power_w, efficiency_percent / 100.0)
        self.automatic_controller.on_voltage_record_ready(current_a, queued, self.last_point_record_error)

    def pause_automatic_point_if_waiting(self, reason: str) -> None:
        if self.automatic_test_state == AutomaticTestState.WAITING_VOLTAGE:
            self.pause_automatic_test(reason)

    def queue_excel_test_point(
        self,
        current_a: float,
        voltage_v: float,
        power_w: float,
        efficiency: float,
    ) -> bool:
        self.last_point_record_error = ""
        if self.latest_spectrum_wavelength is None or self.latest_spectrum_intensity is None:
            self.last_point_record_error = "暂无光谱数据"
            self.add_log("已跳过 Excel 记录：暂无光谱数据")
            self.statusBar().showMessage("获得光谱数据后才能生成测试点")
            return False
        try:
            spectrum_peak_intensity = max(float(value) for value in self.latest_spectrum_intensity)
        except (TypeError, ValueError):
            spectrum_peak_intensity = 0.0
        self.latest_spectrum_peak_intensity = spectrum_peak_intensity
        if spectrum_peak_intensity < MIN_SPECTRUM_PEAK_INTENSITY:
            self.last_point_record_error = (
                f"光谱信号过弱：峰值 {spectrum_peak_intensity:.0f} counts，"
                f"必须至少达到 {MIN_SPECTRUM_PEAK_INTENSITY:.0f} counts"
            )
            self.statusBar().showMessage(self.last_point_record_error)
            self.add_log(self.last_point_record_error)
            return False
        saturation = detect_spectrum_saturation(self.latest_spectrum_intensity)
        if saturation.saturated:
            self.record_store.discard_pending(current_a)
            self.save_excel_button.setEnabled(
                any(current not in self.excel_recorded_currents for current in self.pending_excel_records)
            )
            self.save_status_label.setText(f"{current_a:.1f} A 光谱饱和，未加入保存队列")
            message = (
                f"{current_a:.1f} A 时光谱饱和"
                f"（{saturation.peak_intensity:.0f} 计数，连续 {saturation.consecutive_pixels} 个像素）；"
                "请缩短积分时间"
            )
            self.last_point_record_error = message
            self.statusBar().showMessage(message)
            self.add_log(message)
            return False
        stats = calculate_stats(self.latest_spectrum_wavelength, self.latest_spectrum_intensity)
        smsr = calculate_smsr(self.latest_spectrum_wavelength, self.latest_spectrum_intensity)
        self.record_store.queue(ExcelTestRecord(
            current_a=current_a,
            voltage_v=voltage_v,
            power_w=power_w,
            efficiency=efficiency,
            peak_wavelength_nm=stats.peak_wavelength_nm,
            centroid_nm=stats.centroid_nm,
            fwhm_nm=stats.fwhm_nm,
            pib=calculate_pib(self.latest_spectrum_wavelength, self.latest_spectrum_intensity),
            wavelength=list(self.latest_spectrum_wavelength),
            intensity=list(self.latest_spectrum_intensity),
            smsr_db=smsr.smsr_db,
        ))
        pending_count = len([current for current in self.pending_excel_records if current not in self.excel_recorded_currents])
        self.save_excel_button.setEnabled(pending_count > 0)
        self.save_status_label.setText(f"{pending_count} 个测试点待保存")
        self.statusBar().showMessage(f"{current_a:.1f} A 测试点已就绪；请单击“保存 Excel”")
        return True

    def save_pending_excel_records(self) -> None:
        if self.excel_save_thread is not None:
            return
        unsaved_records = self.record_store.unsaved_records()
        if not unsaved_records:
            QMessageBox.information(self, "保存 Excel", "没有尚未保存的测试点。")
            return
        if self.excel_workbook_path is None:
            try:
                self.begin_test_session(reset_records=False)
            except ValueError as exc:
                QMessageBox.warning(self, "保存 Excel", user_facing_error_message(exc))
                return

        records_snapshot = list(self.record_store.snapshot())
        self.excel_save_thread = ExcelSaveThread(self.excel_workbook_path, records_snapshot, self)
        self.excel_save_thread.saved.connect(self.on_excel_save_succeeded)
        self.excel_save_thread.failed.connect(self.on_excel_save_failed)
        self.excel_save_thread.finished.connect(self.on_excel_save_finished)
        self.save_excel_button.setEnabled(False)
        self.save_excel_button.setText("保存中…")
        self.start_all_button.setEnabled(False)
        self.save_status_label.setText(f"正在保存 {len(records_snapshot)} 个测试点…")
        self.add_log(f"正在后台保存 {len(records_snapshot)} 个测试点")
        self.excel_save_thread.start()

    def on_excel_save_succeeded(self, elapsed_s: float) -> None:
        thread = self.excel_save_thread
        if thread is None:
            return
        self.record_store.mark_saved(tuple(thread.records))
        remaining_count = len(
            [current for current in self.pending_excel_records if current not in self.excel_recorded_currents]
        )
        if remaining_count:
            self.save_status_label.setText(f"已在 {elapsed_s:.2f} 秒内保存；另有 {remaining_count} 个新测试点待保存")
        else:
            self.save_status_label.setText(f"已在 {elapsed_s:.2f} 秒内保存：{thread.path.name}")
        self.statusBar().showMessage(f"Excel 已在 {elapsed_s:.2f} 秒内保存：{thread.path.name}")
        self.add_log(f"Excel 已在 {elapsed_s:.2f} 秒内保存：{thread.path}")
        self.automatic_controller.on_record_saved()

    def on_excel_save_failed(self, message: str) -> None:
        self.save_status_label.setText("保存失败")
        self.add_log(f"Excel 保存失败：{message}")
        self.automatic_controller.on_record_save_failed(message)
        QMessageBox.critical(self, "保存 Excel", user_facing_error_message(message))

    def on_excel_save_finished(self) -> None:
        thread = self.excel_save_thread
        self.excel_save_thread = None
        automatic_idle = self.automatic_test_state in (AutomaticTestState.IDLE, AutomaticTestState.COMPLETED)
        self.save_excel_button.setEnabled(
            automatic_idle
            and any(current not in self.excel_recorded_currents for current in self.pending_excel_records)
        )
        self.save_excel_button.setText("保存 Excel")
        self.start_all_button.setEnabled(automatic_idle)
        if thread is not None:
            thread.deleteLater()
        self._continue_pending_close()

    def apply_output_current(self) -> None:
        if self._require_manual_i2c_controller() is None:
            return
        if not self.begin_power_supply_command("设置输出电流"):
            return
        try:
            power_supply = self.get_power_supply()
            if power_supply is None:
                raise RuntimeError("电源未连接")
            power_supply.set_current(self.set_current_spin.value())
            self.cancel_auto_vout_read()
            self.active_output_current_a = float(self.set_current_spin.value())
            self.pending_stable_point_current_a = self.active_output_current_a
            self.recorded_stable_point_current_a = None
            self.recorded_stable_point_generation = None
            if self.power_meter_reader is not None:
                self.pending_stable_point_generation = self.power_meter_reader.reset_stability_window()
            else:
                self.pending_stable_point_generation = None
            self.update_stable_power_curve()
            self.add_log(f"输出电流已设为 {self.set_current_spin.value():.1f} A")
            self.statusBar().showMessage(f"输出电流已设为 {self.set_current_spin.value():.1f} A")
        except Exception as exc:
            QMessageBox.critical(self, "设置电流", user_facing_error_message(exc))

    def refresh_power_meter_resources(self) -> None:
        current = self._selected_power_resource()
        try:
            import pyvisa

            rm = pyvisa.ResourceManager()
            try:
                resources = sorted(str(item) for item in rm.list_resources() if str(item).startswith("ASRL"))
            finally:
                rm.close()
            self.power_meter_combo.clear()
            self.power_meter_combo.addItems(resources)
            if current:
                index = self.power_meter_combo.findText(current)
                if index >= 0:
                    self.power_meter_combo.setCurrentIndex(index)
                else:
                    self.power_meter_combo.setEditText(current)
            elif resources:
                self.power_meter_combo.setCurrentIndex(0)
            self.statusBar().showMessage(f"找到 {len(resources)} 个串口资源")
            self.add_log(f"找到 {len(resources)} 个串口资源")
        except Exception as exc:
            QMessageBox.critical(self, "刷新端口", user_facing_error_message(exc))

    def set_power_meter_relative_zero(self, enabled: bool) -> None:
        resource = self._selected_power_resource()
        if not resource:
            QMessageBox.warning(self, "相对调零", "请先选择功率计。")
            return
        try:
            from tools.power_meter_mvp import CaihuangPowerMeter

            meter = CaihuangPowerMeter(resource)
            try:
                meter.set_relative_zero(enabled)
            finally:
                meter.close()
            state = "已启用" if enabled else "已停用"
            self.statusBar().showMessage(f"相对调零{state}")
            self.add_log(f"功率计相对调零{state}")
        except Exception as exc:
            QMessageBox.critical(self, "相对调零", user_facing_error_message(exc))

    def auto_detect_power_meters(self) -> None:
        if self.power_meter_detect_thread is not None:
            return
        self.power_meter_detect_thread = PowerMeterDetectThread(self._selected_power_resource(), self)
        self.power_meter_detect_thread.detected.connect(self.on_power_meter_detected)
        self.power_meter_detect_thread.status.connect(self.on_status)
        self.power_meter_detect_thread.failed.connect(self.on_power_meter_detect_failed)
        self.power_meter_detect_thread.finished.connect(self.on_power_meter_detect_finished)
        self.set_power_meter_detecting_state(True)
        self.statusBar().showMessage("正在检测功率计…")
        self.power_meter_detect_thread.start()

    def auto_detect_spectrometers(self) -> None:
        try:
            OceanSpectrometer, _calculate_stats = load_spectrometer_components(None)

            device_ids = OceanSpectrometer.detect()
            self.spectrometer_combo.clear()
            self.spectrometer_combo.addItem("自动选择第一台 Ocean Insight", None)
            if not device_ids:
                QMessageBox.warning(
                    self,
                    "光谱仪自动检测",
                    "OceanDirect 未找到光谱仪，请检查 Ocean Insight 驱动。",
                )
                self.statusBar().showMessage("未检测到光谱仪")
                return

            for device_id in device_ids:
                option = SpectrometerOption(device_id=int(device_id))
                self.spectrometer_combo.addItem(option.label(), option)
            self.spectrometer_combo.setCurrentIndex(0)
            self.statusBar().showMessage(f"检测到 {len(device_ids)} 台光谱仪")
            self.add_log(f"检测到 {len(device_ids)} 台光谱仪")
        except Exception as exc:
            QMessageBox.critical(self, "光谱仪自动检测", user_facing_error_message(exc))

    def collect_settings(self) -> CombinedTestSettings:
        return CombinedTestSettings(
            i2c_address=DEFAULT_I2C_ADDRESS,
            i2c_speed=DEFAULT_I2C_SPEED,
            set_current_a=self.set_current_spin.value(),
            power_resource=self._selected_power_resource(),
            power_meter_wavelength_nm=self.power_wavelength_spin.value(),
            software_gain=self.software_gain_spin.value(),
            integration_time_us=self.integration_spin.value(),
            interval_ms=self.interval_spin.value(),
            stable_window_s=self.stable_window_spin.value(),
            stable_tolerance_w=self.stable_tolerance_spin.value(),
            output_dir=Path(self.output_dir_field.text()).expanduser(),
            spectrometer_device_id=self._selected_spectrometer_device_id(),
        )

    def _selected_power_resource(self) -> str:
        option = self.power_meter_combo.currentData()
        if isinstance(option, PowerMeterOption):
            return option.resource
        return extract_power_resource_name(self.power_meter_combo.currentText())

    def _selected_spectrometer_device_id(self) -> int | None:
        option = self.spectrometer_combo.currentData()
        if isinstance(option, SpectrometerOption):
            return option.device_id
        return None

    def collect_power_meter_settings(self) -> PowerMeterSettings:
        return PowerMeterSettings(
            resource=self._selected_power_resource(),
            wavelength_nm=self.power_wavelength_spin.value(),
            software_gain=self.software_gain_spin.value(),
            interval_ms=self.power_meter_interval_spin.value(),
            stable_window_s=self.stable_window_spin.value(),
            stable_tolerance_w=self.stable_tolerance_spin.value(),
        )

    def on_stability_settings_changed(self, _value: float) -> None:
        """Synchronize live stability criteria with the acquisition thread."""
        self.reset_wavelength_stability_window()
        if self.power_meter_reader is not None:
            self.power_meter_reader.update_stability_settings(
                self.stable_window_spin.value(),
                self.stable_tolerance_spin.value(),
            )

    def reset_wavelength_stability_window(self) -> None:
        self.wavelength_stability_detector = WavelengthStabilityDetector(
            self.stable_window_spin.value(),
            WAVELENGTH_STABILITY_TOLERANCE_NM,
        )
        self.latest_wavelength_stable = False
        self.latest_wavelength_span_nm = math.inf

    def collect_spectrometer_settings(self) -> SpectrometerSettings:
        return SpectrometerSettings(
            integration_time_us=self.integration_spin.value(),
            interval_ms=self.interval_spin.value(),
            device_id=self._selected_spectrometer_device_id(),
            auto_integration_enabled=self.auto_integration_check.isChecked(),
        )

    def start_power_meter(self) -> None:
        if self.power_meter_reader is not None:
            return
        try:
            settings = self.collect_power_meter_settings()
        except Exception as exc:
            QMessageBox.warning(self, "功率计", user_facing_error_message(exc))
            return
        if not settings.resource:
            QMessageBox.warning(self, "功率计", "功率计资源不能为空。")
            return

        self.cancel_auto_vout_read()
        self.reset_power_curve()
        self.reset_stable_power_curve()
        self.active_output_current_a = float(self.set_current_spin.value())
        self.pending_stable_point_current_a = self.active_output_current_a
        self.pending_stable_point_generation = 0
        self.recorded_stable_point_current_a = None
        self.recorded_stable_point_generation = None
        self.add_log("正在启动功率计采集")
        self.power_meter_reader = PowerMeterReaderThread(settings, self)
        self.power_meter_reader.reading.connect(self.on_power_meter_reading)
        self.power_meter_reader.status.connect(self.on_status)
        self.power_meter_reader.ready.connect(self.on_power_meter_ready)
        self.power_meter_reader.failed.connect(self.on_power_meter_failed)
        self.power_meter_reader.finished.connect(self.on_power_meter_finished)
        self.power_meter_reader.start()
        self.set_power_meter_running_state(True)

    def stop_power_meter(self, wait_for_finish: bool = False) -> None:
        self.cancel_auto_vout_read()
        if self.power_meter_reader is not None:
            self.add_log("正在停止功率计采集")
            self.power_meter_reader.stop()
            if wait_for_finish:
                self.power_meter_reader.wait(3000)

    def start_spectrometer(self) -> None:
        if self.spectrometer_reader is not None:
            return
        try:
            settings = self.collect_spectrometer_settings()
        except Exception as exc:
            QMessageBox.warning(self, "光谱仪", user_facing_error_message(exc))
            return

        self.reset_spectrum_curve()
        self.copy_spectrum_button.setEnabled(False)
        self.save_spectrum_button.setEnabled(False)
        self.add_log("正在启动光谱仪采集")
        self.spectrometer_reader = SpectrometerReaderThread(settings, self)
        self.spectrometer_reader.reading.connect(self.on_spectrometer_reading)
        self.spectrometer_reader.spectrum.connect(self.on_spectrum_curve)
        self.spectrometer_reader.status.connect(self.on_status)
        self.spectrometer_reader.integration_time_changed.connect(self.on_integration_time_changed)
        self.spectrometer_reader.ready.connect(self.on_spectrometer_ready)
        self.spectrometer_reader.failed.connect(self.on_spectrometer_failed)
        self.spectrometer_reader.finished.connect(self.on_spectrometer_finished)
        self.spectrometer_reader.start()
        self.set_spectrometer_running_state(True)

    def on_integration_time_changed(self, integration_time_us: int) -> None:
        self.integration_spin.setValue(int(integration_time_us))
        self.reset_wavelength_stability_window()

    def stop_spectrometer(self, wait_for_finish: bool = False) -> None:
        if self.spectrometer_reader is not None:
            self.add_log("正在停止光谱仪采集")
            self.spectrometer_reader.stop()
            if wait_for_finish:
                self.spectrometer_reader.wait(3000)

    def update_stability_card(
        self,
        stable: bool,
        span_w: float,
        covered_window_s: float,
        tolerance_w: float | None = None,
    ) -> None:
        target_window_s = self.stable_window_spin.value() if hasattr(self, "stable_window_spin") else 0.0
        if tolerance_w is None:
            tolerance_w = self.stable_tolerance_spin.value() if hasattr(self, "stable_tolerance_spin") else 0.0
        self.live_plots.set_power_stability(
            stable,
            covered_window_s,
            target_window_s,
            span_w,
            tolerance_w,
        )

    def on_power_meter_reading(self, reading: PowerMeterReading) -> None:
        self.latest_power_meter_reading = reading
        self.live_plots.set_power_value(reading.power_w)
        tolerance_w = (
            reading.stable_tolerance_w
            if math.isfinite(reading.stable_tolerance_w)
            else stability_tolerance_for_power(reading.power_w)
        )
        signals_were_blocked = self.stable_tolerance_spin.blockSignals(True)
        try:
            self.stable_tolerance_spin.setValue(tolerance_w)
        finally:
            self.stable_tolerance_spin.blockSignals(signals_were_blocked)
        self.update_stability_card(reading.stable, reading.stable_span_w, reading.stable_window_s, tolerance_w)
        self.update_power_curve(reading.elapsed_s, reading.power_w)
        self.capture_stable_power_point(reading)

    def on_spectrometer_reading(self, reading: SpectrometerReading) -> None:
        if math.isfinite(reading.centroid_nm):
            result = self.wavelength_stability_detector.add_sample(time.monotonic(), reading.centroid_nm)
            was_stable = self.latest_wavelength_stable
            self.latest_wavelength_stable = result.stable
            self.latest_wavelength_span_nm = result.span_w
            if (
                was_stable
                and not result.stable
                and self.automatic_test_state == AutomaticTestState.WAITING_VOLTAGE
            ):
                self.invalidate_automatic_stability("中心波长不再稳定")
        else:
            self.latest_wavelength_stable = False
            self.latest_wavelength_span_nm = math.inf
        self.update_centroid_display(reading.centroid_nm)
        self.live_plots.set_spectrum_metrics(
            fwhm_nm=math.nan if self.latest_spectrum_saturated else reading.fwhm_nm,
        )
        self.update_spectrum_center_lock(reading)

    def on_live_reading(self, reading: LiveReading) -> None:
        self.live_plots.set_power_value(reading.power_w)
        self.update_centroid_display(reading.centroid_nm)
        self.live_plots.set_spectrum_metrics(
            fwhm_nm=math.nan if self.latest_spectrum_saturated else reading.fwhm_nm,
        )
        self.update_spectrum_center_lock(
            SpectrometerReading(
                peak_wavelength_nm=reading.peak_wavelength_nm,
                centroid_nm=reading.centroid_nm,
                fwhm_nm=reading.fwhm_nm,
            )
        )
        self.update_stability_card(
            reading.stable,
            reading.stable_span_w,
            reading.stable_window_s,
            stability_tolerance_for_power(reading.power_w),
        )
        self.update_power_curve(reading.elapsed_s, reading.power_w)

    def on_recorded(self, timestamp: str, measurement: CombinedMeasurement) -> None:
        self.save_status_label.setText(f"已记录 {measurement.set_current_a:.1f} A（{timestamp[11:]}）")
        self.add_log(
            "已记录稳定测试点："
            f"设定 {measurement.set_current_a} A，"
            f"输出电流 {measurement.output_current_a:.3f} A，"
            f"输出电压 {measurement.output_voltage_v:.3f} V，"
            f"功率 {measurement.power_w:.3f} W，"
            f"峰值波长 {measurement.peak_wavelength_nm:.3f} nm，"
            f"光谱 {measurement.spectrum_csv_path}"
        )

    def on_spectrum_curve(self, wavelength: Any, intensity: Any) -> None:
        self.latest_spectrum_wavelength = wavelength
        self.latest_spectrum_intensity = intensity
        saturation = detect_spectrum_saturation(intensity)
        try:
            self.latest_spectrum_peak_intensity = max(float(value) for value in intensity)
        except (TypeError, ValueError):
            self.latest_spectrum_peak_intensity = 0.0
        was_saturated = self.latest_spectrum_saturated
        self.latest_spectrum_saturated = saturation.saturated
        try:
            has_enough_pib_samples = len(intensity) >= 3
        except TypeError:
            has_enough_pib_samples = True
        pib = (
            calculate_pib(wavelength, intensity)
            if not saturation.saturated and has_enough_pib_samples
            else math.nan
        )
        smsr = calculate_smsr(wavelength, intensity) if not saturation.saturated else None
        self.live_plots.set_spectrum_metrics(
            pib=pib,
            smsr_db=math.nan if smsr is None else smsr.smsr_db,
            saturated=saturation.saturated,
        )
        if saturation.saturated and not was_saturated:
            message = (
                f"光谱饱和：峰值 {saturation.peak_intensity:.0f} 计数，连续 "
                f"{saturation.consecutive_pixels} 个像素；请缩短积分时间"
            )
            self.statusBar().showMessage(message)
            self.add_log(message)
        elif was_saturated and not saturation.saturated:
            self.statusBar().showMessage("光谱饱和状态已解除")
            self.add_log("光谱饱和状态已解除")
        self.copy_spectrum_button.setEnabled(True)
        self.save_spectrum_button.setEnabled(True)
        self.update_spectrum_curve(wavelength, intensity)

    def reset_curves(self) -> None:
        self.live_plots.reset_integrated_metrics()
        self.reset_power_curve()
        self.reset_stable_power_curve()
        self.reset_spectrum_curve()

    def reset_power_curve(self) -> None:
        self.live_plots.reset_power()

    def reset_stable_power_curve(self) -> None:
        self.cancel_auto_vout_read()
        self.stable_power_points.clear()
        self.efficiency_points.clear()
        self.efficiency_voltage_points.clear()
        self.recorded_stable_point_current_a = None
        self.recorded_stable_point_generation = None
        self.update_stable_power_curve()

    def capture_stable_power_point(self, reading: PowerMeterReading) -> None:
        current_a = self.pending_stable_point_current_a
        require_joint_stability = self.automatic_test_state == AutomaticTestState.WAITING_STABLE
        if current_a is None:
            if (
                not reading.stable
                and self.pending_auto_vout_current_a == self.active_output_current_a
                and self.pending_auto_vout_generation == reading.stability_generation
            ):
                self.cancel_auto_vout_read()
                self.add_log("功率不再稳定，已取消自动读取输出电压")
                if self.automatic_test_state == AutomaticTestState.WAITING_VOLTAGE:
                    self.invalidate_automatic_stability("功率不再稳定")
                    return
            self.update_latest_stable_power_point(reading)
            if (
                reading.stable
                and self.recorded_stable_point_current_a == self.active_output_current_a
                and self.recorded_stable_point_generation == reading.stability_generation
                and self.active_output_current_a not in self.efficiency_voltage_points
                and self.pending_auto_vout_current_a is None
            ):
                self.schedule_auto_vout_read()
            return
        if not reading.stable:
            return
        if require_joint_stability and not self.latest_wavelength_stable:
            return
        if (
            self.pending_stable_point_generation is not None
            and reading.stability_generation != self.pending_stable_point_generation
        ):
            return

        if current_a <= 0.0:
            self.pending_stable_point_current_a = None
            self.pending_stable_point_generation = None
            self.statusBar().showMessage("0 A 已稳定；不记录功率或效率点")
            self.add_log("0 A 已稳定；已跳过功率和效率点")
            return

        self.stable_power_points[current_a] = float(reading.power_w)
        self.efficiency_points.pop(current_a, None)
        self.efficiency_voltage_points.pop(current_a, None)
        self.pending_stable_point_current_a = None
        self.pending_stable_point_generation = None
        self.recorded_stable_point_current_a = current_a
        self.recorded_stable_point_generation = reading.stability_generation
        self.update_stable_power_curve()
        if require_joint_stability:
            self.statusBar().showMessage(
                f"已记录 {current_a:.3f} A 联合稳定点：{reading.power_w:.3f} W，"
                f"波长跨度 {self.latest_wavelength_span_nm:.3f} nm"
            )
            self.add_log(
                f"功率与波长稳定点：{current_a:.3f} A，{reading.power_w:.3f} W，"
                f"波长跨度 {self.latest_wavelength_span_nm:.3f} nm"
            )
        else:
            self.statusBar().showMessage(f"已记录 {current_a:.3f} A 时的稳定功率：{reading.power_w:.3f} W")
            self.add_log(f"稳定功率点：{current_a:.3f} A，{reading.power_w:.3f} W")
        self.schedule_auto_vout_read()
        self.automatic_controller.on_stable_power_captured(current_a)

    def update_latest_stable_power_point(self, reading: PowerMeterReading) -> None:
        current_a = self.recorded_stable_point_current_a
        if (
            current_a is None
            or current_a != self.active_output_current_a
            or self.recorded_stable_point_generation != reading.stability_generation
            or not reading.stable
        ):
            return

        self.stable_power_points[current_a] = float(reading.power_w)
        self.update_efficiency_point(current_a)
        self.update_stable_power_curve()

    def update_efficiency_point(self, current_a: float) -> float:
        power_w = self.stable_power_points[current_a]
        voltage_v = self.efficiency_voltage_points.get(current_a)
        if voltage_v is None or voltage_v <= 0.0:
            return math.nan
        efficiency_percent = power_w / current_a / voltage_v * 100.0
        self.efficiency_points[current_a] = efficiency_percent
        return efficiency_percent

    def update_stable_power_curve(self) -> None:
        self.live_plots.update_stable(self.stable_power_points, self.efficiency_points)

    def reset_spectrum_curve(self) -> None:
        self.centroid_display_samples.clear()
        self.latest_spectrum_saturated = False
        self.spectrum_center_candidate_nm = None
        self.spectrum_center_candidate_count = 0
        self.spectrum_center_locked_nm = None
        self.live_plots.reset_spectrum()

    def update_centroid_display(self, centroid_nm: float) -> None:
        if self.latest_spectrum_saturated:
            self.live_plots.set_spectrum_metrics(centroid_nm=math.nan, saturated=True)
            return
        value = float(centroid_nm)
        if not math.isfinite(value):
            self.live_plots.set_spectrum_metrics(centroid_nm=math.nan)
            return
        self.centroid_display_samples.append(value)
        self.live_plots.set_spectrum_metrics(centroid_nm=median(self.centroid_display_samples))

    def update_power_curve(self, elapsed_s: float, power_w: float) -> None:
        self.live_plots.update_power(elapsed_s, power_w)

    def update_spectrum_curve(self, wavelength: Any, intensity: Any) -> None:
        self.live_plots.update_spectrum(wavelength, intensity, self.spectrum_center_locked_nm)

    def update_spectrum_center_lock(self, reading: SpectrometerReading) -> None:
        if self.spectrum_center_locked_nm is not None:
            return

        # The whole-spectrum centroid moves with baseline and broadband noise.
        # The highest peak is the stable reference that users expect the ±20 nm
        # view to follow; centroid remains a fallback for incomplete readings.
        center_nm = reading.peak_wavelength_nm
        if not math.isfinite(float(center_nm)):
            center_nm = reading.centroid_nm
        center = float(center_nm)
        if not math.isfinite(center):
            self.spectrum_center_candidate_nm = None
            self.spectrum_center_candidate_count = 0
            return

        if (
            self.spectrum_center_candidate_nm is None
            or abs(center - self.spectrum_center_candidate_nm) > SPECTRUM_CENTER_LOCK_TOLERANCE_NM
        ):
            self.spectrum_center_candidate_nm = center
            self.spectrum_center_candidate_count = 1
        else:
            self.spectrum_center_candidate_count += 1
            count = self.spectrum_center_candidate_count
            previous = self.spectrum_center_candidate_nm
            self.spectrum_center_candidate_nm = previous + (center - previous) / count

        if self.spectrum_center_candidate_count >= SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES:
            self.spectrum_center_locked_nm = self.spectrum_center_candidate_nm
            self.on_status(
                "光谱横轴已锁定："
                f"{self.spectrum_center_locked_nm:.3f} nm ± {SPECTRUM_CENTER_LOCK_HALF_RANGE_NM:g} nm"
            )
            if self.latest_spectrum_wavelength is not None and self.latest_spectrum_intensity is not None:
                self.update_spectrum_curve(self.latest_spectrum_wavelength, self.latest_spectrum_intensity)

    def copy_spectrum_csv(self) -> None:
        if self.latest_spectrum_wavelength is None or self.latest_spectrum_intensity is None:
            return
        output = spectrum_curve_to_rows(self.latest_spectrum_wavelength, self.latest_spectrum_intensity)
        text = "\n".join(",".join(row) for row in output) + "\n"
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("光谱已复制为 CSV")
        self.add_log("光谱已复制为 CSV")

    def save_spectrum_csv(self) -> None:
        if self.latest_spectrum_wavelength is None or self.latest_spectrum_intensity is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存光谱 CSV", "spectrum.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        save_spectrum_curve(Path(path), self.latest_spectrum_wavelength, self.latest_spectrum_intensity)
        self.statusBar().showMessage(f"已保存：{path}")
        self.add_log(f"光谱 CSV 已保存：{path}")

    def on_power_meter_detected(self, options: list[PowerMeterOption]) -> None:
        self.power_meter_combo.clear()
        if not options:
            self.power_meter_combo.addItem(DEFAULT_POWER_RESOURCE, None)
            QMessageBox.warning(self, "功率计自动检测", "未检测到支持的功率计。")
            self.statusBar().showMessage("未检测到支持的功率计")
            return

        for option in options:
            self.power_meter_combo.addItem(option.label(), option)
        self.power_meter_combo.setCurrentIndex(0)
        self.statusBar().showMessage(f"检测到 {len(options)} 台功率计")
        self.add_log(f"检测到 {len(options)} 台功率计")

    def on_power_meter_detect_failed(self, message: str) -> None:
        self.add_log(f"功率计自动检测错误：{message}")
        QMessageBox.critical(self, "功率计自动检测", user_facing_error_message(message))

    def on_power_meter_detect_finished(self) -> None:
        thread = self.power_meter_detect_thread
        self.power_meter_detect_thread = None
        self.set_power_meter_detecting_state(False)
        if thread is not None:
            thread.deleteLater()
        self._continue_pending_close()

    def on_status(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self.add_log(message)

    def on_power_meter_failed(self, message: str) -> None:
        self.add_log(f"功率计错误：{message}")
        display_message = user_facing_error_message(message)
        self.automatic_controller.on_acquisition_failed("功率计", message)
        QMessageBox.critical(self, "功率计错误", display_message)

    def on_spectrometer_failed(self, message: str) -> None:
        self.add_log(f"光谱仪错误：{message}")
        display_message = user_facing_error_message(message)
        self.automatic_controller.on_acquisition_failed("光谱仪", message)
        QMessageBox.critical(self, "光谱仪错误", display_message)

    def on_power_meter_finished(self) -> None:
        should_pause = self.automatic_measurement_is_active()
        thread = self.power_meter_reader
        self.automatic_power_meter_ready = False
        self.power_meter_reader = None
        self.set_power_meter_running_state(False)
        self.statusBar().showMessage("功率计已停止")
        self.add_log("功率计已停止")
        if should_pause:
            self.automatic_controller.on_acquisition_stopped("功率计")
        if thread is not None:
            thread.deleteLater()
        self._continue_pending_close()

    def on_spectrometer_finished(self) -> None:
        should_pause = self.automatic_measurement_is_active()
        thread = self.spectrometer_reader
        self.automatic_spectrometer_ready = False
        self.spectrometer_reader = None
        self.set_spectrometer_running_state(False)
        self.statusBar().showMessage("光谱仪已停止")
        self.add_log("光谱仪已停止")
        if should_pause:
            self.automatic_controller.on_acquisition_stopped("光谱仪")
        if thread is not None:
            thread.deleteLater()
        self._continue_pending_close()

    def set_power_meter_running_state(self, running: bool) -> None:
        detecting = self.power_meter_detect_thread is not None
        self.start_power_meter_button.setHidden(running)
        self.stop_power_meter_button.setHidden(not running)
        self.start_power_meter_button.setEnabled(not running and not detecting)
        self.stop_power_meter_button.setEnabled(running)
        self.detect_power_meter_button.setEnabled(not running and not detecting)
        self.refresh_power_meter_button.setEnabled(not running and not detecting)
        self.rel_zero_check.setEnabled(not running and not detecting)
        self.power_meter_combo.setEnabled(not running and not detecting)
        self.power_wavelength_spin.setEnabled(not running and not detecting)
        self.software_gain_spin.setEnabled(not running and not detecting)
        self.power_meter_interval_spin.setEnabled(not running and not detecting)
        self.update_global_status()

    def set_power_meter_detecting_state(self, detecting: bool) -> None:
        running = self.power_meter_reader is not None
        self.start_power_meter_button.setHidden(running)
        self.stop_power_meter_button.setHidden(not running)
        self.start_power_meter_button.setEnabled(not running and not detecting)
        self.stop_power_meter_button.setEnabled(running)
        self.detect_power_meter_button.setEnabled(not running and not detecting)
        self.refresh_power_meter_button.setEnabled(not running and not detecting)
        self.rel_zero_check.setEnabled(not running and not detecting)
        self.power_meter_combo.setEnabled(not running and not detecting)
        self.power_wavelength_spin.setEnabled(not running and not detecting)
        self.software_gain_spin.setEnabled(not running and not detecting)
        self.power_meter_interval_spin.setEnabled(not running and not detecting)
        self.update_global_status()

    def set_spectrometer_running_state(self, running: bool) -> None:
        self.start_spectrometer_button.setHidden(running)
        self.stop_spectrometer_button.setHidden(not running)
        self.start_spectrometer_button.setEnabled(not running)
        self.stop_spectrometer_button.setEnabled(running)
        self.detect_spectrometer_button.setEnabled(not running)
        self.spectrometer_combo.setEnabled(not running)
        self.integration_spin.setEnabled(not running)
        self.auto_integration_check.setEnabled(not running)
        self.interval_spin.setEnabled(not running)
        self.update_global_status()

    def add_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.setText(f"[{timestamp}] {message}")

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if hasattr(self, "live_plots") and hasattr(self, "monitor_panel"):
            available_width = max(
                self.monitor_panel.width(),
                self.width() - self.left_control_panel.width() - 64,
            )
            self.live_plots.relayout(available_width)

    @staticmethod
    def _format_optional(value: float) -> str:
        if not math.isfinite(float(value)):
            return "--"
        return f"{value:.3f}"

    @staticmethod
    def _thread_is_running(thread: Any | None) -> bool:
        if thread is None:
            return False
        is_running = getattr(thread, "isRunning", None)
        return bool(is_running()) if callable(is_running) else False

    def _background_tasks_are_running(self) -> bool:
        return any(
            self._thread_is_running(thread)
            for thread in (
                self.excel_save_thread,
                self.power_meter_detect_thread,
                self.power_meter_reader,
                self.spectrometer_reader,
            )
        )

    def _request_background_stop(self) -> None:
        if self.power_meter_detect_thread is not None:
            self.power_meter_detect_thread.stop()
        self.stop_power_meter()
        self.stop_spectrometer()

    def _continue_pending_close(self) -> None:
        if self.close_after_background_tasks and not self._background_tasks_are_running():
            self.close_after_background_tasks = False
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.automatic_test_state not in (AutomaticTestState.IDLE, AutomaticTestState.COMPLETED):
            event.ignore()
            self.close_after_automatic_ramp_down = True
            if self.automatic_test_state != AutomaticTestState.RAMPING_DOWN:
                self.begin_automatic_ramp_down()
            return
        self.save_input_settings()
        self._request_background_stop()
        if self._background_tasks_are_running():
            self.close_after_background_tasks = True
            self.statusBar().showMessage("正在安全停止后台采集，请稍候…")
            event.ignore()
            return
        self.close_after_background_tasks = False
        if self.manual_ch341_controller is not None:
            if self.power_supply_controller_kind == "tdk":
                try:
                    if bool(getattr(self.manual_ch341_controller, "output_enabled", False)):
                        self.manual_ch341_controller.set_output_enabled(False)
                    self.manual_ch341_controller.disconnect_device()
                except Exception as exc:
                    event.ignore()
                    QMessageBox.critical(
                        self,
                        "TDK 输出",
                        f"关闭 TDK 输出失败，窗口保持开启。\n{user_facing_error_message(exc)}",
                    )
                    return
            else:
                try:
                    self.manual_ch341_controller.disconnect_device()
                except Exception:
                    pass
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    apply_application_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
