mod license;
mod snapshot_stream;

use license::{license_get_status, license_install, LicenseState};
use serde_json::{json, Value};
use snapshot_stream::{run_snapshot_stream, wait_for_generation, StreamRequestError};

const LIVE_SNAPSHOT_INTERVAL_MS: u64 = 200;
const PD_SNAPSHOT_INTERVAL_MS: u64 = 500;
const SHIPPING_SNAPSHOT_INTERVAL_MS: u64 = 500;
const SNAPSHOT_REQUEST_TIMEOUT_SECS: u64 = 15;
const DEFAULT_REQUEST_TIMEOUT_SECS: u64 = 60;
const FILE_SCAN_REQUEST_TIMEOUT_SECS: u64 = 120;
const REPORT_REQUEST_TIMEOUT_SECS: u64 = 10 * 60;
const SHUTDOWN_REQUEST_TIMEOUT_SECS: u64 = 90;
#[cfg(any(windows, test))]
const WEBVIEW_SEND_TIMEOUT_SECS: u64 = 5;
use std::collections::HashMap;
use std::env;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager, State, Webview};

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
    Timeout(String),
    Transport(String),
}

impl PythonBridgeError {
    fn message(self) -> String {
        match self {
            Self::Backend(message) | Self::Timeout(message) | Self::Transport(message) => message,
        }
    }

    fn is_transport(&self) -> bool {
        matches!(self, Self::Transport(_))
    }
}

fn request_timeout(method: &str) -> Duration {
    let seconds = match method {
        "app.snapshot" => SNAPSHOT_REQUEST_TIMEOUT_SECS,
        "app.shutdown" => SHUTDOWN_REQUEST_TIMEOUT_SECS,
        "shippingReport.inspect" | "shippingReport.batchInspect" | "dataBrowser.list" | "dataBrowser.inspect" => FILE_SCAN_REQUEST_TIMEOUT_SECS,
        "shippingReport.preview"
        | "shippingReport.generate"
        | "shippingReport.batchGenerateItem" => REPORT_REQUEST_TIMEOUT_SECS,
        _ => DEFAULT_REQUEST_TIMEOUT_SECS,
    };
    Duration::from_secs(seconds)
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
        _queued_at: Instant,
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

        let timeout = request_timeout(method);
        let result = match receiver.recv_timeout(timeout) {
            Ok(result) => result,
            Err(RecvTimeoutError::Timeout) => {
                if let Ok(mut pending) = self.pending.lock() {
                    pending.remove(&request_id);
                }
                Err(PythonBridgeError::Timeout(format!(
                    "Python 后端请求超时：{method}（{} 秒）",
                    timeout.as_secs()
                )))
            }
            Err(RecvTimeoutError::Disconnected) => Err(PythonBridgeError::Transport(
                "Python 响应线程意外退出".to_string(),
            )),
        };
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

fn bridge_is_active(state: &BridgeState) -> bool {
    state
        .bridge
        .lock()
        .map(|bridge| bridge.is_some())
        .unwrap_or(false)
}

fn is_safe_unlicensed_method(method: &str) -> bool {
    matches!(
        method,
        "app.snapshot"
            | "app.stopAll"
            | "app.shutdown"
            | "automatic.end"
            | "automatic.retryPersist"
            | "automatic.retryExport"
            | "pd.stop"
            | "powerMeter.stop"
            | "spectrometer.stop"
    )
}

fn authorize_bridge_request(
    app: &AppHandle,
    license_state: &LicenseState,
    bridge_state: &BridgeState,
    method: &str,
) -> Result<(), String> {
    let status = license_state.status(app);
    if status.valid {
        return Ok(());
    }
    if bridge_is_active(bridge_state) && is_safe_unlicensed_method(method) {
        return Ok(());
    }
    Err(format!(
        "软件授权无效：{}。请导入当前设备的有效许可证。",
        status.reason.message()
    ))
}

fn request_shared_bridge(
    shared_bridge: &Arc<Mutex<Option<Arc<PythonBridge>>>>,
    method: &str,
    params: Value,
) -> Result<Value, String> {
    request_shared_bridge_result(shared_bridge, method, params).map_err(PythonBridgeError::message)
}

fn request_shared_bridge_result(
    shared_bridge: &Arc<Mutex<Option<Arc<PythonBridge>>>>,
    method: &str,
    params: Value,
) -> Result<Value, PythonBridgeError> {
    let queued_at = Instant::now();
    let bridge = {
        let mut bridge_guard = shared_bridge
            .lock()
            .map_err(|_| PythonBridgeError::Transport("Python 后端状态锁不可用".to_string()))?;
        if bridge_guard.is_none() {
            *bridge_guard = Some(Arc::new(
                PythonBridge::spawn().map_err(PythonBridgeError::Transport)?,
            ));
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
    result
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
        "shipping" | "data" => SHIPPING_SNAPSHOT_INTERVAL_MS,
        _ => LIVE_SNAPSHOT_INTERVAL_MS,
    }
}

fn stream_envelope(subscription_id: &str, message: Value) -> Value {
    json!({
        "__arpSnapshotStream": subscription_id,
        "message": message,
    })
}

#[cfg(any(windows, test))]
fn await_webview_send_result<F>(dispatch: F) -> Result<(), String>
where
    F: FnOnce(Sender<Result<(), String>>) -> Result<(), String>,
{
    let (result_sender, result_receiver) = mpsc::channel();
    dispatch(result_sender)?;
    match result_receiver.recv_timeout(Duration::from_secs(WEBVIEW_SEND_TIMEOUT_SECS)) {
        Ok(result) => result,
        Err(RecvTimeoutError::Timeout) => Err(format!(
            "WebView2 实时消息发送在 {} 秒内未返回结果",
            WEBVIEW_SEND_TIMEOUT_SECS
        )),
        Err(RecvTimeoutError::Disconnected) => {
            Err("WebView2 实时消息发送回执通道意外关闭".to_string())
        }
    }
}

struct StreamSender {
    subscription_id: String,
    #[allow(dead_code)]
    webview: Webview,
    #[cfg(not(windows))]
    on_event: Channel<Value>,
}

impl StreamSender {
    #[cfg(windows)]
    fn send(&self, message: Value) -> Result<(), String> {
        let payload = serde_json::to_string(&stream_envelope(&self.subscription_id, message))
            .map_err(|error| format!("无法编码 WebView2 实时消息：{error}"))?;
        await_webview_send_result(|result_sender| {
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
                            .and_then(|core| {
                                core.PostWebMessageAsJson(PCWSTR(wide_payload.as_ptr()))
                            })
                    }
                    .map_err(|error| format!("无法发送 WebView2 实时消息：{error}"));
                    let _ = result_sender.send(result);
                })
                .map_err(|error| format!("无法调度 WebView2 实时消息：{error}"))
        })
    }

    #[cfg(not(windows))]
    fn send(&self, message: Value) -> Result<(), String> {
        self.on_event
            .send(stream_envelope(&self.subscription_id, message))
            .map_err(|error| error.to_string())
    }
}

#[tauri::command]
async fn bridge_snapshot(
    app: AppHandle,
    license_state: State<'_, LicenseState>,
    state: State<'_, BridgeState>,
) -> Result<Value, String> {
    authorize_bridge_request(&app, &license_state, &state, "app.snapshot")?;
    let shared_bridge = Arc::clone(&state.bridge);
    tauri::async_runtime::spawn_blocking(move || {
        request_shared_bridge(&shared_bridge, "app.snapshot", json!({}))
    })
    .await
    .map_err(|error| format!("Python 后端任务异常结束：{error}"))?
}

#[tauri::command]
async fn bridge_request(
    method: String,
    params: Value,
    app: AppHandle,
    license_state: State<'_, LicenseState>,
    state: State<'_, BridgeState>,
) -> Result<Value, String> {
    authorize_bridge_request(&app, &license_state, &state, &method)?;
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
        run_snapshot_stream(
            &view,
            Duration::from_millis(interval),
            |params| {
                request_shared_bridge_result(&shared_bridge, "app.snapshot", params).map_err(
                    |error| match error {
                        PythonBridgeError::Timeout(message) => StreamRequestError::Timeout(message),
                        other => StreamRequestError::Fatal(other.message()),
                    },
                )
            },
            |message| {
                sender
                    .send(message)
                    .inspect_err(|error| eprintln!("WebView 实时推流已停止：{error}"))
            },
            |delay| wait_for_generation(&active_generation, generation, delay),
        );
    });
    generation
}

#[cfg(windows)]
#[tauri::command]
async fn bridge_subscribe(
    view: String,
    subscription_id: String,
    webview: Webview,
    app: AppHandle,
    license_state: State<'_, LicenseState>,
    state: State<'_, BridgeState>,
) -> Result<u64, String> {
    authorize_bridge_request(&app, &license_state, &state, "app.snapshot")?;
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
    app: AppHandle,
    license_state: State<'_, LicenseState>,
    state: State<'_, BridgeState>,
) -> Result<u64, String> {
    authorize_bridge_request(&app, &license_state, &state, "app.snapshot")?;
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
    eprintln!("操作员已确认实体电源状态，执行强制断开 Python 后端");
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
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .manage(BridgeState::default())
        .manage(LicenseState::new())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            bridge_snapshot,
            bridge_request,
            bridge_subscribe,
            bridge_unsubscribe,
            bridge_disconnect,
            bridge_force_disconnect,
            license_get_status,
            license_install,
            open_directory
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::snapshot_stream::snapshot_state_fingerprint;
    use super::{
        await_webview_send_result, request_timeout, should_reset_bridge, snapshot_interval_ms,
        stream_envelope, validate_columnar_spectrum, PythonBridge, PythonBridgeError,
    };

    #[test]
    fn webview_send_callback_error_is_propagated() {
        let result = await_webview_send_result(|sender| {
            sender
                .send(Err("post failed".to_string()))
                .map_err(|error| error.to_string())
        });

        assert_eq!(result, Err("post failed".to_string()));
    }

    #[test]
    fn webview_send_dispatch_error_is_propagated() {
        let result = await_webview_send_result(|_sender| Err("dispatch failed".to_string()));

        assert_eq!(result, Err("dispatch failed".to_string()));
    }

    #[test]
    fn backend_action_errors_do_not_restart_the_python_process() {
        let backend_error = Err(PythonBridgeError::Backend("invalid settings".to_string()));
        let timeout_error = Err(PythonBridgeError::Timeout("request timed out".to_string()));
        let transport_error = Err(PythonBridgeError::Transport("broken pipe".to_string()));

        assert!(!should_reset_bridge(&backend_error));
        assert!(!should_reset_bridge(&timeout_error));
        assert!(should_reset_bridge(&transport_error));
    }

    #[test]
    fn request_timeouts_match_operation_cost() {
        assert_eq!(request_timeout("app.snapshot").as_secs(), 15);
        assert_eq!(request_timeout("shippingReport.generate").as_secs(), 600);
        assert_eq!(request_timeout("app.shutdown").as_secs(), 90);
        assert_eq!(request_timeout("automatic.start").as_secs(), 60);
    }

    #[test]
    fn snapshot_state_fingerprint_ignores_transport_only_changes() {
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

        assert_eq!(
            snapshot_state_fingerprint(&first),
            snapshot_state_fingerprint(&second)
        );
        assert!(first["measurements"].is_object());
        assert!(first["pd"]["points"].is_array());

        let mut changed = second.clone();
        changed["seriesRevisions"]["power"] = serde_json::json!(8);
        assert_ne!(
            snapshot_state_fingerprint(&first),
            snapshot_state_fingerprint(&changed)
        );

        let mut pd_points_changed = second;
        pd_points_changed["pd"]["points"] = serde_json::json!([{ "elapsedS": 2.0, "value": 1.0 }]);
        assert_eq!(
            snapshot_state_fingerprint(&first),
            snapshot_state_fingerprint(&pd_points_changed)
        );
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
    fn expired_license_policy_keeps_only_safe_runtime_methods() {
        for method in [
            "app.snapshot",
            "app.stopAll",
            "app.shutdown",
            "automatic.end",
            "automatic.retryPersist",
            "automatic.retryExport",
            "pd.stop",
            "powerMeter.stop",
            "spectrometer.stop",
        ] {
            assert!(super::is_safe_unlicensed_method(method), "{method}");
        }
        for method in [
            "automatic.start",
            "app.configure",
            "powerSupply.setCurrent",
            "powerSupply.setOutput",
            "device.refresh",
            "shippingReport.generate",
        ] {
            assert!(!super::is_safe_unlicensed_method(method), "{method}");
        }
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
