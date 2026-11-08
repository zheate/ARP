#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]
extern crate gpui_kit as gpui;
mod charts;
mod fields;
mod views;
use arp_gpui::report;
use arp_gpui::{
    controller::Controller,
    model::{SnapshotCache, shutdown_complete, snapshot_interval},
};
use gpui::component::{
    ActiveTheme, Disableable, Theme, ThemeMode,
    button::{Button, ButtonVariants},
    input::{Input, InputEvent, InputState},
    select::{Select, SelectEvent, SelectItem, SelectState},
};
use gpui::*;
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, Sender},
    },
    time::Duration,
};

#[derive(Clone)]
struct Choice {
    title: String,
    value: String,
}
impl SelectItem for Choice {
    type Value = String;
    fn title(&self) -> SharedString {
        self.title.clone().into()
    }
    fn value(&self) -> &String {
        &self.value
    }
}
struct Editor {
    input: Option<Entity<InputState>>,
    select: Option<Entity<SelectState<Vec<Choice>>>>,
    options: Vec<(String, Value)>,
}
#[derive(Clone)]
enum Action {
    Command(&'static str, Value),
    Save(Option<&'static str>),
    Dialog(Option<&'static str>),
    Pick(String, bool),
    License,
    Close,
    CancelClose,
    ForceExit,
    DataLoad,
    DataInspect(Value),
    ReportInspect,
    Report(&'static str),
    Profile(&'static str, Option<Value>),
    Page(&'static str),
    Theme,
    Coupling,
    Preset(&'static str),
    DeleteProfile(Value),
    PageData(i64),
    ConfirmDark,
    AddReportField,
    BatchReports,
    Compare(Value),
    OpenOutput,
}
enum Event {
    Snapshot(u64, Result<Value, String>),
    Command(&'static str, Result<Value, String>),
    Picked(String, Option<PathBuf>),
    License(Result<Value, String>),
    Closed(Result<Value, String>),
    Quit,
}
struct Workbench {
    client: Arc<Controller>,
    tx: Sender<Event>,
    rx: Receiver<Event>,
    stream: Arc<Mutex<(String, u64)>>,
    alive: Arc<AtomicBool>,
    allow_exit: Arc<AtomicBool>,
    cache: SnapshotCache,
    page: &'static str,
    dialog: Option<&'static str>,
    editors: HashMap<String, Editor>,
    draft: HashMap<String, Value>,
    subscriptions: Vec<Subscription>,
    pending: Option<&'static str>,
    dialog_saving: bool,
    error: String,
    notice: String,
    license: Value,
    demo: bool,
    closing: bool,
    force_confirmed: bool,
    coupling: bool,
    data: Value,
    detail: Value,
    comparison: Value,
    compare_next: bool,
    report: Value,
    batch: Value,
    report_signature: String,
    profile_delete: Option<Value>,
    previews: Vec<PathBuf>,
    data_page: u64,
    invalid: HashSet<String>,
    dark_confirmed: bool,
    _timer: Task<()>,
}
const NAV: &[(&str, &str)] = &[
    ("automatic", "自动测试"),
    ("manual", "手动配置"),
    ("pd", "PD 采集"),
    ("data", "数据浏览"),
    ("shipping", "出货报告"),
    ("settings", "系统设置"),
];
impl Workbench {
    fn new(
        window: &mut Window,
        cx: &mut Context<Self>,
        allow_exit: Arc<AtomicBool>,
        tx: Sender<Event>,
        rx: Receiver<Event>,
    ) -> Self {
        let demo = std::env::args().any(|a| a == "--demo")
            || std::env::var("ARP_GPUI_DEMO").as_deref() == Ok("1");
        let client = Arc::new(Controller::new());
        let license = serde_json::to_value(client.license()).unwrap_or(Value::Null);
        let stream = Arc::new(Mutex::new(("automatic".to_string(), 0)));
        let alive = Arc::new(AtomicBool::new(true));
        if !demo {
            let (client, stream, alive, tx) =
                (client.clone(), stream.clone(), alive.clone(), tx.clone());
            std::thread::spawn(move || {
                let mut cache = SnapshotCache::default();
                let mut previous = u64::MAX;
                while alive.load(Ordering::SeqCst) {
                    let (view, generation) = stream.lock().unwrap().clone();
                    if client.license().valid || !cache.value.is_null() {
                        if previous != generation {
                            cache = SnapshotCache::default();
                            previous = generation;
                        }
                        let result = client.request("app.snapshot", cache.request_params(&view));
                        let result = result.and_then(|snapshot| {
                            cache.merge(snapshot)?;
                            Ok(cache.value.clone())
                        });
                        if result.is_err() {
                            cache = SnapshotCache::default();
                        }
                        if tx.send(Event::Snapshot(generation, result)).is_err() {
                            break;
                        }
                    }
                    std::thread::sleep(Duration::from_millis(snapshot_interval(&view)));
                }
            });
        }
        let timer = cx.spawn(async move |this, cx| {
            loop {
                cx.background_executor()
                    .timer(Duration::from_millis(32))
                    .await;
                if this.update(cx, |this, cx| this.tick(cx)).is_err() {
                    break;
                }
            }
        });
        let weak = cx.entity().downgrade();
        window.on_window_should_close(cx, move |_, cx| {
            let _ = weak.update(cx, |this, cx| this.begin_close(cx));
            false
        });
        let mut cache = SnapshotCache::default();
        if demo {
            cache
                .merge(serde_json::from_str(include_str!("../fixtures/demo.json")).unwrap())
                .unwrap();
        }
        if demo && std::env::var("ARP_GPUI_DEMO_IDLE").as_deref() == Ok("1") {
            cache = SnapshotCache::default();
            cache
                .merge(serde_json::from_str(include_str!("../fixtures/demo-idle.json")).unwrap())
                .unwrap();
        }
        Self {
            client,
            tx,
            rx,
            stream,
            alive,
            allow_exit,
            cache,
            page: "automatic",
            dialog: None,
            editors: HashMap::new(),
            draft: HashMap::new(),
            subscriptions: vec![],
            pending: None,
            dialog_saving: false,
            error: String::new(),
            notice: String::new(),
            license,
            demo,
            closing: false,
            force_confirmed: false,
            coupling: false,
            data: Value::Null,
            detail: Value::Null,
            comparison: Value::Null,
            compare_next: false,
            report: Value::Null,
            batch: Value::Null,
            report_signature: String::new(),
            profile_delete: None,
            previews: vec![],
            data_page: 1,
            invalid: HashSet::new(),
            dark_confirmed: false,
            _timer: timer,
        }
    }
    fn tick(&mut self, cx: &mut Context<Self>) {
        let mut changed = false;
        while let Ok(event) = self.rx.try_recv() {
            changed = true;
            match event {
                Event::Snapshot(g, result) => {
                    if self.stream.lock().unwrap().1 != g {
                        continue;
                    }
                    match result {
                        Ok(value) => {
                            self.cache.value = value;
                        }
                        Err(e) => {
                            self.error = e;
                        }
                    }
                }
                Event::Command(method, result) => {
                    self.pending = None;
                    self.dialog_saving = false;
                    match result {
                        Ok(value) => {
                            self.error.clear();
                            if value.get("backend").is_some() {
                                if let Err(e) = self.cache.merge(value) {
                                    self.error = e;
                                }
                            } else {
                                match method {
                                    "dataBrowser.list" => self.data = value,
                                    "dataBrowser.inspect" => {
                                        if self.compare_next {
                                            self.comparison = value;
                                        } else {
                                            self.detail = value;
                                        }
                                        self.compare_next = false;
                                    }
                                    "shippingReport.batchInspect" => {
                                        self.batch = value;
                                    }
                                    "shippingReport.inspect" => {
                                        self.report = value;
                                        self.report_signature.clear();
                                        self.editors.retain(|k, _| !k.starts_with("report."));
                                        self.draft.retain(|k, _| !k.starts_with("report."));
                                        self.draft.insert(
                                            "report.sourcePath".into(),
                                            self.report["sourcePath"].clone(),
                                        );
                                    }
                                    "shippingReport.preview" => {
                                        self.previews.clear();
                                        if let Some(pages) = value["pages"].as_array() {
                                            use base64::Engine;
                                            for (i, p) in pages.iter().enumerate() {
                                                if let Some(s) = p.as_str() {
                                                    if let Ok(bytes) =
                                                        base64::engine::general_purpose::STANDARD
                                                            .decode(s)
                                                    {
                                                        let path =
                                                            std::env::temp_dir().join(format!(
                                                                "arp-gpui-preview-{}-{i}.png",
                                                                std::process::id()
                                                            ));
                                                        if std::fs::write(&path, bytes).is_ok() {
                                                            self.previews.push(path);
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    _ => {
                                        self.notice = value["outputPath"]
                                            .as_str()
                                            .or(value["path"].as_str())
                                            .unwrap_or("操作完成")
                                            .to_string()
                                    }
                                }
                            }
                            if method.starts_with("powerMeter.profile.") {
                                self.editors.retain(|k, _| !k.starts_with("profile."));
                                self.draft.retain(|k, _| !k.starts_with("profile."));
                                if method == "powerMeter.profile.delete" {
                                    self.dialog = Some("meter");
                                }
                            }
                            if method == "device.refresh" {
                                self.editors.retain(|k, _| {
                                    !k.contains("Resource") && !k.contains("LocationPath")
                                });
                            }
                            if method == "app.configure" || method == "pd.configure" {
                                self.dialog = None;
                                self.editors.clear();
                                self.draft.clear();
                            }
                            if method == "automatic.nextDevice" {
                                self.editors.clear();
                                self.draft.clear();
                            }
                        }
                        Err(e) => self.error = e,
                    }
                }
                Event::Picked(key, path) => {
                    if let Some(path) = path {
                        let s = path.to_string_lossy().to_string();
                        self.draft.insert(key.clone(), json!(s));
                        self.editors.remove(&key);
                        if key == "report.sourcePath" {
                            self.dispatch("shippingReport.inspect", json!({"path":s}), cx);
                        }
                        if key == "data.rootPath" {
                            self.load_data(cx);
                        }
                        if key == "report.batchRoot" {
                            self.dispatch("shippingReport.batchInspect", json!({"rootPath":s}), cx);
                        }
                    }
                }
                Event::License(result) => match result {
                    Ok(status) => {
                        self.license = status;
                        self.error.clear();
                    }
                    Err(e) => self.error = e,
                },
                Event::Closed(result) => match result {
                    Ok(value) if shutdown_complete(&value) => {
                        self.alive.store(false, Ordering::SeqCst);
                        self.allow_exit.store(true, Ordering::SeqCst);
                        cx.quit();
                    }
                    Ok(value) => {
                        self.closing = false;
                        self.dialog = Some("close");
                        self.error = format!(
                            "安全关闭未确认：{}。导出完成：{}",
                            value["shutdown"]["reason"]
                                .as_str()
                                .unwrap_or("请检查电源面板"),
                            value["exportsDrained"]
                        );
                    }
                    Err(e) => {
                        self.closing = false;
                        self.dialog = Some("close");
                        self.error = e;
                    }
                },
                Event::Quit => self.begin_close(cx),
            }
        }
        if changed {
            if !self.demo {
                self.license = serde_json::to_value(self.client.license()).unwrap_or(Value::Null);
            }
            cx.notify();
        }
    }
    fn begin_close(&mut self, cx: &mut Context<Self>) {
        if self.closing {
            return;
        }
        if self.demo {
            self.allow_exit.store(true, Ordering::SeqCst);
            self.alive.store(false, Ordering::SeqCst);
            cx.quit();
            return;
        }
        self.closing = true;
        self.dialog = Some("close");
        let (client, tx) = (self.client.clone(), self.tx.clone());
        std::thread::spawn(move || {
            let _ = tx.send(Event::Closed(client.close()));
        });
        cx.notify();
    }
    fn dispatch(&mut self, method: &'static str, params: Value, cx: &mut Context<Self>) {
        let safety = matches!(
            method,
            "app.stopAll" | "automatic.end" | "powerSupply.safeRampDown"
        );
        if self.pending.is_some() && !safety {
            return;
        }
        if !safety && !arp_gpui::model::safe_without_license(method) && !self.invalid.is_empty() {
            self.error = "请修正无效参数后继续".into();
            cx.notify();
            return;
        }
        if self.demo {
            self.notice = "演示数据：此操作未发送设备命令".into();
            cx.notify();
            return;
        }
        self.pending = Some(method);
        let (client, tx) = (self.client.clone(), self.tx.clone());
        std::thread::spawn(move || {
            let result = client.command(method, params);
            let _ = tx.send(Event::Command(method, result));
        });
        cx.notify();
    }
    fn config(&self) -> Value {
        let mut config = self.cache.value["configuration"].clone();
        if !config.is_object() {
            config = json!({});
        }
        for (key, val) in &self.draft {
            if let Some(name) = key.strip_prefix("cfg.") {
                config[name] = val.clone();
            }
        }
        config
    }
    fn value(&self, key: &str, default: Value) -> Value {
        self.draft.get(key).cloned().unwrap_or(default)
    }
    fn execute(&mut self, a: Action, window: &mut Window, cx: &mut Context<Self>) {
        match a {
            Action::Command(method, params) => self.dispatch(method, params, cx),
            Action::Page(page) => {
                if self.pending.is_some() {
                    return;
                }
                self.page = page;
                self.detail = Value::Null;
                self.coupling = false;
                let mut stream = self.stream.lock().unwrap();
                stream.0 = if page == "settings" {
                    "automatic"
                } else {
                    page
                }
                .into();
                stream.1 += 1;
                drop(stream);
                if page == "data" && self.data.is_null() {
                    self.load_data(cx);
                }
            }
            Action::Dialog(dialog) => {
                self.dialog = dialog;
                if dialog.is_none() {
                    self.draft.retain(|k, _| !k.starts_with("cfg."));
                    self.editors.clear();
                    self.invalid.clear();
                }
            }
            Action::Save(after) => {
                if self.pending.is_some() {
                    return;
                }
                if !self.invalid.is_empty() {
                    self.error = "请修正无效参数后再保存或开始测试".into();
                    cx.notify();
                    return;
                }
                let mut patch = json!({});
                for (key, val) in &self.draft {
                    if let Some(name) = key.strip_prefix("cfg.") {
                        patch[name] = val.clone();
                    }
                }
                if let Some(method) = after {
                    if method == "automatic.start" {
                        let mut config = self.config();
                        for key in [
                            "powerMeterDriverKind",
                            "powerMeterSerialNumber",
                            "powerMeterWavelengthNm",
                            "softwareGain",
                            "powerMeterProfileId",
                            "powerMeterProfileName",
                            "powerMeterProfileVersion",
                            "powerMeterProfileNotes",
                            "powerMeterProfileWavelengthPending",
                        ] {
                            config.as_object_mut().unwrap().remove(key);
                        }
                        self.dispatch(method, config, cx);
                    } else if patch.as_object().is_some_and(|o| !o.is_empty()) {
                        if self.pending.is_some() {
                            return;
                        }
                        self.pending = Some(method);
                        let (client, tx) = (self.client.clone(), self.tx.clone());
                        let params = match method {
                            "powerSupply.setCurrent" => {
                                json!({"currentA":self.config()["setCurrentA"]})
                            }
                            _ => json!({}),
                        };
                        std::thread::spawn(move || {
                            let result = client
                                .command("app.configure", patch)
                                .and_then(|_| client.command(method, params));
                            let _ = tx.send(Event::Command(method, result));
                        });
                    } else {
                        let params = if method == "powerSupply.setCurrent" {
                            json!({"currentA":self.config()["setCurrentA"]})
                        } else {
                            json!({})
                        };
                        self.dispatch(method, params, cx);
                    }
                } else {
                    self.dialog_saving = true;
                    self.dispatch("app.configure", patch, cx);
                }
            }
            Action::Pick(key, directory) => {
                let tx = self.tx.clone();
                std::thread::spawn(move || {
                    let dialog = rfd::FileDialog::new();
                    let path = if directory {
                        dialog.pick_folder()
                    } else {
                        dialog.pick_file()
                    };
                    let _ = tx.send(Event::Picked(key, path));
                });
            }
            Action::License => {
                let (client, tx) = (self.client.clone(), self.tx.clone());
                std::thread::spawn(move || {
                    if let Some(path) = rfd::FileDialog::new()
                        .add_filter("ARP 许可证", &["json"])
                        .pick_file()
                    {
                        let result = client
                            .install(&path)
                            .and_then(|s| serde_json::to_value(s).map_err(|e| e.to_string()));
                        let _ = tx.send(Event::License(result));
                    }
                });
            }
            Action::Close => self.begin_close(cx),
            Action::CancelClose => {
                if !self.closing {
                    self.client.cancel_close();
                    self.dialog = None;
                }
            }
            Action::ForceExit => {
                if !self.closing && self.force_confirmed {
                    self.client.force_exit();
                    self.alive.store(false, Ordering::SeqCst);
                    self.allow_exit.store(true, Ordering::SeqCst);
                    cx.quit();
                }
            }
            Action::PageData(delta) => {
                self.data_page = (self.data_page as i64 + delta).max(1) as u64;
                self.load_data(cx);
            }
            Action::Compare(row) => {
                self.compare_next = true;
                self.execute(Action::DataInspect(row), window, cx);
            }
            Action::ConfirmDark => self.dark_confirmed = !self.dark_confirmed,
            Action::AddReportField => {
                let mut custom = self.value("report.customFields", json!([]));
                let rows = custom.as_array_mut().unwrap();
                let i = rows.len();
                rows.push(json!({"key":format!("custom_native_{i}"),"label":"自定义参数","unit":"","side":"left"}));
                self.draft.insert("report.customFields".into(), custom);
            }
            Action::BatchReports => self.batch_reports(cx),
            Action::OpenOutput => {
                let p = self.value("report.outputDirectory", json!(""));
                if let Some(path) = p.as_str().filter(|s| !s.is_empty()) {
                    cx.reveal_path(std::path::Path::new(path));
                }
            }
            Action::DataLoad => {
                self.data_page = 1;
                self.load_data(cx);
            }
            Action::DataInspect(row) => {
                let mut params = json!({"includeSpectra":true});
                if row["storage"] == "database" {
                    params["recordId"] = row["id"].clone();
                } else {
                    params["path"] = row["sourcePath"].clone();
                }
                if let Some(v) = row.get("version") {
                    params["version"] = v.clone();
                }
                self.dispatch("dataBrowser.inspect", params, cx);
            }
            Action::ReportInspect => {
                let path = self.value("report.sourcePath", json!(""));
                self.dispatch("shippingReport.inspect", json!({"path":path}), cx);
            }
            Action::Report(method) => self.report_action(method, cx),
            Action::Profile(method, profile) => self.profile_action(method, profile, cx),
            Action::Theme => {
                let mode = if cx.theme().mode.is_dark() {
                    ThemeMode::Light
                } else {
                    ThemeMode::Dark
                };
                apply_theme(mode, Some(window), cx);
                if !self.demo {
                    if let Err(error) = save_theme(mode) {
                        self.error = error;
                    }
                }
            }
            Action::Preset(action) => self.preset_action(action, cx),
            Action::DeleteProfile(profile) => {
                self.profile_delete = Some(profile);
                self.dialog = Some("delete-profile");
            }
            Action::Coupling => self.coupling = !self.coupling,
        }
        cx.notify();
    }
    fn button(
        &self,
        id: impl Into<SharedString>,
        label: impl Into<SharedString>,
        enabled: bool,
        action: Action,
        cx: &mut Context<Self>,
    ) -> Button {
        Button::new(ElementId::Name(id.into()))
            .label(label)
            .disabled(!enabled)
            .on_click(
                cx.listener(move |this, _, window, cx| this.execute(action.clone(), window, cx)),
            )
    }
    fn options(&self, key: &str, default: &Value) -> Vec<(String, Value)> {
        let cfg = &self.cache.value["configuration"];
        match key {
            "report.reportType" => self.report["allowedReportTypes"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|v| {
                    (
                        (if v == "pole" {
                            "极图报告"
                        } else {
                            "光谱报告"
                        })
                        .into(),
                        v.clone(),
                    )
                })
                .collect(),
            "report.reportLanguage" => {
                vec![("中文".into(), json!("zh")), ("英文".into(), json!("en"))]
            }
            "report.operatingCurrentA" => self.report["currents"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|v| (format!("{} A", text(v)), v.clone()))
                .collect(),
            "report.axis.mode" => vec![
                ("原始强度".into(), json!("counts")),
                ("相对强度 / dB".into(), json!("relative_db")),
            ],
            "cfg.powerSupplyKind" => vec![
                ("CH341 I²C".into(), json!("ch341")),
                ("TDK RS-232".into(), json!("tdk")),
                ("RU 串口".into(), json!("ru")),
            ],
            "cfg.selectedScriptPresetId" => self.cache.value["configuration"]["testScriptPresets"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|p| (text(&p["name"]), p["id"].clone()))
                .collect(),
            "cfg.currentPlanMode" => vec![
                ("范围与步进".into(), json!("range")),
                ("自定义电流点".into(), json!("custom")),
            ],
            "cfg.selectedScriptId" => self.cache.value["testScripts"]
                .as_array()
                .unwrap_or(&vec![])
                .iter()
                .map(|s| (s["name"].as_str().unwrap_or("").into(), s["id"].clone()))
                .collect(),
            "cfg.powerMeterResource"
            | "cfg.spectrometerResource"
            | "cfg.tdkResource"
            | "cfg.ruResource" => {
                let device = if key.contains("powerMeter") {
                    "powerMeter"
                } else if key.contains("spectrometer") {
                    "spectrometer"
                } else {
                    "powerSupply"
                };
                self.cache.value["devices"][device]["resources"]
                    .as_array()
                    .unwrap_or(&vec![])
                    .iter()
                    .filter_map(|v| v.as_str().map(|s| (s.into(), v.clone())))
                    .chain(
                        default
                            .as_str()
                            .filter(|s| !s.is_empty())
                            .map(|s| (s.into(), default.clone())),
                    )
                    .collect()
            }
            "cfg.ch341SelectedLocationPath" => self.cache.value["devices"]["powerSupply"]["ch341"]
                ["ports"]
                .as_array()
                .unwrap_or(&vec![])
                .iter()
                .map(|p| {
                    (
                        p["name"]
                            .as_str()
                            .or(p["locationPath"].as_str())
                            .unwrap_or("")
                            .into(),
                        p["locationPath"].clone(),
                    )
                })
                .collect(),
            "pd.device" => self.cache.value["pd"]["devices"]
                .as_array()
                .unwrap_or(&vec![])
                .iter()
                .map(|v| (v.as_str().unwrap_or("").into(), v.clone()))
                .collect(),
            "pd.channel" => self.cache.value["pd"]["channels"]
                .as_array()
                .unwrap_or(&vec![])
                .iter()
                .map(|v| (v.as_str().unwrap_or("").into(), v.clone()))
                .collect(),
            "pd.terminal" => ["RSE", "NRSE", "DIFF", "DEFAULT"]
                .iter()
                .map(|v| (v.to_string(), json!(v)))
                .collect(),
            "pd.range" => self.cache.value["pd"]["ranges"]
                .as_array()
                .unwrap_or(&vec![])
                .iter()
                .map(|v| (v["label"].as_str().unwrap_or("").into(), v["value"].clone()))
                .collect(),
            _ if default.is_boolean() => {
                vec![("关闭".into(), json!(false)), ("开启".into(), json!(true))]
            }
            _ => {
                let _ = cfg;
                vec![]
            }
        }
    }
    fn field(
        &mut self,
        key: String,
        label: &str,
        default: Value,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let value = self.value(&key, default.clone());
        let mut options = self.options(&key, &value);
        let mut seen = Vec::<Value>::new();
        options.retain(|(_, value)| {
            if seen.contains(value) {
                false
            } else {
                seen.push(value.clone());
                true
            }
        });
        if self.editors.get(&key).is_some_and(|e| e.options != options) {
            self.editors.remove(&key);
        }

        if !self.editors.contains_key(&key) {
            let editor = if options.is_empty() {
                let display = if value.is_string() {
                    value.as_str().unwrap().to_string()
                } else if key.ends_with("customCurrentPoints") {
                    value
                        .as_array()
                        .unwrap_or(&vec![])
                        .iter()
                        .map(|p| format!("{},{}", p["currentA"], p["dwellS"]))
                        .collect::<Vec<_>>()
                        .join("; ")
                } else {
                    value.to_string()
                };
                let input = cx.new(|cx| InputState::new(window, cx).default_value(display));
                let k = key.clone();
                let original = value.clone();
                self.subscriptions
                    .push(cx.subscribe(&input, move |this, state, event, cx| {
                        if matches!(event, InputEvent::Change) {
                            let s = state.read(cx).value().to_string();
                            let parsed = if original.is_boolean() {
                                match s.as_str() {
                                    "true" => Ok(json!(true)),
                                    "false" => Ok(json!(false)),
                                    _ => Err("请输入 true 或 false".to_string()),
                                }
                            } else if original.is_number() {
                                serde_json::from_str::<Value>(&s)
                                    .map_err(|_| "请输入有效数字".into())
                                    .and_then(|v| {
                                        if v.is_number() {
                                            Ok(v)
                                        } else {
                                            Err("请输入有效数字".into())
                                        }
                                    })
                            } else if k.ends_with("customCurrentPoints") {
                                parse_currents(&s)
                            } else {
                                Ok(json!(s))
                            };
                            match parsed {
                                Ok(v) => {
                                    this.draft.insert(k.clone(), v);
                                    this.invalid.remove(&k);
                                    this.error.clear();
                                }
                                Err(e) => {
                                    this.invalid.insert(k.clone());
                                    this.error = e;
                                }
                            }
                            cx.notify();
                        }
                    }));
                Editor {
                    input: Some(input),
                    select: None,
                    options: vec![],
                }
            } else {
                let choices = options
                    .iter()
                    .enumerate()
                    .map(|(i, (title, _))| Choice {
                        title: title.clone(),
                        value: i.to_string(),
                    })
                    .collect::<Vec<_>>();
                let selected = options
                    .iter()
                    .position(|(_, v)| *v == value)
                    .map(|i| gpui::component::IndexPath::new(i));
                let select = cx.new(|cx| SelectState::new(choices, selected, window, cx));
                let k = key.clone();
                let opts = options.clone();
                self.subscriptions
                    .push(cx.subscribe(&select, move |this, _, event, cx| {
                        if let SelectEvent::Confirm(Some(i)) = event {
                            if let Ok(i) = i.parse::<usize>() {
                                if let Some((_, v)) = opts.get(i) {
                                    this.draft.insert(k.clone(), v.clone());
                                    if k == "report.reportType" {
                                        this.draft.retain(|key, _| {
                                            !key.starts_with("report.include.")
                                                && !key.starts_with("report.field.")
                                                && !key.starts_with("report.axis.")
                                                && key != "report.customFields"
                                        });
                                        this.editors.retain(|key, _| {
                                            !key.starts_with("report.include.")
                                                && !key.starts_with("report.field.")
                                                && !key.starts_with("report.axis.")
                                        });
                                    }
                                    if k == "cfg.selectedScriptId" {
                                        let id = v.as_str().unwrap_or("standard");
                                        this.draft.insert(
                                            "cfg.currentPlanMode".into(),
                                            json!(if id == "standard" {
                                                "range"
                                            } else {
                                                "custom"
                                            }),
                                        );
                                        if id == "aging" {
                                            for (key, value) in [
                                                ("initialCurrentA", json!(2)),
                                                ("targetCurrentA", json!(20)),
                                                ("currentStepA", json!(2)),
                                                (
                                                    "customCurrentPoints",
                                                    json!([{"currentA":20,"dwellS":7200}]),
                                                ),
                                            ] {
                                                this.draft.insert(format!("cfg.{key}"), value);
                                            }
                                        }
                                        this.editors
                                            .retain(|key, _| key == &k || !key.starts_with("cfg."));
                                    }
                                    if k == "cfg.selectedScriptPresetId" {
                                        let presets =
                                            this.cache.value["configuration"]["testScriptPresets"]
                                                .as_array()
                                                .cloned()
                                                .unwrap_or_default();
                                        if let Some(p) = presets.iter().find(|p| p["id"] == *v) {
                                            if let Some(params) = p["parameters"].as_object() {
                                                for (key, value) in params {
                                                    this.draft.insert(
                                                        format!("cfg.{key}"),
                                                        value.clone(),
                                                    );
                                                }
                                            }
                                            this.editors.retain(|key, _| {
                                                key == &k || !key.starts_with("cfg.")
                                            });
                                        }
                                    }
                                    cx.notify();
                                }
                            }
                        }
                    }));
                Editor {
                    input: None,
                    select: Some(select),
                    options: options.clone(),
                }
            };
            self.editors.insert(key.clone(), editor);
        }
        let editor = self.editors.get(&key).unwrap();
        let control = if let Some(input) = &editor.input {
            Input::new(input)
                .aria_label(label.to_string())
                .disabled(
                    key.starts_with("cfg.") && !self.manual()
                        || key.starts_with("pd.") && self.cache.value["pd"]["state"] == "running"
                        || key.starts_with("report.field.")
                            && self.report["fieldDefinitions"][self
                                .value("report.reportType", json!("spectrum"))
                                .as_str()
                                .unwrap_or("spectrum")]
                            .as_array()
                            .is_some_and(|defs| {
                                defs.iter().any(|f| {
                                    Some(f["key"].as_str().unwrap_or(""))
                                        == key.strip_prefix("report.field.")
                                        && f["measuredKey"].is_string()
                                })
                            }),
                )
                .into_any_element()
        } else {
            Select::new(editor.select.as_ref().unwrap())
                .accessibility_label(label.to_string())
                .disabled(
                    key.starts_with("cfg.") && !self.manual()
                        || key.starts_with("pd.") && self.cache.value["pd"]["state"] == "running",
                )
                .into_any_element()
        };
        div()
            .flex()
            .flex_col()
            .gap_1()
            .w_full()
            .min_w_0()
            .child(
                div()
                    .text_sm()
                    .text_color(cx.theme().muted_foreground)
                    .child(label.to_string()),
            )
            .child(control)
            .into_any_element()
    }
    fn config_fields(
        &mut self,
        spec: &[(&str, &str)],
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let mut rows = div().flex().flex_col().gap_3();
        for &(key, label) in spec {
            if key == "ch341PortBindings" || key == "powerMeterWavelengthNm" {
                continue;
            }
            let val = self.cache.value["configuration"][key].clone();
            if val.is_null() {
                continue;
            }
            rows = rows.child(self.field(format!("cfg.{key}"), label, val, window, cx));
            if key == "customCurrentPoints" {
                rows = rows.child(
                    div()
                        .text_xs()
                        .child("格式：电流 A,停留秒；多个电流点用分号分隔"),
                );
            }
        }
        rows.into_any_element()
    }
    fn load_data(&mut self, cx: &mut Context<Self>) {
        let mut p = json!({"source":"database","page":self.data_page,"pageSize":50});
        for k in [
            "rootPath",
            "search",
            "sn",
            "station",
            "status",
            "sortBy",
            "sortOrder",
        ] {
            if let Some(v) = self.draft.get(&format!("data.{k}")) {
                p[k] = v.clone();
            }
        }
        if p["rootPath"].as_str().is_some_and(|s| !s.is_empty()) {
            p["source"] = json!("excel");
        }
        self.dispatch("dataBrowser.list", p, cx);
    }
    fn profile_action(
        &mut self,
        method: &'static str,
        profile: Option<Value>,
        cx: &mut Context<Self>,
    ) {
        let cfg = self.config();
        let library = &self.cache.value["devices"]["powerMeter"]["coefficientLibrary"];
        let mut p =
            json!({"driverKind":library["driverKind"],"serialNumber":library["serialNumber"]});
        if let Some(profile) = profile {
            p["expectedVersion"] = profile["versions"]
                .as_array()
                .and_then(|a| a.last())
                .map(|v| v["version"].clone())
                .unwrap_or(Value::Null);
            p["profileId"] = profile["id"].clone();
            if let Some(version) = profile["version"].as_u64() {
                p["restoreVersion"] = json!(version);
            }
        } else {
            p["profileId"] = library["selectedProfileId"].clone();
            for (key, _) in fields::PROFILE {
                p[key] = self.value(
                    &format!("profile.{key}"),
                    if *key == "softwareGain" {
                        cfg["softwareGain"].clone()
                    } else if *key == "wavelengthNm" {
                        cfg["powerMeterWavelengthNm"].clone()
                    } else {
                        json!("")
                    },
                );
            }
            p["expectedVersion"] = library["selectedVersion"].clone();
        }
        self.dispatch(method, p, cx);
    }
    fn report_action(&mut self, method: &'static str, cx: &mut Context<Self>) {
        let kind = self
            .value("report.reportType", json!("spectrum"))
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
        let mut p = report::defaults(&self.report, &kind, current);
        for (key, val) in &self.draft {
            if let Some(k) = key.strip_prefix("report.") {
                if let Some(field) = k.strip_prefix("include.") {
                    p["fields"][field]["include"] = val.clone();
                } else if let Some(field) = k.strip_prefix("field.") {
                    p["fields"][field]["value"] = val.clone();
                } else if let Some(axis) = k.strip_prefix("axis.") {
                    p["spectrumAxis"][axis] = val.clone();
                } else {
                    p[k] = val.clone();
                }
            }
        }
        if let Some(custom) = p["customFields"].as_array_mut() {
            for f in custom {
                let key = f["key"].as_str().unwrap_or("").to_string();
                if let Some(label) = self.draft.get(&format!("report.customLabel.{key}")) {
                    f["label"] = label.clone();
                }
                if let Some(unit) = self.draft.get(&format!("report.customUnit.{key}")) {
                    f["unit"] = unit.clone();
                }
            }
        }
        self.dispatch(method, p, cx);
    }
}
fn parse_currents(s: &str) -> Result<Value, String> {
    let mut rows = vec![];
    for row in s.split(';').map(str::trim).filter(|s| !s.is_empty()) {
        let vals = row
            .split(',')
            .map(str::trim)
            .map(str::parse::<f64>)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| "电流计划格式无效")?;
        if vals.len() != 2 || vals.iter().any(|v| !v.is_finite() || *v < 0.0) {
            return Err("每个电流点必须包含非负电流和停留秒数".into());
        }
        rows.push(json!({"currentA":vals[0],"dwellS":vals[1]}));
    }
    Ok(json!(rows))
}

impl Render for Workbench {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let licensed = self.demo || self.license["valid"] == true;
        let mut root = div()
            .id("workbench")
            .key_context("Workbench")
            .size_full()
            .flex()
            .flex_col()
            .bg(cx.theme().background)
            .text_color(cx.theme().foreground)
            .text_sm()
            .on_action(cx.listener(|this, _: &Quit, _, cx| this.begin_close(cx)))
            .on_key_down(cx.listener(|this, event: &KeyDownEvent, _, cx| {
                if event.keystroke.key == "escape" {
                    if this.coupling {
                        this.coupling = false;
                    } else if this.dialog.is_some() && !this.closing {
                        this.dialog = None;
                    }
                    cx.notify();
                }
            }));
        let header = div()
            .flex()
            .h(px(56.))
            .flex_shrink_0()
            .items_center()
            .justify_between()
            .px_4()
            .border_b_1()
            .border_color(cx.theme().border)
            .child(
                div()
                    .flex()
                    .gap_4()
                    .child(div().font_weight(FontWeight::MEDIUM).child("Power Test"))
                    .child(if self.demo {
                        "演示数据 · 不连接仪器".into()
                    } else {
                        self.cache.value["backend"]["mode"]
                            .as_str()
                            .unwrap_or("等待后端连接")
                            .to_string()
                    }),
            )
            .child(
                div()
                    .flex()
                    .gap_2()
                    .child(self.button("coupling", "功率耦合", self.active(), Action::Coupling, cx))
                    .child(
                        self.button(
                            "global-stop",
                            "急停",
                            self.cache.value.is_object(),
                            Action::Command("app.stopAll", json!({})),
                            cx,
                        )
                        .danger(),
                    )
                    .child(self.button("app-close", "关闭", true, Action::Close, cx)),
            );
        root = root.child(header);
        let status = if !self.error.is_empty() {
            format!("错误：{}", self.error)
        } else if let Some(method) = self.pending {
            format!("{}进行中", command_label(method))
        } else {
            self.notice.clone()
        };
        root = root.child(
            div()
                .id("status")
                .h(px(32.))
                .flex_shrink_0()
                .px_4()
                .py_1()
                .overflow_y_scroll()
                .text_color(if self.error.is_empty() {
                    cx.theme().muted_foreground
                } else {
                    rgb(0xf87171).into()
                })
                .child(status),
        );
        if licensed && self.cache.value.is_null() {
            root = root.child(
                div()
                    .flex_1()
                    .flex()
                    .items_center()
                    .justify_center()
                    .child("正在连接 Python 后端"),
            );
        } else if !licensed && self.cache.value.is_null() {
            let machine = text(&self.license["machineCode"]);
            let copy = machine.clone();
            root = root.child(
                div().flex_1().flex().items_center().justify_center().child(
                    div()
                        .w(px(560.))
                        .p_6()
                        .flex()
                        .flex_col()
                        .gap_4()
                        .border_1()
                        .border_color(cx.theme().border)
                        .rounded_lg()
                        .child(div().text_2xl().child("激活 Power Test"))
                        .child("导入与当前电脑匹配的离线许可证")
                        .child(format!("机器码：{machine}"))
                        .child(Button::new("copy-machine").label("复制机器码").on_click(
                            move |_, _, cx| {
                                cx.write_to_clipboard(ClipboardItem::new_string(copy.clone()))
                            },
                        ))
                        .child(
                            self.button("license-import", "导入许可证", true, Action::License, cx)
                                .primary(),
                        ),
                ),
            );
        } else {
            if !licensed {
                root = root.child(
                    div()
                        .px_4()
                        .py_2()
                        .text_color(rgb(0xf59e0b))
                        .child("许可证无效。新测试和输出修改不可用，安全停止仍然可用。"),
                );
            }
            let mut navigation = div()
                .w(px(176.))
                .flex_shrink_0()
                .flex()
                .flex_col()
                .gap_2()
                .p_3()
                .border_r_1()
                .border_color(cx.theme().border);
            for &(id, label) in NAV {
                let locked = self.cache.value["safety"]["workflowBusy"] == true
                    && !matches!(id, "automatic" | "manual")
                    || id == "manual" && self.cache.value["safety"]["controlOwner"] == "automatic";
                let button = self
                    .button(format!("nav-{id}"), label, !locked, Action::Page(id), cx)
                    .w_full();
                navigation = navigation.child(if self.page == id {
                    button.primary()
                } else {
                    button
                });
            }
            let body = if self.coupling {
                div()
                    .flex()
                    .flex_col()
                    .gap_4()
                    .child(self.metric(
                        "功率",
                        self.device_metric("powerMeter", "powerW", 4),
                        "W",
                        cx,
                    ))
                    .child(self.realtime(cx))
                    .into_any_element()
            } else {
                match self.page {
                    "automatic" => self.automatic_page(window, cx),
                    "manual" => self.manual_page(window, cx),
                    "pd" => self.pd_page(window, cx),
                    "data" => self.data_page(window, cx),
                    "shipping" => self.shipping_page(window, cx),
                    _ => self.settings_page(window, cx),
                }
            };
            root = root.child(
                div().flex().flex_1().min_h_0().child(navigation).child(
                    div()
                        .id("page-scroll")
                        .flex_1()
                        .min_w_0()
                        .overflow_y_scroll()
                        .p_4()
                        .child(body),
                ),
            );
        }
        for notice in self.cache.value["backend"]["notices"]
            .as_array()
            .into_iter()
            .flatten()
        {
            root = root.child(
                div()
                    .px_4()
                    .py_2()
                    .text_color(rgb(if notice["level"] == "error" {
                        0xf87171
                    } else {
                        0xf59e0b
                    }))
                    .child(format!(
                        "{}：{}",
                        text(&notice["title"]),
                        text(&notice["message"])
                    )),
            );
        }
        if self.cache.value["devices"]["spectrometer"]["saturated"] == true {
            root = root.child(
                div()
                    .px_4()
                    .py_2()
                    .text_color(rgb(0xf87171))
                    .child("光谱饱和：当前数据不计入测试记录，请调整积分时间"),
            );
        }
        if let Some(kind) = self.dialog {
            root = root.child(self.dialog_view(kind, window, cx));
        }
        root
    }
}
fn text(v: &Value) -> String {
    if v.is_null() {
        "—".into()
    } else {
        v.as_str()
            .map(str::to_string)
            .unwrap_or_else(|| v.to_string())
    }
}
gpui::actions!(arp, [Quit]);
fn main() {
    use fs2::FileExt;
    let demo = std::env::args().any(|a| a == "--demo")
        || std::env::var("ARP_GPUI_DEMO").as_deref() == Ok("1");
    let _instance = if demo {
        None
    } else {
        let directory = dirs::data_dir()
            .expect("应用数据目录不可用")
            .join("com.changguanghuaxin.arp");
        std::fs::create_dir_all(&directory).expect("创建应用数据目录失败");
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(directory.join("operator.lock"))
            .expect("创建单实例锁失败");
        if file.try_lock_exclusive().is_err() {
            rfd::MessageDialog::new()
                .set_title("Power Test")
                .set_description("Power Test 正在运行，请使用现有窗口。第二个硬件后端未启动。")
                .show();
            return;
        }
        Some(file)
    };
    let platform = gpui::platform::current_platform(false);
    let app = Application::with_platform(platform.clone())
        .with_assets(gpui::assets::Assets)
        .with_quit_mode(QuitMode::Explicit);
    let allow = Arc::new(AtomicBool::new(false));
    let (tx, rx) = mpsc::channel();
    #[cfg(unix)]
    {
        let signal_tx = tx.clone();
        ctrlc::set_handler(move || {
            let _ = signal_tx.send(Event::Quit);
        })
        .expect("注册安全退出信号失败");
    }
    let gate = allow.clone();
    let quit_tx = tx.clone();
    // Intercept Dock/OS quit as well as titlebar close. The OS gets permission
    // only after both shutdown and export draining are explicitly confirmed.
    platform.on_quit(Box::new(move || {
        if gate.load(Ordering::SeqCst) {
            true
        } else {
            let _ = quit_tx.send(Event::Quit);
            false
        }
    }));
    app.run(move |cx| {
        gpui::init(cx);
        cx.set_global(charts::ChartRegistry::default());
        apply_theme(preferred_theme(), None, cx);
        gpui::component::set_locale("zh-CN");
        cx.bind_keys([
            KeyBinding::new("cmd-q", Quit, Some("Workbench")),
            KeyBinding::new("ctrl-q", Quit, Some("Workbench")),
        ]);
        let window_size = std::env::var("ARP_GPUI_WINDOW_SIZE")
            .ok()
            .and_then(|s| {
                s.split_once('x')
                    .and_then(|(w, h)| Some((w.parse::<f32>().ok()?, h.parse::<f32>().ok()?)))
            })
            .unwrap_or((1440., 960.));
        let options = WindowOptions {
            window_bounds: Some(WindowBounds::Windowed(Bounds::centered(
                None,
                size(px(window_size.0), px(window_size.1)),
                cx,
            ))),
            window_min_size: Some(size(px(960.), px(640.))),
            titlebar: Some(TitlebarOptions {
                title: Some("Power Test".into()),
                ..Default::default()
            }),
            ..Default::default()
        };
        gpui::open_window(options, cx, move |window, cx| {
            cx.new(|cx| Workbench::new(window, cx, allow, tx, rx))
        })
        .expect("打开 Power Test 窗口失败");
        cx.activate(true);
    });
}
impl Workbench {
    fn batch_reports(&mut self, cx: &mut Context<Self>) {
        if self.pending.is_some() || self.demo {
            return;
        }
        let directory = self.value("report.outputDirectory", json!(""));
        if directory.as_str().is_none_or(|s| s.is_empty()) {
            self.error = "请选择批量报告输出目录".into();
            return;
        }
        let items = self.batch["items"].as_array().cloned().unwrap_or_default();
        let current = self
            .value("report.operatingCurrentA", json!(0))
            .as_f64()
            .unwrap_or(0.);
        let kind = self
            .value("report.reportType", json!("spectrum"))
            .as_str()
            .unwrap_or("spectrum")
            .to_string();
        let drafts = self.draft.clone();
        let (client, tx) = (self.client.clone(), self.tx.clone());
        self.pending = Some("shippingReport.batchGenerateItem");
        std::thread::spawn(move || {
            let mut results = vec![];
            for item in items {
                if item["status"] != "ready"
                    || !item["allowedReportTypes"]
                        .as_array()
                        .is_some_and(|a| a.iter().any(|v| v == kind.as_str()))
                    || !item["currents"].as_array().is_some_and(|a| {
                        a.iter()
                            .any(|v| v.as_f64().is_some_and(|v| (v - current).abs() < 1e-9))
                    })
                {
                    results.push(json!({"source":item["sourcePath"],"status":"跳过","reason":"文件状态、报告类型或电流点不匹配"}));
                    continue;
                }
                let result = client
                    .command("shippingReport.inspect", json!({"path":item["sourcePath"]}))
                    .and_then(|inspection| {
                        let mut p = report::defaults(&inspection, &kind, current);
                        p["outputDirectory"] = directory.clone();
                        for (key, v) in &drafts {
                            if let Some(k) = key.strip_prefix("report.") {
                                if let Some(f) = k.strip_prefix("include.") {
                                    p["fields"][f]["include"] = v.clone();
                                } else if let Some(f) = k.strip_prefix("field.") {
                                    let measured = inspection["fieldDefinitions"][&kind]
                                        .as_array()
                                        .is_some_and(|a| {
                                            a.iter().any(|d| {
                                                d["key"] == f && d["measuredKey"].is_string()
                                            })
                                        });
                                    if !measured {
                                        p["fields"][f]["value"] = v.clone();
                                    }
                                } else if let Some(axis) = k.strip_prefix("axis.") {
                                    p["spectrumAxis"][axis] = v.clone();
                                } else if matches!(
                                    k,
                                    "productName"
                                        | "reportLanguage"
                                        | "customFields"
                                        | "encircledEnergyNa"
                                        | "legacyCompletionConfirmed"
                                ) {
                                    p[k] = v.clone();
                                }
                            }
                        }
                        client.command("shippingReport.batchGenerateItem", p)
                    });
                results.push(match result {Ok(r)=>json!({"source":item["sourcePath"],"status":"已完成","outputPath":r["outputPath"]}),Err(e)=>json!({"source":item["sourcePath"],"status":"失败","reason":e})});
            }
            let message = results
                .iter()
                .map(|r| {
                    format!(
                        "{}：{} {}",
                        text(&r["source"]),
                        text(&r["status"]),
                        r.get("reason")
                            .or(r.get("outputPath"))
                            .map(text)
                            .unwrap_or_default()
                    )
                })
                .collect::<Vec<_>>()
                .join("\n");
            let _ = tx.send(Event::Command(
                "shippingReport.batchGenerateItem",
                Ok(json!({"path":message})),
            ));
        });
        cx.notify();
    }
}
fn command_label(method: &str) -> &str {
    match method {
        "app.configure" => "保存设置",
        "device.refresh" => "识别设备",
        "automatic.start" => "启动测试",
        "automatic.end" | "powerSupply.safeRampDown" | "app.stopAll" => "安全停止",
        "manual.saveExcel" => "保存 Excel",
        m if m.starts_with("shippingReport.") => "报告任务",
        m if m.starts_with("dataBrowser.") => "读取历史数据",
        m if m.starts_with("powerMeter.profile.") => "保存系数方案",
        _ => "设备操作",
    }
}
impl Workbench {
    fn preset_action(&mut self, action: &str, cx: &mut Context<Self>) {
        let config = self.config();
        let mut presets = config["testScriptPresets"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let mut selected = config["selectedScriptPresetId"].clone();
        if action == "save" {
            let name = self
                .value("preset.name", json!(""))
                .as_str()
                .unwrap_or("")
                .trim()
                .to_string();
            if name.is_empty() {
                self.error = "请填写脚本名称".into();
                return;
            }
            let mut parameters = json!({});
            for key in [
                "selectedScriptId",
                "initialCurrentA",
                "targetCurrentA",
                "currentStepA",
                "currentPlanMode",
                "customCurrentPoints",
                "pointTimeoutS",
                "rampDownStepA",
                "rampDownIntervalS",
            ] {
                parameters[key] = config[key].clone();
            }
            let id = format!(
                "preset-{}",
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            );
            selected = json!(id);
            presets.push(json!({"id":id,"name":name,"parameters":parameters}));
        } else {
            if selected.as_str().is_none_or(|s| s.is_empty()) {
                self.error = "请先选择自定义脚本".into();
                return;
            }
            presets.retain(|p| p["id"] != selected);
            selected = json!("");
        }
        self.dispatch(
            "app.configure",
            json!({"testScriptPresets":presets,"selectedScriptPresetId":selected}),
            cx,
        );
    }
}
fn apply_theme(mode: ThemeMode, window: Option<&mut Window>, cx: &mut App) {
    Theme::change(mode, window, cx);
    let theme = Theme::global_mut(cx);
    theme.colors.background = rgb(if mode.is_dark() { 0x0f1112 } else { 0xf4f6f8 }).into();
    theme.colors.primary = rgb(if mode.is_dark() { 0xff7a1a } else { 0xb65310 }).into();
    theme.colors.primary_hover = theme.colors.primary;
    theme.colors.primary_active = theme.colors.primary;
    theme.colors.primary_foreground = rgb(if mode.is_dark() { 0x121212 } else { 0xffffff }).into();
}
fn status_label(value: &Value) -> String {
    match value.as_str().unwrap_or("") {
        "recording" => "记录中",
        "queued" => "等待导出",
        "exporting" => "导出中",
        "saved" | "completed" => "已完成",
        "failed" => "失败",
        "none" => "无导出任务",
        _ => return text(value),
    }
    .into()
}

fn appearance_path() -> Option<PathBuf> {
    Some(
        dirs::data_dir()?
            .join("com.changguanghuaxin.arp")
            .join("gpui-appearance.json"),
    )
}
fn preferred_theme() -> ThemeMode {
    appearance_path()
        .and_then(|p| std::fs::read(p).ok())
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .filter(|v| v["theme"] == "light")
        .map(|_| ThemeMode::Light)
        .unwrap_or(ThemeMode::Dark)
}
fn save_theme(mode: ThemeMode) -> Result<(), String> {
    let path = appearance_path().ok_or("应用数据目录不可用")?;
    std::fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    let temporary = path.with_extension("json.tmp");
    std::fs::write(
        &temporary,
        serde_json::to_vec(&json!({"theme":if mode.is_dark(){"dark"}else{"light"}})).unwrap(),
    )
    .map_err(|e| e.to_string())?;
    std::fs::rename(temporary, path).map_err(|e| format!("保存界面设置失败：{e}"))
}
