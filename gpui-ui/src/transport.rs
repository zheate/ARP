use serde_json::{Value, json};
const SNAPSHOT_REQUEST_TIMEOUT_SECS: u64 = 15;
const DEFAULT_REQUEST_TIMEOUT_SECS: u64 = 60;
const FILE_SCAN_REQUEST_TIMEOUT_SECS: u64 = 120;
const REPORT_REQUEST_TIMEOUT_SECS: u64 = 10 * 60;
const SHUTDOWN_REQUEST_TIMEOUT_SECS: u64 = 90;

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

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x08000000;
pub struct PythonBridge {
    child: Mutex<Child>,
    stdin: Mutex<BufWriter<ChildStdin>>,
    pending: Arc<Mutex<HashMap<String, Sender<Result<Value, PythonBridgeError>>>>>,
    next_request_id: AtomicU64,
    shutdown_confirmed: AtomicBool,
    force_exit_requested: AtomicBool,
}

#[derive(Clone, Debug)]
pub enum PythonBridgeError {
    Remote {
        code: String,
        message: String,
        runtime_id: Option<String>,
    },
    Contextual {
        source: Box<PythonBridgeError>,
        request_id: String,
    },
    Timeout(String),
    Transport(String),
}

impl PythonBridgeError {
    pub fn message(self) -> String {
        match self {
            Self::Timeout(message) | Self::Transport(message) => message,
            Self::Remote { message, .. } => message,
            Self::Contextual { source, .. } => source.message(),
        }
    }

    pub fn outcome_unknown(&self) -> bool {
        match self {
            Self::Contextual { source, .. } => source.outcome_unknown(),
            Self::Timeout(_) | Self::Transport(_) => true,
            _ => false,
        }
    }

    pub fn is_transport(&self) -> bool {
        match self {
            Self::Contextual { source, .. } => source.is_transport(),
            _ => matches!(self, Self::Transport(_)),
        }
    }
}

#[derive(Clone, Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BridgeFailure {
    kind: String,
    code: String,
    message: String,
    method: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    runtime_id: Option<String>,
    outcome_unknown: bool,
}

impl PythonBridgeError {
    pub fn failure(&self, method: &str) -> BridgeFailure {
        let (kind, code, runtime_id) = match self {
            Self::Contextual { source, request_id } => {
                let mut failure = source.failure(method);
                failure.request_id = Some(request_id.clone());
                return failure;
            }
            Self::Remote {
                code, runtime_id, ..
            } => ("backend", code.as_str(), runtime_id.clone()),
            Self::Timeout(_) => ("timeout", "request_timeout", None),
            Self::Transport(_) => ("transport", "transport_failed", None),
        };
        BridgeFailure {
            kind: kind.into(),
            code: code.into(),
            message: self.clone().message(),
            method: method.into(),
            request_id: None,
            runtime_id,
            outcome_unknown: kind == "timeout" || kind == "transport",
        }
    }
}

fn request_timeout(method: &str) -> Duration {
    let seconds = match method {
        "app.snapshot" => SNAPSHOT_REQUEST_TIMEOUT_SECS,
        "app.shutdown" => SHUTDOWN_REQUEST_TIMEOUT_SECS,
        "shippingReport.inspect"
        | "shippingReport.batchInspect"
        | "dataBrowser.list"
        | "dataBrowser.inspect"
        | "dataBrowser.import" => FILE_SCAN_REQUEST_TIMEOUT_SECS,
        "shippingReport.preview"
        | "automatic.exportDiagnostics"
        | "shippingReport.generate"
        | "shippingReport.batchGenerateItem" => REPORT_REQUEST_TIMEOUT_SECS,
        _ => DEFAULT_REQUEST_TIMEOUT_SECS,
    };
    Duration::from_secs(seconds)
}

impl PythonBridge {
    pub fn allow_forced_exit(&self) {
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

                    let mut response: Value = match serde_json::from_str(&line) {
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
                    let Some(request_id) = response
                        .get("id")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                    else {
                        Self::fail_pending(
                            &pending,
                            PythonBridgeError::Transport("Python 响应缺少请求编号".to_string()),
                        );
                        break;
                    };
                    let result = if response.get("ok").and_then(Value::as_bool) == Some(true) {
                        // The reader owns this response. Transfer the result to
                        // the waiting request instead of cloning every sample.
                        response
                            .as_object_mut()
                            .and_then(|root| root.remove("result"))
                            .ok_or_else(|| {
                                PythonBridgeError::Transport("Python 响应缺少 result".to_string())
                            })
                    } else {
                        let message = response
                            .pointer("/error/message")
                            .and_then(Value::as_str)
                            .unwrap_or("Python 后端返回未知错误");
                        Err(PythonBridgeError::Remote {
                            message: message.to_string(),
                            code: response
                                .pointer("/error/code")
                                .and_then(Value::as_str)
                                .unwrap_or("action_failed")
                                .to_string(),
                            runtime_id: response
                                .pointer("/error/runtimeId")
                                .and_then(Value::as_str)
                                .map(str::to_string),
                        })
                    };
                    let sender = pending
                        .lock()
                        .ok()
                        .and_then(|mut requests| requests.remove(&request_id));
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
        let started_at = Instant::now();
        let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
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
        if is_bundled_sidecar {
            if let Some(directory) = python.parent() {
                command.current_dir(directory);
            }
        } else if repo_root.is_dir() {
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
        let ping = match bridge.request("system.ping", json!({})) {
            Ok(ping) => ping,
            Err(error) => {
                // No operator command has been sent to this newly spawned process.
                bridge.allow_forced_exit();
                return Err(error.message());
            }
        };
        if ping.get("protocolVersion").and_then(Value::as_u64) != Some(2) {
            // This new process has not accepted any device command.
            bridge.allow_forced_exit();
            return Err("前后端协议版本不兼容，请重新安装匹配版本".into());
        }
        eprintln!(
            "ARP backend ready | handshake_ms={:.3}",
            started_at.elapsed().as_secs_f64() * 1000.0
        );
        let mode = ping
            .get("mode")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        Ok((bridge, mode))
    }

    pub fn spawn() -> Result<Self, String> {
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

    pub fn request(&self, method: &str, params: Value) -> Result<Value, PythonBridgeError> {
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
            return Err(PythonBridgeError::Transport(format!(
                "Python 后端已退出：{status}"
            )));
        }

        let request_id = format!(
            "gpui-{}",
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
        if method != "app.snapshot" || queued_at.elapsed() >= Duration::from_millis(100) {
            eprintln!(
                "ARP bridge request | id={request_id} | method={method} | elapsed_ms={:.3} | ok={}",
                queued_at.elapsed().as_secs_f64() * 1000.0,
                result.is_ok()
            );
        }
        let result = result.map_err(|source| PythonBridgeError::Contextual {
            source: Box::new(source),
            request_id: request_id.clone(),
        })?;
        validate_columnar_spectrum(&result)?;
        if method == "app.shutdown"
            && result.get("shutdownConfirmed").and_then(Value::as_bool) == Some(true)
            && result.get("exportsDrained").and_then(Value::as_bool) == Some(true)
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
        if self.shutdown_confirmed.load(Ordering::SeqCst)
            || self.force_exit_requested.load(Ordering::SeqCst)
        {
            if let Ok(mut child) = self.child.lock() {
                let _ = child.kill();
                let _ = child.wait();
            }
        } else {
            eprintln!(
                "ARP backend shutdown unconfirmed; refusing to force-kill the hardware process"
            );
        }
    }
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

#[cfg(test)]
mod native_transport_tests {
    use super::*;
    fn fixture() -> PythonBridge {
        let python = if cfg!(windows) { "python" } else { "python3" };
        let mut child=Command::new(python).args(["-u","-c",r#"import sys,json
for line in sys.stdin:
 r=json.loads(line)
 if r['method']=='fixture.reject': print(json.dumps({'v':2,'id':r['id'],'ok':False,'error':{'code':'device_not_connected','message':'未连接','runtimeId':'fixture'}}),flush=True)
 else: print(json.dumps({'v':2,'id':r['id'],'ok':True,'result':r['params']}),flush=True)
"#]).stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
        let stdin = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let pending = Arc::new(Mutex::new(HashMap::new()));
        PythonBridge::start_response_reader(stdout, pending.clone()).unwrap();
        PythonBridge {
            child: Mutex::new(child),
            stdin: Mutex::new(BufWriter::new(stdin)),
            pending,
            next_request_id: AtomicU64::new(1),
            shutdown_confirmed: AtomicBool::new(false),
            force_exit_requested: AtomicBool::new(true),
        }
    }
    #[test]
    fn actual_pipe_preserves_full_native_spectrum_and_binary64() {
        let bridge = fixture();
        let samples = (0..8192)
            .map(|i| json!(976.1234567890123 + i as f64 * 0.0000123456789))
            .collect::<Vec<_>>();
        let input = json!({"measurements":{"spectrum":{"wavelengthNm":samples,"intensity":samples,"gridRevision":4}},"中文":"采集"});
        let result = bridge.request("fixture.echo", input.clone()).unwrap();
        assert_eq!(result, input);
    }
    #[test]
    fn backend_rejection_is_not_a_transport_failure() {
        let bridge = fixture();
        let error = bridge.request("fixture.reject", json!({})).unwrap_err();
        assert!(!error.outcome_unknown());
        assert_eq!(error.failure("fixture.reject").code, "device_not_connected");
    }
    #[test]
    fn wrapped_timeout_is_explicitly_uncertain() {
        let error = PythonBridgeError::Contextual {
            source: Box::new(PythonBridgeError::Timeout("超时".into())),
            request_id: "1".into(),
        };
        assert!(error.outcome_unknown());
    }
}
