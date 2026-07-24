"""Qt main window for the combined optical test application."""

from __future__ import annotations

import math
import os
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from PySide6.QtCore import QEvent, QLockFile, QSettings, QStandardPaths, QTimer, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QSizePolicy,
    QStackedWidget,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .automatic_controller import AutomaticTestController, AutomaticTestTerminalOutcome
from .automation import (
    AutomaticTestOrchestrator,
    AutomaticTestSettings,
    AutomaticTestState,
    MIN_POWER_SUPPLY_COMMAND_INTERVAL_S,
    build_test_currents,
    validate_automatic_test_settings,
)
from .core import (
    CombinedMeasurement,
    PowerStabilityDetector,
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
from .record_store import (
    AttemptValidity,
    DeviceSnapshot,
    RecordStore,
    SessionRecordStore,
)
from .plot_types import PlotLayoutContext
from .spectrum import (
    SPECTRUM_CENTER_LOCK_HALF_RANGE_NM,
    SPECTRUM_CENTER_LOCK_REQUIRED_SAMPLES,
    SPECTRUM_CENTER_LOCK_TOLERANCE_NM,
    detect_spectrum_saturation,
)
from .excel_export import ExcelTestRecord, sanitize_sn
from .spectrum_math import calculate_pib, calculate_smsr, calculate_stats
from .shipping_report import (
    generate_shipping_report,
    inspect_shipping_workbook,
    save_shipping_report_preferences,
)
from .shipping_report_dialog import ShippingReportConfigurationDialog
from .tdk_power_supply import (
    TdkLambdaPowerSupply,
    compensate_tdk_output_voltage,
    list_tdk_serial_resources,
)
from .theme import FontRole, apply_application_theme, font_for_role, semantic_colors_for_palette
from tools.pd_daq_mvp import PdDaqPanel
from tools.visa_session import visa_resource_manager


DEFAULT_POWER_RESOURCE = "ASRL3::INSTR"
UNDETECTED_POWER_METER_NAME = "功率计（待检测）"
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
BACKGROUND_STOP_TIMEOUT_S = 10.0
SPECTRUM_UI_REFRESH_MS = 200
PREPARE_CHECKLIST_MIN_WIDTH = 300
NAVIGATION_WIDTH = 148
MANUAL_COLUMN_MIN_WIDTH = 360
LEGACY_CURRENT_LIMIT_A = 20.0
TDK_CURRENT_INPUT_MAX_A = math.inf


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
    def __init__(
        self,
        input_settings: QSettings | None = None,
        *,
        headless: bool = False,
    ) -> None:
        super().__init__()
        self.headless = bool(headless)
        self.input_settings = input_settings or QSettings("Changguang Huaxin", "Pump Driver Integrated Test")
        self.setWindowTitle("Power Test")
        self.resize(1280, 800)
        self.setMinimumSize(1100, 700)
        self.power_meter_detect_thread: PowerMeterDetectThread | None = None
        self._power_meter_detection_silent = False
        self.power_meter_reader: PowerMeter | None = None
        self.spectrometer_reader: SpectrumMeter | None = None
        self._power_meter_fault_message = ""
        self._spectrometer_fault_message = ""
        self.manual_ch341_controller: Any | None = None
        self.power_supply_controller_kind = "ch341"
        self.latest_spectrum_wavelength: Any | None = None
        self.latest_spectrum_intensity: Any | None = None
        self.latest_spectrum_smsr_db: float | None = None
        self.spectrum_refresh_timer = QTimer(self)
        self.spectrum_refresh_timer.setInterval(SPECTRUM_UI_REFRESH_MS)
        self.spectrum_refresh_timer.timeout.connect(self.refresh_latest_spectrum)
        self.stable_power_points: dict[float, float] = {}
        self.efficiency_points: dict[float, float] = {}
        self.efficiency_voltage_points: dict[float, float] = {}
        self.active_output_current_a: float | None = None
        self.manual_power_tab_lock_active = False
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
        self.test_session_station = ""
        self.record_store: RecordStore = SessionRecordStore()
        self.last_raw_output_voltage_v = math.nan
        self.excel_save_thread: ExcelSaveThread | None = None
        self._excel_export_retry_blocked = False
        self._automatic_finalization_pending: tuple[ExcelTestRecord | None, str] | None = None
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
        self.background_stop_timeout_timer = QTimer(self)
        self.background_stop_timeout_timer.setSingleShot(True)
        self.background_stop_timeout_timer.timeout.connect(self.on_background_stop_timeout)
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
        self.automatic_pause_safety_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.automatic_pause_safety_timer.timeout.connect(self.on_automatic_pause_safety_timeout)
        self.automatic_run_started_monotonic_s: float | None = None
        self.automatic_elapsed_timer = QTimer(self)
        self.automatic_elapsed_timer.setInterval(1000)
        self.automatic_elapsed_timer.timeout.connect(self.update_automatic_elapsed)

        self.central_shell = QWidget(self)
        self.setCentralWidget(self.central_shell)
        application_layout = QGridLayout(self.central_shell)
        application_layout.setContentsMargins(0, 0, 0, 0)
        application_layout.setSpacing(0)
        self._build_navigation_panel(application_layout)
        self._build_global_status_bar(application_layout)

        self.main_tabs = QTabWidget(self.central_shell)
        self.main_tabs.tabBar().hide()
        application_layout.addWidget(self.main_tabs, 1, 1)
        application_layout.setColumnStretch(1, 1)
        application_layout.setRowStretch(1, 1)

        self.content_widget = QWidget(self.main_tabs)
        self.automatic_tab_index = self.main_tabs.addTab(self.content_widget, "自动测试")
        automatic_layout = QVBoxLayout(self.content_widget)
        automatic_layout.setContentsMargins(16, 14, 16, 12)
        automatic_layout.setSpacing(10)

        self.automatic_stack = QStackedWidget(self.content_widget)
        automatic_layout.addWidget(self.automatic_stack, stretch=1)
        self.automatic_prepare_page = QWidget(self.automatic_stack)
        self.automatic_run_page = QWidget(self.automatic_stack)
        self.automatic_result_page = QWidget(self.automatic_stack)
        self.automatic_prepare_index = self.automatic_stack.addWidget(self.automatic_prepare_page)
        self.automatic_run_index = self.automatic_stack.addWidget(self.automatic_run_page)
        self.automatic_result_index = self.automatic_stack.addWidget(self.automatic_result_page)

        prepare_layout = QHBoxLayout(self.automatic_prepare_page)
        prepare_layout.setContentsMargins(0, 0, 0, 0)
        prepare_layout.setSpacing(12)
        self.prepare_scroll_area = QScrollArea(self.automatic_prepare_page)
        self.prepare_scroll_area.setWidgetResizable(True)
        self.prepare_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.prepare_scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.prepare_content = QWidget(self.prepare_scroll_area)
        self.prepare_scroll_area.setWidget(self.prepare_content)
        prepare_left = QVBoxLayout(self.prepare_content)
        self.prepare_left_layout = prepare_left
        prepare_left.setContentsMargins(0, 0, 0, 0)
        prepare_left.setSpacing(10)
        self._build_session_group(prepare_left)
        self._build_automatic_test_group(prepare_left)
        self._build_device_prepare_group(prepare_left)
        prepare_left.addStretch(1)
        prepare_layout.addWidget(self.prepare_scroll_area, stretch=1)
        self._build_preflight_panel(prepare_layout)

        self.manual_page = QWidget(self.main_tabs)
        self.manual_page.setFont(font_for_role(FontRole.BODY))
        self.manual_tab_index = self.main_tabs.addTab(self.manual_page, "手动调试")
        manual_layout = QVBoxLayout(self.manual_page)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.setSpacing(0)
        self.manual_scroll_area = QScrollArea(self.manual_page)
        self.manual_scroll_area.setWidgetResizable(True)
        self.manual_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.manual_scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.manual_scroll_content = QWidget(self.manual_scroll_area)
        self.manual_scroll_area.setWidget(self.manual_scroll_content)
        manual_content_layout = QVBoxLayout(self.manual_scroll_content)
        manual_content_layout.setContentsMargins(16, 14, 16, 12)
        manual_content_layout.setSpacing(10)
        manual_layout.addWidget(self.manual_scroll_area)
        # Compatibility aliases retained for callers that focus manual settings.
        self.left_control_panel = self.manual_scroll_area
        self._build_manual_toolbar(manual_content_layout)
        self.left_control_content = QWidget(self.manual_scroll_content)
        manual_columns = QHBoxLayout(self.left_control_content)
        manual_columns.setContentsMargins(0, 0, 0, 0)
        manual_columns.setSpacing(10)
        power_column_widget = QWidget(self.left_control_content)
        measurement_column_widget = QWidget(self.left_control_content)
        power_column = QVBoxLayout(power_column_widget)
        measurement_column = QVBoxLayout(measurement_column_widget)
        for column in (power_column, measurement_column):
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(8)
        power_column_widget.setMinimumWidth(MANUAL_COLUMN_MIN_WIDTH)
        measurement_column_widget.setMinimumWidth(MANUAL_COLUMN_MIN_WIDTH)
        self._build_power_supply_group(power_column)
        power_column.addStretch(1)
        self._build_power_meter_group(measurement_column)
        self._build_spectrometer_group(measurement_column)
        self._wire_prepare_device_controls()
        measurement_column.addStretch(1)
        manual_columns.addWidget(power_column_widget, stretch=1)
        manual_columns.addWidget(measurement_column_widget, stretch=1)
        manual_content_layout.addWidget(self.left_control_content)
        self.manual_monitor_panel = QWidget(self.manual_scroll_content)
        self.manual_monitor_layout = QVBoxLayout(self.manual_monitor_panel)
        self.manual_monitor_layout.setContentsMargins(0, 0, 0, 0)
        self.manual_monitor_layout.setSpacing(0)
        manual_content_layout.addWidget(self.manual_monitor_panel, stretch=1)
        self._build_log_panel(manual_content_layout)

        run_layout = QVBoxLayout(self.automatic_run_page)
        run_layout.setContentsMargins(0, 0, 0, 0)
        run_layout.setSpacing(10)
        self._build_automatic_run_header(run_layout)
        self.monitor_panel = QWidget(self.automatic_run_page)
        monitor = QVBoxLayout(self.monitor_panel)
        monitor.setContentsMargins(0, 0, 0, 0)
        monitor.setSpacing(10)
        self.automatic_monitor_layout = monitor
        run_layout.addWidget(self.monitor_panel, stretch=1)
        self._build_curve_panel(monitor)
        self._build_automatic_run_footer(run_layout)

        self._build_automatic_result_page()

        self.pd_panel = PdDaqPanel(
            self.main_tabs,
            auto_refresh=False,
            headless=self.headless,
        )
        self.pd_tab_index = self.main_tabs.addTab(self.pd_panel, "PD 采集")
        # Excel saving remains part of the automatic-test workflow, but it no
        # longer has a separate records page or operator-facing database action.
        self.save_status_label = QLabel("暂无可保存的测试点", self)
        self.save_status_label.hide()
        self.save_excel_button = QPushButton("保存 Excel", self)
        self.save_excel_button.hide()
        self._build_navigation_buttons()
        self.main_tabs.currentChanged.connect(self.on_main_tab_changed)
        self.pd_panel.running_changed.connect(lambda _running: self.update_global_status())
        self.pd_panel.acquisition_finished.connect(self._continue_pending_close)
        self._configure_button_semantics()
        self._disable_wheel_input_changes()
        self._restore_input_settings()
        self._connect_preflight_updates()
        self._configure_keyboard_focus_order()

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("就绪")
        self.update_global_status()
        self.refresh_preflight_checklist()

    def _build_navigation_panel(self, parent: QGridLayout) -> None:
        self.navigation_panel = QWidget(self.central_shell)
        self.navigation_panel.setObjectName("navigationPanel")
        self.navigation_panel.setFixedWidth(NAVIGATION_WIDTH)
        navigation_layout = QVBoxLayout(self.navigation_panel)
        navigation_layout.setContentsMargins(12, 16, 12, 14)
        navigation_layout.setSpacing(6)

        navigation_title = QLabel("综合测试", self.navigation_panel)
        navigation_title.setObjectName("navigationTitle")
        navigation_title.setFont(font_for_role(FontRole.PAGE_TITLE))
        navigation_layout.addWidget(navigation_title)
        navigation_subtitle = QLabel("设备与数据采集", self.navigation_panel)
        navigation_subtitle.setFont(font_for_role(FontRole.SECONDARY))
        navigation_subtitle.setStyleSheet(
            f"color: {semantic_colors_for_palette(self.palette()).secondary_text};"
        )
        navigation_layout.addWidget(navigation_subtitle)
        navigation_layout.addSpacing(14)

        self.navigation_button_group = QButtonGroup(self.navigation_panel)
        self.navigation_button_group.setExclusive(True)
        self.navigation_buttons_layout = navigation_layout
        self.navigation_buttons: dict[int, QPushButton] = {}
        parent.addWidget(self.navigation_panel, 0, 0, 2, 1)

    def _build_navigation_buttons(self) -> None:
        pages = (
            (self.automatic_tab_index, "自动测试"),
            (self.manual_tab_index, "手动调试"),
            (self.pd_tab_index, "PD 采集"),
        )
        for index, title in pages:
            button = QPushButton(title, self.navigation_panel)
            button.setCheckable(True)
            button.setProperty("navigation", True)
            button.setAccessibleName(f"切换到{title}")
            button.clicked.connect(lambda _checked=False, page_index=index: self._activate_navigation(page_index))
            self.navigation_button_group.addButton(button, index)
            self.navigation_buttons[index] = button
            self.navigation_buttons_layout.addWidget(button)
        self.navigation_buttons_layout.addStretch(1)
        self._sync_navigation_state(self.main_tabs.currentIndex())

    def _activate_navigation(self, index: int) -> None:
        if self.main_tabs.isTabEnabled(index):
            self.main_tabs.setCurrentIndex(index)
        else:
            self._sync_navigation_state(self.main_tabs.currentIndex())

    def _sync_navigation_state(self, index: int) -> None:
        if not hasattr(self, "navigation_buttons"):
            return
        for page_index, button in self.navigation_buttons.items():
            button.setChecked(page_index == index)
        titles = {
            getattr(self, "automatic_tab_index", -1): "自动测试",
            getattr(self, "manual_tab_index", -1): "手动调试",
            getattr(self, "pd_tab_index", -1): "PD 采集",
        }
        if hasattr(self, "page_title_label"):
            self.page_title_label.setText(titles.get(index, "综合测试"))

    def _sync_navigation_access(self) -> None:
        if not hasattr(self, "navigation_buttons"):
            return
        for index, button in self.navigation_buttons.items():
            button.setEnabled(self.main_tabs.isTabEnabled(index))
            button.setToolTip(self.main_tabs.tabToolTip(index))

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
        saved_device_type = str(settings.value(prefix + "power_meter_device_type", "")).strip()
        saved_detail = str(settings.value(prefix + "power_meter_detail", "")).strip()
        saved_driver_kind = str(settings.value(prefix + "power_meter_driver_kind", "caihuang")).strip()
        resource_index = self._find_power_meter_resource_index(saved_resource)
        if resource_index >= 0 and saved_device_type:
            saved_option = PowerMeterOption(
                resource=saved_resource,
                device_type=saved_device_type,
                detail=saved_detail,
                driver_kind=saved_driver_kind or "caihuang",
            )
            self.power_meter_combo.setItemText(resource_index, saved_option.label())
            self.power_meter_combo.setItemData(resource_index, saved_option)
        elif resource_index < 0 and saved_resource:
            resource_index = self._add_power_meter_resource_option(
                saved_resource,
                device_type=saved_device_type or UNDETECTED_POWER_METER_NAME,
                detail=saved_detail,
                driver_kind=saved_driver_kind or "caihuang",
            )
        if resource_index >= 0:
            self.power_meter_combo.setCurrentIndex(resource_index)
            # Both workflow pages share the item model but editable combo boxes
            # keep separate line-edit text. Updating the current item's label
            # does not reliably notify the preparation-page line edit.
            restored_label = self.power_meter_combo.itemText(resource_index)
            self.power_meter_combo.setEditText(restored_label)
            if hasattr(self, "prepare_power_meter_combo"):
                self.prepare_power_meter_combo.setCurrentIndex(resource_index)
                self.prepare_power_meter_combo.setEditText(restored_label)
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
        self.auto_use_spectrometer_check.setChecked(
            settings.value(
                prefix + "auto_use_spectrometer",
                self.auto_use_spectrometer_check.isChecked(),
                type=bool,
            )
        )
        self.sn_field.setText(str(settings.value(prefix + "sn", self.sn_field.text())))
        self.product_model_field.setText(
            str(settings.value(prefix + "product_model", self.product_model_field.text()))
        )
        self.batch_field.setText(str(settings.value(prefix + "batch", self.batch_field.text())))
        self.test_station_field.setText(
            str(settings.value(prefix + "test_station", self.test_station_field.text()))
        )
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
        power_meter_option = self.power_meter_combo.currentData()
        if isinstance(power_meter_option, PowerMeterOption):
            settings.setValue(prefix + "power_meter_device_type", power_meter_option.device_type)
            settings.setValue(prefix + "power_meter_detail", power_meter_option.detail)
            settings.setValue(prefix + "power_meter_driver_kind", power_meter_option.driver_kind)
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
        settings.setValue(prefix + "auto_use_spectrometer", self.auto_use_spectrometer_check.isChecked())
        settings.setValue(prefix + "sn", self.sn_field.text().strip())
        settings.setValue(prefix + "product_model", self.product_model_field.text().strip())
        settings.setValue(prefix + "batch", self.batch_field.text().strip())
        settings.setValue(prefix + "test_station", self.test_station_field.text().strip())
        settings.setValue(prefix + "output_dir", self.output_dir_field.text().strip())
        settings.sync()

    def _build_global_status_bar(self, parent: QGridLayout) -> None:
        self.page_header = QWidget(self.central_shell)
        self.page_header.setObjectName("pageHeader")
        row = QHBoxLayout(self.page_header)
        row.setContentsMargins(16, 10, 16, 8)
        row.setSpacing(10)

        self.page_title_label = QLabel("自动测试", self.page_header)
        self.page_title_label.setFont(font_for_role(FontRole.PAGE_TITLE))
        row.addWidget(self.page_title_label)

        self.global_status_label = QLabel("", self)
        self.global_status_label.setFont(font_for_role(FontRole.SECTION_TITLE))
        self.global_status_label.setStyleSheet(
            f"color: {semantic_colors_for_palette(self.palette()).secondary_text};"
        )
        self.global_status_label.hide()
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

        self.global_progress_label = QLabel("准备测试", self)
        self.global_progress_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.global_progress_label.setMinimumWidth(180)
        self.global_progress_label.hide()

        parent.addWidget(self.page_header, 0, 1)

    def _build_manual_toolbar(self, parent: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        title = QLabel("设备手动控制与诊断", self.manual_page)
        title.setFont(font_for_role(FontRole.SECTION_TITLE))
        description = QLabel("按电源、功率计、光谱仪逐台连接和诊断", self.manual_page)
        description.setStyleSheet(
            f"color: {semantic_colors_for_palette(self.palette()).secondary_text};"
        )
        row.addWidget(title)
        row.addWidget(description)
        row.addStretch(1)
        self.emergency_stop_button = QPushButton("紧急停止", self.manual_page)
        self.emergency_stop_button.setAccessibleName("手动测试紧急停止")
        self.emergency_stop_button.setToolTip("立即将电源电流置零、关闭 TDK 输出，并停止全部采集设备")
        self.emergency_stop_button.setMinimumSize(112, 36)
        self.emergency_stop_button.clicked.connect(self.emergency_stop)
        row.addWidget(self.emergency_stop_button)
        self.stop_all_button = QPushButton("停止全部设备", self.manual_page)
        self.stop_all_button.setMinimumSize(112, 32)
        self.stop_all_button.clicked.connect(self.stop_all)
        row.addWidget(self.stop_all_button)
        parent.addLayout(row)

    def _set_status_indicator(self, indicator: QLabel, state: str | bool) -> None:
        semantic = semantic_colors_for_palette(self.palette())
        normalized_state = ("ready" if state else "stopped") if isinstance(state, bool) else state
        color, label = {
            "ready": (semantic.success_text, "已就绪"),
            "pending": (semantic.warning_text, "启动或检测中"),
            "error": (semantic.error_text, "故障"),
            "stopped": (semantic.secondary_text, "已停止"),
        }.get(normalized_state, (semantic.secondary_text, str(normalized_state)))
        indicator.setStyleSheet(
            f"background-color: {color}; border: 1px solid {color}; border-radius: 6px;"
        )
        indicator.setToolTip(label)

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

    def _build_manual_details_dialog(
        self,
        group: QGroupBox,
        accessible_name: str,
    ) -> tuple[QPushButton, QDialog, QWidget, QFormLayout]:
        button = QPushButton("详细配置…", group)
        button.setAccessibleName(f"打开{accessible_name}详细配置")
        button.setFont(font_for_role(FontRole.SECONDARY))
        button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._configure_action_button(button, minimum_width=96)

        dialog = QDialog(self)
        dialog.setObjectName(f"{accessible_name}DetailsDialog")
        dialog.setWindowTitle(f"{accessible_name}详细配置")
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setModal(False)
        dialog.setMinimumWidth(480)
        dialog.setFont(font_for_role(FontRole.BODY))
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(16, 16, 16, 12)
        dialog_layout.setSpacing(12)

        content = QWidget(dialog)
        form = QFormLayout(content)
        self._configure_left_form(form)
        form.setContentsMargins(0, 0, 0, 0)
        dialog_layout.addWidget(content)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        close_button = button_box.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText("关闭")
        button_box.rejected.connect(dialog.reject)
        dialog_layout.addWidget(button_box)
        button.clicked.connect(
            lambda _checked=False, popup=dialog: self._show_manual_details_dialog(popup)
        )
        return button, dialog, content, form

    @staticmethod
    def _show_manual_details_dialog(dialog: QDialog) -> None:
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    @staticmethod
    def _keep_manual_group_compact(group: QGroupBox) -> None:
        group.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

    @staticmethod
    def _configure_action_button(button: QPushButton, minimum_width: int = 88) -> None:
        button.setMinimumWidth(minimum_width)
        button.setMinimumHeight(28)

    def _configure_keyboard_focus_order(self) -> None:
        """Keep the preparation workflow predictable for keyboard operators."""

        focus_order = (
            self.sn_field,
            self.product_model_field,
            self.batch_field,
            self.test_station_field,
            self.output_dir_field,
            self.browse_button,
            self.auto_initial_current_spin,
            self.auto_target_current_spin,
            self.auto_current_step_spin,
            self.auto_point_timeout_spin,
            self.auto_ramp_down_step_spin,
            self.auto_ramp_down_interval_spin,
            self.auto_pause_ramp_down_timeout_spin,
            self.auto_use_spectrometer_check,
            self.prepare_power_supply_combo,
            self.prepare_psu_button,
            self.prepare_power_meter_combo,
            self.prepare_power_meter_button,
            self.prepare_power_meter_open_button,
            self.prepare_power_meter_close_button,
            self.prepare_spectrometer_combo,
            self.prepare_spectrometer_button,
            self.prepare_spectrometer_open_button,
            self.prepare_spectrometer_close_button,
            self.preflight_action_button,
            self.start_automatic_test_button,
        )
        for current, following in zip(focus_order, focus_order[1:]):
            QWidget.setTabOrder(current, following)

    def _configure_button_semantics(self) -> None:
        """Keep native controls and reserve color for destructive actions."""
        emphasized_font = font_for_role(FontRole.SECTION_TITLE)
        self.start_automatic_test_button.setFont(emphasized_font)
        self.apply_current_button.setFont(emphasized_font)
        self.save_excel_button.setFont(emphasized_font)

        semantic = semantic_colors_for_palette(self.palette())
        danger_color = semantic.error_text
        destructive_style = (
            f"QPushButton {{ color: {danger_color}; }}"
            f"QPushButton:disabled {{ color: {semantic.secondary_text}; }}"
        )
        self.emergency_stop_button.setFont(emphasized_font)
        self.emergency_stop_button.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; "
            "border: none; border-radius: 4px; padding: 6px 14px; }"
            "QPushButton:hover { background-color: #b71c1c; }"
            "QPushButton:pressed { background-color: #8e0000; }"
        )
        for button in (
            self.stop_all_button,
            self.stop_power_meter_button,
            self.stop_spectrometer_button,
            self.prepare_power_meter_close_button,
            self.prepare_spectrometer_close_button,
            self.pd_panel.stop_button,
            self.end_automatic_test_button,
        ):
            button.setFont(emphasized_font)
            button.setStyleSheet(destructive_style)

    def _build_session_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("1. 测试任务", self)
        self.session_group = group
        form = QFormLayout(group)
        self._configure_left_form(form)

        self.sn_field = QLineEdit(self)
        self.sn_field.setPlaceholderText("开始测试前必填")
        self.sn_field.setAccessibleName("产品 SN")
        self.sn_field.setMaximumWidth(420)
        sn_label = QLabel("SN", self)
        sn_label.setBuddy(self.sn_field)
        form.addRow(sn_label, self.sn_field)

        self.product_model_field = QLineEdit(self)
        self.product_model_field.setPlaceholderText("可选，用于历史分组")
        self.product_model_field.setAccessibleName("产品型号")
        self.product_model_field.setMaximumWidth(420)
        model_label = QLabel("产品型号", self)
        model_label.setBuddy(self.product_model_field)
        form.addRow(model_label, self.product_model_field)

        self.batch_field = QLineEdit(self)
        self.batch_field.setPlaceholderText("可选，用于批次趋势")
        self.batch_field.setAccessibleName("批次号")
        self.batch_field.setMaximumWidth(420)
        batch_label = QLabel("批次", self)
        batch_label.setBuddy(self.batch_field)
        form.addRow(batch_label, self.batch_field)

        self.test_station_field = QLineEdit(self)
        self.test_station_field.setPlaceholderText("开始测试前必填，如老化站 1")
        self.test_station_field.setAccessibleName("测试站别")
        self.test_station_field.setMaximumWidth(420)
        station_label = QLabel("测试站别", self)
        station_label.setBuddy(self.test_station_field)
        form.addRow(station_label, self.test_station_field)

        self.output_dir_field = QLineEdit(str(Path(DEFAULT_OUTPUT_DIR).resolve()), self)
        self.output_dir_field.setPlaceholderText("Excel 输出文件夹")
        self.output_dir_field.setToolTip(self.output_dir_field.text())
        self.output_dir_field.setAccessibleName("结果输出文件夹")
        self.browse_button = QPushButton("浏览", self)
        self._configure_action_button(self.browse_button, 72)
        self.browse_button.clicked.connect(self.browse_output_dir)
        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_row.addWidget(self.output_dir_field, stretch=1)
        path_row.addWidget(self.browse_button)
        folder_label = QLabel("文件夹", self)
        folder_label.setBuddy(self.output_dir_field)
        form.addRow(folder_label, path_row)

        self.shipping_report_button = QPushButton("生成出货报告", self)
        self.shipping_report_button.clicked.connect(self.generate_shipping_report_from_excel)
        form.addRow("出货文件", self.shipping_report_button)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_device_prepare_group(self, parent: QVBoxLayout) -> None:
        power_group = QGroupBox("3. 电源", self)
        self.power_prepare_group = power_group
        power_grid = QGridLayout(power_group)
        power_grid.setContentsMargins(10, 10, 10, 10)
        power_grid.setHorizontalSpacing(10)
        power_grid.setVerticalSpacing(8)

        self.prepare_power_supply_combo = QComboBox(self)
        self.prepare_power_supply_combo.setAccessibleName("自动测试电源控制器")
        self.prepare_power_supply_combo.setMaximumWidth(360)
        self.prepare_power_supply_combo.addItem("CH341 I²C", "ch341")
        self.prepare_power_supply_combo.addItem("TDK RS232", "tdk")
        self.prepare_psu_button = QPushButton("连接", self)
        self.prepare_psu_button.clicked.connect(self.connect_i2c_device)
        self._configure_action_button(self.prepare_psu_button, 84)
        controller_label = QLabel("控制器", self)
        controller_label.setBuddy(self.prepare_power_supply_combo)
        power_grid.addWidget(controller_label, 0, 0)
        power_grid.addWidget(self.prepare_power_supply_combo, 0, 1)
        power_grid.addWidget(self.prepare_psu_button, 0, 2)

        self.prepare_tdk_resource_label = QLabel("TDK 串口", self)
        self.prepare_tdk_resource_combo = QComboBox(self)
        self.prepare_tdk_resource_combo.setEditable(True)
        self.prepare_tdk_resource_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.prepare_tdk_resource_combo.setPlaceholderText("ASRL3::INSTR")
        self.prepare_tdk_resource_combo.setAccessibleName("自动测试 TDK 通信端口")
        self.prepare_tdk_resource_combo.setMaximumWidth(360)
        self.prepare_tdk_resource_label.setBuddy(self.prepare_tdk_resource_combo)
        self.prepare_tdk_output_button = QPushButton("开启输出", self)
        self.prepare_tdk_output_button.clicked.connect(self.toggle_tdk_output)
        self._configure_action_button(self.prepare_tdk_output_button, 84)
        power_grid.addWidget(self.prepare_tdk_resource_label, 1, 0)
        power_grid.addWidget(self.prepare_tdk_resource_combo, 1, 1)
        power_grid.addWidget(self.prepare_tdk_output_button, 1, 2)
        power_grid.setColumnStretch(1, 1)
        parent.addWidget(power_group)

        measurement_group = QGroupBox("4. 测量设备", self)
        self.measurement_prepare_group = measurement_group
        # Compatibility alias for callers that referenced the former combined group.
        self.device_prepare_group = measurement_group
        measurement_grid = QGridLayout(measurement_group)
        measurement_grid.setContentsMargins(10, 10, 10, 10)
        measurement_grid.setHorizontalSpacing(10)
        measurement_grid.setVerticalSpacing(8)
        self.prepare_power_meter_combo = QComboBox(self)
        self.prepare_power_meter_combo.setEditable(True)
        self.prepare_power_meter_combo.setAccessibleName("自动测试功率计资源")
        self.prepare_power_meter_combo.setMaximumWidth(360)
        self.prepare_spectrometer_combo = QComboBox(self)
        self.prepare_spectrometer_combo.setAccessibleName("自动测试光谱仪设备")
        self.prepare_spectrometer_combo.setMaximumWidth(360)
        for combo in (self.prepare_power_meter_combo, self.prepare_spectrometer_combo):
            # Resource labels can be very long; let the field shrink and show
            # the complete value in its dropdown instead of widening the page.
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(12)
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.prepare_power_meter_button = QPushButton("自动检测", self)
        self.prepare_spectrometer_button = QPushButton("自动检测", self)
        self.prepare_power_meter_open_button = QPushButton("打开", self)
        self.prepare_power_meter_close_button = QPushButton("关闭", self)
        self.prepare_spectrometer_open_button = QPushButton("打开", self)
        self.prepare_spectrometer_close_button = QPushButton("关闭", self)
        self.prepare_power_meter_button.clicked.connect(self.auto_detect_power_meters)
        self.prepare_spectrometer_button.clicked.connect(self.auto_detect_spectrometers)
        self.prepare_power_meter_open_button.clicked.connect(self.start_power_meter)
        self.prepare_power_meter_close_button.clicked.connect(self.stop_power_meter)
        self.prepare_spectrometer_open_button.clicked.connect(self.start_spectrometer)
        self.prepare_spectrometer_close_button.clicked.connect(self.stop_spectrometer)

        rows = (
            (
                "功率计",
                self.prepare_power_meter_combo,
                self.prepare_power_meter_button,
                self.prepare_power_meter_open_button,
                self.prepare_power_meter_close_button,
            ),
            (
                "光谱仪",
                self.prepare_spectrometer_combo,
                self.prepare_spectrometer_button,
                self.prepare_spectrometer_open_button,
                self.prepare_spectrometer_close_button,
            ),
        )
        for row, (name, combo, detect_button, open_button, close_button) in enumerate(rows):
            name_label = QLabel(name, self)
            name_label.setBuddy(combo)
            for button in (detect_button, open_button, close_button):
                self._configure_action_button(button, 70)
            measurement_grid.addWidget(name_label, row, 0)
            measurement_grid.addWidget(combo, row, 1)
            measurement_grid.addWidget(detect_button, row, 2)
            measurement_grid.addWidget(open_button, row, 3)
            measurement_grid.addWidget(close_button, row, 4)
        measurement_grid.setColumnStretch(1, 3)
        for column in range(2, 5):
            measurement_grid.setColumnStretch(column, 1)
        parent.addWidget(measurement_group)

    def _build_preflight_panel(self, parent: QHBoxLayout) -> None:
        group = QGroupBox("5. 启动前检查", self.automatic_prepare_page)
        self.preflight_group = group
        group.setMinimumWidth(PREPARE_CHECKLIST_MIN_WIDTH)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self.preflight_labels: dict[str, QLabel] = {}
        for key in ("sn", "station", "output", "power", "tdk", "power_meter", "spectrometer", "settings"):
            label = QLabel(group)
            label.setWordWrap(True)
            self.preflight_labels[key] = label
            layout.addWidget(label)

        layout.addSpacing(8)
        sequence_title = QLabel("测试序列", group)
        sequence_title.setFont(font_for_role(FontRole.SECTION_TITLE))
        self.preflight_sequence_label = QLabel("1.0 → 20.0 A\n间隔 1.0 A，共 20 点", group)
        self.preflight_sequence_label.setWordWrap(True)
        layout.addWidget(sequence_title)
        layout.addWidget(self.preflight_sequence_label)
        layout.addStretch(1)

        self.preflight_blocker_label = QLabel("正在检查测试条件", group)
        self.preflight_blocker_label.setWordWrap(True)
        layout.addWidget(self.preflight_blocker_label)
        self.preflight_action_button = QPushButton("处理未完成项", group)
        self.preflight_action_button.clicked.connect(self.perform_preflight_action)
        layout.addWidget(self.preflight_action_button)

        self.start_automatic_test_button = QPushButton("开始自动测试", group)
        self.start_automatic_test_button.setMinimumHeight(36)
        self.start_automatic_test_button.setDefault(True)
        self.start_automatic_test_button.clicked.connect(self.start_automatic_test)
        layout.addWidget(self.start_automatic_test_button)
        self.preflight_scroll_area = QScrollArea(self.automatic_prepare_page)
        self.preflight_scroll_area.setWidgetResizable(True)
        self.preflight_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preflight_scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.preflight_scroll_area.setMinimumWidth(PREPARE_CHECKLIST_MIN_WIDTH + 16)
        self.preflight_scroll_area.setMaximumWidth(400)
        self.preflight_scroll_area.setWidget(group)
        parent.addWidget(self.preflight_scroll_area)

    def _build_automatic_run_header(self, parent: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        self.run_state_label = QLabel("当前测试点", self.automatic_run_page)
        self.run_state_label.setFont(font_for_role(FontRole.PAGE_TITLE))
        self.run_state_label.setAccessibleName("自动测试运行状态")
        self.run_progress_label = QLabel("0 / 0 点", self.automatic_run_page)
        self.run_current_label = QLabel("当前 -- A", self.automatic_run_page)
        self.run_elapsed_label = QLabel("已运行 00:00", self.automatic_run_page)
        self.run_remaining_label = QLabel("剩余时间由判稳速度决定", self.automatic_run_page)
        self.run_stage_label = QLabel("正在启动设备", self.automatic_run_page)
        self.run_stage_label.setFont(font_for_role(FontRole.SECTION_TITLE))
        row.addWidget(self.run_state_label)
        row.addWidget(self.run_progress_label)
        row.addWidget(self.run_current_label)
        row.addWidget(self.run_elapsed_label)
        row.addWidget(self.run_remaining_label)
        row.addStretch(1)
        row.addWidget(self.run_stage_label)
        parent.addLayout(row)

    def _build_automatic_run_footer(self, parent: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        latest_title = QLabel("最新事件：", self.automatic_run_page)
        latest_title.setFont(font_for_role(FontRole.SECTION_TITLE))
        self.run_event_label = QLabel("等待开始", self.automatic_run_page)
        self.run_event_label.setMinimumWidth(0)
        row.addWidget(latest_title)
        row.addWidget(self.run_event_label, stretch=1)
        self.pause_automatic_test_button = QPushButton("暂停", self.automatic_run_page)
        self.retry_automatic_test_button = QPushButton("重试当前点", self.automatic_run_page)
        self.end_automatic_test_button = QPushButton("结束并安全下电", self.automatic_run_page)
        self.pause_automatic_test_button.clicked.connect(self.toggle_automatic_pause)
        self.retry_automatic_test_button.clicked.connect(self.retry_automatic_test)
        self.end_automatic_test_button.clicked.connect(self.end_automatic_test)
        self.pause_automatic_test_button.setEnabled(False)
        self.retry_automatic_test_button.setEnabled(False)
        self.retry_automatic_test_button.hide()
        self.end_automatic_test_button.setEnabled(False)
        row.addWidget(self.pause_automatic_test_button)
        row.addWidget(self.retry_automatic_test_button)
        row.addWidget(self.end_automatic_test_button)
        parent.addLayout(row)

    def _build_automatic_result_page(self) -> None:
        layout = QVBoxLayout(self.automatic_result_page)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        self.result_outcome_panel = QWidget(self.automatic_result_page)
        self.result_outcome_panel.setObjectName("resultOutcomePanel")
        outcome_layout = QVBoxLayout(self.result_outcome_panel)
        outcome_layout.setContentsMargins(18, 14, 18, 14)
        outcome_layout.setSpacing(4)
        self.result_title_label = QLabel("测试完成", self.result_outcome_panel)
        self.result_title_label.setFont(font_for_role(FontRole.METRIC))
        self.result_title_label.setAccessibleName("自动测试结果")
        self.result_completion_label = QLabel("测试流程完整完成", self.result_outcome_panel)
        self.result_completion_label.setFont(font_for_role(FontRole.SECTION_TITLE))
        self.result_completion_label.setWordWrap(True)
        outcome_layout.addWidget(self.result_title_label)
        outcome_layout.addWidget(self.result_completion_label)
        layout.addWidget(self.result_outcome_panel)

        result_details = QHBoxLayout()
        result_details.setSpacing(12)

        summary_group = QGroupBox("测试摘要", self.automatic_result_page)
        summary_form = QFormLayout(summary_group)
        self.result_sn_label = QLabel("--", summary_group)
        self.result_station_label = QLabel("--", summary_group)
        self.result_points_label = QLabel("0 / 0", summary_group)
        self.result_time_label = QLabel("--", summary_group)
        self.result_file_label = QLabel("--", summary_group)
        self.result_file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result_file_label.setWordWrap(True)
        summary_form.addRow("SN", self.result_sn_label)
        summary_form.addRow("测试站别", self.result_station_label)
        summary_form.addRow("测试点", self.result_points_label)
        summary_form.addRow("完成时间", self.result_time_label)
        summary_form.addRow("结果文件", self.result_file_label)
        result_details.addWidget(summary_group, stretch=1)

        metrics_group = QGroupBox("最终测试点", self.automatic_result_page)
        metrics_form = QFormLayout(metrics_group)
        self.result_metric_labels: dict[str, QLabel] = {}
        for key, title in (
            ("current", "电流"),
            ("power", "功率"),
            ("efficiency", "效率"),
            ("wavelength", "中心波长"),
            ("fwhm", "FWHM"),
            ("pib", "PIB"),
        ):
            label = QLabel("--", metrics_group)
            self.result_metric_labels[key] = label
            metrics_form.addRow(title, label)
        result_details.addWidget(metrics_group, stretch=1)
        layout.addLayout(result_details)
        layout.addStretch(1)

        actions = QHBoxLayout()
        self.open_result_button = QPushButton("打开结果文件", self.automatic_result_page)
        self.open_result_folder_button = QPushButton("打开所在文件夹", self.automatic_result_page)
        self.generate_result_report_button = QPushButton("生成出货报告", self.automatic_result_page)
        self.return_to_prepare_button = QPushButton("返回准备页", self.automatic_result_page)
        self.open_result_button.clicked.connect(self.open_result_file)
        self.open_result_folder_button.clicked.connect(self.open_result_folder)
        self.generate_result_report_button.clicked.connect(self.generate_shipping_report_from_excel)
        self.return_to_prepare_button.clicked.connect(self.return_to_automatic_prepare)
        actions.addWidget(self.open_result_button)
        actions.addWidget(self.open_result_folder_button)
        actions.addWidget(self.generate_result_report_button)
        actions.addStretch(1)
        actions.addWidget(self.return_to_prepare_button)
        layout.addLayout(actions)

    def _build_power_supply_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("电源", self)
        self.power_supply_group = group
        self._keep_manual_group_compact(group)
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(8, 10, 8, 10)
        group_layout.setSpacing(6)
        self.power_supply_details_content = QWidget(group)
        form = QFormLayout(self.power_supply_details_content)
        self._configure_left_form(form)
        form.setContentsMargins(0, 0, 0, 0)
        group_layout.addWidget(self.power_supply_details_content)
        self.power_supply_form = form
        self.power_supply_details_form = form

        self.power_supply_controller_combo = QComboBox(self)
        self.power_supply_controller_combo.setAccessibleName("电源控制器")
        self.power_supply_controller_combo.setMaximumWidth(360)
        self.power_supply_controller_combo.addItem("CH341 I²C", "ch341")
        self.power_supply_controller_combo.addItem("TDK RS232", "tdk")
        self.power_supply_controller_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.power_supply_controller_combo.setMinimumContentsLength(10)
        self.power_supply_controller_combo.currentIndexChanged.connect(self.on_power_supply_controller_changed)

        self.tdk_resource_combo = QComboBox(self)
        self.tdk_resource_combo.setAccessibleName("TDK 通信端口")
        self.tdk_resource_combo.setMaximumWidth(360)
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

        self.connect_i2c_button = QPushButton("连接 CH341", self)
        self._configure_action_button(self.connect_i2c_button)
        self.connect_i2c_button.clicked.connect(self.connect_i2c_device)
        self.i2c_status_label = QLabel("未连接", self)
        connection_row = QHBoxLayout()
        connection_row.setSpacing(6)
        connection_row.addWidget(self.i2c_status_label, stretch=1)
        connection_row.addWidget(self.connect_i2c_button)
        self.power_supply_connection_row = connection_row

        self.tdk_output_button = QPushButton("开启输出", self)
        self._configure_action_button(self.tdk_output_button)
        self.tdk_output_button.clicked.connect(self.toggle_tdk_output)
        self.tdk_output_status_label = QLabel("输出关闭", self)
        output_row = QHBoxLayout()
        output_row.setSpacing(6)
        output_row.addWidget(self.tdk_output_status_label, stretch=1)
        output_row.addWidget(self.tdk_output_button)
        self.tdk_output_row = output_row

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
        self.power_supply_read_row = read_grid

        # Follow the safe operating sequence shown to the operator. Enabling
        # TDK output confirms 0 A, so the final current command must come after it.
        form.addRow("控制器", self.power_supply_controller_combo)
        form.addRow("TDK 串口", self.tdk_resource_row)
        form.addRow("连接", self.power_supply_connection_row)
        form.addRow("TDK 电压", self.tdk_voltage_row)
        form.addRow("TDK 输出", self.tdk_output_row)
        form.addRow("设定电流", current_row)
        form.addRow("读取", self.power_supply_read_row)

        parent.addWidget(group)
        self.on_power_supply_controller_changed()
        self._reserve_group_height(group)

    def _build_automatic_test_group(self, parent: QVBoxLayout) -> None:
        self.automatic_test_section = QWidget(self)
        section_layout = QVBoxLayout(self.automatic_test_section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        # Kept as a hidden compatibility attribute for older callers. The
        # customer workflow is always expanded and no longer looks like one
        # optional device panel among several others.
        self.automatic_test_toggle = QToolButton(self)
        self.automatic_test_toggle.setText("自动测试")
        self.automatic_test_toggle.setCheckable(True)
        self.automatic_test_toggle.setChecked(True)
        self.automatic_test_toggle.hide()

        self.automatic_test_content = QGroupBox("2. 测试计划", self)
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

        self.stable_window_spin = QDoubleSpinBox(self)
        self.stable_window_spin.setRange(0.5, 300.0)
        self.stable_window_spin.setDecimals(1)
        self.stable_window_spin.setValue(3.0)
        self.stable_window_spin.setSuffix(" s")
        self.stable_window_spin.valueChanged.connect(self.on_stability_settings_changed)

        # Existing calculation code reads this value. The customer-facing UI
        # shows a plain-language explanation instead of a disabled-looking
        # spin box.
        self.stable_tolerance_spin = QDoubleSpinBox(self)
        self.stable_tolerance_spin.setRange(0.0, 100000.0)
        self.stable_tolerance_spin.setDecimals(4)
        self.stable_tolerance_spin.setValue(0.15)
        self.stable_tolerance_spin.setReadOnly(True)
        self.stable_tolerance_spin.hide()

        self.auto_use_spectrometer_check = QCheckBox("使用光谱仪（同时判断波长稳定）", self)
        self.auto_use_spectrometer_check.setChecked(True)
        self.auto_use_spectrometer_check.setAccessibleName("光谱判稳")
        self.auto_use_spectrometer_check.setToolTip(
            "未连接光谱仪时取消勾选；自动测试将只等待功率稳定，并继续计算和保存效率"
        )
        parameter_grid = QGridLayout()
        parameter_grid.setHorizontalSpacing(10)
        parameter_grid.setVerticalSpacing(8)
        parameter_grid.setColumnStretch(1, 1)
        parameter_grid.setColumnStretch(3, 1)
        parameters = (
            ("初始电流", self.auto_initial_current_spin),
            ("目标电流", self.auto_target_current_spin),
            ("电流步长", self.auto_current_step_spin),
            ("稳定窗口", self.stable_window_spin),
        )
        for index, (label_text, spin_box) in enumerate(parameters):
            row = index // 2
            column = (index % 2) * 2
            label = QLabel(label_text, self)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            label.setBuddy(spin_box)
            spin_box.setAccessibleName(label_text)
            spin_box.setMinimumWidth(140)
            spin_box.setMaximumWidth(180)
            parameter_grid.addWidget(label, row, column)
            parameter_grid.addWidget(spin_box, row, column + 1)
        form.addRow(parameter_grid)
        form.addRow("测量策略", self.auto_use_spectrometer_check)

        self.stability_tolerance_label = QLabel(
            "当前功率峰峰值：判稳 ≤0.1000 W；稳定保持 ≤0.1500 W",
            self,
        )
        self.stability_tolerance_label.setWordWrap(True)
        self.stability_tolerance_label.setStyleSheet(
            f"color: {semantic_colors_for_palette(self.palette()).secondary_text};"
        )
        form.addRow("判稳规则", self.stability_tolerance_label)

        self.advanced_settings_toggle = QToolButton(self)
        self.advanced_settings_toggle.setText("高级采集参数")
        self.advanced_settings_toggle.setCheckable(True)
        self.advanced_settings_toggle.setChecked(False)
        self.advanced_settings_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_settings_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_settings_toggle.toggled.connect(self._set_advanced_settings_expanded)
        form.addRow(self.advanced_settings_toggle)

        self.advanced_settings_content = QWidget(self)
        advanced_layout = QVBoxLayout(self.advanced_settings_content)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(6)
        self.advanced_settings_summary_label = QLabel(
            "功率计波长、软件增益、采样间隔、光谱积分时间和自动积分沿用当前设备设置。",
            self,
        )
        self.advanced_settings_summary_label.setWordWrap(True)
        advanced_layout.addWidget(self.advanced_settings_summary_label)
        self.advanced_settings_content.hide()
        form.addRow(self.advanced_settings_content)

        self.safety_settings_toggle = QToolButton(self)
        self.safety_settings_toggle.setText("安全策略")
        self.safety_settings_toggle.setCheckable(True)
        self.safety_settings_toggle.setChecked(False)
        self.safety_settings_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.safety_settings_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.safety_settings_toggle.toggled.connect(self._set_safety_settings_expanded)
        form.addRow(self.safety_settings_toggle)

        self.safety_settings_content = QWidget(self)
        safety_grid = QGridLayout(self.safety_settings_content)
        safety_grid.setContentsMargins(0, 0, 0, 0)
        safety_grid.setHorizontalSpacing(10)
        safety_grid.setVerticalSpacing(6)
        safety_parameters = (
            ("单点超时", self.auto_point_timeout_spin),
            ("下电步长", self.auto_ramp_down_step_spin),
            ("下电间隔", self.auto_ramp_down_interval_spin),
            ("暂停下电", self.auto_pause_ramp_down_timeout_spin),
        )
        for index, (label_text, spin_box) in enumerate(safety_parameters):
            row = index // 2
            column = (index % 2) * 2
            label = QLabel(label_text, self)
            label.setBuddy(spin_box)
            spin_box.setAccessibleName(label_text)
            spin_box.setMinimumWidth(140)
            spin_box.setMaximumWidth(180)
            safety_grid.addWidget(label, row, column)
            safety_grid.addWidget(spin_box, row, column + 1)
            safety_grid.setColumnStretch(column + 1, 1)
        self.safety_settings_content.hide()
        form.addRow(self.safety_settings_content)

        self.safety_summary_label = QLabel(self)
        self.safety_summary_label.setWordWrap(True)
        self.safety_summary_label.setStyleSheet(
            f"color: {semantic_colors_for_palette(self.palette()).secondary_text};"
        )
        form.addRow("", self.safety_summary_label)

        self.automatic_test_status_label = QLabel("未开始", self)
        self.automatic_test_status_label.hide()
        self.automatic_test_status_label.setWordWrap(True)

        section_layout.addWidget(self.automatic_test_content)
        parent.addWidget(self.automatic_test_section)
        self._update_safety_summary()

    def _set_automatic_test_expanded(self, expanded: bool) -> None:
        self.automatic_test_toggle.setChecked(True)
        self.automatic_test_content.setVisible(True)

    def _set_safety_settings_expanded(self, expanded: bool) -> None:
        self.safety_settings_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.safety_settings_content.setVisible(expanded)
        self.automatic_test_section.updateGeometry()

    def _set_advanced_settings_expanded(self, expanded: bool) -> None:
        self.advanced_settings_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.advanced_settings_content.setVisible(expanded)
        self.automatic_test_section.updateGeometry()

    def _update_safety_summary(self) -> None:
        timeout_s = self.auto_pause_ramp_down_timeout_spin.value()
        pause_text = "暂停后不自动下电" if timeout_s <= 0.0 else f"暂停超过 {timeout_s:g} s 后"
        self.safety_summary_label.setText(
            f"安全策略：{pause_text}，以每次 {self.auto_ramp_down_step_spin.value():g} A、"
            f"间隔 {self.auto_ramp_down_interval_spin.value():g} s 降至 0 A；"
            f"单点最长等待 {self.auto_point_timeout_spin.value():g} s。"
        )

    def _update_advanced_settings_summary(self, *_args: Any) -> None:
        if not hasattr(self, "power_wavelength_spin"):
            return
        spectrum_mode = "自动积分" if self.auto_integration_check.isChecked() else f"积分 {self.integration_spin.value()} us"
        self.advanced_settings_summary_label.setText(
            f"功率计 {self.power_wavelength_spin.value():g} nm · 增益 {self.software_gain_spin.value():g} · "
            f"采样 {self.power_meter_interval_spin.value()} ms；光谱仪 {spectrum_mode} · "
            f"采样 {self.interval_spin.value()} ms。"
        )

    def _build_power_meter_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("功率计", self)
        self._keep_manual_group_compact(group)
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(8, 10, 8, 10)
        group_layout.setSpacing(6)
        form = QFormLayout()
        self._configure_left_form(form)
        form.setContentsMargins(0, 0, 0, 0)
        group_layout.addLayout(form)
        (
            self.power_meter_details_button,
            self.power_meter_details_dialog,
            self.power_meter_details_content,
            details_form,
        ) = self._build_manual_details_dialog(group, "功率计")
        self.power_meter_form = form
        self.power_meter_details_form = details_form

        self.power_meter_combo = QComboBox(self)
        self.power_meter_combo.setAccessibleName("功率计资源")
        self.power_meter_combo.setMaximumWidth(360)
        self.power_meter_combo.setEditable(True)
        self.power_meter_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.power_meter_combo.setMinimumContentsLength(8)
        self._add_power_meter_resource_option(DEFAULT_POWER_RESOURCE)
        self.detect_power_meter_button = QPushButton("自动检测", self)
        self._configure_action_button(self.detect_power_meter_button)
        self.detect_power_meter_button.clicked.connect(self.auto_detect_power_meters)
        device_row = QHBoxLayout()
        device_row.setSpacing(6)
        device_row.addWidget(self.power_meter_combo, stretch=1)
        device_row.addWidget(self.detect_power_meter_button)
        details_form.addRow("设备", device_row)

        power_actions = QHBoxLayout()
        power_actions.setSpacing(8)
        self.refresh_power_meter_button = QPushButton("刷新端口", self)
        self._configure_action_button(self.refresh_power_meter_button)
        self.rel_zero_check = QCheckBox("相对调零", self)
        self.refresh_power_meter_button.clicked.connect(self.refresh_power_meter_resources)
        self.rel_zero_check.toggled.connect(self.set_power_meter_relative_zero)
        power_actions.addWidget(self.refresh_power_meter_button)
        power_actions.addWidget(self.rel_zero_check)
        details_form.addRow(power_actions)

        self.power_wavelength_spin = QDoubleSpinBox(self)
        self.power_wavelength_spin.setRange(190.0, 25000.0)
        self.power_wavelength_spin.setDecimals(3)
        self.power_wavelength_spin.setSingleStep(0.1)
        self.power_wavelength_spin.setValue(976.0)
        self.power_wavelength_spin.setSuffix(" nm")
        details_form.addRow("波长", self.power_wavelength_spin)

        self.software_gain_spin = QDoubleSpinBox(self)
        self.software_gain_spin.setRange(0.000001, 1000000.0)
        self.software_gain_spin.setDecimals(6)
        self.software_gain_spin.setValue(1.0)
        details_form.addRow("软件增益", self.software_gain_spin)

        self.power_meter_interval_spin = QSpinBox(self)
        self.power_meter_interval_spin.setRange(20, 5000)
        self.power_meter_interval_spin.setValue(300)
        self.power_meter_interval_spin.setSingleStep(50)
        self.power_meter_interval_spin.setSuffix(" ms")
        details_form.addRow("采样间隔", self.power_meter_interval_spin)

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
        power_run_actions.addWidget(self.power_meter_details_button)
        power_run_actions.addWidget(self.start_power_meter_button)
        power_run_actions.addWidget(self.stop_power_meter_button)
        form.addRow("状态", power_run_actions)

        parent.addWidget(group)
        self._reserve_group_height(group)

    def _build_spectrometer_group(self, parent: QVBoxLayout) -> None:
        group = QGroupBox("光谱仪", self)
        self._keep_manual_group_compact(group)
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(8, 10, 8, 10)
        group_layout.setSpacing(6)
        form = QFormLayout()
        self._configure_left_form(form)
        form.setContentsMargins(0, 0, 0, 0)
        group_layout.addLayout(form)
        (
            self.spectrometer_details_button,
            self.spectrometer_details_dialog,
            self.spectrometer_details_content,
            details_form,
        ) = self._build_manual_details_dialog(group, "光谱仪")
        self.spectrometer_form = form
        self.spectrometer_details_form = details_form

        self.spectrometer_combo = QComboBox(self)
        self.spectrometer_combo.setAccessibleName("光谱仪设备")
        self.spectrometer_combo.setMaximumWidth(360)
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
        details_form.addRow("设备", device_row)

        self.integration_spin = QSpinBox(self)
        self.integration_spin.setRange(1, 10_000_000)
        self.integration_spin.setValue(DEFAULT_SPECTROMETER_INTEGRATION_US)
        self.integration_spin.setSingleStep(100)
        self.integration_spin.setSuffix(" us")
        details_form.addRow("积分时间", self.integration_spin)

        self.auto_integration_check = QCheckBox("启用（目标 8k–14k）", self)
        self.auto_integration_check.setToolTip("自动调整积分时间，使光谱峰值保持在 8000–14000 counts")
        self.auto_integration_check.setChecked(False)
        details_form.addRow("自动积分", self.auto_integration_check)

        self.interval_spin = QSpinBox(self)
        self.interval_spin.setRange(50, 5000)
        self.interval_spin.setValue(300)
        self.interval_spin.setSingleStep(50)
        self.interval_spin.setSuffix(" ms")
        details_form.addRow("采样间隔", self.interval_spin)

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
        spectrometer_run_actions.addWidget(self.spectrometer_details_button)
        spectrometer_run_actions.addWidget(self.start_spectrometer_button)
        spectrometer_run_actions.addWidget(self.stop_spectrometer_button)
        form.addRow("状态", spectrometer_run_actions)

        parent.addWidget(group)
        self._reserve_group_height(group)

    @staticmethod
    def _sync_combo_index(target: QComboBox, index: int) -> None:
        if index < 0 or target.currentIndex() == index:
            return
        blocked = target.blockSignals(True)
        try:
            target.setCurrentIndex(index)
        finally:
            target.blockSignals(blocked)

    @staticmethod
    def _sync_combo_text(target: QComboBox, text: str) -> None:
        if not target.isEditable() or target.currentText() == text:
            return
        blocked = target.blockSignals(True)
        try:
            target.setEditText(text)
        finally:
            target.blockSignals(blocked)

    @staticmethod
    def _apply_combo_index(target: QComboBox, index: int) -> None:
        if index >= 0 and target.currentIndex() != index:
            target.setCurrentIndex(index)

    @staticmethod
    def _apply_combo_text(target: QComboBox, text: str) -> None:
        if target.isEditable() and target.currentText() != text:
            target.setEditText(text)

    def _bind_prepare_combo(self, source: QComboBox, prepare: QComboBox) -> None:
        """Share device options while keeping one selection on both workflow pages."""
        prepare.setModel(source.model())
        prepare.setEditable(source.isEditable())
        prepare.setInsertPolicy(source.insertPolicy())
        prepare.setCurrentIndex(source.currentIndex())
        if source.isEditable():
            prepare.setEditText(source.currentText())

        source.currentIndexChanged.connect(
            lambda _index, source=source, target=prepare: self._sync_combo_index(
                target,
                source.currentIndex(),
            )
        )
        prepare.currentIndexChanged.connect(
            lambda index, target=source: self._apply_combo_index(target, index)
        )
        if source.isEditable():
            source.currentTextChanged.connect(
                lambda text, target=prepare: self._sync_combo_text(target, text)
            )
            prepare.currentTextChanged.connect(
                lambda text, target=source: self._apply_combo_text(target, text)
            )

    def _wire_prepare_device_controls(self) -> None:
        """Keep routine device selection inside the automatic preparation flow."""
        self._bind_prepare_combo(
            self.power_supply_controller_combo,
            self.prepare_power_supply_combo,
        )
        self._bind_prepare_combo(self.tdk_resource_combo, self.prepare_tdk_resource_combo)
        self._bind_prepare_combo(self.power_meter_combo, self.prepare_power_meter_combo)
        self._bind_prepare_combo(self.spectrometer_combo, self.prepare_spectrometer_combo)
        self._update_prepare_power_controls()

    def _update_prepare_power_controls(self) -> None:
        if not hasattr(self, "prepare_tdk_resource_combo"):
            return
        is_tdk = self._selected_power_supply_kind() == "tdk"
        connected = self._manual_i2c_connected()
        automatic_active = self._automatic_workflow_is_active()
        selection_enabled = not connected and not automatic_active
        output_enabled = bool(
            is_tdk
            and connected
            and getattr(self.manual_ch341_controller, "output_enabled", False)
        )
        self.power_supply_controller_combo.setEnabled(selection_enabled)
        self.prepare_power_supply_combo.setEnabled(selection_enabled)
        self.prepare_tdk_resource_label.setVisible(is_tdk)
        self.prepare_tdk_resource_combo.setVisible(is_tdk)
        self.prepare_tdk_output_button.setVisible(is_tdk)
        tdk_resource_enabled = is_tdk and selection_enabled
        self.tdk_resource_combo.setEnabled(tdk_resource_enabled)
        self.prepare_tdk_resource_combo.setEnabled(tdk_resource_enabled)
        self.refresh_tdk_resources_button.setEnabled(tdk_resource_enabled)
        self.prepare_psu_button.setText("断开" if connected else "连接")
        self.prepare_tdk_output_button.setText("关闭输出" if output_enabled else "开启输出")
        self.prepare_tdk_output_button.setEnabled(is_tdk and connected and not automatic_active)

    def _update_prepare_measurement_controls(self) -> None:
        """Keep preparation-page device actions aligned with acquisition state."""
        if not hasattr(self, "prepare_power_meter_open_button"):
            return
        power_running = self.power_meter_reader is not None
        power_detecting = self.power_meter_detect_thread is not None
        spectrometer_running = self.spectrometer_reader is not None
        automatic_active = self._automatic_workflow_is_active()

        self.prepare_power_meter_open_button.setEnabled(
            not power_running and not power_detecting and not automatic_active
        )
        self.prepare_power_meter_close_button.setEnabled(power_running and not automatic_active)
        self.prepare_power_meter_button.setEnabled(
            not power_running and not power_detecting and not automatic_active
        )
        self.prepare_power_meter_combo.setEnabled(
            not power_running and not power_detecting and not automatic_active
        )

        self.prepare_spectrometer_open_button.setEnabled(
            not spectrometer_running and not automatic_active
        )
        self.prepare_spectrometer_close_button.setEnabled(
            spectrometer_running and not automatic_active
        )
        self.prepare_spectrometer_button.setEnabled(
            not spectrometer_running and not automatic_active
        )
        self.prepare_spectrometer_combo.setEnabled(
            not spectrometer_running and not automatic_active
        )

    def sync_tdk_output_controls(self, enabled: bool) -> None:
        """Show one confirmed TDK output state on both workflow pages."""
        output_enabled = bool(enabled)
        self.tdk_output_status_label.setText("输出开启" if output_enabled else "输出关闭")
        button_text = "关闭输出" if output_enabled else "开启输出"
        self.tdk_output_button.setText(button_text)
        self.prepare_tdk_output_button.setText(button_text)

    def _build_curve_panel(self, parent: QVBoxLayout) -> None:
        if self.headless:
            from .headless_plots import NullLivePlots

            self.live_plots = NullLivePlots(self)
        else:
            from .plots import LivePlots

            self.live_plots = LivePlots(self)
        self.live_plots.expose_compatibility_attributes(self)
        parent.addWidget(self.live_plots.group, stretch=2)
        self._live_plots_layout = parent
        self.reset_curves()

    def on_main_tab_changed(self, index: int) -> None:
        self._sync_navigation_state(index)
        self._place_live_plots_for_tab(index)
        self.update_global_status()
        if index != self.pd_tab_index or self.pd_panel.reader is not None:
            return
        if self.pd_panel.device_combo.count() == 0:
            self.pd_panel.refresh_devices()

    def _place_live_plots_for_tab(self, index: int) -> None:
        """Keep one live plot history visible in both automatic and manual modes."""
        if not hasattr(self, "live_plots"):
            return
        self.live_plots.set_layout_context(
            PlotLayoutContext.MANUAL
            if index == self.manual_tab_index
            else PlotLayoutContext.AUTOMATIC
        )
        target_layout = (
            self.manual_monitor_layout
            if index == self.manual_tab_index
            else self.automatic_monitor_layout
        )
        if self._live_plots_layout is target_layout:
            return
        self._live_plots_layout.removeWidget(self.live_plots.group)
        target_layout.addWidget(self.live_plots.group, stretch=2)
        self._live_plots_layout = target_layout
        self.live_plots.group.show()
        self.live_plots.relayout(
            max(self.live_plots.group.width(), self.width() - 64)
        )

    def open_manual_settings(self, section: str = "") -> None:
        self.main_tabs.setCurrentIndex(self.manual_tab_index)
        details_dialog = {
            "power_meter": getattr(self, "power_meter_details_dialog", None),
            "spectrometer": getattr(self, "spectrometer_details_dialog", None),
        }.get(section)
        if details_dialog is not None:
            self._show_manual_details_dialog(details_dialog)
        target = {
            "power": getattr(self, "power_supply_controller_combo", None),
            "power_meter": getattr(self, "power_meter_combo", None),
            "spectrometer": getattr(self, "spectrometer_combo", None),
        }.get(section)
        if target is not None:
            QTimer.singleShot(0, lambda widget=target: self._focus_manual_setting(widget))

    def _focus_manual_setting(self, target: QWidget) -> None:
        self.left_control_panel.ensureWidgetVisible(target)
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _connect_preflight_updates(self) -> None:
        for line_edit in (
            self.sn_field,
            self.product_model_field,
            self.batch_field,
            self.test_station_field,
            self.output_dir_field,
        ):
            line_edit.textChanged.connect(self.refresh_preflight_checklist)
        for spin_box in (
            self.auto_initial_current_spin,
            self.auto_target_current_spin,
            self.auto_current_step_spin,
            self.auto_point_timeout_spin,
            self.auto_ramp_down_step_spin,
            self.auto_ramp_down_interval_spin,
            self.auto_pause_ramp_down_timeout_spin,
            self.stable_window_spin,
        ):
            spin_box.valueChanged.connect(self.refresh_preflight_checklist)
        for safety_widget in (
            self.auto_point_timeout_spin,
            self.auto_ramp_down_step_spin,
            self.auto_ramp_down_interval_spin,
            self.auto_pause_ramp_down_timeout_spin,
        ):
            safety_widget.valueChanged.connect(self._update_safety_summary)
        for advanced_spin in (
            self.power_wavelength_spin,
            self.software_gain_spin,
            self.power_meter_interval_spin,
            self.integration_spin,
            self.interval_spin,
        ):
            advanced_spin.valueChanged.connect(self._update_advanced_settings_summary)
        self.auto_integration_check.toggled.connect(self._update_advanced_settings_summary)
        self.auto_use_spectrometer_check.toggled.connect(self.refresh_preflight_checklist)
        self._update_advanced_settings_summary()

    def _set_checklist_item(self, label: QLabel, ok: bool, text: str, *, pending: bool = False) -> None:
        semantic = semantic_colors_for_palette(self.palette())
        if pending:
            label.setText(f"● {text}")
            label.setStyleSheet(f"color: {semantic.warning_text};")
        elif ok:
            label.setText(f"✓ {text}")
            label.setStyleSheet(f"color: {semantic.success_text};")
        else:
            label.setText(f"! {text}")
            label.setStyleSheet(f"color: {semantic.warning_text};")

    @staticmethod
    def _output_directory_is_writable(path_text: str) -> bool:
        if not path_text:
            return False
        candidate = Path(path_text).expanduser()
        if candidate.exists():
            return candidate.is_dir() and os.access(candidate, os.W_OK)
        existing_parent = candidate
        while not existing_parent.exists() and existing_parent != existing_parent.parent:
            existing_parent = existing_parent.parent
        return existing_parent.is_dir() and os.access(existing_parent, os.W_OK)

    def refresh_preflight_checklist(self, *_args: Any) -> None:
        if not all(
            hasattr(self, name)
            for name in (
                "preflight_labels",
                "power_meter_combo",
                "spectrometer_combo",
                "start_automatic_test_button",
            )
        ):
            return
        sn_ok = bool(self.sn_field.text().strip())
        station_ok = bool(self.test_station_field.text().strip())
        output_text = self.output_dir_field.text().strip()
        output_ok = self._output_directory_is_writable(output_text)
        power_ok = self._manual_i2c_connected()
        is_tdk = self._selected_power_supply_kind() == "tdk"
        tdk_ok = not is_tdk or bool(
            power_ok and getattr(self.manual_ch341_controller, "output_enabled", False)
        )
        power_resource_ok = bool(self._selected_power_resource())
        power_ready = bool(
            self.power_meter_reader is not None and getattr(self.power_meter_reader, "is_ready", False)
        )
        use_spectrometer = self.auto_use_spectrometer_check.isChecked()
        spectrum_ready = bool(
            self.spectrometer_reader is not None and getattr(self.spectrometer_reader, "is_ready", False)
        )

        settings_ok = False
        settings_error = "测试参数无效"
        point_count = 0
        try:
            settings = self.collect_automatic_test_settings()
            point_count = len(build_test_currents(settings))
            settings_ok = True
            settings_error = "测试参数有效"
        except (TypeError, ValueError) as exc:
            settings_error = str(exc) or settings_error

        self._set_checklist_item(self.preflight_labels["sn"], sn_ok, "SN 已填写" if sn_ok else "请填写产品 SN")
        self._set_checklist_item(
            self.preflight_labels["station"],
            station_ok,
            "测试站别已填写" if station_ok else "请填写测试站别",
        )
        self._set_checklist_item(
            self.preflight_labels["output"],
            output_ok,
            "输出目录有效" if output_ok else "请选择有效输出目录",
        )
        self._set_checklist_item(
            self.preflight_labels["power"],
            power_ok,
            "电源已连接" if power_ok else "电源尚未连接",
        )
        self._set_checklist_item(
            self.preflight_labels["tdk"],
            tdk_ok,
            "TDK 输出已开启" if is_tdk and tdk_ok else ("TDK 输出尚未开启" if is_tdk else "CH341 无需输出开关"),
        )
        self._set_checklist_item(
            self.preflight_labels["power_meter"],
            power_resource_ok,
            "功率计已就绪" if power_ready else ("功率计将在启动后确认" if power_resource_ok else "请选择功率计资源"),
            pending=power_resource_ok and not power_ready,
        )
        self._set_checklist_item(
            self.preflight_labels["spectrometer"],
            not use_spectrometer or spectrum_ready,
            (
                "本方案不使用光谱仪"
                if not use_spectrometer
                else ("光谱仪已就绪" if spectrum_ready else "光谱仪将在启动后确认")
            ),
            pending=use_spectrometer and not spectrum_ready,
        )
        self._set_checklist_item(self.preflight_labels["settings"], settings_ok, settings_error)

        self._update_prepare_power_controls()

        if settings_ok:
            self.preflight_sequence_label.setText(
                f"{self.auto_initial_current_spin.value():g} → {self.auto_target_current_spin.value():g} A\n"
                f"间隔 {self.auto_current_step_spin.value():g} A，共 {point_count} 点"
            )
        else:
            self.preflight_sequence_label.setText("请先修正测试参数")

        blockers: list[str] = []
        if not sn_ok:
            blockers.append("请填写产品 SN")
        if not station_ok:
            blockers.append("请填写测试站别")
        if not output_ok:
            blockers.append("请选择有效输出目录")
        if not power_ok:
            blockers.append(f"请连接 {'TDK' if is_tdk else 'CH341'} 电源")
        elif not tdk_ok:
            blockers.append("请开启 TDK 输出")
        if not power_resource_ok:
            blockers.append("请选择功率计资源")
        if not settings_ok:
            blockers.append(settings_error)

        active = self._automatic_workflow_is_active()
        can_start = not blockers and not active and self.excel_save_thread is None
        self.start_automatic_test_button.setEnabled(can_start)
        ready_text = (
            "配置已完成；开始后将自动确认测量设备"
            if not power_ready or (use_spectrometer and not spectrum_ready)
            else "设备和配置均已就绪，可以开始测试"
        )
        self.preflight_blocker_label.setText(
            ready_text if can_start else f"无法开始：{blockers[0] if blockers else '测试正在运行'}"
        )
        self.preflight_action_button.setVisible(bool(blockers))
        if not sn_ok:
            self.preflight_action_button.setText("填写 SN")
        elif not station_ok:
            self.preflight_action_button.setText("填写测试站别")
        elif not output_ok:
            self.preflight_action_button.setText("选择输出目录")
        elif not power_ok:
            self.preflight_action_button.setText(f"连接 {'TDK' if is_tdk else 'CH341'}")
        elif not power_resource_ok:
            self.preflight_action_button.setText("选择功率计")
        elif not settings_ok:
            self.preflight_action_button.setText("检查测试计划")
        else:
            self.preflight_action_button.setText("检查准备项")

    def perform_preflight_action(self) -> None:
        if not self.sn_field.text().strip():
            self.sn_field.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if not self.test_station_field.text().strip():
            self.test_station_field.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if not self.output_dir_field.text().strip():
            self.browse_output_dir()
            return
        if not self._manual_i2c_connected():
            self.connect_i2c_device()
            return
        if not self._selected_power_resource():
            self.prepare_power_meter_combo.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.auto_initial_current_spin.setFocus(Qt.FocusReason.OtherFocusReason)

    def _automatic_workflow_is_active(self) -> bool:
        return self.automatic_test_state not in (
            AutomaticTestState.IDLE,
            AutomaticTestState.COMPLETED,
        )

    def _manual_power_action_context(self) -> bool:
        return bool(
            hasattr(self, "main_tabs")
            and self.main_tabs.currentIndex() == self.manual_tab_index
            and not self._automatic_workflow_is_active()
        )

    def _manual_output_is_energized(self) -> bool:
        if self._selected_power_supply_kind() == "tdk":
            return bool(
                self._manual_i2c_connected()
                and getattr(self.manual_ch341_controller, "output_enabled", False)
            )
        return float(self.active_output_current_a or 0.0) > 0.0

    def _refresh_manual_power_tab_lock(self, manual_action: bool = False) -> None:
        if not manual_action and not self.manual_power_tab_lock_active:
            return
        self.manual_power_tab_lock_active = self._manual_output_is_energized()
        self._update_main_tab_access()

    def _update_main_tab_access(self) -> None:
        if not hasattr(self, "main_tabs"):
            return
        automatic_active = self._automatic_workflow_is_active()
        manual_lock = self.manual_power_tab_lock_active and not automatic_active
        was_on_pd_tab = self.main_tabs.currentIndex() == self.pd_tab_index
        locked_hint = "手动电源正在输出，请先将电流降至 0 A；TDK 电源还需关闭输出"
        # PD uses an independent NI-DAQ task, so operators may start or stop it
        # after the power output or automatic test has already begun.
        self.main_tabs.setTabEnabled(self.pd_tab_index, True)
        self.main_tabs.setTabToolTip(
            self.pd_tab_index,
            "加电期间可手动启动或停止 PD 采集" if manual_lock or automatic_active else "",
        )
        # Keep the automatic tab enabled while locking the other workflow tabs.
        # Disabling the current tab even briefly makes QTabWidget select PD as
        # the only available page, which looks like an automatic navigation.
        self.main_tabs.setTabEnabled(self.automatic_tab_index, not manual_lock)
        self.main_tabs.setTabToolTip(
            self.automatic_tab_index,
            locked_hint if manual_lock else "",
        )
        self.main_tabs.setTabEnabled(
            self.manual_tab_index,
            manual_lock or not automatic_active or self.automatic_test_state == AutomaticTestState.PAUSED,
        )
        self._sync_navigation_access()
        if manual_lock and not was_on_pd_tab:
            self.main_tabs.setCurrentIndex(self.manual_tab_index)
            return
        if (
            automatic_active
            and self.automatic_test_state != AutomaticTestState.PAUSED
            and not was_on_pd_tab
        ):
            self.main_tabs.setCurrentIndex(self.automatic_tab_index)

    def on_automatic_state_ui_changed(self, state: AutomaticTestState, detail: str = "") -> None:
        if state == AutomaticTestState.STARTING and self.automatic_run_started_monotonic_s is None:
            self.automatic_run_started_monotonic_s = time.monotonic()
            self.automatic_elapsed_timer.start()
        elif state in (AutomaticTestState.IDLE, AutomaticTestState.COMPLETED):
            self.automatic_elapsed_timer.stop()
        previous_page = self.automatic_stack.currentWidget()
        if state == AutomaticTestState.IDLE:
            target_page = self.automatic_prepare_page
        elif state == AutomaticTestState.COMPLETED:
            target_page = self.automatic_result_page
        else:
            target_page = self.automatic_run_page
        self.automatic_stack.setCurrentWidget(target_page)
        if previous_page is not target_page:
            if target_page is self.automatic_prepare_page:
                focus_target = self.sn_field
            elif target_page is self.automatic_run_page:
                focus_target = self.pause_automatic_test_button
            else:
                focus_target = self.return_to_prepare_button
            QTimer.singleShot(
                0,
                lambda widget=focus_target: widget.setFocus(Qt.FocusReason.OtherFocusReason),
            )

        point_count = len(self.automatic_test_currents)
        current_index = self.automatic_test_current_index
        current_a = (
            self.automatic_test_currents[current_index]
            if 0 <= current_index < point_count
            else self.active_output_current_a
        )
        self.run_progress_label.setText(
            f"{current_index + 1 if current_index >= 0 else 0} / {point_count} 点"
        )
        self.run_current_label.setText("当前 -- A" if current_a is None else f"当前 {current_a:.1f} A")
        state_names = {
            AutomaticTestState.STARTING: "启动设备",
            AutomaticTestState.SETTING_CURRENT: "设置电流",
            AutomaticTestState.WAITING_STABLE: "等待稳定",
            AutomaticTestState.WAITING_VOLTAGE: "读取输出电压",
            AutomaticTestState.SAVING_POINT: "保存测试点",
            AutomaticTestState.PAUSED: "测试已暂停",
            AutomaticTestState.RAMPING_DOWN: "安全下电",
            AutomaticTestState.COMPLETED: "测试完成",
        }
        self.run_state_label.setText("测试已暂停" if state == AutomaticTestState.PAUSED else "当前测试点")
        self.run_stage_label.setText(state_names.get(state, "准备测试"))
        self._update_main_tab_access()
        self.set_power_meter_running_state(self.power_meter_reader is not None)
        self.set_spectrometer_running_state(self.spectrometer_reader is not None)
        self.pause_automatic_test_button.setText("暂停")
        self.pause_automatic_test_button.setVisible(state != AutomaticTestState.PAUSED)
        self.retry_automatic_test_button.setText("修复后重试当前点")
        self.retry_automatic_test_button.setVisible(state == AutomaticTestState.PAUSED)
        self.pause_automatic_test_button.setEnabled(
            state
            in (
                AutomaticTestState.STARTING,
                AutomaticTestState.SETTING_CURRENT,
                AutomaticTestState.WAITING_STABLE,
                AutomaticTestState.WAITING_VOLTAGE,
            )
        )
        if detail:
            self.run_event_label.setText(detail)
        self.global_progress_label.setText(self.automatic_test_status_label.text() if detail else state_names.get(state, "准备测试"))
        self.update_automatic_elapsed()
        if state == AutomaticTestState.PAUSED:
            QTimer.singleShot(0, self.update_automatic_elapsed)
        self.refresh_preflight_checklist()

    def update_automatic_elapsed(self) -> None:
        started_at = self.automatic_run_started_monotonic_s
        elapsed_s = 0 if started_at is None else max(0, int(time.monotonic() - started_at))
        minutes, seconds = divmod(elapsed_s, 60)
        hours, minutes = divmod(minutes, 60)
        elapsed_text = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
        self.run_elapsed_label.setText(f"已运行 {elapsed_text}")
        if self.automatic_test_state == AutomaticTestState.PAUSED:
            current_a = max(0.0, float(self.active_output_current_a or 0.0))
            self.run_current_label.setText(f"保持 {current_a:.1f} A")
            remaining_ms = self.automatic_pause_safety_timer.remainingTime()
            if remaining_ms >= 0:
                remaining_s = math.ceil(remaining_ms / 1000.0)
                countdown_minutes, countdown_seconds = divmod(remaining_s, 60)
                self.run_remaining_label.setText(
                    f"{countdown_minutes:02d}:{countdown_seconds:02d} 后自动安全下电"
                )
            else:
                self.run_remaining_label.setText("保持当前电流，等待处理")
        else:
            self.run_remaining_label.setText("剩余时间由判稳速度决定")

    def toggle_automatic_pause(self) -> None:
        if self.automatic_test_state == AutomaticTestState.SAVING_POINT:
            return
        if self.automatic_test_state in (
            AutomaticTestState.STARTING,
            AutomaticTestState.SETTING_CURRENT,
            AutomaticTestState.WAITING_STABLE,
            AutomaticTestState.WAITING_VOLTAGE,
        ):
            self.pause_automatic_test("操作者暂停", operator_requested=True)

    def show_automatic_result(self, record: ExcelTestRecord | None, detail: str) -> None:
        saved_points = len(self.record_store.recorded_currents)
        planned_points = len(self.automatic_test_currents)
        outcome = self.automatic_controller.terminal_outcome
        reason = self.automatic_controller.terminal_reason
        if outcome == AutomaticTestTerminalOutcome.SUCCEEDED:
            self.result_title_label.setText("测试完整完成")
            completion_text = (
                f"计划的 {planned_points or saved_points} 个测试点已完成，"
                "Excel 将自动保存，并已安全下电"
            )
        elif outcome == AutomaticTestTerminalOutcome.ABORTED_SAFELY:
            self.result_title_label.setText("测试异常中止")
            reason_text = reason or detail or "测试过程中发生异常"
            completion_text = (
                f"{reason_text}；已采集 {saved_points}/{planned_points or saved_points} 个测试点，"
                "本轮未保存到 Excel，设备已安全下电"
            )
        else:
            self.result_title_label.setText("测试已提前结束")
            completion_text = (
                f"已采集 {saved_points}/{planned_points or saved_points} 个测试点，"
                "本轮未保存到 Excel，设备已安全下电"
            )
        self.result_completion_label.setText(completion_text)
        self.result_sn_label.setText(self.sn_field.text().strip() or "--")
        self.result_station_label.setText(self.test_session_station or self.test_station_field.text().strip() or "--")
        self.result_points_label.setText(f"{saved_points} / {planned_points or saved_points}")
        self.result_time_label.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        result_path = self.excel_workbook_path
        result_exists = bool(result_path is not None and result_path.is_file())
        if result_path is None:
            result_label = "尚未创建结果文件"
        elif result_exists:
            result_label = str(result_path)
        else:
            result_label = f"{result_path}（尚未生成）"
        self.result_file_label.setText(result_label)
        self.open_result_button.setEnabled(result_exists)
        self.open_result_folder_button.setEnabled(result_exists)

        values = {
            "current": "--",
            "power": "--",
            "efficiency": "--",
            "wavelength": "--",
            "fwhm": "--",
            "pib": "--",
        }
        if record is not None:
            values.update(
                current=f"{record.current_a:.1f} A",
                power=f"{record.power_w:.3f} W",
                efficiency=f"{record.efficiency * 100.0:.2f} %",
            )
            if self.automatic_controller.automatic_uses_spectrometer():
                values.update(
                    wavelength=f"{record.peak_wavelength_nm:.3f} nm",
                    fwhm=f"{record.fwhm_nm:.3f} nm",
                    pib=f"{record.pib * 100.0:.2f} %",
                )
        for key, value in values.items():
            self.result_metric_labels[key].setText(value)
        self.automatic_stack.setCurrentIndex(self.automatic_result_index)
        self.main_tabs.setCurrentIndex(self.automatic_tab_index)
        focus_target = self.open_result_button if result_exists else self.return_to_prepare_button
        QTimer.singleShot(
            0,
            lambda widget=focus_target: widget.setFocus(Qt.FocusReason.OtherFocusReason),
        )

    def return_to_automatic_prepare(self) -> None:
        self.reset_automatic_test()
        self.main_tabs.setCurrentIndex(self.automatic_tab_index)
        self.refresh_preflight_checklist()
        QTimer.singleShot(0, self._focus_automatic_sn)

    def _focus_automatic_sn(self) -> None:
        self.sn_field.setFocus(Qt.FocusReason.OtherFocusReason)
        self.sn_field.selectAll()

    def open_result_file(self) -> None:
        if self.excel_workbook_path is not None and self.excel_workbook_path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.excel_workbook_path)))

    def open_result_folder(self) -> None:
        if self.excel_workbook_path is not None and self.excel_workbook_path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.excel_workbook_path.parent)))

    def generate_shipping_report_from_excel(self) -> None:
        if self.automatic_test_state not in (
            AutomaticTestState.IDLE,
            AutomaticTestState.COMPLETED,
        ):
            QMessageBox.warning(self, "生成出货报告", "自动测试运行期间不能生成出货报告。")
            return
        initial_dir = self.output_dir_field.text().strip() or str(Path.cwd())
        source_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择出货报告测试数据",
            initial_dir,
            "Excel 测试文件 (*.xlsx)",
        )
        if not source_path:
            return
        try:
            inspection = inspect_shipping_workbook(source_path)
        except ValueError as exc:
            QMessageBox.warning(self, "生成出货报告", str(exc))
            return
        if inspection.compatibility.kind == "rejected":
            QMessageBox.warning(self, "生成出货报告", inspection.compatibility.message)
            return
        dialog = ShippingReportConfigurationDialog(inspection, self.input_settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.request is None:
            return
        request = dialog.request
        safe_sn = sanitize_sn(request.sn)
        default_path = inspection.source_path.with_name(f"{safe_sn}_出货报告.pdf")
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存出货报告",
            str(default_path),
            "PDF 文件 (*.pdf)",
        )
        if not output_path:
            return
        try:
            target = generate_shipping_report(source_path, output_path, request)
            save_shipping_report_preferences(self.input_settings, request)
        except Exception as exc:
            QMessageBox.critical(self, "生成出货报告", user_facing_error_message(exc))
            return
        self.statusBar().showMessage(f"出货报告已生成：{target}")
        self.add_log(f"出货报告已生成：{target}")
        confirmation = QMessageBox(self)
        confirmation.setWindowTitle("出货报告已生成")
        confirmation.setIcon(QMessageBox.Icon.Information)
        confirmation.setText(f"PDF 已保存：\n{target}")
        open_pdf_button = confirmation.addButton("打开 PDF", QMessageBox.ButtonRole.AcceptRole)
        open_folder_button = confirmation.addButton("打开文件夹", QMessageBox.ButtonRole.ActionRole)
        confirmation.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        confirmation.exec()
        if confirmation.clickedButton() is open_pdf_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        elif confirmation.clickedButton() is open_folder_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))

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
                QMessageBox.warning(self, "Excel 输出", user_facing_error_message(exc))
                return
        self.start_power_meter()
        self.start_spectrometer()

    def _test_settings_snapshot(self, mode: str) -> dict[str, Any]:
        return {
            "mode": mode,
            "power_supply_controller": self._selected_power_supply_kind(),
            "power_meter_resource": self._selected_power_resource(),
            "power_meter_wavelength_nm": self.power_wavelength_spin.value(),
            "software_gain": self.software_gain_spin.value(),
            "power_meter_interval_ms": self.power_meter_interval_spin.value(),
            "spectrometer_device_id": self._selected_spectrometer_device_id(),
            "integration_time_us": self.integration_spin.value(),
            "auto_integration_enabled": self.auto_integration_check.isChecked(),
            "spectrometer_interval_ms": self.interval_spin.value(),
            "stable_window_s": self.stable_window_spin.value(),
            "initial_current_a": self.auto_initial_current_spin.value(),
            "target_current_a": self.auto_target_current_spin.value(),
            "current_step_a": self.auto_current_step_spin.value(),
            "point_timeout_s": self.auto_point_timeout_spin.value(),
            "ramp_down_step_a": self.auto_ramp_down_step_spin.value(),
            "ramp_down_interval_s": self.auto_ramp_down_interval_spin.value(),
            "pause_ramp_down_timeout_s": self.auto_pause_ramp_down_timeout_spin.value(),
            "use_spectrometer": self.auto_use_spectrometer_check.isChecked(),
        }

    def _device_snapshots(self) -> tuple[DeviceSnapshot, ...]:
        power_option = self.power_meter_combo.currentData()
        spectrum_option = self.spectrometer_combo.currentData()
        power_detail = power_option.detail if isinstance(power_option, PowerMeterOption) else ""
        spectrum_detail = (
            f"device id {spectrum_option.device_id}"
            if isinstance(spectrum_option, SpectrometerOption)
            else ""
        )
        return (
            DeviceSnapshot(
                role="电源",
                kind=self._selected_power_supply_kind(),
                resource=self.tdk_resource_combo.currentText().strip()
                if self._selected_power_supply_kind() == "tdk"
                else "CH341 I²C",
            ),
            DeviceSnapshot(
                role="功率计",
                kind=power_option.device_type if isinstance(power_option, PowerMeterOption) else "",
                resource=self._selected_power_resource(),
                detail=power_detail,
            ),
            DeviceSnapshot(
                role="光谱仪",
                kind="Ocean Insight" if spectrum_option is not None else "",
                detail=spectrum_detail,
            ),
        )

    def begin_test_session(
        self,
        reset_records: bool = True,
        *,
        require_station: bool = False,
    ) -> Path:
        sn = self.sn_field.text().strip()
        sanitize_sn(sn)
        test_station = self.test_station_field.text().strip()
        if require_station and not test_station:
            raise ValueError("测试站别不能为空")
        output_dir_text = self.output_dir_field.text().strip()
        if not output_dir_text:
            raise ValueError("Excel 输出文件夹不能为空")
        mode = "automatic" if require_station else "manual"
        self.test_session_started_at = datetime.now()
        self.test_session_station = test_station
        self.excel_workbook_path = self.record_store.begin_session(
            Path(output_dir_text),
            sn,
            self.test_session_started_at,
            test_station=test_station,
            reset=reset_records,
            mode=mode,
            product_model=self.product_model_field.text().strip(),
            batch=self.batch_field.text().strip(),
            settings=self._test_settings_snapshot(mode),
            devices=self._device_snapshots(),
        )
        if reset_records:
            self.save_status_label.setText("暂无可保存的测试点")
            self.save_excel_button.setEnabled(False)
        self.add_log(f"Excel 输出：{self.excel_workbook_path}")
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
        self.pd_panel.stop_acquisition()

    def emergency_stop(self) -> None:
        """Immediately de-energize the supply after an operator request."""
        self._execute_emergency_stop(
            terminal_outcome=AutomaticTestTerminalOutcome.STOPPED_BY_OPERATOR,
            terminal_reason="操作者执行紧急停止",
            completion_detail="紧急停止已执行，电源已安全停止",
            action_label="紧急停止",
        )

    def emergency_stop_for_power_protection(self, reason: str) -> None:
        """Immediately de-energize the supply after an automatic power loss."""
        self._execute_emergency_stop(
            terminal_outcome=AutomaticTestTerminalOutcome.ABORTED_SAFELY,
            terminal_reason=reason,
            completion_detail=f"功率保护已触发，电源已立即断电：{reason}",
            action_label="功率保护",
        )

    def show_power_protection_alert(self, reason: str) -> None:
        """Require an operator-facing acknowledgement after protection trips."""
        QMessageBox.critical(
            self,
            "功率保护",
            "检测到功率异常下降，已执行断电保护。\n\n"
            f"{reason}\n\n"
            "请检查被测器件和电源状态，确认安全后再重新测试。",
        )

    def _execute_emergency_stop(
        self,
        *,
        terminal_outcome: AutomaticTestTerminalOutcome,
        terminal_reason: str,
        completion_detail: str,
        action_label: str,
    ) -> None:
        """Apply the shared immediate hardware shutdown boundary."""
        automatic_active = self._automatic_workflow_is_active()
        if automatic_active:
            self.automatic_controller._set_pending_terminal_outcome(
                terminal_outcome,
                terminal_reason,
            )
            for timer in (
                self.automatic_device_start_timer,
                self.automatic_point_timer,
                self.automatic_command_timer,
                self.automatic_ramp_down_timer,
                self.automatic_pause_safety_timer,
            ):
                timer.stop()
            self.automatic_ramp_up_currents.clear()
            self.automatic_ramp_down_currents.clear()
            self.pending_automatic_current_a = None
            self.pending_automatic_command_kind = None

        self.cancel_auto_vout_read()
        power_supply = self.get_power_supply()
        is_tdk = self.power_supply_controller_kind == "tdk"
        power_supply_connected = bool(power_supply is not None and power_supply.connected)
        current_zero_confirmed = (
            not power_supply_connected
            and float(self.active_output_current_a or 0.0) <= 0.0
            and not self._manual_output_is_energized()
        )
        output_off_confirmed = not is_tdk
        errors: list[str] = []

        if not power_supply_connected:
            if not current_zero_confirmed:
                errors.append("电源未连接，无法确认输出电流已归零")
        else:
            assert power_supply is not None
            try:
                # Emergency commands deliberately bypass the routine command interval.
                power_supply.set_current(0.0)
                current_zero_confirmed = True
            except Exception as exc:
                errors.append(f"电流置零失败：{user_facing_error_message(exc)}")

            if is_tdk:
                try:
                    power_supply.set_output_enabled(False)
                    output_off_confirmed = True
                    self.automatic_controller._mark_output_shutdown_confirmed()
                    self.sync_tdk_output_controls(False)
                except Exception as exc:
                    self.automatic_controller._mark_output_shutdown_unconfirmed()
                    errors.append(f"TDK 输出关闭失败：{user_facing_error_message(exc)}")

        supply_is_safe = output_off_confirmed if is_tdk else current_zero_confirmed
        if current_zero_confirmed or (is_tdk and output_off_confirmed):
            self.active_output_current_a = 0.0
            self.set_current_spin.setValue(0.0)
            self.pending_stable_point_current_a = None
            self.pending_stable_point_generation = None
            self.recorded_stable_point_current_a = None
            self.recorded_stable_point_generation = None
            self.last_power_supply_command_monotonic_s = time.monotonic()
            self.update_stable_power_curve()

        # Acquisition shutdown must still be requested even when a supply command fails.
        self.stop_power_meter()
        self.stop_spectrometer()
        self.pd_panel.stop_acquisition()
        self._refresh_manual_power_tab_lock(manual_action=True)

        if automatic_active and supply_is_safe:
            self.complete_automatic_test(
                tdk_output_already_disabled=is_tdk,
                completion_detail=completion_detail,
            )
        else:
            self.update_global_status()

        if errors:
            detail = "\n".join(errors)
            self.add_log(f"{action_label}存在异常：{'；'.join(errors)}")
            if supply_is_safe:
                self.statusBar().showMessage(f"{action_label}已执行，但部分电源命令返回异常")
            else:
                self.statusBar().showMessage(f"{action_label}断电未完成，请立即检查电源面板")
            QMessageBox.critical(
                self,
                action_label,
                f"{detail}\n\n请立即检查电源面板并确认输出状态。",
            )
            return

        message = f"{action_label}已执行：电源电流已归零，采集设备正在停止"
        if not power_supply_connected:
            message = f"{action_label}已执行：未检测到已连接电源，采集设备正在停止"
        elif is_tdk:
            message = f"{action_label}已执行：TDK 输出已关闭，采集设备正在停止"
        self.statusBar().showMessage(message)
        self.add_log(message)
        if action_label == "功率保护":
            self.show_power_protection_alert(terminal_reason)

    def update_global_status(self) -> None:
        if not hasattr(self, "global_status_label"):
            return
        psu_connected = self._manual_i2c_connected()
        power_running = self.power_meter_reader is not None
        spectrometer_running = self.spectrometer_reader is not None
        pd_panel = getattr(self, "pd_panel", None)
        pd_running = pd_panel is not None and pd_panel.reader is not None
        power_connected = power_running and bool(getattr(self.power_meter_reader, "is_ready", False))
        spectrometer_connected = spectrometer_running and bool(getattr(self.spectrometer_reader, "is_ready", False))
        power_detecting = self.power_meter_detect_thread is not None
        automatic_active = self._automatic_workflow_is_active()
        is_tdk = self._selected_power_supply_kind() == "tdk"
        tdk_output_enabled = bool(
            is_tdk
            and psu_connected
            and getattr(self.manual_ch341_controller, "output_enabled", False)
        )
        last_output_current_a = max(0.0, float(self.active_output_current_a or 0.0))
        shutdown_unconfirmed = bool(
            getattr(self.automatic_controller, "output_shutdown_unconfirmed", False)
        )
        output_state_unknown = bool(
            shutdown_unconfirmed
            or (
                not psu_connected
                and automatic_active
                and last_output_current_a > 0.0
            )
        )
        power_fault = bool(self._power_meter_fault_message)
        spectrometer_fault = bool(self._spectrometer_fault_message)

        if self.automatic_test_state == AutomaticTestState.PAUSED:
            status_text = "自动测试已暂停"
        elif self.automatic_test_state == AutomaticTestState.RAMPING_DOWN:
            status_text = "自动测试下电中"
        elif automatic_active:
            status_text = "自动测试运行中"
        elif (
            self.automatic_test_state == AutomaticTestState.COMPLETED
            and self.main_tabs.currentIndex() == self.automatic_tab_index
        ):
            status_text = "测试完成"
        else:
            status_text = ""
        self.global_status_label.setText(status_text)
        self.global_status_label.setVisible(bool(status_text))
        if shutdown_unconfirmed:
            current_detail = (
                f" · 最近设定 {last_output_current_a:.1f} A"
                if last_output_current_a > 0.0
                else ""
            )
            self.global_psu_status_label.setText(
                f"电源：故障 · 输出状态未确认{current_detail}"
            )
            psu_state = "error"
        elif output_state_unknown:
            self.global_psu_status_label.setText(
                f"电源：连接异常 · 最近输出 {last_output_current_a:.1f} A"
            )
            psu_state = "error"
        elif not psu_connected:
            self.global_psu_status_label.setText("电源：已停止")
            psu_state = "stopped"
        elif is_tdk and not tdk_output_enabled:
            self.global_psu_status_label.setText("电源：已连接 · 输出关闭")
            psu_state = "pending"
        else:
            output_text = " · 输出开启" if is_tdk else ""
            self.global_psu_status_label.setText(f"电源：已就绪{output_text}")
            psu_state = "ready"
        if power_fault:
            self.global_power_meter_status_label.setText("功率计：故障")
            self.global_power_meter_status_label.setToolTip(self._power_meter_fault_message)
            power_state = "error"
        elif power_detecting:
            self.global_power_meter_status_label.setText("功率计：检测中")
            self.global_power_meter_status_label.setToolTip("")
            power_state = "pending"
        elif power_connected:
            self.global_power_meter_status_label.setText("功率计：已就绪")
            self.global_power_meter_status_label.setToolTip("")
            power_state = "ready"
        elif power_running:
            self.global_power_meter_status_label.setText("功率计：启动中")
            self.global_power_meter_status_label.setToolTip("")
            power_state = "pending"
        else:
            self.global_power_meter_status_label.setText("功率计：已停止")
            self.global_power_meter_status_label.setToolTip("")
            power_state = "stopped"
        if spectrometer_fault:
            self.global_spectrometer_status_label.setText("光谱仪：故障")
            self.global_spectrometer_status_label.setToolTip(self._spectrometer_fault_message)
            spectrometer_state = "error"
        elif spectrometer_connected:
            self.global_spectrometer_status_label.setText("光谱仪：已就绪")
            self.global_spectrometer_status_label.setToolTip("")
            spectrometer_state = "ready"
        elif spectrometer_running:
            self.global_spectrometer_status_label.setText("光谱仪：启动中")
            self.global_spectrometer_status_label.setToolTip("")
            spectrometer_state = "pending"
        else:
            self.global_spectrometer_status_label.setText("光谱仪：已停止")
            self.global_spectrometer_status_label.setToolTip("")
            spectrometer_state = "stopped"
        self._set_status_indicator(self.global_psu_status_indicator, psu_state)
        self._set_status_indicator(self.global_power_meter_status_indicator, power_state)
        self._set_status_indicator(self.global_spectrometer_status_indicator, spectrometer_state)
        self._update_prepare_measurement_controls()
        self.stop_all_button.setEnabled(
            power_running or spectrometer_running or pd_running or automatic_active
        )

        if hasattr(self, "power_meter_status_label"):
            self.power_meter_status_label.setText(
                "故障"
                if power_fault
                else (
                    "检测中"
                    if power_detecting
                    else ("已就绪" if power_connected else ("启动中" if power_running else "已停止"))
                )
            )
        if hasattr(self, "spectrometer_status_label"):
            self.spectrometer_status_label.setText(
                "故障"
                if spectrometer_fault
                else ("已就绪" if spectrometer_connected else ("启动中" if spectrometer_running else "已停止"))
            )
        if hasattr(self, "refresh_preflight_checklist"):
            self.refresh_preflight_checklist()

    def _manual_i2c_connected(self) -> bool:
        power_supply = self.get_power_supply()
        return power_supply is not None and power_supply.connected

    def _selected_power_supply_kind(self) -> str:
        return str(self.power_supply_controller_combo.currentData() or "ch341")

    def on_power_supply_controller_changed(self) -> None:
        selected_kind = self._selected_power_supply_kind()
        previous_kind = self.power_supply_controller_kind
        if self._manual_i2c_connected() and selected_kind != self.power_supply_controller_kind:
            previous_index = self.power_supply_controller_combo.findData(
                self.power_supply_controller_kind
            )
            for combo in (
                self.power_supply_controller_combo,
                getattr(self, "prepare_power_supply_combo", None),
            ):
                if combo is None:
                    continue
                blocked = combo.blockSignals(True)
                try:
                    combo.setCurrentIndex(previous_index)
                finally:
                    combo.blockSignals(blocked)
            self.statusBar().showMessage("电源已加电，请先断开电源再切换控制器或串口")
            self._update_prepare_power_controls()
            return
        if selected_kind != previous_kind and self.manual_ch341_controller is not None:
            # A disconnected CH341 object cannot be reused as a TDK RS-232
            # controller (or vice versa). Keeping it here sends the next
            # connection attempt through the previous controller's protocol.
            self.manual_ch341_controller = None
        self.power_supply_controller_kind = selected_kind
        is_tdk = selected_kind == "tdk"
        self.power_supply_details_form.setRowVisible(self.tdk_resource_row, is_tdk)
        self.power_supply_details_form.setRowVisible(self.tdk_voltage_row, is_tdk)
        self.power_supply_form.setRowVisible(self.tdk_output_row, is_tdk)
        self.power_supply_details_form.setRowVisible(self.power_supply_read_row, not is_tdk)
        current_maximum = TDK_CURRENT_INPUT_MAX_A if is_tdk else LEGACY_CURRENT_LIMIT_A
        for current_widget in (
            self.set_current_spin,
            self.auto_initial_current_spin,
            self.auto_target_current_spin,
            self.auto_current_step_spin,
            self.auto_ramp_down_step_spin,
        ):
            current_widget.setMaximum(current_maximum)
            current_widget.setToolTip(
                "TDK 模式不设置软件电流上限，请勿超过电源及被测产品的额定值。"
                if is_tdk
                else "CH341 控制协议的电流范围为 0–20 A。"
            )
        if not is_tdk:
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
        self.sync_tdk_output_controls(False)
        if hasattr(self, "power_supply_group"):
            self._reserve_group_height(self.power_supply_group)
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
                if label == "TDK":
                    controller.set_output_enabled(False)
                controller.disconnect_device()
            except Exception as exc:
                QMessageBox.critical(self, label, f"安全断开失败。\n{user_facing_error_message(exc)}")
                return
            if label == "TDK":
                self.manual_ch341_controller = None
                self.active_output_current_a = 0.0
            self.connect_i2c_button.setText(f"连接 {label}")
            self.i2c_status_label.setText("未连接")
            self.sync_tdk_output_controls(False)
            self._refresh_manual_power_tab_lock()
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
                self.sync_tdk_output_controls(output_enabled)
                maximum_voltage = getattr(controller, "maximum_voltage_v", None)
                if maximum_voltage is not None:
                    self.tdk_voltage_spin.setMaximum(float(maximum_voltage))
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
        manual_action = self._manual_power_action_context()
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
            if enabled:
                self._confirm_tdk_zero_current_before_output(power_supply)
            power_supply.set_output_enabled(enabled)
            self.sync_tdk_output_controls(enabled)
            if not enabled:
                self.active_output_current_a = 0.0
            self._refresh_manual_power_tab_lock(manual_action=manual_action)
            self.add_log(f"TDK 输出已{'开启' if enabled else '关闭'}")
            self.statusBar().showMessage(f"TDK 输出已{'开启' if enabled else '关闭'}")
            self.update_global_status()
        except Exception as exc:
            QMessageBox.critical(self, "TDK 输出", user_facing_error_message(exc))

    def _confirm_tdk_zero_current_before_output(self, power_supply: PowerSupply) -> None:
        """Program and verify a safe zero-current state before enabling TDK output."""
        power_supply.set_current(0.0)
        self.cancel_auto_vout_read()
        self.set_current_spin.setValue(0.0)
        self.active_output_current_a = 0.0
        self.pending_stable_point_current_a = None
        self.pending_stable_point_generation = None
        self.recorded_stable_point_current_a = None
        self.recorded_stable_point_generation = None

        measured_current_a = float(power_supply.read_output_current())
        if not math.isfinite(measured_current_a) or abs(measured_current_a) > 0.01:
            raise RuntimeError(
                "开启输出已取消：TDK 电流未归零，"
                f"当前实测 {measured_current_a:.3f} A"
            )
        self.add_log("TDK 开启输出前已确认电流为 0 A")

    def begin_power_supply_command(self, command_name: str) -> bool:
        """Reserve the CH341 bus so its I2C commands remain safely spaced."""
        if self.power_supply_controller_kind == "tdk":
            return True
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
        if self.power_supply_controller_kind == "tdk":
            return 0.0
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
            if automatic:
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
                and self.automatic_controller.automatic_uses_spectrometer()
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
                command_label = "RS-232 MV?" if self.power_supply_controller_kind == "tdk" else None
            elif command[1] == 0x8C and power_supply is not None:
                value = power_supply.read_output_current()
                command_label = "RS-232 MC?" if self.power_supply_controller_kind == "tdk" else None
            else:
                value = read_power_status_value(controller, DEFAULT_I2C_ADDRESS, command)
                command_label = None
            raw_command = " ".join(f"{item:02X}" for item in command)
            self.add_log(f"{name}: {value:.2f} {unit} ({command_label or raw_command})")
            self.statusBar().showMessage(f"{name}: {value:.2f} {unit}")
            return value
        except Exception as exc:
            if (
                self.power_supply_controller_kind == "tdk"
                and not bool(getattr(controller, "is_connected", False))
            ):
                self.i2c_status_label.setText("连接已失效")
                self.connect_i2c_button.setText("重新连接 TDK")
                self.tdk_output_status_label.setText("输出状态未知")
                self.update_global_status()
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

        raw_voltage_v = float(voltage_v)
        if self.power_supply_controller_kind == "tdk":
            voltage_v = compensate_tdk_output_voltage(raw_voltage_v, current_a)
            self.add_log(
                f"TDK 线阻补偿：MV? {raw_voltage_v:.3f} V → "
                f"负载端 {voltage_v:.3f} V（修正 {voltage_v - raw_voltage_v:+.3f} V）"
            )
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

        queued = self.queue_excel_test_point(
            current_a,
            voltage_v,
            power_w,
            efficiency_percent / 100.0,
            raw_voltage_v=raw_voltage_v,
        )
        self.automatic_controller.on_voltage_record_ready(current_a, queued, self.last_point_record_error)

    def pause_automatic_point_if_waiting(self, reason: str) -> None:
        if self.automatic_test_state == AutomaticTestState.WAITING_VOLTAGE:
            self.pause_automatic_test(reason)

    def _record_invalid_attempt(
        self,
        current_a: float,
        validity: AttemptValidity,
        reason: str,
        **spectrum: Any,
    ) -> bool:
        try:
            self.record_store.record_invalid_attempt(
                current_a,
                validity,
                reason,
                **spectrum,
            )
        except Exception as exc:
            self.last_point_record_error = f"无效测量尝试归档失败：{exc}"
            self.add_log(self.last_point_record_error)
            self.statusBar().showMessage(self.last_point_record_error)
            return False
        return True

    def queue_excel_test_point(
        self,
        current_a: float,
        voltage_v: float,
        power_w: float,
        efficiency: float,
        *,
        raw_voltage_v: float = math.nan,
    ) -> bool:
        self.last_point_record_error = ""
        spectrum_optional = (
            self.automatic_measurement_is_active()
            and not self.automatic_controller.automatic_uses_spectrometer()
        )
        if spectrum_optional:
            try:
                self.record_store.queue(
                    ExcelTestRecord(
                        current_a=current_a,
                        voltage_v=voltage_v,
                        power_w=power_w,
                        efficiency=efficiency,
                        peak_wavelength_nm=math.nan,
                        centroid_nm=math.nan,
                        fwhm_nm=math.nan,
                        pib=math.nan,
                        wavelength=[],
                        intensity=[],
                        smsr_db=math.nan,
                        test_station=self.test_session_station,
                    ),
                    actual_current_a=current_a,
                    voltage_raw_v=raw_voltage_v,
                    stable_span_w=(
                        self.latest_power_meter_reading.stable_span_w
                        if self.latest_power_meter_reading is not None
                        else math.nan
                    ),
                    stable_window_s=(
                        self.latest_power_meter_reading.stable_window_s
                        if self.latest_power_meter_reading is not None
                        else math.nan
                    ),
                    stable_tolerance_w=(
                        self.latest_power_meter_reading.stable_tolerance_w
                        if self.latest_power_meter_reading is not None
                        else math.nan
                    ),
                )
            except Exception as exc:
                self.last_point_record_error = f"测试档案写入失败：{exc}"
                self.add_log(self.last_point_record_error)
                return False
            self.save_excel_button.setEnabled(True)
            self.add_log(f"已加入无光谱测试点：{current_a:.3f} A")
            return True
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
            self._record_invalid_attempt(
                current_a,
                AttemptValidity.WEAK_SIGNAL,
                self.last_point_record_error,
                wavelength=self.latest_spectrum_wavelength,
                intensity=self.latest_spectrum_intensity,
                integration_time_us=self.integration_spin.value(),
            )
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
            self._record_invalid_attempt(
                current_a,
                AttemptValidity.SATURATED,
                message,
                wavelength=self.latest_spectrum_wavelength,
                intensity=self.latest_spectrum_intensity,
                integration_time_us=self.integration_spin.value(),
            )
            return False
        stats = calculate_stats(self.latest_spectrum_wavelength, self.latest_spectrum_intensity)
        smsr = calculate_smsr(self.latest_spectrum_wavelength, self.latest_spectrum_intensity)
        try:
            self.record_store.queue(
                ExcelTestRecord(
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
                    test_station=self.test_session_station,
                ),
                actual_current_a=current_a,
                voltage_raw_v=raw_voltage_v,
                stable_span_w=(
                    self.latest_power_meter_reading.stable_span_w
                    if self.latest_power_meter_reading is not None
                    else math.nan
                ),
                stable_window_s=(
                    self.latest_power_meter_reading.stable_window_s
                    if self.latest_power_meter_reading is not None
                    else math.nan
                ),
                stable_tolerance_w=(
                    self.latest_power_meter_reading.stable_tolerance_w
                    if self.latest_power_meter_reading is not None
                    else math.nan
                ),
                integration_time_us=self.integration_spin.value(),
            )
        except Exception as exc:
            self.last_point_record_error = f"测试数据暂存失败：{exc}"
            self.add_log(self.last_point_record_error)
            self.statusBar().showMessage(self.last_point_record_error)
            return False
        pending_count = len(self.record_store.unsaved_records())
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
        self._excel_export_retry_blocked = False
        session = self.record_store.current_session
        attempts = (
            self.record_store.list_attempts(session.session_id)
            if session is not None
            else ()
        )
        self.excel_save_thread = ExcelSaveThread(
            self.excel_workbook_path,
            records_snapshot,
            self,
            session=session,
            attempts=attempts,
        )
        self.excel_save_thread.saved.connect(self.on_excel_save_succeeded)
        self.excel_save_thread.failed.connect(self.on_excel_save_failed)
        self.excel_save_thread.finished.connect(self.on_excel_save_finished)
        self.save_excel_button.setEnabled(False)
        self.save_excel_button.setText("保存中…")
        self.save_status_label.setText(f"正在保存 {len(records_snapshot)} 个测试点…")
        self.add_log(f"正在后台保存 {len(records_snapshot)} 个测试点")
        self.excel_save_thread.start()

    def finalize_automatic_session_workbook(
        self,
        completion_record: ExcelTestRecord | None,
        completed_message: str,
    ) -> bool:
        """Rewrite the workbook once so its terminal session status is durable."""
        if self.excel_save_thread is not None or self.excel_workbook_path is None:
            return False
        records_snapshot = list(self.record_store.snapshot())
        session = self.record_store.current_session
        if not records_snapshot or session is None or not self.excel_workbook_path.is_file():
            return False
        attempts = self.record_store.list_attempts(session.session_id)
        self._automatic_finalization_pending = (completion_record, completed_message)
        self.excel_save_thread = ExcelSaveThread(
            self.excel_workbook_path,
            records_snapshot,
            self,
            session=session,
            attempts=attempts,
        )
        self.excel_save_thread.saved.connect(self.on_excel_save_succeeded)
        self.excel_save_thread.failed.connect(self.on_excel_save_failed)
        self.excel_save_thread.finished.connect(self.on_excel_save_finished)
        self.save_excel_button.setEnabled(False)
        self.save_excel_button.setText("保存中…")
        self.save_status_label.setText("正在写入最终测试状态…")
        self.excel_save_thread.start()
        return True

    def on_excel_save_succeeded(self, elapsed_s: float) -> None:
        thread = self.excel_save_thread
        if thread is None:
            return
        try:
            self.record_store.mark_saved(tuple(thread.records))
        except Exception as exc:
            message = f"Excel 已保存，但测试状态更新失败：{exc}"
            self._excel_export_retry_blocked = True
            self.save_status_label.setText("测试状态更新失败")
            self.statusBar().showMessage(message)
            self.add_log(message)
            self.automatic_controller.on_record_save_failed(message)
            return
        remaining_count = len(self.record_store.unsaved_records())
        if remaining_count:
            self.save_status_label.setText(f"已在 {elapsed_s:.2f} 秒内保存；另有 {remaining_count} 个新测试点待保存")
        else:
            self.save_status_label.setText(f"已在 {elapsed_s:.2f} 秒内保存：{thread.path.name}")
        self.statusBar().showMessage(f"Excel 已在 {elapsed_s:.2f} 秒内保存：{thread.path.name}")
        self.add_log(f"Excel 已在 {elapsed_s:.2f} 秒内保存：{thread.path}")
        pending_finalization = self._automatic_finalization_pending
        if pending_finalization is not None:
            self._automatic_finalization_pending = None
            completion_record, completed_message = pending_finalization
            self.automatic_controller.finish_automatic_test_presentation(
                completion_record,
                completed_message,
            )
        else:
            self.automatic_controller.on_record_saved()

    def on_excel_save_failed(self, message: str) -> None:
        self._excel_export_retry_blocked = True
        self.save_status_label.setText("保存失败")
        self.add_log(f"Excel 保存失败：{message}")
        pending_finalization = self._automatic_finalization_pending
        if pending_finalization is not None:
            self._automatic_finalization_pending = None
            completion_record, completed_message = pending_finalization
            self.automatic_controller.finish_automatic_test_presentation(
                completion_record,
                f"{completed_message}；Excel 最终状态写入失败",
            )
        else:
            self.automatic_controller.on_record_save_failed(message)
        QMessageBox.critical(self, "保存 Excel", user_facing_error_message(message))

    def on_excel_save_finished(self) -> None:
        thread = self.excel_save_thread
        self.excel_save_thread = None
        automatic_idle = self.automatic_test_state in (AutomaticTestState.IDLE, AutomaticTestState.COMPLETED)
        self.save_excel_button.setEnabled(
            automatic_idle
            and bool(self.record_store.unsaved_records())
        )
        self.save_excel_button.setText("保存 Excel")
        if thread is not None:
            thread.deleteLater()
        self._continue_pending_close()

    def apply_output_current(self) -> None:
        manual_action = self._manual_power_action_context()
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
            self._refresh_manual_power_tab_lock(manual_action=manual_action)
            self.add_log(f"输出电流已设为 {self.set_current_spin.value():.1f} A")
            self.statusBar().showMessage(f"输出电流已设为 {self.set_current_spin.value():.1f} A")
        except Exception as exc:
            QMessageBox.critical(self, "设置电流", user_facing_error_message(exc))

    def refresh_power_meter_resources(self) -> None:
        current = self._selected_power_resource()
        try:
            with visa_resource_manager() as rm:
                resources = sorted(str(item) for item in rm.list_resources() if str(item).startswith("ASRL"))
            self.power_meter_combo.clear()
            for resource in resources:
                self._add_power_meter_resource_option(resource)
            if current:
                index = self._find_power_meter_resource_index(current)
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
        option = self.power_meter_combo.currentData()
        if isinstance(option, PowerMeterOption) and option.driver_kind == "laserpoint":
            signals_were_blocked = self.rel_zero_check.blockSignals(True)
            try:
                self.rel_zero_check.setChecked(False)
            finally:
                self.rel_zero_check.blockSignals(signals_were_blocked)
            if enabled:
                QMessageBox.warning(self, "相对调零", "LaserPoint 不支持彩煌的相对调零指令。")
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

    def auto_detect_power_meters(self, _checked: bool = False) -> None:
        self._start_power_meter_detection(scan_all_resources=True, silent=False)

    def auto_detect_selected_power_meter(self) -> None:
        """Quietly identify the saved meter without probing unrelated serial ports."""
        self._start_power_meter_detection(scan_all_resources=False, silent=True)

    def _start_power_meter_detection(self, *, scan_all_resources: bool, silent: bool) -> None:
        if self.power_meter_detect_thread is not None:
            return
        self._power_meter_fault_message = ""
        self._power_meter_detection_silent = silent
        self.power_meter_detect_thread = PowerMeterDetectThread(
            self._selected_power_resource(),
            self,
            scan_all_resources=scan_all_resources,
        )
        self.power_meter_detect_thread.detected.connect(self.on_power_meter_detected)
        self.power_meter_detect_thread.status.connect(self.on_status)
        self.power_meter_detect_thread.failed.connect(self.on_power_meter_detect_failed)
        self.power_meter_detect_thread.finished.connect(self.on_power_meter_detect_finished)
        self.set_power_meter_detecting_state(True)
        self.statusBar().showMessage("正在检测功率计…")
        self.power_meter_detect_thread.start()

    def auto_detect_spectrometers(self) -> None:
        self._spectrometer_fault_message = ""
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
            self._spectrometer_fault_message = user_facing_error_message(exc)
            self.update_global_status()
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
        current_text = self.power_meter_combo.currentText()
        option = self.power_meter_combo.currentData()
        if isinstance(option, PowerMeterOption) and current_text == option.label():
            return option.resource
        return extract_power_resource_name(current_text)

    def _add_power_meter_resource_option(
        self,
        resource: str,
        *,
        device_type: str = UNDETECTED_POWER_METER_NAME,
        detail: str = "",
        driver_kind: str = "caihuang",
    ) -> int:
        """Add a selectable resource while presenting the supported meter name."""
        option = PowerMeterOption(
            resource=normalize_power_resource_name(resource),
            device_type=device_type,
            detail=detail,
            driver_kind=driver_kind,
        )
        self.power_meter_combo.addItem(option.label(), option)
        return self.power_meter_combo.count() - 1

    def _find_power_meter_resource_index(self, resource: str) -> int:
        normalized = normalize_power_resource_name(resource)
        for index in range(self.power_meter_combo.count()):
            option = self.power_meter_combo.itemData(index)
            if isinstance(option, PowerMeterOption) and option.resource == normalized:
                return index
            if extract_power_resource_name(self.power_meter_combo.itemText(index)) == normalized:
                return index
        return -1

    def _selected_spectrometer_device_id(self) -> int | None:
        option = self.spectrometer_combo.currentData()
        if isinstance(option, SpectrometerOption):
            return option.device_id
        return None

    def collect_power_meter_settings(self) -> PowerMeterSettings:
        option = self.power_meter_combo.currentData()
        return PowerMeterSettings(
            resource=self._selected_power_resource(),
            wavelength_nm=self.power_wavelength_spin.value(),
            software_gain=self.software_gain_spin.value(),
            interval_ms=self.power_meter_interval_spin.value(),
            stable_window_s=self.stable_window_spin.value(),
            stable_tolerance_w=self.stable_tolerance_spin.value(),
            driver_kind=(option.driver_kind if isinstance(option, PowerMeterOption) else "caihuang"),
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
        self._power_meter_fault_message = ""
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
        self._spectrometer_fault_message = ""
        try:
            settings = self.collect_spectrometer_settings()
        except Exception as exc:
            QMessageBox.warning(self, "光谱仪", user_facing_error_message(exc))
            return

        self.reset_spectrum_curve()
        self.add_log("正在启动光谱仪采集")
        self.spectrometer_reader = SpectrometerReaderThread(settings, self)
        self.spectrometer_reader.reading.connect(self.on_spectrometer_reading)
        self.spectrometer_reader.status.connect(self.on_status)
        self.spectrometer_reader.integration_time_changed.connect(self.on_integration_time_changed)
        self.spectrometer_reader.ready.connect(self.on_spectrometer_ready)
        self.spectrometer_reader.failed.connect(self.on_spectrometer_failed)
        self.spectrometer_reader.finished.connect(self.on_spectrometer_finished)
        self.spectrometer_reader.start()
        self.spectrum_refresh_timer.start()
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

    def refresh_latest_spectrum(self) -> None:
        """Render at most one newest frame, dropping superseded frames."""
        reader = self.spectrometer_reader
        if reader is None:
            return
        take_latest = getattr(reader, "take_latest_spectrum", None)
        if not callable(take_latest):
            return
        spectrum = take_latest()
        if spectrum is not None:
            self.on_spectrum_curve(*spectrum)

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
        if self.automatic_controller.on_automatic_power_sample(
            reading.power_w,
            reading.elapsed_s,
        ):
            return
        self.live_plots.set_power_value(reading.power_w)
        entry_tolerance_w = stability_tolerance_for_power(reading.power_w)
        active_tolerance_w = (
            reading.stable_tolerance_w
            if math.isfinite(reading.stable_tolerance_w)
            else entry_tolerance_w
        )
        signals_were_blocked = self.stable_tolerance_spin.blockSignals(True)
        try:
            self.stable_tolerance_spin.setValue(entry_tolerance_w)
        finally:
            self.stable_tolerance_spin.blockSignals(signals_were_blocked)
        exit_tolerance_w = (
            entry_tolerance_w * PowerStabilityDetector.EXIT_TOLERANCE_MULTIPLIER
        )
        self.stability_tolerance_label.setText(
            f"当前功率峰峰值：判稳 ≤{entry_tolerance_w:.4f} W；"
            f"稳定保持 ≤{exit_tolerance_w:.4f} W"
        )
        self.update_stability_card(
            reading.stable,
            reading.stable_span_w,
            reading.stable_window_s,
            active_tolerance_w,
        )
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
                and self.automatic_controller.automatic_uses_spectrometer()
            ):
                self.invalidate_automatic_stability("中心波长不再稳定")
        else:
            self.wavelength_stability_detector.reset()
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
        self.latest_spectrum_smsr_db = None if smsr is None else float(smsr.smsr_db)
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
        require_spectrum_stability = (
            require_joint_stability and self.automatic_controller.automatic_uses_spectrometer()
        )
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
        if require_spectrum_stability and not self.latest_wavelength_stable:
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
        if require_spectrum_stability:
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
        self.latest_spectrum_smsr_db = None
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
        self._power_meter_fault_message = ""
        selected_resource = self._selected_power_resource() or DEFAULT_POWER_RESOURCE
        self.power_meter_combo.clear()
        if not options:
            self._add_power_meter_resource_option(selected_resource)
            if not self._power_meter_detection_silent:
                QMessageBox.warning(self, "功率计自动检测", "未检测到支持的功率计。")
            self.statusBar().showMessage("未检测到支持的功率计")
            return

        for option in options:
            self.power_meter_combo.addItem(option.label(), option)
        self.power_meter_combo.setCurrentIndex(0)
        self.statusBar().showMessage(f"检测到 {len(options)} 台功率计")
        self.add_log(f"检测到 {len(options)} 台功率计")

    def on_power_meter_detect_failed(self, message: str) -> None:
        self._power_meter_fault_message = user_facing_error_message(message)
        self.add_log(f"功率计自动检测错误：{message}")
        self.update_global_status()
        if not self._power_meter_detection_silent:
            QMessageBox.critical(self, "功率计自动检测", user_facing_error_message(message))

    def on_power_meter_detect_finished(self) -> None:
        thread = self.power_meter_detect_thread
        self.power_meter_detect_thread = None
        self._power_meter_detection_silent = False
        self.set_power_meter_detecting_state(False)
        if thread is not None:
            thread.deleteLater()
        self._continue_pending_close()

    def on_status(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self.add_log(message)

    def on_power_meter_failed(self, message: str) -> None:
        self._power_meter_fault_message = user_facing_error_message(message)
        self.add_log(f"功率计错误：{message}")
        display_message = user_facing_error_message(message)
        self.automatic_controller.on_acquisition_failed("功率计", message)
        self.update_global_status()
        QMessageBox.critical(self, "功率计错误", display_message)

    def on_spectrometer_failed(self, message: str) -> None:
        self._spectrometer_fault_message = user_facing_error_message(message)
        self.add_log(f"光谱仪错误：{message}")
        display_message = user_facing_error_message(message)
        self.automatic_controller.on_acquisition_failed("光谱仪", message)
        self.update_global_status()
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
        self.refresh_latest_spectrum()
        self.spectrum_refresh_timer.stop()
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
        automatic_active = self._automatic_workflow_is_active()
        self.start_power_meter_button.setHidden(running or automatic_active)
        self.stop_power_meter_button.setHidden(not running or automatic_active)
        self.start_power_meter_button.setEnabled(not running and not detecting)
        self.stop_power_meter_button.setEnabled(running and not automatic_active)
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
        automatic_active = self._automatic_workflow_is_active()
        self.start_power_meter_button.setHidden(running or automatic_active)
        self.stop_power_meter_button.setHidden(not running or automatic_active)
        self.start_power_meter_button.setEnabled(not running and not detecting)
        self.stop_power_meter_button.setEnabled(running and not automatic_active)
        self.detect_power_meter_button.setEnabled(not running and not detecting)
        self.refresh_power_meter_button.setEnabled(not running and not detecting)
        self.rel_zero_check.setEnabled(not running and not detecting)
        self.power_meter_combo.setEnabled(not running and not detecting)
        self.power_wavelength_spin.setEnabled(not running and not detecting)
        self.software_gain_spin.setEnabled(not running and not detecting)
        self.power_meter_interval_spin.setEnabled(not running and not detecting)
        self.update_global_status()

    def set_spectrometer_running_state(self, running: bool) -> None:
        automatic_active = self._automatic_workflow_is_active()
        self.start_spectrometer_button.setHidden(running or automatic_active)
        self.stop_spectrometer_button.setHidden(not running or automatic_active)
        self.start_spectrometer_button.setEnabled(not running)
        self.stop_spectrometer_button.setEnabled(running and not automatic_active)
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
            plot_parent = self.live_plots.group.parentWidget()
            available_width = max(
                plot_parent.width() if plot_parent is not None else 0,
                self.width() - 64,
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
                self.pd_panel.reader,
            )
        )

    def _request_background_stop(self) -> None:
        if self.power_meter_detect_thread is not None:
            self.power_meter_detect_thread.stop()
        self.stop_power_meter()
        self.stop_spectrometer()
        self.pd_panel.stop_acquisition()

    def _continue_pending_close(self) -> None:
        if self.close_after_background_tasks and not self._background_tasks_are_running():
            self.background_stop_timeout_timer.stop()
            self.close_after_background_tasks = False
            QTimer.singleShot(0, self.close)

    def _ask_background_stop_timeout_action(self, can_force: bool) -> str:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle("后台设备停止超时")
        dialog.setText("采集设备未能在限定时间内停止。")
        dialog.setInformativeText(
            "可以继续等待，或取消退出并检查设备连接。"
            + ("\n强制停止可能使设备驱动需要重新连接。" if can_force else "\nExcel 正在保存，不能强制停止。")
        )
        retry_button = dialog.addButton("继续等待", QMessageBox.ButtonRole.AcceptRole)
        force_button = (
            dialog.addButton("强制停止并退出", QMessageBox.ButtonRole.DestructiveRole)
            if can_force
            else None
        )
        cancel_button = dialog.addButton("取消退出", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(retry_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec()
        clicked_button = dialog.clickedButton()
        if force_button is not None and clicked_button is force_button:
            return "force"
        if clicked_button is retry_button:
            return "retry"
        return "cancel"

    def on_background_stop_timeout(self) -> None:
        if not self.close_after_background_tasks:
            return
        if not self._background_tasks_are_running():
            self._continue_pending_close()
            return

        excel_is_running = self._thread_is_running(self.excel_save_thread)
        action = self._ask_background_stop_timeout_action(not excel_is_running)
        if action == "retry":
            self._request_background_stop()
            self.background_stop_timeout_timer.start(round(BACKGROUND_STOP_TIMEOUT_S * 1000.0))
            return
        if action == "cancel":
            self.close_after_background_tasks = False
            self.statusBar().showMessage("已取消退出；请检查未响应的设备连接")
            return

        for thread in (
            self.power_meter_detect_thread,
            self.power_meter_reader,
            self.spectrometer_reader,
            self.pd_panel.reader,
        ):
            if not self._thread_is_running(thread):
                continue
            terminate = getattr(thread, "terminate", None)
            if callable(terminate):
                terminate()
            wait = getattr(thread, "wait", None)
            if callable(wait):
                wait(1000)

        if self._background_tasks_are_running():
            self.close_after_background_tasks = False
            QMessageBox.critical(self, "退出失败", "后台设备仍未停止，窗口将保持打开。")
            return
        self.close_after_background_tasks = False
        QTimer.singleShot(0, self.close)

    def _ask_tdk_shutdown_failure_action(self, error: BaseException) -> str:
        """Let the operator recover when the supply no longer answers during exit."""
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle("TDK 输出关闭失败")
        dialog.setText("无法确认 TDK 输出已经关闭。")
        dialog.setInformativeText(
            f"{user_facing_error_message(error)}\n\n"
            "可重试发送关闭命令；如果设备已经断开，可强制退出程序。\n"
            "强制退出前请在电源面板确认输出已关闭。"
        )
        retry_button = dialog.addButton("重试关闭输出", QMessageBox.ButtonRole.AcceptRole)
        force_button = dialog.addButton("强制退出", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(retry_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec()
        clicked_button = dialog.clickedButton()
        if clicked_button is retry_button:
            return "retry"
        if clicked_button is force_button:
            return "force"
        return "cancel"

    def _shutdown_tdk_for_close(self, event: QCloseEvent) -> bool:
        """Turn off and release TDK, with an explicit escape hatch for a dead link."""
        controller = self.manual_ch341_controller
        if controller is None:
            return True

        # Always send OUT 0. The cached flag starts as False after connecting
        # and cannot prove that the supply was already off before connection.
        while True:
            try:
                controller.set_output_enabled(False)
                break
            except Exception as exc:
                action = self._ask_tdk_shutdown_failure_action(exc)
                if action == "retry":
                    continue
                if action == "cancel":
                    event.ignore()
                    return False

                self.add_log("TDK 未确认输出关闭；操作者选择强制退出，请检查电源面板")
                break

        # Once OUT 0 succeeded (or the operator explicitly accepted the
        # unknown hardware state), a serial-close failure must not trap the UI.
        try:
            controller.disconnect_device()
        except Exception as exc:
            self.add_log(f"退出时释放 TDK 串口失败：{user_facing_error_message(exc)}")
        self.manual_ch341_controller = None
        return True

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
            self.background_stop_timeout_timer.start(round(BACKGROUND_STOP_TIMEOUT_S * 1000.0))
            self.statusBar().showMessage("正在安全停止后台采集，请稍候…")
            event.ignore()
            return
        self.background_stop_timeout_timer.stop()
        self.close_after_background_tasks = False
        if self.manual_ch341_controller is not None:
            if self.power_supply_controller_kind == "tdk":
                if not self._shutdown_tdk_for_close(event):
                    return
            else:
                try:
                    self.manual_ch341_controller.disconnect_device()
                except Exception:
                    pass
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    lock_directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    instance_lock = QLockFile(
        str(Path(lock_directory or Path.home()) / "changguang_huaxin_pump_driver_test.lock")
    )
    instance_lock.setStaleLockTime(30_000)
    if not instance_lock.tryLock(100):
        QMessageBox.warning(None, "程序已在运行", "检测到测试程序已经启动，请使用现有窗口。")
        return 0
    apply_application_theme(app)
    window = MainWindow()
    window.show()
    QTimer.singleShot(0, window.auto_detect_selected_power_meter)
    try:
        return app.exec()
    finally:
        instance_lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
