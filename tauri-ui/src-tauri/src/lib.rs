use serde_json::{json, Value};
use std::env;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::State;

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
struct BridgeState(Arc<Mutex<Option<PythonBridge>>>);

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
    let shared_bridge = Arc::clone(&state.0);
    tauri::async_runtime::spawn_blocking(move || {
        let mut bridge_guard = shared_bridge
            .lock()
            .map_err(|_| "Python 后端状态锁不可用".to_string())?;
        if bridge_guard.is_none() {
            *bridge_guard = Some(PythonBridge::spawn()?);
        }

        let result = bridge_guard
            .as_mut()
            .expect("bridge was initialized")
            .request(&method, params);
        if result.is_err() {
            *bridge_guard = None;
        }
        result
    })
    .await
    .map_err(|error| format!("Python 后端任务异常结束：{error}"))?
}

#[tauri::command]
async fn bridge_disconnect(state: State<'_, BridgeState>) -> Result<(), String> {
    let shared_bridge = Arc::clone(&state.0);
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
            bridge_disconnect
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::PythonBridge;

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
