//! The original operator workbench layout, implemented with native GPUI controls.
use super::*;
use gpui::assets::IconName;
use gpui::component::Icon;
use std::borrow::Cow;

gpui::assets::icon_assets!(
    WorkbenchIcons,
    [
        CircleGauge,
        Hand,
        ChartColumn,
        FileText,
        Settings,
        Power,
        Gauge,
        Radio,
        FolderOpen,
        Play,
        Square,
        ChevronRight,
        Pause,
        Maximize2,
        OctagonAlert
    ]
);
pub(super) struct WorkbenchAssets;
impl AssetSource for WorkbenchAssets {
    fn load(&self, path: &str) -> gpui::Result<Option<Cow<'static, [u8]>>> {
        if let Some(asset) = WorkbenchIcons.load(path)? {
            Ok(Some(asset))
        } else {
            gpui::assets::Assets.load(path)
        }
    }
    fn list(&self, path: &str) -> gpui::Result<Vec<SharedString>> {
        let mut paths = gpui::assets::Assets.list(path)?;
        paths.extend(WorkbenchIcons.list(path)?);
        paths.sort();
        paths.dedup();
        Ok(paths)
    }
}
pub(super) fn surface(cx: &App) -> Hsla {
    rgb(if cx.theme().mode.is_dark() {
        0x181a1b
    } else {
        0xffffff
    })
    .into()
}
fn icon(name: IconName, size: f32) -> Icon {
    Icon::new(name).size(px(size)).flex_shrink_0()
}
impl Workbench {
    pub(super) fn workbench_header(&self, status: &str, cx: &mut Context<Self>) -> AnyElement {
        let mut actions = div().flex().items_center().gap_2();
        if self.cache.value["automaticTest"]["canPause"] == true {
            actions = actions.child(
                self.button(
                    "header-pause",
                    "暂停测试",
                    self.active(),
                    Action::Command("automatic.pause", json!({})),
                    cx,
                )
                .icon(IconName::Pause),
            );
        }
        actions = actions
            .child(
                self.button("coupling", "功率耦合", self.active(), Action::Coupling, cx)
                    .icon(IconName::Maximize2),
            )
            .child(
                self.button(
                    "global-stop",
                    "急停",
                    self.cache.value.is_object(),
                    Action::Command("app.stopAll", json!({})),
                    cx,
                )
                .icon(IconName::OctagonAlert)
                .danger(),
            );
        div()
            .h(px(60.))
            .flex_shrink_0()
            .flex()
            .items_center()
            .justify_between()
            .gap_4()
            .px_4()
            .border_b_1()
            .border_color(cx.theme().border)
            .child(
                div()
                    .w(px(172.))
                    .flex_shrink_0()
                    .flex()
                    .flex_col()
                    .gap_1()
                    .child(
                        div()
                            .text_size(px(18.))
                            .font_weight(FontWeight::MEDIUM)
                            .child("Power Test"),
                    )
                    .child(
                        div()
                            .text_size(px(11.))
                            .text_color(cx.theme().muted_foreground)
                            .text_ellipsis()
                            .child(if !status.is_empty() {
                                status.to_string()
                            } else if self.demo {
                                "演示数据 · 不连接仪器".into()
                            } else {
                                String::new()
                            }),
                    ),
            )
            .child(self.phase_track(cx))
            .child(actions)
            .into_any_element()
    }
    fn phase_track(&self, cx: &App) -> AnyElement {
        let a = &self.cache.value["automaticTest"];
        let state = a["state"].as_str().unwrap_or("idle");
        let phase = match state {
            "ramping" | "ramping_up" | "setting_current" => 1,
            "waiting_stable" | "dwelling" => 2,
            "recording" | "saving" => 3,
            "completed" | "stopping" | "ramping_down" => 4,
            _ => 0,
        };
        let mut track = div()
            .flex()
            .flex_1()
            .min_w_0()
            .items_center()
            .justify_center()
            .gap_3();
        let mut nodes = div().flex().items_center().w(px(340.)).max_w_full();
        for (i, label) in ["准备", "升流", "稳定", "记录", "完成"].iter().enumerate() {
            if i > 0 {
                nodes = nodes.child(div().flex_1().h(px(1.)).bg(if i <= phase {
                    cx.theme().primary
                } else {
                    cx.theme().border
                }));
            }
            nodes = nodes.child(
                div()
                    .flex()
                    .flex_col()
                    .items_center()
                    .gap_1()
                    .child(
                        div()
                            .size(px(10.))
                            .rounded_full()
                            .border_2()
                            .border_color(if i <= phase {
                                cx.theme().primary
                            } else {
                                cx.theme().muted_foreground
                            })
                            .bg(if i < phase {
                                cx.theme().primary
                            } else {
                                cx.theme().background
                            }),
                    )
                    .child(
                        div()
                            .text_size(px(10.))
                            .text_color(if i == phase {
                                cx.theme().primary
                            } else {
                                cx.theme().muted_foreground
                            })
                            .child(label.to_string()),
                    ),
            );
        }
        track = track.child(nodes);
        if let (Some(index), Some(points)) = (a["currentIndex"].as_i64(), a["currents"].as_array())
        {
            if index >= 0 && !points.is_empty() {
                track = track.child(
                    div()
                        .border_l_1()
                        .border_color(cx.theme().border)
                        .pl_3()
                        .text_size(px(12.))
                        .whitespace_nowrap()
                        .child(format!(
                            "第 {} / {} 点 · {} A",
                            index + 1,
                            points.len(),
                            text(&a["currentA"])
                        )),
                );
            }
        }
        track.into_any_element()
    }
    pub(super) fn workbench_navigation(&self, width: f32, cx: &mut Context<Self>) -> AnyElement {
        let mut navigation = div()
            .w(px(width))
            .flex_shrink_0()
            .flex()
            .flex_col()
            .gap(px(6.))
            .px(px(10.))
            .py_4()
            .border_r_1()
            .border_color(cx.theme().border)
            .bg(rgb(if cx.theme().mode.is_dark() {
                0x17191a
            } else {
                0xffffff
            }));
        for (index, &(id, label)) in NAV.iter().enumerate() {
            let selected = self.page == id;
            let locked = self.cache.value["safety"]["workflowBusy"] == true
                && !matches!(id, "automatic" | "manual")
                || id == "manual" && self.cache.value["safety"]["controlOwner"] == "automatic";
            let name = [
                IconName::CircleGauge,
                IconName::Hand,
                IconName::ChartColumn,
                IconName::FileText,
                IconName::FileText,
                IconName::Settings,
            ][index];
            navigation = navigation.child(
                Button::new(SharedString::from(format!("nav-{id}")))
                    .accessibility_label(label)
                    .disabled(locked)
                    .on_click(cx.listener(move |this, _, window, cx| {
                        this.execute(Action::Page(id), window, cx)
                    }))
                    .ghost()
                    .w_full()
                    .h(px(48.))
                    .px_3()
                    .border_l_2()
                    .border_color(if selected {
                        cx.theme().primary
                    } else {
                        gpui::transparent_black()
                    })
                    .bg(if selected {
                        cx.theme().primary.opacity(0.12)
                    } else {
                        gpui::transparent_black()
                    })
                    .text_color(if selected {
                        cx.theme().foreground
                    } else {
                        cx.theme().muted_foreground
                    })
                    .child(
                        div()
                            .w_full()
                            .flex()
                            .items_center()
                            .gap_3()
                            .child(icon(name, 20.))
                            .child(div().text_size(px(15.)).child(label)),
                    ),
            );
        }
        navigation
            .child(div().flex_1())
            .child(
                div()
                    .border_t_1()
                    .border_color(cx.theme().border)
                    .pt_3()
                    .text_center()
                    .text_size(px(14.))
                    .text_color(cx.theme().muted_foreground)
                    .child("Stay True"),
            )
            .into_any_element()
    }
    fn nameplate_field(
        &mut self,
        key: &str,
        label: &str,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let cfg = self.config();
        if self.manual() {
            self.field(format!("cfg.{key}"), label, cfg[key].clone(), window, cx)
        } else {
            div()
                .flex()
                .flex_col()
                .gap_2()
                .w_full()
                .min_w_0()
                .child(
                    div()
                        .text_size(px(12.))
                        .text_color(cx.theme().muted_foreground)
                        .child(label.to_string()),
                )
                .child(
                    div()
                        .h(px(40.))
                        .flex()
                        .items_center()
                        .text_size(px(15.))
                        .text_ellipsis()
                        .child(if key == "selectedScriptId" {
                            self.cache.value["testScripts"]
                                .as_array()
                                .into_iter()
                                .flatten()
                                .find(|s| s["id"] == cfg[key])
                                .map(|s| text(&s["name"]))
                                .unwrap_or_else(|| text(&cfg[key]))
                        } else {
                            text(&cfg[key])
                        }),
                )
                .into_any_element()
        }
    }
    pub(super) fn automatic_workbench(
        &mut self,
        window: &mut Window,
        cx: &mut Context<Self>,
        details: AnyElement,
    ) -> AnyElement {
        let wide = f32::from(window.viewport_size().width) > 1400.;
        let mut nameplate = div()
            .flex()
            .gap_3()
            .p_3()
            .border_1()
            .border_color(cx.theme().border)
            .rounded_lg()
            .bg(surface(cx));
        let mut identity = div().flex().gap_4().flex_1().min_w_0();
        for (key, label) in [
            ("sn", "当前样品"),
            ("testTemperatureC", "测试温度"),
            ("station", "测试站别"),
        ] {
            identity = identity.child(
                div()
                    .flex_1()
                    .min_w_0()
                    .child(self.nameplate_field(key, label, window, cx)),
            );
        }
        let script = self.nameplate_field("selectedScriptId", "脚本选择", window, cx);
        let script_group = div()
            .flex()
            .flex_1()
            .min_w_0()
            .items_end()
            .gap_2()
            .child(div().flex_1().min_w_0().child(script))
            .child(
                self.button(
                    "nameplate-save",
                    "保存",
                    self.manual(),
                    Action::Save(None),
                    cx,
                )
                .h(px(36.)),
            )
            .child(
                self.button(
                    "nameplate-edit",
                    "编辑",
                    self.manual(),
                    Action::Dialog(Some("test")),
                    cx,
                )
                .h(px(36.)),
            );
        let output = self.nameplate_field("outputDir", "输出目录", window, cx);
        let output_group = div()
            .flex()
            .flex_1()
            .min_w_0()
            .items_end()
            .gap_2()
            .child(div().flex_1().min_w_0().child(output))
            .child(
                self.button(
                    "nameplate-output",
                    "",
                    self.manual(),
                    Action::Pick("cfg.outputDir".into(), true),
                    cx,
                )
                .icon(IconName::FolderOpen)
                .accessibility_label("选择输出目录")
                .tooltip("选择输出目录")
                .w(px(40.))
                .h(px(36.)),
            );
        nameplate = if wide {
            nameplate
                .child(identity)
                .child(script_group)
                .child(output_group)
        } else {
            nameplate
                .flex_col()
                .child(identity)
                .child(div().flex().gap_4().child(script_group).child(output_group))
        };
        let charts = self.automatic_charts(cx);
        let rail = self.automatic_device_rail(window, cx);
        div()
            .flex()
            .flex_col()
            .gap_3()
            .child(nameplate)
            .child(rail)
            .child(charts)
            .child(self.panel("运行详情与恢复操作", details, cx))
            .into_any_element()
    }
    fn automatic_device_rail(&self, window: &Window, cx: &mut Context<Self>) -> AnyElement {
        let cfg = self.config();
        let mut devices = div().flex().flex_1().min_w_0();
        for (device, title, kind, name) in [
            ("powerSupply", "电源", "supply", IconName::Power),
            ("powerMeter", "功率计", "meter", IconName::Gauge),
            ("spectrometer", "光谱仪", "spectrum", IconName::Radio),
        ] {
            let d = &self.cache.value["devices"][device];
            let state = d["state"].as_str().unwrap_or("disconnected");
            let connected = matches!(state, "connected" | "running");
            let status = if d["detecting"] == true {
                "识别中"
            } else if connected {
                "在线"
            } else if state == "error" {
                "异常"
            } else {
                "未连接"
            };
            let activity = if d["outputEnabled"] == true {
                " · 输出中"
            } else if d["running"] == true {
                " · 采集中"
            } else {
                ""
            };
            let model = if device == "powerSupply" {
                cfg["powerSupplyKind"]
                    .as_str()
                    .unwrap_or("CH341")
                    .to_uppercase()
            } else if device == "spectrometer" {
                "Ocean Insight".into()
            } else {
                d["detail"]
                    .as_str()
                    .unwrap_or("未选择")
                    .split('|')
                    .next()
                    .unwrap_or("")
                    .to_string()
            };
            let summary = match device {
                "powerSupply" => format!(
                    "电流 {} → {} A · 步进 {} A",
                    text(&cfg["initialCurrentA"]),
                    text(&cfg["targetCurrentA"]),
                    text(&cfg["currentStepA"])
                ),
                "powerMeter" => format!(
                    "{} · 系数 ×{}",
                    text(&cfg["powerMeterProfileName"]),
                    text(&cfg["softwareGain"])
                ),
                _ => format!(
                    "中心波长 {} nm · FWHM ≤ {} nm",
                    text(&cfg["centerWavelengthSpecNm"]),
                    text(&cfg["fwhmMaximumNm"])
                ),
            };
            let tooltip = format!(
                "{title} · {}\n{summary}\n{}",
                d["detail"].as_str().unwrap_or(""),
                if self.manual() {
                    "点击设备栏进入设置"
                } else {
                    "测试期间设置已锁定"
                }
            );
            let copy = div()
                .flex()
                .flex_col()
                .gap(px(5.))
                .min_w_0()
                .flex_1()
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap_2()
                        .min_w_0()
                        .child(div().text_size(px(14.)).child(title))
                        .child(
                            div()
                                .text_size(px(11.))
                                .text_color(cx.theme().muted_foreground)
                                .text_ellipsis()
                                .child(model),
                        ),
                )
                .child(
                    div()
                        .flex()
                        .items_center()
                        .gap_2()
                        .text_size(px(12.))
                        .text_color(cx.theme().muted_foreground)
                        .child(div().size(px(6.)).rounded_full().bg(rgb(if connected {
                            0x22c55e
                        } else if state == "error" {
                            0xef4444
                        } else {
                            0x73777b
                        })))
                        .child(format!("{status}{activity}")),
                )
                .child(
                    div()
                        .text_size(px(11.))
                        .text_color(cx.theme().muted_foreground)
                        .text_ellipsis()
                        .child(summary),
                );
            devices = devices.child(
                div()
                    .flex_1()
                    .min_w_0()
                    .border_r_1()
                    .border_color(cx.theme().border)
                    .child(
                        Button::new(SharedString::from(format!("device-rail-{device}")))
                            .accessibility_label(format!(
                                "{title}信息，{}",
                                if self.manual() {
                                    "点击打开设置"
                                } else {
                                    "测试期间设置已锁定"
                                }
                            ))
                            .tooltip(tooltip)
                            .ghost()
                            .w_full()
                            .h(px(84.))
                            .px_3()
                            .on_click(cx.listener(move |this, _, window, cx| {
                                if this.manual() {
                                    this.execute(Action::Dialog(Some(kind)), window, cx)
                                }
                            }))
                            .child(
                                div()
                                    .w_full()
                                    .flex()
                                    .items_center()
                                    .gap_3()
                                    .min_w_0()
                                    .child(icon(name, 24.).text_color(
                                        if d["outputEnabled"] == true {
                                            cx.theme().primary
                                        } else {
                                            cx.theme().muted_foreground
                                        },
                                    ))
                                    .child(copy),
                            ),
                    ),
            );
        }
        let a = &self.cache.value["automaticTest"];
        let controls = div()
            .flex()
            .gap_2()
            .items_center()
            .px_3()
            .child(
                self.button(
                    "rail-start",
                    "开始",
                    self.active() && a["canStart"] == true,
                    Action::Save(Some("automatic.start")),
                    cx,
                )
                .primary()
                .icon(IconName::Play)
                .h(px(40.))
                .flex_1(),
            )
            .child(
                self.button(
                    "rail-stop",
                    "停止",
                    self.cache.value.is_object() && a["canEnd"] == true,
                    Action::Command("automatic.end", json!({})),
                    cx,
                )
                .danger()
                .icon(IconName::Square)
                .h(px(40.))
                .flex_1(),
            );
        let rail = div()
            .flex()
            .border_1()
            .border_color(cx.theme().border)
            .rounded_lg()
            .bg(surface(cx))
            .py(px(6.));
        if f32::from(window.viewport_size().width) <= 1100. {
            rail.flex_col()
                .child(devices)
                .child(
                    div()
                        .border_t_1()
                        .border_color(cx.theme().border)
                        .mt_1()
                        .pt_2()
                        .child(div().w(px(220.)).child(controls)),
                )
                .into_any_element()
        } else {
            rail.items_center()
                .child(devices)
                .child(div().w(px(204.)).flex_shrink_0().child(controls))
                .into_any_element()
        }
    }
    fn automatic_charts(&self, cx: &App) -> AnyElement {
        let m = &self.cache.value["measurements"];
        let cfg = self.config();
        let power = charts::workbench_chart(
            "实时功率",
            "s",
            "W",
            vec![(
                "功率".into(),
                0xff7a1a,
                charts::rows(&m["power"], "elapsedS", "powerW"),
            )],
            charts::ChartOptions {
                smooth: cfg["powerSmoothing"] != false,
                ..Default::default()
            },
            cx,
        );
        let liv = charts::workbench_chart(
            "功率 / 效率",
            "A",
            "W",
            vec![(
                "功率".into(),
                0xff7a1a,
                charts::rows(&m["stable"], "currentA", "powerW"),
            )],
            charts::ChartOptions {
                secondary: Some((
                    "效率".into(),
                    0xf5c56b,
                    charts::rows(&m["stable"], "currentA", "efficiencyPercent"),
                )),
                x_range: cfg["initialCurrentA"]
                    .as_f64()
                    .zip(cfg["targetCurrentA"].as_f64()),
                smooth: cfg["powerSmoothing"] != false,
                ..Default::default()
            },
            cx,
        );
        let spectrum = charts::workbench_chart(
            "光谱（实时）",
            "nm",
            "counts",
            vec![("光谱".into(), 0xff7a1a, charts::spectrum(&m["spectrum"]))],
            charts::ChartOptions {
                height: 320.,
                smooth: cfg["spectrumSmoothing"] != false,
                ..Default::default()
            },
            cx,
        );
        let mut metrics = div().flex().gap_3();
        for (title, device, key, unit, digits) in [
            ("实时功率", "powerMeter", "powerW", "W", 4),
            ("中心波长", "spectrometer", "centroidNm", "nm", 3),
            ("FWHM", "spectrometer", "fwhmNm", "nm", 3),
            ("PIB", "spectrometer", "pibPercent", "%", 2),
        ] {
            let value =
                arp_gpui::model::live_device_metric(&self.cache.value["devices"][device], key);
            let good = value.map(|v| match key {
                "centroidNm" => {
                    (v - cfg["centerWavelengthSpecNm"].as_f64().unwrap_or(f64::NAN)).abs()
                        <= cfg["centerWavelengthToleranceNm"]
                            .as_f64()
                            .unwrap_or(f64::NAN)
                }
                "fwhmNm" => v <= cfg["fwhmMaximumNm"].as_f64().unwrap_or(f64::NAN),
                "pibPercent" => v >= cfg["pibMinimumPercent"].as_f64().unwrap_or(f64::NAN),
                _ => true,
            });
            let color = if key == "powerW" {
                cx.theme().primary
            } else if good == Some(true) {
                rgb(0x22c55e).into()
            } else if good == Some(false) {
                rgb(0xef4444).into()
            } else {
                cx.theme().muted_foreground
            };
            metrics = metrics.child(
                div()
                    .flex_1()
                    .min_w_0()
                    .h(px(76.))
                    .px_4()
                    .py_2()
                    .rounded_lg()
                    .border_1()
                    .border_color(cx.theme().border)
                    .bg(surface(cx))
                    .child(
                        div()
                            .text_right()
                            .text_size(px(12.))
                            .text_color(cx.theme().muted_foreground)
                            .child(title),
                    )
                    .child(
                        div()
                            .flex()
                            .items_end()
                            .justify_center()
                            .gap_2()
                            .child(
                                div()
                                    .text_size(px(32.))
                                    .line_height(relative(1.1))
                                    .font_family(cx.theme().mono_font_family.clone())
                                    .font_weight(FontWeight::SEMIBOLD)
                                    .text_color(color)
                                    .child(
                                        value
                                            .map(|v| format!("{v:.digits$}"))
                                            .unwrap_or("—".into()),
                                    ),
                            )
                            .child(
                                div()
                                    .text_size(px(12.))
                                    .line_height(relative(1.1))
                                    .pb_1()
                                    .text_color(cx.theme().muted_foreground)
                                    .child(unit),
                            ),
                    ),
            );
        }
        div()
            .flex()
            .flex_col()
            .gap_3()
            .child(
                div()
                    .flex()
                    .gap_3()
                    .child(div().flex_1().min_w_0().child(power))
                    .child(div().flex_1().min_w_0().child(liv)),
            )
            .child(metrics)
            .child(spectrum)
            .into_any_element()
    }
}
