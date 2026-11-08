use super::*;
impl Workbench {
    pub(super) fn active(&self) -> bool {
        self.demo
            || self.license["valid"] == true && self.cache.value["backend"]["mode"] == "active"
    }
    pub(super) fn manual(&self) -> bool {
        self.active() && self.cache.value["safety"]["manualControlsAllowed"] == true
    }
    pub(super) fn panel(&self, title: &str, body: impl IntoElement, cx: &App) -> AnyElement {
        div()
            .flex()
            .flex_col()
            .gap_3()
            .p_4()
            .w_full()
            .min_w_0()
            .border_1()
            .border_color(cx.theme().border)
            .rounded_lg()
            .bg(cx.theme().background)
            .child(
                div()
                    .font_weight(FontWeight::MEDIUM)
                    .child(title.to_string()),
            )
            .child(body)
            .into_any_element()
    }
    pub(super) fn metric(&self, label: &str, value: String, unit: &str, cx: &App) -> AnyElement {
        div()
            .flex()
            .flex_col()
            .gap_1()
            .flex_1()
            .min_w_0()
            .child(
                div()
                    .text_sm()
                    .text_color(cx.theme().muted_foreground)
                    .child(label.to_string()),
            )
            .child(div().text_2xl().child(format!("{value} {unit}")))
            .into_any_element()
    }
    pub(super) fn device_metric(&self, device: &str, key: &str, digits: usize) -> String {
        let d = &self.cache.value["devices"][device];
        if d["quality"] != "live" || d["saturated"] == true {
            return "—".into();
        }
        d[key]
            .as_f64()
            .or_else(|| {
                if key == "pibPercent" {
                    d["pib"].as_f64().map(|x| x * 100.)
                } else {
                    None
                }
            })
            .map(|v| format!("{v:.digits$}"))
            .unwrap_or("—".into())
    }
    pub(super) fn device_cards(&mut self, cx: &mut Context<Self>) -> AnyElement {
        let mut row = div().flex().gap_4();
        for (device, title) in [
            ("powerSupply", "电源"),
            ("powerMeter", "功率计"),
            ("spectrometer", "光谱仪"),
        ] {
            let d = &self.cache.value["devices"][device];
            let state = d["state"].as_str().unwrap_or("disconnected");
            let status = match state {
                "connected" => "连接",
                "connecting" => "连接中",
                "error" => "连接失败",
                _ => "未连接",
            };
            let detail = d["detail"].as_str().unwrap_or("").to_string();
            let setting = match device {
                "powerSupply" => "supply",
                "powerMeter" => "meter",
                _ => "spectrum",
            };
            let mut body = div()
                .flex()
                .flex_col()
                .gap_2()
                .h(px(152.))
                .child(div().flex().justify_between().child(title).child(status))
                .child(
                    div()
                        .text_xs()
                        .h(px(36.))
                        .overflow_hidden()
                        .text_color(cx.theme().muted_foreground)
                        .child(detail),
                );
            let controls = if device == "powerSupply" {
                div()
                    .flex()
                    .gap_2()
                    .child(self.button(
                        "supply-connect",
                        "连接",
                        self.manual(),
                        Action::Save(Some("powerSupply.connect")),
                        cx,
                    ))
                    .child(self.button(
                        "supply-disconnect",
                        "断开",
                        self.manual(),
                        Action::Command("powerSupply.disconnect", json!({})),
                        cx,
                    ))
            } else {
                let (method, label) = if device == "powerMeter" {
                    if d["running"] == true {
                        ("powerMeter.stop", "停止采集")
                    } else {
                        ("powerMeter.start", "开始采集")
                    }
                } else if d["running"] == true {
                    ("spectrometer.stop", "停止采集")
                } else {
                    ("spectrometer.start", "开始采集")
                };
                div().child(self.button(
                    format!("start-{device}"),
                    label,
                    self.manual(),
                    if method.ends_with(".stop") {
                        Action::Command(method, json!({}))
                    } else {
                        Action::Save(Some(method))
                    },
                    cx,
                ))
            };
            let reading = if device == "powerSupply" {
                if d["state"] == "connected" {
                    format!(
                        "{} A · {} V",
                        text(&d["outputCurrentA"]),
                        text(&d["outputVoltageV"])
                    )
                } else {
                    "— A · — V".into()
                }
            } else if device == "powerMeter" {
                format!("{} W", self.device_metric("powerMeter", "powerW", 4))
            } else {
                format!(
                    "{} nm",
                    self.device_metric("spectrometer", "peakWavelengthNm", 3)
                )
            };
            body = body.child(div().text_lg().child(reading));
            body = body.child(div().flex_1()).child(
                div()
                    .flex()
                    .justify_between()
                    .child(controls)
                    .child(self.button(
                        format!("settings-{device}"),
                        "设置",
                        self.manual(),
                        Action::Dialog(Some(setting)),
                        cx,
                    )),
            );
            row = row.child(
                div()
                    .flex_1()
                    .min_w_0()
                    .p_4()
                    .border_1()
                    .border_color(cx.theme().border)
                    .rounded_lg()
                    .bg(cx.theme().background)
                    .child(body),
            );
        }
        row.into_any_element()
    }
    pub(super) fn realtime(&mut self, cx: &mut Context<Self>) -> AnyElement {
        let m = &self.cache.value["measurements"];
        let power = charts::chart(
            "功率曲线",
            "时间 / s",
            "功率 / W",
            vec![(
                "功率".into(),
                0xf59e0b,
                charts::rows(&m["power"], "elapsedS", "powerW"),
            )],
            cx,
        );
        let spectrum = charts::chart(
            "光谱",
            "波长 / nm",
            "强度 / counts",
            vec![("光谱".into(), 0x60a5fa, charts::spectrum(&m["spectrum"]))],
            cx,
        );
        let liv = charts::chart(
            "LIV",
            "电流 / A",
            "功率 / W",
            vec![(
                "功率".into(),
                0xf59e0b,
                charts::rows(&m["stable"], "currentA", "powerW"),
            )],
            cx,
        );
        let metrics = div()
            .flex()
            .gap_4()
            .child(self.metric(
                "功率",
                self.device_metric("powerMeter", "powerW", 4),
                "W",
                cx,
            ))
            .child(self.metric(
                "中心波长",
                self.device_metric("spectrometer", "centroidNm", 3),
                "nm",
                cx,
            ))
            .child(self.metric(
                "FWHM",
                self.device_metric("spectrometer", "fwhmNm", 3),
                "nm",
                cx,
            ))
            .child(self.metric(
                "PIB",
                self.device_metric("spectrometer", "pibPercent", 2),
                "%",
                cx,
            ))
            .child(self.metric(
                "SMSR",
                self.device_metric("spectrometer", "smsrDb", 2),
                "dB",
                cx,
            ));
        div()
            .flex()
            .flex_col()
            .gap_4()
            .child(self.panel("实时测量", metrics, cx))
            .child(
                div()
                    .flex()
                    .gap_4()
                    .child(div().flex_1().min_w_0().child(power))
                    .child(div().flex_1().min_w_0().child(spectrum)),
            )
            .child(liv)
            .into_any_element()
    }
    pub(super) fn automatic_page(
        &mut self,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let cfg = self.cache.value["configuration"].clone();
        let form = div()
            .flex()
            .gap_3()
            .child(self.field("cfg.sn".into(), "SN", cfg["sn"].clone(), window, cx))
            .child(self.field(
                "cfg.station".into(),
                "站别",
                cfg["station"].clone(),
                window,
                cx,
            ))
            .child(self.field(
                "cfg.selectedScriptId".into(),
                "测试脚本",
                cfg["selectedScriptId"].clone(),
                window,
                cx,
            ));
        let a = self.cache.value["automaticTest"].clone();
        let mut controls = div()
            .flex()
            .gap_2()
            .flex_wrap()
            .child(
                self.button(
                    "auto-start",
                    "开始测试",
                    self.active() && a["canStart"] == true,
                    Action::Save(Some("automatic.start")),
                    cx,
                )
                .primary(),
            )
            .child(self.button(
                "auto-pause",
                "暂停",
                self.active() && a["canPause"] == true,
                Action::Command("automatic.pause", json!({})),
                cx,
            ))
            .child(self.button(
                "auto-end",
                "结束并安全下电",
                self.cache.value.is_object() && a["canEnd"] == true,
                Action::Command("automatic.end", json!({})),
                cx,
            ))
            .child(self.button(
                "auto-next",
                "下一台",
                self.active() && a["canNextDevice"] == true,
                Action::Command("automatic.nextDevice", json!({})),
                cx,
            ))
            .child(self.button(
                "auto-export",
                "重试 Excel",
                self.active() && a["canRetryExport"] == true,
                Action::Command("automatic.retryExport", json!({})),
                cx,
            ))
            .child(self.button(
                "auto-config",
                "测试设置",
                self.active() && a["controlsEnabled"] == true,
                Action::Dialog(Some("test")),
                cx,
            ));
        if let Some(actions) = a["pauseActions"].as_array() {
            for action in actions {
                let (method, label) = match action.as_str().unwrap_or("") {
                    "resume" => ("automatic.resume", "继续测试"),
                    "retry_point" => ("automatic.retryPoint", "重试当前点"),
                    "recover_power_meter" => ("automatic.recoverPowerMeter", "恢复功率计"),
                    "recover_spectrometer" => ("automatic.recoverSpectrometer", "恢复光谱仪"),
                    "adjust_spectrum" => ("automatic.adjustSpectrum", "应用光谱参数"),
                    "retry_persist" => ("automatic.retryPersist", "重试本地记录"),
                    _ => ("automatic.end", "结束并安全下电"),
                };
                controls=controls.child(self.button(format!("pause-{method}"),label,self.active(),Action::Command(method,json!({"integrationTimeUs":self.value("recovery.integrationTimeUs",cfg["integrationTimeUs"].clone()),"autoIntegration":self.value("recovery.autoIntegration",cfg["autoIntegration"].clone())})),cx));
            }
        }
        let mut status = div()
            .flex()
            .flex_col()
            .gap_2()
            .child(a["detail"].as_str().unwrap_or("等待后端连接").to_string());
        if a["state"] == "paused" {
            status = status
                .child(self.field(
                    "recovery.integrationTimeUs".into(),
                    "恢复采集的积分时间 / μs",
                    cfg["integrationTimeUs"].clone(),
                    window,
                    cx,
                ))
                .child(self.field(
                    "recovery.autoIntegration".into(),
                    "恢复采集的自动积分",
                    cfg["autoIntegration"].clone(),
                    window,
                    cx,
                ));
            status = status.child(div().text_color(rgb(0xf59e0b)).child(format!(
                "暂停原因：{}　安全截止时间：{}",
                text(&a["pauseReason"]),
                text(&a["pauseDeadlineAtUtc"])
            )));
        }
        status = status.child(div().text_sm().child(format!(
            "计划电流 {} A · 运行时间 {} s · 当前停留 {}/{} s",
            text(&a["currentA"]),
            text(&a["elapsedS"]),
            text(&a["currentDwellElapsedS"]),
            text(&a["currentDwellS"])
        )));
        if a["stability"].is_object() {
            let stability = &a["stability"];
            status = status
                .child(div().text_sm().child(format!(
                    "判稳覆盖 {}/{} s · 保持 {}/{} s · 功率稳定：{} · 波长稳定：{}",
                    text(&stability["elapsedS"]),
                    text(&stability["requiredS"]),
                    text(&stability["holdElapsedS"]),
                    text(&stability["holdRequiredS"]),
                    if stability["powerStable"] == true {
                        "是"
                    } else {
                        "否"
                    },
                    if stability["wavelengthStable"] == true {
                        "是"
                    } else {
                        "否"
                    }
                )))
                .child(div().text_sm().child(format!(
                    "2σ {}/{} W · 漂移 {}/{} W",
                    text(&stability["twoSigmaW"]),
                    text(&stability["effectiveLimitW"]),
                    text(&stability["driftW"]),
                    text(&stability["driftLimitW"])
                )));
        }
        for blocker in a["readiness"]["blockers"].as_array().into_iter().flatten() {
            status = status.child(
                div()
                    .text_sm()
                    .text_color(cx.theme().muted_foreground)
                    .child(text(&blocker["message"])),
            );
        }
        status = status.child(
            div()
                .flex()
                .justify_between()
                .text_sm()
                .child(format!(
                    "持久记录 {} 点 · Excel {} 点",
                    text(&a["savedPointCount"]),
                    text(&a["exportedPointCount"])
                ))
                .child(format!(
                    "{}　{}",
                    status_label(&a["exportStatus"]),
                    text(&a["exportPath"])
                )),
        );
        if a["terminalOutcome"] == "shutdown_unconfirmed" {
            status = status.child(
                div()
                    .text_color(rgb(0xf87171))
                    .child("下电未确认，请核对电源物理面板"),
            );
        }
        let top = self.panel(
            "测试计划",
            div()
                .flex()
                .flex_col()
                .gap_3()
                .child(form)
                .child(controls)
                .child(status),
            cx,
        );
        div()
            .flex()
            .flex_col()
            .gap_4()
            .child(top)
            .child(self.device_cards(cx))
            .child(self.realtime(cx))
            .into_any_element()
    }
    pub(super) fn manual_page(
        &mut self,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let cfg = self.cache.value["configuration"].clone();
        let supply = self.cache.value["devices"]["powerSupply"].clone();
        let enabled = supply["outputEnabled"] == true;
        let mut form = div()
            .flex()
            .gap_3()
            .items_end()
            .child(self.field("cfg.sn".into(), "SN", cfg["sn"].clone(), window, cx))
            .child(self.field(
                "cfg.setCurrentA".into(),
                "设定电流 / A",
                cfg["setCurrentA"].clone(),
                window,
                cx,
            ))
            .child(
                self.button(
                    "manual-apply",
                    "设置电流",
                    self.manual(),
                    Action::Save(Some("powerSupply.setCurrent")),
                    cx,
                )
                .primary(),
            )
            .child(self.button(
                "manual-output",
                if enabled {
                    "关闭输出"
                } else {
                    "开启输出"
                },
                self.manual(),
                Action::Command("powerSupply.setOutput", json!({"enabled":!enabled})),
                cx,
            ))
            .child(self.button(
                "manual-stop",
                "安全下电",
                self.cache.value.is_object(),
                Action::Command("powerSupply.safeRampDown", json!({})),
                cx,
            ));
        form = form.child(
            self.button(
                "manual-save",
                "保存 Excel",
                self.manual()
                    && self.cache.value["manualTest"]["pendingCount"]
                        .as_u64()
                        .unwrap_or(0)
                        > 0,
                Action::Command("manual.saveExcel", json!({})),
                cx,
            ),
        );
        let pending = self.cache.value["manualTest"].clone();
        div()
            .flex()
            .flex_col()
            .gap_4()
            .child(self.panel(
                "手动测量",
                div().flex().flex_col().gap_3().child(form).child(format!(
                    "{} · 待保存 {} 点 · {}",
                    text(&pending["capture"]["detail"]),
                    text(&pending["pendingCount"]),
                    status_label(&pending["exportStatus"])
                )),
                cx,
            ))
            .child(self.device_cards(cx))
            .child(self.realtime(cx))
            .into_any_element()
    }
    pub(super) fn pd_page(&mut self, window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let pd = self.cache.value["pd"].clone();
        let mut form = div().flex().flex_col().gap_3();
        for (key, label) in fields::PD {
            form = form.child(self.field(
                format!("pd.{key}"),
                label,
                pd["settings"][key].clone(),
                window,
                cx,
            ));
        }
        let mut p = pd["settings"].clone();
        for (k, v) in &self.draft {
            if let Some(key) = k.strip_prefix("pd.") {
                p[key] = v.clone();
            }
        }
        let controls = div()
            .flex()
            .gap_2()
            .child(self.button(
                "pd-refresh",
                "识别",
                self.active() && pd["state"] != "running",
                Action::Command("pd.refresh", json!({})),
                cx,
            ))
            .child(
                self.button(
                    "pd-start",
                    "开始采集",
                    self.active() && pd["state"] != "running",
                    Action::Command("pd.start", p.clone()),
                    cx,
                )
                .primary(),
            )
            .child(self.button(
                "pd-stop",
                "停止采集",
                self.active() && pd["state"] == "running",
                Action::Command("pd.stop", json!({})),
                cx,
            ))
            .child(self.button(
                "pd-save",
                "保存设置",
                self.active() && pd["state"] != "running",
                Action::Command("pd.configure", p),
                cx,
            ));
        let chart = charts::chart(
            "PD 信号",
            "时间 / s",
            pd["settings"]["unit"].as_str().unwrap_or("V"),
            vec![(
                "信号".into(),
                0x60a5fa,
                charts::rows(&pd["points"], "elapsedS", "value"),
            )],
            cx,
        );
        div()
            .flex()
            .gap_4()
            .child(
                div()
                    .w(px(300.))
                    .flex_shrink_0()
                    .child(self.panel("采集设置", form, cx)),
            )
            .child(
                div()
                    .flex_1()
                    .flex()
                    .flex_col()
                    .gap_4()
                    .child(controls)
                    .child(text(&pd["status"]))
                    .child(self.metric(
                        "当前值",
                        text(&pd["currentValue"]),
                        pd["settings"]["unit"].as_str().unwrap_or("V"),
                        cx,
                    ))
                    .child(chart)
                    .child(format!(
                        "样本数 {} · 均值 {} · 标准差 {}",
                        text(&pd["sampleCount"]),
                        text(&pd["mean"]),
                        text(&pd["standardDeviation"])
                    )),
            )
            .into_any_element()
    }
    pub(super) fn data_page(&mut self, window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let mut filters = div().flex().gap_3().items_end();
        for (key, label) in [("search", "搜索"), ("sn", "SN"), ("station", "站别")] {
            filters =
                filters.child(self.field(format!("data.{key}"), label, json!(""), window, cx));
        }
        filters = filters
            .child(self.button("data-query", "查询", self.active(), Action::DataLoad, cx))
            .child(self.button(
                "data-dir",
                "选择 Excel 目录",
                self.active(),
                Action::Pick("data.rootPath".into(), true),
                cx,
            ));
        let mut table = div()
            .flex()
            .flex_col()
            .gap_2()
            .child(div().text_sm().child(format!(
                "共 {} 条 · 第 {} 页",
                text(&self.data["total"]),
                self.data_page
            )));
        let rows = self.data["items"]
            .as_array()
            .or(self.data["records"].as_array())
            .cloned()
            .unwrap_or_default();
        if rows.is_empty() {
            table = table.child(
                div()
                    .p_4()
                    .text_color(cx.theme().muted_foreground)
                    .child("暂无记录。可以选择 Excel 目录，或查询本地记录库。"),
            );
        }
        for (i, row) in rows.iter().enumerate() {
            table = table.child(
                div()
                    .flex()
                    .gap_4()
                    .items_center()
                    .border_b_1()
                    .border_color(cx.theme().border)
                    .py_2()
                    .child(div().w(px(200.)).child(text(&row["sn"])))
                    .child(div().w(px(140.)).child(text(&row["station"])))
                    .child(
                        div().flex_1().child(text(
                            row.get("sourcePath")
                                .or(row.get("path"))
                                .unwrap_or(&Value::Null),
                        )),
                    )
                    .child(self.button(
                        format!("inspect-{i}"),
                        "查看",
                        self.active(),
                        Action::DataInspect(row.clone()),
                        cx,
                    ))
                    .child(self.button(
                        format!("compare-{i}"),
                        "对比",
                        self.active(),
                        Action::Compare(row.clone()),
                        cx,
                    )),
            );
        }
        let mut content = div()
            .flex()
            .flex_col()
            .gap_4()
            .child(filters)
            .child(table)
            .child(
                div()
                    .flex()
                    .gap_2()
                    .child(self.button(
                        "data-previous",
                        "上一页",
                        self.data_page > 1,
                        Action::PageData(-1),
                        cx,
                    ))
                    .child(self.button(
                        "data-next",
                        "下一页",
                        self.data_page < self.data["pageCount"].as_u64().unwrap_or(1),
                        Action::PageData(1),
                        cx,
                    )),
            );
        let root = self.value("data.rootPath", json!(""));
        content = content.child(self.button(
            "data-import",
            "将所选 Excel 目录导入记录库",
            self.active() && root.as_str().is_some_and(|s| !s.is_empty()),
            Action::Command("dataBrowser.import", json!({"rootPath":root})),
            cx,
        ));
        if !self.detail.is_null() {
            content = content.child(self.record_detail(cx));
        }
        content.into_any_element()
    }
    pub(super) fn record_detail(&self, cx: &App) -> AnyElement {
        let mut body = div().flex().flex_col().gap_2();
        let detail = &self.detail;
        let points = detail["points"]
            .as_array()
            .or(detail["liv"].as_array())
            .cloned()
            .unwrap_or_default();
        body = body.child(format!(
            "SN {} · {} 个电流点",
            text(&detail["record"]["sn"]),
            points.len()
        ));
        for p in &points {
            body = body.child(div().text_sm().child(format!(
                "{} A　{} V　{} W　{} %",
                text(&p["currentA"]),
                text(&p["voltageV"]),
                text(&p["powerW"]),
                text(&p["efficiencyPercent"])
            )));
        }
        body = body.child(charts::chart(
            "历史 LIV",
            "电流 / A",
            "功率 / W",
            vec![(
                "功率".into(),
                0xf59e0b,
                charts::rows(&json!(points), "currentA", "powerW"),
            )],
            cx,
        ));
        let colors = [0x60a5fa, 0xf59e0b, 0x34d399, 0xa78bfa];
        let mut spectra = vec![];
        for (i, spectrum) in detail["spectra"]
            .as_array()
            .into_iter()
            .flatten()
            .enumerate()
        {
            spectra.push((
                format!("{} A", text(&spectrum["currentA"])),
                colors[i % colors.len()],
                charts::spectrum(spectrum),
            ));
        }
        if !spectra.is_empty() {
            body = body.child(charts::chart(
                "历史光谱",
                "波长 / nm",
                "强度 / counts",
                spectra,
                cx,
            ));
        }
        if !self.comparison.is_null() {
            body = body.child(charts::chart(
                "LIV 对比",
                "电流 / A",
                "功率 / W",
                vec![
                    (
                        "当前记录".into(),
                        0xf59e0b,
                        charts::rows(&detail["points"], "currentA", "powerW"),
                    ),
                    (
                        "对比记录".into(),
                        0x60a5fa,
                        charts::rows(&self.comparison["points"], "currentA", "powerW"),
                    ),
                ],
                cx,
            ));
        }
        for warning in detail["warnings"].as_array().into_iter().flatten() {
            body = body.child(div().text_color(rgb(0xf59e0b)).child(text(warning)));
        }
        for (k, v) in detail["metadata"].as_object().into_iter().flatten() {
            body = body.child(div().text_xs().child(format!("{k}：{}", text(v))));
        }
        self.panel("记录详情", body, cx)
    }
    pub(super) fn shipping_page(
        &mut self,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let chooser = div()
            .flex()
            .gap_3()
            .items_end()
            .child(self.field(
                "report.sourcePath".into(),
                "测试 Excel 文件",
                json!(""),
                window,
                cx,
            ))
            .child(self.button(
                "report-pick",
                "选择文件",
                self.active(),
                Action::Pick("report.sourcePath".into(), false),
                cx,
            ))
            .child(self.button(
                "report-inspect",
                "读取",
                self.active(),
                Action::ReportInspect,
                cx,
            ));
        let mut body = div().flex().flex_col().gap_4().child(chooser);
        body = body.child(
            div()
                .flex()
                .gap_2()
                .child(self.button(
                    "report-batch-root",
                    "选择批量 Excel 根目录",
                    self.active(),
                    Action::Pick("report.batchRoot".into(), true),
                    cx,
                ))
                .child(self.button(
                    "report-batch-output",
                    "选择 PDF 输出目录",
                    self.active(),
                    Action::Pick("report.outputDirectory".into(), true),
                    cx,
                ))
                .child(self.button(
                    "report-open-output",
                    "打开输出目录",
                    true,
                    Action::OpenOutput,
                    cx,
                ))
                .child(self.button(
                    "report-batch-generate",
                    "批量生成 PDF",
                    self.active() && !self.report.is_null() && !self.batch.is_null(),
                    Action::BatchReports,
                    cx,
                )),
        );
        for (i, item) in self.batch["items"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .iter()
            .enumerate()
        {
            body = body.child(
                div()
                    .flex()
                    .gap_2()
                    .items_center()
                    .child(div().flex_1().child(format!(
                        "{} · {} · {}",
                        text(&item["sn"]),
                        text(&item["status"]),
                        text(&item["message"])
                    )))
                    .child(self.button(
                        format!("batch-open-{i}"),
                        "设置与预览",
                        item["status"] == "ready",
                        Action::Command(
                            "shippingReport.inspect",
                            json!({"path":item["sourcePath"]}),
                        ),
                        cx,
                    )),
            );
        }
        if self.report.is_null() {
            return body
                .child(div().p_4().child("选择测试 Excel 文件后设置报告字段"))
                .into_any_element();
        }
        let kind = self
            .value(
                "report.reportType",
                self.report["allowedReportTypes"]
                    .as_array()
                    .and_then(|a| a.first())
                    .cloned()
                    .unwrap_or(json!("spectrum")),
            )
            .as_str()
            .unwrap_or("spectrum")
            .to_string();
        let current = self
            .value(
                "report.operatingCurrentA",
                self.report["currents"]
                    .as_array()
                    .and_then(|a| a.last())
                    .cloned()
                    .unwrap_or(json!(0)),
            )
            .as_f64()
            .unwrap_or(0.);
        let signature = format!("{}:{kind}:{current}", text(&self.report["sourcePath"]));
        if signature != self.report_signature {
            let defaults = report::defaults(&self.report, &kind, current);
            self.draft.insert("report.reportType".into(), json!(kind));
            self.draft
                .insert("report.operatingCurrentA".into(), json!(current));
            for k in ["reportLanguage", "encircledEnergyNa", "customFields"] {
                self.draft
                    .entry(format!("report.{k}"))
                    .or_insert(defaults[k].clone());
            }
            for (k, v) in defaults["fields"].as_object().unwrap() {
                self.draft
                    .entry(format!("report.include.{k}"))
                    .or_insert(v["include"].clone());
                if self.report["fieldDefinitions"][&kind]
                    .as_array()
                    .is_some_and(|defs| {
                        defs.iter()
                            .any(|f| f["key"] == *k && f["measuredKey"].is_string())
                    })
                {
                    self.draft
                        .insert(format!("report.field.{k}"), v["value"].clone());
                    self.editors.remove(&format!("report.field.{k}"));
                } else {
                    self.draft
                        .entry(format!("report.field.{k}"))
                        .or_insert(v["value"].clone());
                }
            }
            for (k, v) in defaults["spectrumAxis"].as_object().unwrap() {
                self.draft
                    .entry(format!("report.axis.{k}"))
                    .or_insert(v.clone());
            }
            self.report_signature = signature;
        }
        let r = self.report.clone();
        let mut form = div().flex().flex_col().gap_3();
        for (key, label, default) in [
            ("reportType", "报告类型", json!("spectrum")),
            ("reportLanguage", "报告语言", json!("zh")),
            ("productName", "产品名称", r["productName"].clone()),
            ("sn", "SN", r["sn"].clone()),
            (
                "operatingCurrentA",
                "报告电流 / A",
                r["currents"]
                    .as_array()
                    .and_then(|a| a.last())
                    .cloned()
                    .unwrap_or(json!(0)),
            ),
            ("encircledEnergyNa", "包围能量 NA", json!("")),
            ("outputPath", "PDF 输出路径", json!("")),
            (
                "legacyCompletionConfirmed",
                "确认旧版记录完整性",
                json!(false),
            ),
        ] {
            form = form.child(self.field(format!("report.{key}"), label, default, window, cx));
        }
        let kind = self
            .value("report.reportType", json!("spectrum"))
            .as_str()
            .unwrap_or("spectrum")
            .to_string();
        let mut definitions = r["fieldDefinitions"][&kind]
            .as_array()
            .cloned()
            .unwrap_or_default();
        definitions.extend(
            self.value("report.customFields", json!([]))
                .as_array()
                .cloned()
                .unwrap_or_default(),
        );
        for f in definitions {
            let key = f["key"].as_str().unwrap_or("");
            let label = format!("{} {}", text(&f["label"]), text(&f["unit"]));
            let included = self.field(
                format!("report.include.{key}"),
                &format!("包含：{label}"),
                json!(false),
                window,
                cx,
            );
            if key.starts_with("custom_native_") {
                form = form
                    .child(self.field(
                        format!("report.customLabel.{key}"),
                        "参数名称",
                        f["label"].clone(),
                        window,
                        cx,
                    ))
                    .child(self.field(
                        format!("report.customUnit.{key}"),
                        "单位",
                        f["unit"].clone(),
                        window,
                        cx,
                    ));
            }
            let value = self.field(
                format!("report.field.{key}"),
                "参数值",
                json!(""),
                window,
                cx,
            );
            form = form.child(included).child(value);
        }
        if kind == "spectrum" {
            for (key, label, default) in [
                ("mode", "强度显示", json!("counts")),
                ("minimum", "强度下限", json!(0.)),
                ("maximum", "强度上限", json!(10000.)),
                ("wavelengthMinimum", "波长下限 / nm", json!(961.)),
                ("wavelengthMaximum", "波长上限 / nm", json!(991.)),
            ] {
                form = form.child(self.field(
                    format!("report.axis.{key}"),
                    label,
                    default,
                    window,
                    cx,
                ));
            }
        }
        form = form.child(self.button(
            "report-add-field",
            "增加报告参数",
            true,
            Action::AddReportField,
            cx,
        ));
        let actions = div()
            .flex()
            .gap_2()
            .child(self.button(
                "report-preview",
                "预览",
                self.active(),
                Action::Report("shippingReport.preview"),
                cx,
            ))
            .child(
                self.button(
                    "report-generate",
                    "生成 PDF",
                    self.active(),
                    Action::Report("shippingReport.generate"),
                    cx,
                )
                .primary(),
            );
        let mut preview = div().flex().flex_col().gap_4();
        for path in &self.previews {
            preview = preview.child(img(path.clone()).w_full());
        }
        body = body.child(
            div()
                .flex()
                .gap_4()
                .child(div().w(px(360.)).flex_shrink_0().child(form).child(actions))
                .child(div().flex_1().min_w_0().child(preview)),
        );
        body.into_any_element()
    }
    pub(super) fn settings_page(
        &mut self,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let general = self.config_fields(fields::GENERAL, window, cx);
        let safety = self.config_fields(fields::SAFETY, window, cx);
        div()
            .flex()
            .flex_col()
            .gap_4()
            .child(
                div()
                    .flex()
                    .gap_4()
                    .child(
                        div()
                            .flex_1()
                            .child(self.panel("默认测试设置", general, cx)),
                    )
                    .child(
                        div()
                            .flex_1()
                            .child(self.panel("规格与安全时序", safety, cx)),
                    ),
            )
            .child(
                div()
                    .flex()
                    .gap_2()
                    .child(
                        self.button(
                            "settings-save",
                            "保存设置",
                            self.manual(),
                            Action::Save(None),
                            cx,
                        )
                        .primary(),
                    )
                    .child(self.button("settings-theme", "切换明暗", true, Action::Theme, cx))
                    .child(self.button(
                        "settings-license",
                        "导入许可证",
                        true,
                        Action::License,
                        cx,
                    )),
            )
            .into_any_element()
    }
    pub(super) fn dialog_view(
        &mut self,
        kind: &'static str,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let title = match kind {
            "supply" => "电源设置",
            "meter" => "功率计设置",
            "spectrum" => "光谱仪设置",
            "test" => "测试设置",
            "close" => "安全关闭",
            _ => "设置",
        };
        let mut body = div().flex().flex_col().gap_4();
        let mut footer = div().flex().gap_2().justify_end();
        if kind == "delete-profile" {
            body = body.child("确认删除当前探头的此系数方案？既有测试记录保留原有系数版本。");
            footer = footer
                .child(self.button(
                    "profile-delete-cancel",
                    "取消",
                    true,
                    Action::Dialog(Some("meter")),
                    cx,
                ))
                .child(self.button(
                    "profile-delete-confirm",
                    "确认删除",
                    self.manual() && self.profile_delete.is_some(),
                    Action::Profile("powerMeter.profile.delete", self.profile_delete.clone()),
                    cx,
                ));
        } else if kind == "delete-preset" {
            body = body.child("删除当前选择的自定义脚本？测试记录和内置脚本保持不变。");
            footer = footer
                .child(self.button(
                    "preset-delete-cancel",
                    "取消",
                    true,
                    Action::Dialog(None),
                    cx,
                ))
                .child(self.button(
                    "preset-delete",
                    "确认删除",
                    self.manual(),
                    Action::Preset("delete"),
                    cx,
                ));
        } else if kind == "close" {
            body = body.child(if self.closing {
                "正在等待安全下电与导出任务结束"
            } else {
                "下电或导出尚未确认。请检查电源面板并决定后续操作。"
            });
            if !self.closing {
                body = body.child(
                    self.button(
                        "force-confirm",
                        if self.force_confirmed {
                            "已确认：核对物理电源状态并接受未完成导出的风险"
                        } else {
                            "确认：我已核对物理电源状态，并接受未完成导出的风险"
                        },
                        true,
                        Action::Dialog(Some("close")),
                        cx,
                    )
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.force_confirmed = !this.force_confirmed;
                        cx.notify();
                    })),
                );
            }
            footer = footer
                .child(self.button(
                    "close-cancel",
                    "取消关闭",
                    !self.closing,
                    Action::CancelClose,
                    cx,
                ))
                .child(self.button(
                    "close-retry",
                    "重试安全关闭",
                    !self.closing,
                    Action::Close,
                    cx,
                ))
                .child(self.button(
                    "close-force",
                    "强制退出",
                    !self.closing && self.force_confirmed,
                    Action::ForceExit,
                    cx,
                ));
        } else {
            let specs = match kind {
                "supply" => fields::SUPPLY,
                "meter" => fields::METER,
                "spectrum" => fields::SPECTRUM,
                _ => fields::GENERAL,
            };
            body = body.child(self.config_fields(specs, window, cx));
            if kind == "test" {
                body = body.child(self.config_fields(fields::SAFETY, window, cx));
                body = body
                    .child(
                        self.field(
                            "cfg.selectedScriptPresetId".into(),
                            "已保存脚本",
                            self.config()
                                .get("selectedScriptPresetId")
                                .cloned()
                                .unwrap_or(json!("")),
                            window,
                            cx,
                        ),
                    )
                    .child(self.field("preset.name".into(), "脚本名称", json!(""), window, cx))
                    .child(
                        div()
                            .flex()
                            .gap_2()
                            .child(self.button(
                                "preset-save",
                                "保存为脚本",
                                self.manual(),
                                Action::Preset("save"),
                                cx,
                            ))
                            .child(self.button(
                                "preset-delete-confirm",
                                "删除当前脚本",
                                self.manual(),
                                Action::Dialog(Some("delete-preset")),
                                cx,
                            )),
                    );
            }
            if matches!(kind, "supply" | "meter" | "spectrum") {
                let device = match kind {
                    "supply" => "powerSupply",
                    "meter" => "powerMeter",
                    _ => "spectrometer",
                };
                body = body.child(
                    self.button(
                        format!("refresh-{device}"),
                        "识别",
                        self.manual(),
                        Action::Command("device.refresh", json!({"device":device})),
                        cx,
                    )
                    .w(px(88.)),
                );
            }
            if kind == "supply" {
                let enabled = self.config()["powerSupplyKind"] != "tdk";
                let mut reads = div().flex().gap_2().flex_wrap();
                for (value, label) in [
                    ("outputVoltage", "读取输出电压"),
                    ("outputCurrent", "读取输出电流"),
                    ("inputVoltage", "读取输入电压"),
                    ("temperature", "读取温度"),
                ] {
                    reads = reads.child(self.button(
                        format!("read-{value}"),
                        label,
                        self.manual()
                            && (enabled || matches!(value, "outputVoltage" | "outputCurrent")),
                        Action::Command("powerSupply.read", json!({"value":value})),
                        cx,
                    ));
                }
                body = body.child(reads).child(format!(
                    "输出电压 {} V · 输出电流 {} A · 输入电压 {} V · 温度 {} °C",
                    text(&self.cache.value["devices"]["powerSupply"]["outputVoltageV"]),
                    text(&self.cache.value["devices"]["powerSupply"]["outputCurrentA"]),
                    text(&self.cache.value["devices"]["powerSupply"]["inputVoltageV"]),
                    text(&self.cache.value["devices"]["powerSupply"]["temperatureC"])
                ));
            }
            if kind == "spectrum" {
                body = body.child(self.button(
                    "spectrum-save",
                    "保存光谱 CSV",
                    self.manual(),
                    Action::Command("spectrometer.saveCsv", json!({})),
                    cx,
                ));
            }
            if kind == "meter" {
                let d = self.cache.value["devices"]["powerMeter"].clone();
                let library = d["coefficientLibrary"].clone();
                body = body.child(
                    div()
                        .flex()
                        .gap_2()
                        .child(self.button(
                            "meter-zero",
                            "相对归零",
                            self.manual(),
                            Action::Command("powerMeter.setRelativeZero", json!({"enabled":true})),
                            cx,
                        ))
                        .child(self.button(
                            "meter-zero-off",
                            "取消归零",
                            self.manual(),
                            Action::Command("powerMeter.setRelativeZero", json!({"enabled":false})),
                            cx,
                        )),
                );
                body = body.child(format!(
                    "探头 SN {} · 系数版本 {}",
                    text(&library["serialNumber"]),
                    text(&library["selectedVersion"])
                ));
                for p in library["profiles"].as_array().into_iter().flatten() {
                    body = body.child(self.button(
                        format!("profile-delete-{}", text(&p["id"])),
                        format!(
                                "删除方案：{}",
                                p["versions"]
                                    .as_array()
                                    .and_then(|a| a.last())
                                    .map(|v| text(&v["name"]))
                                    .unwrap_or_default()
                            ),
                        self.manual() && d["running"] != true,
                        Action::DeleteProfile(p.clone()),
                        cx,
                    ));
                    let latest = p["versions"]
                        .as_array()
                        .and_then(|a| a.last())
                        .cloned()
                        .unwrap_or(Value::Null);
                    body = body.child(
                        div()
                            .flex()
                            .items_center()
                            .gap_2()
                            .child(div().flex_1().child(format!(
                                "{} · ×{} · {} nm",
                                text(&latest["name"]),
                                text(&latest["softwareGain"]),
                                text(&latest["wavelengthNm"])
                            )))
                            .child(self.button(
                                format!("profile-select-{}", text(&p["id"])),
                                "使用",
                                self.manual() && d["running"] != true,
                                Action::Profile("powerMeter.profile.select", Some(p.clone())),
                                cx,
                            )),
                    );
                    for rev in p["versions"].as_array().into_iter().flatten() {
                        let mut target = p.clone();
                        target["version"] = rev["version"].clone();
                        body = body.child(
                            div()
                                .flex()
                                .gap_2()
                                .child(div().flex_1().text_xs().child(format!(
                                    "v{} · {} · {} · {}",
                                    text(&rev["version"]),
                                    text(&rev["createdAt"]),
                                    text(&rev["operator"]),
                                    text(&rev["reason"])
                                )))
                                .child(self.button(
                                    format!(
                                        "profile-restore-{}-{}",
                                        text(&p["id"]),
                                        text(&rev["version"])
                                    ),
                                    "恢复此版本",
                                    self.manual() && d["running"] != true,
                                    Action::Profile("powerMeter.profile.restore", Some(target)),
                                    cx,
                                )),
                        );
                    }
                }
                for (key, label) in fields::PROFILE {
                    let selected = library["profiles"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .find(|p| p["id"] == library["selectedProfileId"])
                        .and_then(|p| p["versions"].as_array().and_then(|a| a.last()))
                        .cloned()
                        .unwrap_or(Value::Null);
                    let default = if !selected[key].is_null() {
                        selected[key].clone()
                    } else {
                        match *key {
                            "softwareGain" => self.config()["softwareGain"].clone(),
                            "wavelengthNm" => self.config()["powerMeterWavelengthNm"].clone(),
                            _ => json!(""),
                        }
                    };
                    body = body.child(self.field(
                        format!("profile.{key}"),
                        label,
                        default,
                        window,
                        cx,
                    ));
                }
                body = body.child(
                    div()
                        .flex()
                        .gap_2()
                        .child(self.button(
                            "profile-create",
                            "创建系数方案",
                            self.manual() && d["running"] != true,
                            Action::Profile("powerMeter.profile.create", None),
                            cx,
                        ))
                        .child(self.button(
                            "profile-save",
                            "保存方案修订",
                            self.manual() && d["running"] != true,
                            Action::Profile("powerMeter.profile.save", None),
                            cx,
                        )),
                );
                let bg = &d["powerBackground"];
                body = body.child(format!(
                    "本底状态：{} · {}",
                    text(&bg["state"]),
                    text(&bg["decision"]["detail"])
                ));
                body = body.child(self.button(
                    "background-confirm",
                    if self.dark_confirmed {
                        "已确认：0 A、无激光输出"
                    } else {
                        "确认 0 A、无激光输出"
                    },
                    self.manual(),
                    Action::ConfirmDark,
                    cx,
                ));
                body = body.child(self.button(
                    "background-learn",
                    "学习跳动量（20 秒）",
                    self.manual() && self.dark_confirmed && d["running"] == true,
                    Action::Command(
                        "powerMeter.background.learn",
                        json!({"confirmedDark":self.dark_confirmed}),
                    ),
                    cx,
                ));
                body = body.child(self.button(
                    "background-cancel",
                    "取消本底任务",
                    self.manual(),
                    Action::Command("powerMeter.background.cancel", json!({})),
                    cx,
                ));
            }
            footer = footer
                .child(
                    self.button("dialog-close", "关闭", true, Action::Dialog(None), cx)
                        .w(px(96.)),
                )
                .child(
                    self.button(
                        "dialog-save",
                        if self.dialog_saving {
                            "保存中…"
                        } else {
                            "保存设置"
                        },
                        self.manual(),
                        Action::Save(None),
                        cx,
                    )
                    .primary()
                    .w(px(112.)),
                );
        }
        let dialog = div()
            .flex()
            .flex_col()
            .w(px(720.))
            .h(relative(0.84))
            .p_5()
            .gap_4()
            .bg(cx.theme().background)
            .border_1()
            .border_color(cx.theme().border)
            .rounded_lg()
            .child(div().text_xl().child(title))
            .child(
                div()
                    .id("dialog-scroll")
                    .flex_1()
                    .min_h_0()
                    .overflow_y_scroll()
                    .child(body),
            )
            .child(footer);
        div()
            .absolute()
            .inset_0()
            .flex()
            .items_center()
            .justify_center()
            .bg(rgba(0x00000099))
            .child(dialog)
            .into_any_element()
    }
}
