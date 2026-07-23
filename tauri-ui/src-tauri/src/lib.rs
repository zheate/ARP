use serde_json::{json, Value};
use std::env;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{ipc::Channel, State};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct PythonBridge {
    child: Child,
    stdin: BufWriter<ChildStdin>,
    stdout: BufReader<ChildStdout>,
    next_request_id: u64,
}

impl PythonBridge {
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
        let mut command = Command::new(&python);
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
        let mut bridge = Self {
            child,
            stdin: BufWriter::new(stdin),
            stdout: BufReader::new(stdout),
            next_request_id: 1,
        };
        let ping = bridge.request("system.ping", json!({}))?;
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

    fn request(&mut self, method: &str, params: Value) -> Result<Value, String> {
        if let Some(status) = self
            .child
            .try_wait()
            .map_err(|error| format!("无法读取 Python 后端状态：{error}"))?
        {
            return Err(format!("Python 后端已退出：{status}"));
        }

        let request_id = format!("tauri-{}", self.next_request_id);
        self.next_request_id += 1;
        let request = json!({
            "v": 1,
            "id": request_id,
            "method": method,
            "params": params,
        });
        serde_json::to_writer(&mut self.stdin, &request)
            .map_err(|error| format!("无法编码 Python 请求：{error}"))?;
        self.stdin
            .write_all(b"\n")
            .and_then(|_| self.stdin.flush())
            .map_err(|error| format!("无法发送 Python 请求：{error}"))?;

        let mut line = String::new();
        let bytes_read = self
            .stdout
            .read_line(&mut line)
            .map_err(|error| format!("无法读取 Python 响应：{error}"))?;
        if bytes_read == 0 {
            return Err("Python 后端在返回响应前关闭了输出通道".to_string());
        }

        let response: Value = serde_json::from_str(&line)
            .map_err(|error| format!("Python 响应不是有效 JSON：{error}"))?;
        if response.get("id").and_then(Value::as_str) != Some(request_id.as_str()) {
            return Err("Python 响应与请求编号不匹配".to_string());
        }
        if response.get("ok").and_then(Value::as_bool) != Some(true) {
            let message = response
                .pointer("/error/message")
                .and_then(Value::as_str)
                .unwrap_or("Python 后端返回未知错误");
            return Err(message.to_string());
        }
        response
            .get("result")
            .cloned()
            .ok_or_else(|| "Python 响应缺少 result".to_string())
    }
}

impl Drop for PythonBridge {
    fn drop(&mut self) {
        let _ = self.request("app.shutdown", json!({}));
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Default)]
struct BridgeState {
    bridge: Arc<Mutex<Option<PythonBridge>>>,
    stream_generation: Arc<AtomicU64>,
}

fn request_shared_bridge(
    shared_bridge: &Arc<Mutex<Option<PythonBridge>>>,
    method: &str,
    params: Value,
) -> Result<Value, String> {
    let mut bridge_guard = shared_bridge
        .lock()
        .map_err(|_| "Python 后端状态锁不可用".to_string())?;
    if bridge_guard.is_none() {
        *bridge_guard = Some(PythonBridge::spawn()?);
    }

    let result = bridge_guard
        .as_mut()
        .expect("bridge was initialized")
        .request(method, params);
    if result.is_err() {
        *bridge_guard = None;
    }
    result
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

fn take_compact_spectrum(snapshot: &mut Value) -> Option<Value> {
    let revision = snapshot.pointer("/seriesRevisions/spectrum")?.as_u64()?;
    let measurements = snapshot.get_mut("measurements")?.as_object_mut()?;
    let spectrum = measurements.remove("spectrum")?.as_array()?.to_owned();
    let points = spectrum
        .into_iter()
        .filter_map(|point| {
            Some(json!([
                point.get("wavelengthNm")?.as_f64()?,
                point.get("intensity")?.as_f64()?,
            ]))
        })
        .collect::<Vec<_>>();
    Some(json!({ "revision": revision, "points": points }))
}

fn snapshot_fingerprint(snapshot: &Value) -> Value {
    let mut fingerprint = snapshot.clone();
    if let Some(root) = fingerprint.as_object_mut() {
        root.remove("capturedAt");
        root.remove("measurements");
        root.remove("seriesPatches");
    }
    if let Some(pd) = fingerprint.get_mut("pd").and_then(Value::as_object_mut) {
        pd.remove("points");
    }
    fingerprint
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

#[tauri::command]
async fn bridge_subscribe(
    view: String,
    on_event: Channel<Value>,
    state: State<'_, BridgeState>,
) -> Result<u64, String> {
    let generation = state.stream_generation.fetch_add(1, Ordering::SeqCst) + 1;
    let active_generation = Arc::clone(&state.stream_generation);
    let shared_bridge = Arc::clone(&state.bridge);
    let interval = if view == "pd" { 1000 } else { 250 };

    tauri::async_runtime::spawn_blocking(move || {
        let mut since: Option<Value> = None;
        let mut cursors = serde_json::Map::new();
        let mut last_fingerprint: Option<Value> = None;
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
                Ok(mut snapshot) => {
                    since = snapshot.get("seriesRevisions").cloned();
                    update_stream_cursor(&snapshot, "power", "/measurements/power", &mut cursors);
                    update_stream_cursor(&snapshot, "pd", "/pd/points", &mut cursors);
                    let fingerprint = snapshot_fingerprint(&snapshot);
                    if last_fingerprint.as_ref() == Some(&fingerprint) {
                        std::thread::sleep(Duration::from_millis(interval));
                        continue;
                    }
                    last_fingerprint = Some(fingerprint);
                    if let Some(spectrum) = take_compact_spectrum(&mut snapshot) {
                        if on_event.send(json!({ "spectrum": spectrum })).is_err() {
                            break;
                        }
                    }
                    if on_event.send(json!({ "snapshot": snapshot })).is_err() {
                        break;
                    }
                }
                Err(error) => {
                    let _ = on_event.send(json!({ "error": error }));
                    break;
                }
            }
            std::thread::sleep(Duration::from_millis(interval));
        }
    });
    Ok(generation)
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BridgeState::default())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            bridge_snapshot,
            bridge_request,
            bridge_subscribe,
            bridge_unsubscribe,
            bridge_disconnect
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::{snapshot_fingerprint, take_compact_spectrum, PythonBridge};

    #[test]
    fn snapshot_fingerprint_ignores_transport_only_changes() {
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

        assert_eq!(snapshot_fingerprint(&first), snapshot_fingerprint(&second));

        let mut changed = second;
        changed["seriesRevisions"]["power"] = serde_json::json!(8);
        assert_ne!(snapshot_fingerprint(&first), snapshot_fingerprint(&changed));
    }

    #[test]
    fn compact_spectrum_message_stays_below_direct_channel_limit() {
        let spectrum = (0..160)
            .map(|index| {
                serde_json::json!({
                    "wavelengthNm": 956.0 + index as f64 * 0.25,
                    "intensity": 16_000.0 - index as f64 * 3.5,
                })
            })
            .collect::<Vec<_>>();
        let mut snapshot = serde_json::json!({
            "seriesRevisions": { "spectrum": 42 },
            "measurements": { "spectrum": spectrum },
        });

        let compact = take_compact_spectrum(&mut snapshot).expect("spectrum should be extracted");

        assert!(snapshot.pointer("/measurements/spectrum").is_none());
        assert_eq!(compact["revision"], 42);
        assert_eq!(compact["points"].as_array().map(Vec::len), Some(160));
        assert!(
            serde_json::to_vec(&serde_json::json!({ "spectrum": compact }))
                .expect("compact spectrum should serialize")
                .len()
                < 8192
        );
    }

    #[test]
    fn python_bridge_returns_snapshot() {
        let mut bridge = PythonBridge::spawn().expect("Python bridge should start");
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
