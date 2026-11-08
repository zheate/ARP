//! Owns the one Python process. Blocking I/O never runs on the UI thread.
use crate::{
    license::{LicenseState, LicenseStatus, license_install},
    model::{safe_without_license, shutdown_complete},
    transport::PythonBridge,
};
use serde_json::{Value, json};
use std::{
    path::Path,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
};

pub struct Controller {
    license: Mutex<LicenseState>,
    bridge: Mutex<Option<Arc<PythonBridge>>>,
    command_busy: AtomicBool,
    closing: AtomicBool,
}
impl Controller {
    pub fn new() -> Self {
        Self {
            license: Mutex::new(LicenseState::new()),
            bridge: Mutex::new(None),
            command_busy: AtomicBool::new(false),
            closing: AtomicBool::new(false),
        }
    }
    pub fn license(&self) -> LicenseStatus {
        self.license.lock().unwrap().status()
    }
    pub fn install(&self, path: &Path) -> Result<LicenseStatus, String> {
        license_install(
            path.to_string_lossy().to_string(),
            &*self.license.lock().map_err(|_| "许可证状态锁不可用")?,
        )
    }
    pub fn request(&self, method: &str, params: Value) -> Result<Value, String> {
        let licensed = self.license().valid;
        let bridge = {
            let mut holder = self.bridge.lock().map_err(|_| "后端状态锁不可用")?;
            if !licensed && !(holder.is_some() && safe_without_license(method)) {
                return Err("软件授权无效，请导入有效许可证".into());
            }
            if self.closing.load(Ordering::SeqCst) && !safe_without_license(method) {
                return Err("正在关闭应用，新设备命令不可用".into());
            }
            if holder.is_none() {
                *holder = Some(Arc::new(PythonBridge::spawn()?));
            }
            holder.as_ref().unwrap().clone()
        };
        bridge.request(method, params).map_err(|e| {
            let uncertain = e.outcome_unknown();
            let message = e.message();
            if uncertain {
                format!("{message}。操作结果未确认，请读取实际电源状态；通信不可用时检查物理面板。")
            } else {
                message
            }
        })
    }
    pub fn command(&self, method: &str, params: Value) -> Result<Value, String> {
        let safety = matches!(
            method,
            "app.stopAll" | "app.shutdown" | "automatic.end" | "powerSupply.safeRampDown"
        );
        if !safety && self.command_busy.swap(true, Ordering::SeqCst) {
            return Err("上一项设备命令尚未结束".into());
        }
        let result = self.request(method, params);
        if !safety {
            self.command_busy.store(false, Ordering::SeqCst);
        }
        result
    }
    pub fn close(&self) -> Result<Value, String> {
        if self.closing.swap(true, Ordering::SeqCst) {
            return Err("安全关闭进行中".into());
        }
        if self
            .bridge
            .lock()
            .map_err(|_| "后端状态锁不可用")?
            .is_none()
        {
            return Ok(json!({"shutdownConfirmed":true,"exportsDrained":true}));
        }
        let result = self.command("app.shutdown", json!({}));
        if !result.as_ref().is_ok_and(shutdown_complete) {
            self.closing.store(false, Ordering::SeqCst);
        }
        result
    }
    pub fn cancel_close(&self) {
        self.closing.store(false, Ordering::SeqCst);
    }
    pub fn force_exit(&self) {
        if let Ok(holder) = self.bridge.lock() {
            if let Some(b) = holder.as_ref() {
                b.allow_forced_exit();
            }
        }
    }
}
