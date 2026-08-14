use serde_json::{json, Value};

const LIVE_SNAPSHOT_INTERVAL_MS: u64 = 200;
const PD_SNAPSHOT_INTERVAL_MS: u64 = 500;
const SHIPPING_SNAPSHOT_INTERVAL_MS: u64 = 500;
use std::collections::HashMap;
use std::env;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Manager, State, Webview};

#[cfg(windows)]
use windows_core::PCWSTR;

#[cfg(not(windows))]
use tauri::ipc::Channel;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct PythonBridge {
    child: Mutex<Child>,
    stdin: Mutex<BufWriter<ChildStdin>>,
    pending: Arc<Mutex<HashMap<String, Sender<Result<Value, PythonBridgeError>>>>>,
    next_request_id: AtomicU64,
    shutdown_confirmed: AtomicBool,
    force_exit_requested: AtomicBool,
}

#[derive(Clone, Debug)]
enum PythonBridgeError {
    Backend(String),
    Transport(String),
}

impl PythonBridgeError {
    fn message(self) -> String {
        match self {
            Self::Backend(message) | Self::Transport(message) => message,
        }
    }

    fn is_transport(&self) -> bool {
        matches!(self, Self::Transport(_))
    }
}

impl PythonBridge {
    fn allow_forced_exit(&self) {
        self.force_exit_requested.store(true, Ordering::SeqCst);
    }

    fn fail_pending(
        pending: &Arc<Mutex<HashMap<String, Sender<Result<Value, PythonBridgeError>>>>>,
        error: PythonBridgeError,
    ) {
        let senders = pending
            .lock()
            .map(|mut requests| {
                requests
                    .drain()
                    .map(|(_, sender)| sender)
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        for sender in senders {
            let _ = sender.send(Err(error.clone()));
        }
    }

    fn start_response_reader(
        stdout: ChildStdout,
        pending: Arc<Mutex<HashMap<String, Sender<Result<Value, PythonBridgeError>>>>>,
    ) -> Result<(), String> {
        thread::Builder::new()
            .name("python-bridge-reader".to_string())
            .spawn(move || {
                let mut stdout = BufReader::new(stdout);
                loop {
                    let mut line = String::new();
                    match stdout.read_line(&mut line) {
                        Ok(0) => {
                            Self::fail_pending(
                                &pending,
                                PythonBridgeError::Transport(
                                    "Python 后端在返回响应前关闭了输出通道".to_string(),
                                ),
                            );
                            break;
                        }
                        Ok(_) => {}
                        Err(error) => {
                            Self::fail_pending(
                                &pending,
                                PythonBridgeError::Transport(format!(
                                    "无法读取 Python 响应：{error}"
                                )),
                            );
                            break;
                        }
                    }

                    let response: Value = match serde_json::from_str(&line) {
                        Ok(response) => response,
                        Err(error) => {
                            Self::fail_pending(
                                &pending,
                                PythonBridgeError::Transport(format!(
                                    "Python 响应不是有效 JSON：{error}"
                                )),
                            );
                            break;
                        }
                    };
                    let Some(request_id) = response.get("id").and_then(Value::as_str) else {
                        Self::fail_pending(
                            &pending,
                            PythonBridgeError::Transport("Python 响应缺少请求编号".to_string()),
                        );
                        break;
                    };
                    let result = if response.get("ok").and_then(Value::as_bool) == Some(true) {
                        response.get("result").cloned().ok_or_else(|| {
                            PythonBridgeError::Transport("Python 响应缺少 result".to_string())
                        })
                    } else {
                        let message = response
                            .pointer("/error/message")
                            .and_then(Value::as_str)
                            .unwrap_or("Python 后端返回未知错误");
                        Err(PythonBridgeError::Backend(message.to_string()))
                    };
                    let sender = pending
                        .lock()
                        .ok()
                        .and_then(|mut requests| requests.remove(request_id));
                    if let Some(sender) = sender {
                        let _ = sender.send(result);
                    }
                }
            })
            .map(|_| ())
            .map_err(|error| format!("无法启动 Python 响应线程：{error}"))
    }

    fn push_python_candidate(candidates: &mut Vec<PathBuf>, candidate: PathBuf) {
        if !candidates.iter().any(|existing| existing == &candidate) {
            candidates.push(candidate);
        }
    }

    fn conda_environment_python(conda_root: &Path) -> PathBuf {
        conda_root
            .join("envs")
            .join("sth_eb314")
            .join(if cfg!(target_os = "windows") {
                "python.exe"
            } else {
                "bin/python"
            })
    }

    fn python_executables() -> Vec<PathBuf> {
        let mut candidates = Vec::new();

        if let Ok(configured) = env::var("ARP_PYTHON_EXECUTABLE") {
            Self::push_python_candidate(&mut candidates, PathBuf::from(configured));
        }

        if let Ok(current_exe) = env::current_exe() {
            if let Some(directory) = current_exe.parent() {
                let bundled = directory.join(if cfg!(target_os = "windows") {
                    "arp-python.exe"
                } else {
                    "arp-python"
                });
                if bundled.is_file() {
                    Self::push_python_candidate(&mut candidates, bundled);
                }
            }
        }

        if let Ok(prefix) = env::var("CONDA_PREFIX") {
            let candidate = PathBuf::from(prefix).join(if cfg!(target_os = "windows") {
                "python.exe"
            } else {
                "bin/python"
            });
            if candidate.is_file() {
                Self::push_python_candidate(&mut candidates, candidate);
            }
        }

        if let Ok(conda_exe) = env::var("CONDA_EXE") {
            let conda_exe = PathBuf::from(conda_exe);
            if let Some(conda_root) = conda_exe.parent().and_then(Path::parent) {
                let candidate = Self::conda_environment_python(conda_root);
                if candidate.is_file() {
                    Self::push_python_candidate(&mut candidates, candidate);
                }
            }
        }

        let home_key = if cfg!(target_os = "windows") {
            "USERPROFILE"
        } else {
            "HOME"
        };
        if let Ok(home) = env::var(home_key) {
            let candidate = PathBuf::from(&home)
                .join("miniconda3")
                .join("envs")
                .join("sth_eb314")
                .join(if cfg!(target_os = "windows") {
                    "python.exe"
                } else {
                    "bin/python"
                });
            if candidate.is_file() {
                Self::push_python_candidate(&mut candidates, candidate);
            }

            let candidate = PathBuf::from(&home)
                .join("anaconda3")
                .join("envs")
                .join("sth_eb314")
                .join(if cfg!(target_os = "windows") {
                    "python.exe"
                } else {
                    "bin/python"
                });
            if candidate.is_file() {
                Self::push_python_candidate(&mut candidates, candidate);
            }
        }

        Self::push_python_candidate(
            &mut candidates,
            PathBuf::from(if cfg!(target_os = "windows") {
                "python"
            } else {
                "python3"
            }),
        );
        candidates
    }

    fn spawn_with(python: &Path) -> Result<(Self, String), String> {
        let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        let is_bundled_sidecar = python
            .file_stem()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value == "arp-python");
        let mut command = Command::new(python);
        if !is_bundled_sidecar {
            command.args(["-u", "-m", "tauri_bridge"]);
        }
        #[cfg(target_os = "windows")]
        command.creation_flags(CREATE_NO_WINDOW);
        if repo_root.is_dir() {
            command.current_dir(&repo_root);
        }
        let mut child = command
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("QT_QPA_PLATFORM", "offscreen")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|error| format!("无法启动 Python 后端（{}）：{error}", python.display()))?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "无法打开 Python 后端输入通道".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "无法打开 Python 后端输出通道".to_string())?;
        let pending = Arc::new(Mutex::new(HashMap::new()));
        Self::start_response_reader(stdout, Arc::clone(&pending))?;
        let bridge = Self {
            child: Mutex::new(child),
            stdin: Mutex::new(BufWriter::new(stdin)),
            pending,
            next_request_id: AtomicU64::new(1),
            shutdown_confirmed: AtomicBool::new(false),
            force_exit_requested: AtomicBool::new(false),
        };
        let ping = bridge
            .request("system.ping", json!({}))
            .map_err(PythonBridgeError::message)?;
        let mode = ping
            .get("mode")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        Ok((bridge, mode))
    }

    fn spawn() -> Result<Self, String> {
        let mut errors = Vec::new();
        let mut read_only_fallback = None;
        for python in Self::python_executables() {
            match Self::spawn_with(&python) {
                Ok((bridge, mode)) if mode == "active" => return Ok(bridge),
                Ok((bridge, mode)) if mode == "read_only" => {
                    errors.push(format!("Python 后端仅支持只读模式（{}）", python.display()));
                    if read_only_fallback.is_none() {
                        read_only_fallback = Some(bridge);
                    }
                }
                Ok((_bridge, mode)) => errors.push(format!(
                    "Python 后端返回未知模式 {mode}（{}）",
                    python.display()
                )),
                Err(error) => errors.push(error),
            }
        }

        if let Some(bridge) = read_only_fallback {
            return Ok(bridge);
        }

        Err(format!(
            "未找到可用的控制模式 Python 后端：{}",
            errors.join("；")
        ))
    }

    fn request(&self, method: &str, params: Value) -> Result<Value, PythonBridgeError> {
        self.request_queued(method, params, Instant::now())
    }

    fn request_queued(
        &self,
        method: &str,
        params: Value,
        queued_at: Instant,
    ) -> Result<Value, PythonBridgeError> {
        let status = self
            .child
            .lock()
            .map_err(|_| PythonBridgeError::Transport("Python 进程状态锁不可用".to_string()))?
            .try_wait()
            .map_err(|error| {
                PythonBridgeError::Transport(format!("无法读取 Python 后端状态：{error}"))
            })?;
        if let Some(status) = status {
            eprintln!(
                "Python bridge timing | method={method} | queue_ms={:.3} | execution_ms=0.000",
                queued_at.elapsed().as_secs_f64() * 1000.0,
            );
            return Err(PythonBridgeError::Transport(format!(
                "Python 后端已退出：{status}"
            )));
        }

        let request_id = format!(
            "tauri-{}",
            self.next_request_id.fetch_add(1, Ordering::SeqCst)
        );
        let request = json!({
            "v": 2,
            "id": request_id,
            "method": method,
            "params": params,
        });
        let payload = serde_json::to_vec(&request).map_err(|error| {
            PythonBridgeError::Transport(format!("无法编码 Python 请求：{error}"))
        })?;
        let (sender, receiver) = mpsc::channel();
        self.pending
            .lock()
            .map_err(|_| PythonBridgeError::Transport("Python 请求表锁不可用".to_string()))?
            .insert(request_id.clone(), sender);
        let write_result = self
            .stdin
            .lock()
            .map_err(|_| PythonBridgeError::Transport("Python 输入通道锁不可用".to_string()))
            .and_then(|mut stdin| {
                stdin
                    .write_all(&payload)
                    .and_then(|_| stdin.write_all(b"\n"))
                    .and_then(|_| stdin.flush())
                    .map_err(|error| {
                        PythonBridgeError::Transport(format!("无法发送 Python 请求：{error}"))
                    })
            });
        if let Err(error) = write_result {
            if let Ok(mut pending) = self.pending.lock() {
                pending.remove(&request_id);
            }
            return Err(error);
        }

        let execution_started = Instant::now();
        let queue_ms = execution_started.duration_since(queued_at).as_secs_f64() * 1000.0;
        let result = receiver.recv().unwrap_or_else(|_| {
            Err(PythonBridgeError::Transport(
                "Python 响应线程意外退出".to_string(),
            ))
        });
        let execution_ms = execution_started.elapsed().as_secs_f64() * 1000.0;
        if method != "app.snapshot" || queue_ms >= 50.0 || execution_ms >= 50.0 {
            eprintln!(
                "Python bridge timing | id={request_id} | method={method} | queue_ms={queue_ms:.3} | execution_ms={execution_ms:.3}"
            );
        }
        let result = result?;
        validate_columnar_spectrum(&result)?;
        if method == "app.shutdown"
            && result.get("shutdownConfirmed").and_then(Value::as_bool) == Some(true)
        {
            self.shutdown_confirmed.store(true, Ordering::SeqCst);
        }
        Ok(result)
    }
}

impl Drop for PythonBridge {
    fn drop(&mut self) {
        if !self.shutdown_confirmed.load(Ordering::SeqCst)
            && !self.force_exit_requested.load(Ordering::SeqCst)
        {
            let _ = self.request("app.shutdown", json!({}));
        }
        if let Ok(mut child) = self.child.lock() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[derive(Default)]
struct BridgeState {
    bridge: Arc<Mutex<Option<Arc<PythonBridge>>>>,
    stream_generation: Arc<AtomicU64>,
}

fn request_shared_bridge(
    shared_bridge: &Arc<Mutex<Option<Arc<PythonBridge>>>>,
    method: &str,
    params: Value,
) -> Result<Value, String> {
    let queued_at = Instant::now();
    let bridge = {
        let mut bridge_guard = shared_bridge
            .lock()
            .map_err(|_| "Python 后端状态锁不可用".to_string())?;
        if bridge_guard.is_none() {
            *bridge_guard = Some(Arc::new(PythonBridge::spawn()?));
        }
        Arc::clone(bridge_guard.as_ref().expect("bridge was initialized"))
    };

    let result = bridge.request_queued(method, params, queued_at);
    let reset_bridge = should_reset_bridge(&result);
    if reset_bridge {
        bridge.allow_forced_exit();
        if let Ok(mut bridge_guard) = shared_bridge.lock() {
            if bridge_guard
                .as_ref()
                .is_some_and(|current| Arc::ptr_eq(current, &bridge))
            {
                *bridge_guard = None;
            }
        }
    }
    result.map_err(PythonBridgeError::message)
}

fn should_reset_bridge(result: &Result<Value, PythonBridgeError>) -> bool {
    result.as_ref().is_err_and(PythonBridgeError::is_transport)
}

fn validate_columnar_spectrum(snapshot: &Value) -> Result<(), PythonBridgeError> {
    let Some(spectrum) = snapshot.pointer("/measurements/spectrum") else {
        return Ok(());
    };
    let Some(spectrum) = spectrum.as_object() else {
        return Err(PythonBridgeError::Transport(
            "Python 快照中的 measurements.spectrum 必须是列式对象".to_string(),
        ));
    };
    for key in spectrum.keys() {
        if !matches!(key.as_str(), "wavelengthNm" | "intensity" | "gridRevision") {
            return Err(PythonBridgeError::Transport(
                "Python 快照中的 measurements.spectrum 包含未知字段".to_string(),
            ));
        }
    }
    let Some(intensities) = spectrum.get("intensity").and_then(Value::as_array) else {
        return Err(PythonBridgeError::Transport(
            "Python 快照中的 measurements.spectrum.intensity 必须是数组".to_string(),
        ));
    };
    if intensities.iter().any(|value| !value.is_number()) {
        return Err(PythonBridgeError::Transport(
            "Python 快照中的 measurements.spectrum 数组只能包含数值".to_string(),
        ));
    }
    match spectrum.get("wavelengthNm") {
        Some(wavelengths) => {
            let Some(wavelengths) = wavelengths.as_array() else {
                return Err(PythonBridgeError::Transport(
                    "Python 快照中的 measurements.spectrum.wavelengthNm 必须是数组".to_string(),
                ));
            };
            if wavelengths.len() != intensities.len() {
                return Err(PythonBridgeError::Transport(
                    "Python 快照中的 measurements.spectrum 两轴长度不一致".to_string(),
                ));
            }
            if wavelengths.iter().any(|value| !value.is_number()) {
                return Err(PythonBridgeError::Transport(
                    "Python 快照中的 measurements.spectrum 数组只能包含数值".to_string(),
                ));
            }
        }
        None => {
            // Grid-cached frames omit the axis and must carry a gridRevision
            // so the frontend can verify its cached grid still applies.
            if spectrum
                .get("gridRevision")
                .and_then(Value::as_u64)
                .is_none()
            {
                return Err(PythonBridgeError::Transport(
                    "省略 wavelengthNm 时必须携带整数 gridRevision".to_string(),
                ));
            }
        }
    }
    if let Some(grid_revision) = spectrum.get("gridRevision") {
        if !grid_revision.is_u64() {
            return Err(PythonBridgeError::Transport(
                "Python 快照中的 measurements.spectrum.gridRevision 必须是整数".to_string(),
            ));
        }
    }
    Ok(())
}

fn snapshot_interval_ms(view: &str) -> u64 {
    match view {
        "pd" => PD_SNAPSHOT_INTERVAL_MS,
        "shipping" => SHIPPING_SNAPSHOT_INTERVAL_MS,
        _ => LIVE_SNAPSHOT_INTERVAL_MS,
    }
}

fn update_stream_cursor(
    snapshot: &Value,
    series: &str,
    full_pointer: &str,
    cursors: &mut serde_json::Map<String, Value>,
) {
    let patch_pointer = format!("/seriesPatches/{series}/points");
    let latest = snapshot
        .pointer(&patch_pointer)
        .or_else(|| snapshot.pointer(full_pointer))
        .and_then(Value::as_array)
        .and_then(|points| points.last())
        .and_then(|point| point.get("elapsedS"))
        .and_then(Value::as_f64);
    if let Some(value) = latest {
        cursors.insert(series.to_string(), json!(value));
    }
}

fn snapshot_state_changed(previous: &Value, current: &Value) -> bool {
    let Some(previous_root) = previous.as_object() else {
        return true;
    };
    let Some(current_root) = current.as_object() else {
        return true;
    };
    // Transport-only keys never count as a state change; the previous
    // implementation built a fingerprint Value with the same exclusions,
    // this borrows both trees instead of cloning either.
    const IGNORED: [&str; 3] = ["capturedAt", "measurements", "seriesPatches"];
    for (key, previous_value) in previous_root {
        if IGNORED.contains(&key.as_str()) {
            continue;
        }
        let Some(current_value) = current_root.get(key) else {
            return true;
        };
        if key == "pd" {
            if pd_state_changed(previous_value, current_value) {
                return true;
            }
        } else if previous_value != current_value {
            return true;
        }
    }
    for key in current_root.keys() {
        if IGNORED.contains(&key.as_str()) {
            continue;
        }
        if !previous_root.contains_key(key) {
            return true;
        }
    }
    false
}

fn pd_state_changed(previous: &Value, current: &Value) -> bool {
    let (Some(previous), Some(current)) = (previous.as_object(), current.as_object()) else {
        return true;
    };
    for (key, previous_value) in previous {
        if key == "points" {
            continue;
        }
        if current.get(key) != Some(previous_value) {
            return true;
        }
    }
    for key in current.keys() {
        if key == "points" {
            continue;
        }
        if !previous.contains_key(key) {
            return true;
        }
    }
    false
}

fn stream_envelope(subscription_id: &str, message: Value) -> Value {
    json!({
        "__arpSnapshotStream": subscription_id,
        "message": message,
    })
}

struct StreamSender {
    subscription_id: String,
    webview: Webview,
    #[cfg(not(windows))]
    on_event: Channel<Value>,
}

impl StreamSender {
    #[cfg(windows)]
    fn send(&self, message: Value) -> Result<(), String> {
        let payload = serde_json::to_string(&stream_envelope(&self.subscription_id, message))
            .map_err(|error| format!("无法编码 WebView2 实时消息：{error}"))?;
        self.webview
            .with_webview(move |platform_webview| {
                let wide_payload = payload
                    .encode_utf16()
                    .chain(std::iter::once(0))
                    .collect::<Vec<_>>();
                let result = unsafe {
                    platform_webview
                        .controller()
                        .CoreWebView2()
                        .and_then(|core| core.PostWebMessageAsJson(PCWSTR(wide_payload.as_ptr())))
                };
                if let Err(error) = result {
                    eprintln!("无法发送 WebView2 实时消息：{error}");
                }
            })
            .map_err(|error| format!("无法调度 WebView2 实时消息：{error}"))
    }

    #[cfg(not(windows))]
    fn send(&self, message: Value) -> Result<(), String> {
        self.on_event
            .send(stream_envelope(&self.subscription_id, message))
            .map_err(|error| error.to_string())
    }
}

#[tauri::command]
async fn bridge_snapshot(state: State<'_, BridgeState>) -> Result<Value, String> {
    bridge_request("app.snapshot".to_string(), json!({}), state).await
}

#[tauri::command]
async fn bridge_request(
    method: String,
    params: Value,
    state: State<'_, BridgeState>,
) -> Result<Value, String> {
    let shared_bridge = Arc::clone(&state.bridge);
    tauri::async_runtime::spawn_blocking(move || {
        request_shared_bridge(&shared_bridge, &method, params)
    })
    .await
    .map_err(|error| format!("Python 后端任务异常结束：{error}"))?
}

fn start_bridge_stream(view: String, sender: StreamSender, state: &BridgeState) -> u64 {
    let generation = state.stream_generation.fetch_add(1, Ordering::SeqCst) + 1;
    let active_generation = Arc::clone(&state.stream_generation);
    let shared_bridge = Arc::clone(&state.bridge);
    let interval = snapshot_interval_ms(&view);

    tauri::async_runtime::spawn_blocking(move || {
        let mut since: Option<Value> = None;
        let mut cursors = serde_json::Map::new();
        let mut last_snapshot: Option<Value> = None;
        while active_generation.load(Ordering::SeqCst) == generation {
            let mut params = serde_json::Map::new();
            params.insert("view".to_string(), json!(view));
            if let Some(revisions) = since.clone() {
                params.insert("since".to_string(), revisions);
            }
            if !cursors.is_empty() {
                params.insert("cursors".to_string(), Value::Object(cursors.clone()));
            }

            match request_shared_bridge(&shared_bridge, "app.snapshot", Value::Object(params)) {
                Ok(snapshot) => {
                    since = snapshot.get("seriesRevisions").cloned();
                    update_stream_cursor(&snapshot, "power", "/measurements/power", &mut cursors);
                    update_stream_cursor(&snapshot, "pd", "/pd/points", &mut cursors);
                    let changed = last_snapshot
                        .as_ref()
                        .map_or(true, |previous| snapshot_state_changed(previous, &snapshot));
                    if !changed {
                        std::thread::sleep(Duration::from_millis(interval));
                        continue;
                    }
                    last_snapshot = Some(snapshot.clone());
                    if sender.send(json!({ "snapshot": snapshot })).is_err() {
                        break;
                    }
                }
                Err(error) => {
                    let _ = sender.send(json!({ "error": error }));
                    break;
                }
            }
            std::thread::sleep(Duration::from_millis(interval));
        }
    });
    generation
}

#[cfg(windows)]
#[tauri::command]
async fn bridge_subscribe(
    view: String,
    subscription_id: String,
    webview: Webview,
    state: State<'_, BridgeState>,
) -> Result<u64, String> {
    Ok(start_bridge_stream(
        view,
        StreamSender {
            subscription_id,
            webview,
        },
        &state,
    ))
}

#[cfg(not(windows))]
#[tauri::command]
async fn bridge_subscribe(
    view: String,
    subscription_id: String,
    on_event: Channel<Value>,
    webview: Webview,
    state: State<'_, BridgeState>,
) -> Result<u64, String> {
    Ok(start_bridge_stream(
        view,
        StreamSender {
            subscription_id,
            webview,
            on_event,
        },
        &state,
    ))
}

#[tauri::command]
fn bridge_unsubscribe(generation: u64, state: State<'_, BridgeState>) {
    let _ = state.stream_generation.compare_exchange(
        generation,
        generation + 1,
        Ordering::SeqCst,
        Ordering::SeqCst,
    );
}

#[tauri::command]
async fn bridge_disconnect(state: State<'_, BridgeState>) -> Result<(), String> {
    state.stream_generation.fetch_add(1, Ordering::SeqCst);
    let shared_bridge = Arc::clone(&state.bridge);
    tauri::async_runtime::spawn_blocking(move || {
        let mut bridge_guard = shared_bridge
            .lock()
            .map_err(|_| "Python 后端状态锁不可用".to_string())?;
        *bridge_guard = None;
        Ok(())
    })
    .await
    .map_err(|error| format!("Python 后端断开任务异常结束：{error}"))?
}

#[tauri::command]
async fn bridge_force_disconnect(state: State<'_, BridgeState>) -> Result<(), String> {
    state.stream_generation.fetch_add(1, Ordering::SeqCst);
    let shared_bridge = Arc::clone(&state.bridge);
    tauri::async_runtime::spawn_blocking(move || {
        let mut bridge_guard = shared_bridge
            .lock()
            .map_err(|_| "Python 后端状态锁不可用".to_string())?;
        if let Some(bridge) = bridge_guard.as_mut() {
            bridge.allow_forced_exit();
        }
        *bridge_guard = None;
        Ok(())
    })
    .await
    .map_err(|error| format!("Python 后端强制断开任务异常结束：{error}"))?
}

#[tauri::command]
fn open_directory(path: String) -> Result<(), String> {
    let trimmed = path.trim();
    if trimmed.is_empty() {
        return Err("输出文件夹不能为空。".to_string());
    }

    let directory = PathBuf::from(trimmed);
    if !directory.is_dir() {
        return Err(format!("输出文件夹不存在：{}", directory.display()));
    }

    #[cfg(target_os = "windows")]
    let mut command = Command::new("explorer.exe");
    #[cfg(target_os = "macos")]
    let mut command = Command::new("open");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = Command::new("xdg-open");

    command.arg(&directory);
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("无法打开输出文件夹：{error}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BridgeState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                window
                    .set_zoom(1.0)
                    .map_err(|error| format!("无法初始化 WebView2 页面缩放：{error}"))?;
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bridge_snapshot,
            bridge_request,
            bridge_subscribe,
            bridge_unsubscribe,
            bridge_disconnect,
            bridge_force_disconnect,
            open_directory
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::{
        should_reset_bridge, snapshot_interval_ms, snapshot_state_changed, stream_envelope,
        validate_columnar_spectrum, PythonBridge, PythonBridgeError,
    };

    #[test]
    fn backend_action_errors_do_not_restart_the_python_process() {
        let backend_error = Err(PythonBridgeError::Backend("invalid settings".to_string()));
        let transport_error = Err(PythonBridgeError::Transport("broken pipe".to_string()));

        assert!(!should_reset_bridge(&backend_error));
        assert!(should_reset_bridge(&transport_error));
    }

    #[test]
    fn snapshot_state_changed_ignores_transport_only_changes() {
        let first = serde_json::json!({
            "capturedAt": "2026-07-21T10:00:00Z",
            "seriesRevisions": { "power": 7, "stable": 1, "spectrum": 3, "pd": 0 },
            "measurements": { "power": [{ "elapsedS": 1.0, "powerW": 2.0 }] },
            "seriesPatches": { "power": { "startX": 1.0, "points": [] } },
            "pd": { "state": "idle", "points": [{ "elapsedS": 1.0, "value": 0.0 }] },
            "status": { "message": "ready" },
        });
        let second = serde_json::json!({
            "capturedAt": "2026-07-21T10:00:01Z",
            "seriesRevisions": { "power": 7, "stable": 1, "spectrum": 3, "pd": 0 },
            "measurements": {},
            "pd": { "state": "idle" },
            "status": { "message": "ready" },
        });

        assert!(!snapshot_state_changed(&first, &second));
        assert!(first["measurements"].is_object());
        assert!(first["pd"]["points"].is_array());

        let mut changed = second.clone();
        changed["seriesRevisions"]["power"] = serde_json::json!(8);
        assert!(snapshot_state_changed(&first, &changed));

        let mut pd_points_changed = second;
        pd_points_changed["pd"]["points"] = serde_json::json!([{ "elapsedS": 2.0, "value": 1.0 }]);
        assert!(!snapshot_state_changed(&first, &pd_points_changed));
    }

    #[test]
    fn stream_envelope_routes_messages_to_one_subscription() {
        let envelope = stream_envelope(
            "automatic-7",
            serde_json::json!({ "snapshot": { "capturedAt": "now" } }),
        );

        assert_eq!(envelope["__arpSnapshotStream"], "automatic-7");
        assert_eq!(envelope["message"]["snapshot"]["capturedAt"], "now");
    }

    #[test]
    fn snapshot_intervals_keep_shipping_and_pd_at_500_ms() {
        assert_eq!(snapshot_interval_ms("automatic"), 200);
        assert_eq!(snapshot_interval_ms("manual"), 200);
        assert_eq!(snapshot_interval_ms("pd"), 500);
        assert_eq!(snapshot_interval_ms("shipping"), 500);
    }

    #[test]
    fn columnar_spectrum_requires_two_equal_length_arrays() {
        let valid = serde_json::json!({
            "measurements": {
                "spectrum": {
                    "wavelengthNm": [940.0, 941.0],
                    "intensity": [0.5, 0.6],
                }
            }
        });
        let missing = serde_json::json!({ "measurements": {} });
        let legacy_rows = serde_json::json!({
            "measurements": {
                "spectrum": [{ "wavelengthNm": 940.0, "intensity": 0.5 }]
            }
        });
        let mismatched = serde_json::json!({
            "measurements": {
                "spectrum": {
                    "wavelengthNm": [940.0, 941.0],
                    "intensity": [0.5],
                }
            }
        });
        let non_numeric = serde_json::json!({
            "measurements": {
                "spectrum": {
                    "wavelengthNm": [940.0],
                    "intensity": ["invalid"],
                }
            }
        });
        let grid_cached = serde_json::json!({
            "measurements": {
                "spectrum": {
                    "gridRevision": 3,
                    "intensity": [0.5, 0.6],
                }
            }
        });
        let grid_cached_without_revision = serde_json::json!({
            "measurements": {
                "spectrum": {
                    "intensity": [0.5, 0.6],
                }
            }
        });
        let grid_cached_mismatched = serde_json::json!({
            "measurements": {
                "spectrum": {
                    "gridRevision": 3,
                    "wavelengthNm": [940.0, 941.0, 942.0],
                    "intensity": [0.5, 0.6],
                }
            }
        });
        let unknown_key = serde_json::json!({
            "measurements": {
                "spectrum": {
                    "wavelengthNm": [940.0],
                    "intensity": [0.5],
                    "points": [],
                }
            }
        });

        assert!(validate_columnar_spectrum(&valid).is_ok());
        assert!(validate_columnar_spectrum(&missing).is_ok());
        assert!(validate_columnar_spectrum(&grid_cached).is_ok());
        assert!(validate_columnar_spectrum(&legacy_rows).is_err());
        assert!(validate_columnar_spectrum(&mismatched).is_err());
        assert!(validate_columnar_spectrum(&non_numeric).is_err());
        assert!(validate_columnar_spectrum(&grid_cached_without_revision).is_err());
        assert!(validate_columnar_spectrum(&grid_cached_mismatched).is_err());
        assert!(validate_columnar_spectrum(&unknown_key).is_err());
    }

    #[test]
    fn python_bridge_returns_snapshot() {
        let bridge = PythonBridge::spawn().expect("Python bridge should start");
        let snapshot = bridge
            .request("app.snapshot", serde_json::json!({}))
            .expect("snapshot request should succeed");

        assert_eq!(snapshot["backend"]["connected"], true);
        assert!(matches!(
            snapshot["backend"]["mode"].as_str(),
            Some("read_only" | "active")
        ));
    }
}
