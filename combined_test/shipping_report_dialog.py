"""Qt configuration dialog for Excel-backed shipping reports."""

from __future__ import annotations

import base64
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .shipping_report import (
    POLE_FIELD_DEFINITIONS,
    ReportFieldDefinition,
    SPECTRUM_FIELD_DEFINITIONS,
    SelectedReportField,
    ShippingReportRequest,
    ShippingReportType,
    SpectrumAxisMode,
    SpectrumAxisSettings,
    WorkbookInspection,
    load_shipping_report_preferences,
    render_shipping_report_preview,
    suggested_field_values,
    validate_shipping_report_request,
)


class ShippingReportConfigurationDialog(QDialog):
    def __init__(self, inspection: WorkbookInspection, settings: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.inspection = inspection
        self.settings = settings
        self.preferences = load_shipping_report_preferences(settings)
        self.request: ShippingReportRequest | None = None
        self.field_widgets: dict[str, tuple[QCheckBox, QLineEdit]] = {}
        self.custom_field_widgets: list[tuple[str, QCheckBox, QLineEdit, QLineEdit, QLineEdit, QPushButton]] = []
        self._preview_pixmaps: list[QPixmap] = []
        self.setWindowTitle("生成出货报告")
        self.resize(1120, 740)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        layout.addWidget(splitter, stretch=1)

        editor = QWidget(splitter)
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(12)
        splitter.addWidget(editor)

        source_label = QLabel(f"数据文件：{inspection.source_path}", editor)
        source_label.setWordWrap(True)
        source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        editor_layout.addWidget(source_label)

        # Legacy workbooks cannot expose their completion state. Treat the
        # compatibility acknowledgement as accepted by default without adding
        # an extra confirmation control to the report workflow.
        self.legacy_confirmation = QCheckBox(editor)
        self.legacy_confirmation.setChecked(True)
        self.legacy_confirmation.hide()

        basics = QGroupBox("报告信息", editor)
        form = QFormLayout(basics)
        self.report_type_combo = QComboBox(basics)
        for report_type in inspection.allowed_report_types:
            label = "含光谱测试报告" if report_type == ShippingReportType.SPECTRUM.value else "Pole 无光谱测试报告"
            self.report_type_combo.addItem(label, report_type)
        self.product_name_edit = QLineEdit(inspection.product_name, basics)
        self.product_name_edit.setPlaceholderText("请输入产品名称")
        self.sn_edit = QLineEdit(inspection.sn, basics)
        self.sn_edit.setPlaceholderText("请输入 SN")
        self.current_combo = QComboBox(basics)
        for point in inspection.points:
            self.current_combo.addItem(f"{point.current_a:g} A", point.current_a)
        if self.current_combo.count():
            self.current_combo.setCurrentIndex(self.current_combo.count() - 1)
        form.addRow("报告类型", self.report_type_combo)
        form.addRow("产品名称", self.product_name_edit)
        form.addRow("SN", self.sn_edit)
        form.addRow("工作电流点", self.current_combo)
        editor_layout.addWidget(basics)

        self.fields_group = QGroupBox("报告参数", editor)
        self.fields_layout = QGridLayout(self.fields_group)
        self.fields_layout.setColumnStretch(1, 1)
        scroll = QScrollArea(editor)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.fields_group)
        editor_layout.addWidget(scroll, stretch=1)

        self.axis_group = QGroupBox("光谱纵轴", editor)
        axis_form = QFormLayout(self.axis_group)
        self.axis_mode_combo = QComboBox(self.axis_group)
        self.axis_mode_combo.addItem("原始强度 (counts)", SpectrumAxisMode.COUNTS.value)
        self.axis_mode_combo.addItem("相对强度 (dB)", SpectrumAxisMode.RELATIVE_DB.value)
        self.axis_minimum_spin = QDoubleSpinBox(self.axis_group)
        self.axis_maximum_spin = QDoubleSpinBox(self.axis_group)
        for spin in (self.axis_minimum_spin, self.axis_maximum_spin):
            spin.setRange(-1_000_000_000.0, 1_000_000_000.0)
            spin.setDecimals(3)
        axis_form.addRow("单位", self.axis_mode_combo)
        axis_form.addRow("下限", self.axis_minimum_spin)
        axis_form.addRow("上限", self.axis_maximum_spin)
        editor_layout.addWidget(self.axis_group)

        preview_panel = QWidget(splitter)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(8)
        preview_title = QLabel("实时预览", preview_panel)
        preview_title.setStyleSheet("font-weight: 600;")
        preview_layout.addWidget(preview_title)
        self.preview_status_label = QLabel("填写完整后将自动生成预览", preview_panel)
        self.preview_status_label.setWordWrap(True)
        self.preview_status_label.setStyleSheet("color: #68717a;")
        preview_layout.addWidget(self.preview_status_label)
        self.preview_scroll = QScrollArea(preview_panel)
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_pages_widget = QWidget(self.preview_scroll)
        self.preview_pages_layout = QVBoxLayout(self.preview_pages_widget)
        self.preview_pages_layout.setContentsMargins(10, 10, 10, 10)
        self.preview_pages_layout.setSpacing(14)
        self.preview_pages_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.preview_pages_widget.setStyleSheet("background: #eef1f3;")
        self.preview_scroll.setWidget(self.preview_pages_widget)
        preview_layout.addWidget(self.preview_scroll, stretch=1)
        splitter.addWidget(preview_panel)
        splitter.setSizes([650, 420])

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("选择位置并生成")
        buttons.accepted.connect(self._accept_configuration)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(500)
        self.preview_timer.timeout.connect(self._refresh_preview)
        self.report_type_combo.currentIndexChanged.connect(self._rebuild_fields)
        self.current_combo.currentIndexChanged.connect(self._refresh_measured_values)
        self.product_name_edit.textChanged.connect(self._schedule_preview)
        self.sn_edit.textChanged.connect(self._schedule_preview)
        self.legacy_confirmation.toggled.connect(self._schedule_preview)
        self.axis_mode_combo.currentIndexChanged.connect(self._schedule_preview)
        self.axis_minimum_spin.valueChanged.connect(self._schedule_preview)
        self.axis_maximum_spin.valueChanged.connect(self._schedule_preview)
        self._rebuild_fields()

    def _report_type(self) -> ShippingReportType:
        return ShippingReportType(str(self.report_type_combo.currentData()))

    def _definitions(self):
        return SPECTRUM_FIELD_DEFINITIONS if self._report_type() is ShippingReportType.SPECTRUM else POLE_FIELD_DEFINITIONS

    def _clear_fields(self) -> None:
        while self.fields_layout.count():
            item = self.fields_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.field_widgets.clear()
        self.custom_field_widgets.clear()

    def _rebuild_fields(self, *_args: Any) -> None:
        self._clear_fields()
        report_type = self._report_type()
        preference = self.preferences.get(report_type.value, {})
        selected_keys = set(preference.get("selectedFields", []))
        manual_values = preference.get("manualValues", {})
        suggestions = suggested_field_values(
            self.inspection,
            report_type,
            float(self.current_combo.currentData()),
        )
        self.fields_layout.addWidget(QLabel("显示", self.fields_group), 0, 0)
        self.fields_layout.addWidget(QLabel("参数值", self.fields_group), 0, 1)
        self.fields_layout.addWidget(QLabel("单位", self.fields_group), 0, 2)
        for row, definition in enumerate(self._definitions(), start=1):
            checkbox = QCheckBox(definition.label, self.fields_group)
            checkbox.setChecked(definition.key in selected_keys)
            edit = QLineEdit(self.fields_group)
            edit.setText(suggestions.get(definition.key, str(manual_values.get(definition.key, ""))))
            unit = QLabel(definition.unit or "-", self.fields_group)
            self.fields_layout.addWidget(checkbox, row, 0)
            self.fields_layout.addWidget(edit, row, 1)
            self.fields_layout.addWidget(unit, row, 2)
            self.field_widgets[definition.key] = (checkbox, edit)
            checkbox.toggled.connect(self._schedule_preview)
            edit.textChanged.connect(self._schedule_preview)
        custom_fields = preference.get("customFields", [])
        if isinstance(custom_fields, list):
            for item in custom_fields:
                if isinstance(item, dict) and str(item.get("key", "")).strip() and str(item.get("label", "")).strip():
                    self._add_custom_field_row(
                        key=str(item["key"]),
                        label=str(item["label"]),
                        unit=str(item.get("unit", "")),
                        value=str(manual_values.get(str(item["key"]), "")),
                        include=str(item["key"]) in selected_keys,
                    )
        add_button = QPushButton("手动增加参数", self.fields_group)
        add_button.clicked.connect(self._add_custom_field)
        self.fields_layout.addWidget(add_button, self.fields_layout.rowCount(), 0, 1, 3)
        self.axis_group.setVisible(report_type is ShippingReportType.SPECTRUM)
        if report_type is ShippingReportType.SPECTRUM:
            axis = preference.get("spectrumAxis", {})
            mode = str(axis.get("mode", SpectrumAxisMode.RELATIVE_DB.value))
            index = self.axis_mode_combo.findData(mode)
            self.axis_mode_combo.setCurrentIndex(max(0, index))
            self.axis_minimum_spin.setValue(float(axis.get("minimum", -80.0)))
            self.axis_maximum_spin.setValue(float(axis.get("maximum", 0.0)))
        self._schedule_preview()

    def _add_custom_field_row(self, *, key: str, label: str, unit: str, value: str, include: bool) -> None:
        row = self.fields_layout.rowCount()
        checkbox = QCheckBox(self.fields_group)
        checkbox.setChecked(include)
        label_edit = QLineEdit(label, self.fields_group)
        label_edit.setPlaceholderText("参数名称")
        value_edit = QLineEdit(value, self.fields_group)
        value_edit.setPlaceholderText("参数值")
        unit_edit = QLineEdit(unit, self.fields_group)
        unit_edit.setPlaceholderText("单位")
        remove_button = QPushButton("删除", self.fields_group)
        remove_button.clicked.connect(lambda: self._remove_custom_field(key))
        self.fields_layout.addWidget(checkbox, row, 0)
        self.fields_layout.addWidget(label_edit, row, 1)
        self.fields_layout.addWidget(value_edit, row, 2)
        self.fields_layout.addWidget(unit_edit, row, 3)
        self.fields_layout.addWidget(remove_button, row, 4)
        self.custom_field_widgets.append((key, checkbox, label_edit, value_edit, unit_edit, remove_button))
        checkbox.toggled.connect(self._schedule_preview)
        label_edit.textChanged.connect(self._schedule_preview)
        value_edit.textChanged.connect(self._schedule_preview)
        unit_edit.textChanged.connect(self._schedule_preview)

    def _add_custom_field(self) -> None:
        key = f"custom-{len(self.custom_field_widgets) + 1}-{id(self)}"
        self._add_custom_field_row(key=key, label="新参数", unit="", value="", include=True)
        self._schedule_preview()

    def _remove_custom_field(self, key: str) -> None:
        remaining = []
        for item in self.custom_field_widgets:
            if item[0] != key:
                remaining.append(item)
                continue
            for widget in item[1:]:
                widget.deleteLater()
        self.custom_field_widgets = remaining
        self._schedule_preview()

    def _refresh_measured_values(self, *_args: Any) -> None:
        if not self.field_widgets:
            return
        suggestions = suggested_field_values(
            self.inspection,
            self._report_type(),
            float(self.current_combo.currentData()),
        )
        measured_keys = {item.key for item in self._definitions() if item.measured_key is not None}
        for key in measured_keys:
            if key in self.field_widgets:
                self.field_widgets[key][1].setText(suggestions.get(key, ""))
        self._schedule_preview()

    def _schedule_preview(self, *_args: Any) -> None:
        self.preview_status_label.setText("正在更新预览…")
        self.preview_timer.start()

    def _refresh_preview(self) -> None:
        try:
            request = self._build_request()
            validate_shipping_report_request(self.inspection, request)
            pages = render_shipping_report_preview(self.inspection.source_path, request)
            pixmaps: list[QPixmap] = []
            for encoded_page in pages:
                pixmap = QPixmap()
                if not pixmap.loadFromData(base64.b64decode(encoded_page), "PNG"):
                    raise RuntimeError("无法读取出货报告预览页面")
                pixmaps.append(pixmap)
            if not pixmaps:
                raise RuntimeError("出货报告预览没有页面")
            self._preview_pixmaps = pixmaps
            self._show_preview_pages()
            self.preview_status_label.setText(f"已更新，共 {len(pixmaps)} 页；与导出的 PDF 一致")
        except ValueError as exc:
            self._show_preview_issue(str(exc))
        except Exception as exc:
            self._show_preview_issue(f"预览生成失败：{exc}")

    def _show_preview_issue(self, message: str) -> None:
        suffix = "，当前显示上一次预览" if self._preview_pixmaps else ""
        self.preview_status_label.setText(f"当前输入暂无法预览：{message}{suffix}")
        if not self._preview_pixmaps:
            self._show_preview_pages()

    def _show_preview_pages(self) -> None:
        while self.preview_pages_layout.count():
            item = self.preview_pages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._preview_pixmaps:
            placeholder = QLabel("报告预览将在配置有效后显示", self.preview_pages_widget)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #68717a;")
            self.preview_pages_layout.addWidget(placeholder)
            return
        page_width = max(220, self.preview_scroll.viewport().width() - 28)
        for index, pixmap in enumerate(self._preview_pixmaps, start=1):
            page = QLabel(self.preview_pages_widget)
            page.setAlignment(Qt.AlignmentFlag.AlignCenter)
            page.setPixmap(pixmap.scaledToWidth(page_width, Qt.TransformationMode.SmoothTransformation))
            page.setStyleSheet("background: white; border: 1px solid #c8cdd2;")
            self.preview_pages_layout.addWidget(page)
            caption = QLabel(f"第 {index} 页", self.preview_pages_widget)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setStyleSheet("color: #68717a;")
            self.preview_pages_layout.addWidget(caption)

    def _build_request(self) -> ShippingReportRequest:
        report_type = self._report_type()
        fields = {
            key: SelectedReportField(checkbox.isChecked(), edit.text().strip())
            for key, (checkbox, edit) in self.field_widgets.items()
        }
        custom_fields = []
        for key, checkbox, label_edit, value_edit, unit_edit, _remove_button in self.custom_field_widgets:
            custom_fields.append(ReportFieldDefinition(key, label_edit.text().strip(), unit_edit.text().strip(), "left" if report_type is ShippingReportType.SPECTRUM else "single"))
            fields[key] = SelectedReportField(checkbox.isChecked(), value_edit.text().strip())
        spectrum_axis = None
        if report_type is ShippingReportType.SPECTRUM:
            spectrum_axis = SpectrumAxisSettings(
                mode=SpectrumAxisMode(str(self.axis_mode_combo.currentData())),
                minimum=self.axis_minimum_spin.value(),
                maximum=self.axis_maximum_spin.value(),
            )
        return ShippingReportRequest(
            report_type=report_type,
            product_name=self.product_name_edit.text().strip(),
            sn=self.sn_edit.text().strip(),
            operating_current_a=float(self.current_combo.currentData()),
            fields=fields,
            legacy_completion_confirmed=self.legacy_confirmation.isChecked(),
            custom_fields=tuple(custom_fields),
            spectrum_axis=spectrum_axis,
        )

    def _accept_configuration(self) -> None:
        request = self._build_request()
        try:
            validate_shipping_report_request(self.inspection, request)
        except ValueError as exc:
            QMessageBox.warning(self, "生成出货报告", str(exc))
            return
        self.request = request
        self.accept()
