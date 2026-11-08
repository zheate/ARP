//! UI-independent snapshot cache. All stored measurement values remain binary64.
use serde_json::{Value, json};

pub fn safe_without_license(method: &str) -> bool {
    matches!(
        method,
        "app.snapshot"
            | "app.stopAll"
            | "app.shutdown"
            | "automatic.end"
            | "automatic.retryPersist"
            | "automatic.retryExport"
            | "automatic.exportDiagnostics"
            | "powerSupply.safeRampDown"
            | "pd.stop"
            | "powerMeter.stop"
            | "spectrometer.stop"
    )
}

pub fn shutdown_complete(result: &Value) -> bool {
    result["shutdownConfirmed"] == true && result["exportsDrained"] == true
}

pub fn snapshot_interval(view: &str) -> u64 {
    if matches!(view, "pd" | "data" | "shipping") {
        500
    } else {
        100
    }
}

#[derive(Default)]
pub struct SnapshotCache {
    pub value: Value,
}

impl SnapshotCache {
    pub fn request_params(&self, view: &str) -> Value {
        let mut params = json!({"view":view});
        if let Some(revisions) = self.value.get("seriesRevisions") {
            params["since"] = revisions.clone();
            let mut cursors = json!({});
            for (name, path) in [("power", "/measurements/power"), ("pd", "/pd/points")] {
                if let Some(x) = self
                    .value
                    .pointer(path)
                    .and_then(Value::as_array)
                    .and_then(|a| a.last())
                    .and_then(|p| p.get("elapsedS"))
                {
                    cursors[name] = x.clone();
                }
            }
            params["cursors"] = cursors;
        }
        params
    }

    pub fn merge(&mut self, mut next: Value) -> Result<(), String> {
        if !next["backend"].is_object()
            || !next["safety"].is_object()
            || !next["devices"].is_object()
        {
            return Err("后端快照缺少设备或安全状态".into());
        }
        if self.value["backend"]["runtimeId"] != next["backend"]["runtimeId"]
            || self.value["backend"]["mode"] != next["backend"]["mode"]
        {
            self.value = Value::Null;
        }
        let changed = self.value["safety"]["workflowOwner"] != next["safety"]["workflowOwner"];
        let previous = if changed {
            Value::Null
        } else {
            self.value["measurements"].take()
        };
        let mut measurements = next
            .get_mut("measurements")
            .map(Value::take)
            .unwrap_or_else(|| json!({}));
        if measurements.is_null() {
            measurements = json!({});
        }
        for (name, key) in [
            ("power", "power"),
            ("stable", "stable"),
            ("aging", "agingPower"),
            ("spectrum", "spectrum"),
            ("spectrum", "spectrumPeaks"),
        ] {
            let older = self.value["seriesRevisions"][name]
                .as_u64()
                .zip(next["seriesRevisions"][name].as_u64())
                .is_some_and(|(a, b)| b < a);
            if older || measurements.get(key).is_none() {
                if let Some(old) = previous.get(key) {
                    measurements[key] = old.clone();
                }
            }
        }
        let older_power = self.value["seriesRevisions"]["power"]
            .as_u64()
            .zip(next["seriesRevisions"]["power"].as_u64())
            .is_some_and(|(a, b)| b < a);
        if !older_power {
            if let Some(patch) = next.pointer("/seriesPatches/power") {
                merge_series(&mut measurements["power"], patch)?;
            }
        }
        if let Some(spectrum) = measurements.get_mut("spectrum") {
            if spectrum.get("wavelengthNm").is_none() {
                let old = &previous["spectrum"];
                if old["gridRevision"] != spectrum["gridRevision"]
                    || old.get("wavelengthNm").is_none()
                {
                    self.value = Value::Null;
                    return Err("光谱波长网格失配，必须重新读取完整快照".into());
                }
                spectrum["wavelengthNm"] = old["wavelengthNm"].clone();
            }
            let x = spectrum["wavelengthNm"].as_array().ok_or("光谱波长无效")?;
            let y = spectrum["intensity"].as_array().ok_or("光谱强度无效")?;
            if x.len() != y.len() {
                return Err("光谱两轴长度不一致".into());
            }
        }
        next["measurements"] = measurements;
        if let Some(pd) = next.get_mut("pd") {
            if pd.get("points").is_none() {
                pd["points"] = self.value["pd"]["points"].clone();
            }
        }
        let older_pd = self.value["seriesRevisions"]["pd"]
            .as_u64()
            .zip(next["seriesRevisions"]["pd"].as_u64())
            .is_some_and(|(a, b)| b < a);
        if older_pd && next.get("pd").is_some() {
            next["pd"]["points"] = self.value["pd"]["points"].clone();
        }
        if !older_pd {
            if let Some(patch) = next.pointer("/seriesPatches/pd").cloned() {
                merge_series(&mut next["pd"]["points"], &patch)?;
            }
        }
        if let Some(revs) = next["seriesRevisions"].as_object_mut() {
            for (key, rev) in revs {
                if let Some(old) = self.value["seriesRevisions"][key.as_str()].as_u64() {
                    if rev.as_u64().is_some_and(|new| new < old) {
                        *rev = json!(old);
                    }
                }
            }
        }
        self.value = next;
        Ok(())
    }
}

fn merge_series(history: &mut Value, patch: &Value) -> Result<(), String> {
    let start = patch["startX"].as_f64().ok_or("增量曲线缺少起始时间")?;
    let points = patch["points"].as_array().ok_or("增量曲线缺少数据")?;
    let mut rows = history.as_array().cloned().unwrap_or_default();
    rows.retain(|p| p["elapsedS"].as_f64().is_some_and(|x| x >= start));
    rows.extend(points.iter().cloned());
    if rows.iter().any(|p| p["elapsedS"].as_f64().is_none()) {
        return Err("曲线时间无效".into());
    }
    rows.sort_by(|a, b| {
        a["elapsedS"]
            .as_f64()
            .unwrap()
            .total_cmp(&b["elapsedS"].as_f64().unwrap())
    });
    let mut merged: Vec<Value> = Vec::with_capacity(rows.len());
    for row in rows {
        if merged
            .last()
            .is_some_and(|p| p["elapsedS"] == row["elapsedS"])
        {
            merged.pop();
        }
        merged.push(row);
    }
    *history = Value::Array(merged);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn snapshot() -> Value {
        json!({"backend":{"mode":"active","runtimeId":"a"},"devices":{},"safety":{"workflowOwner":"manual"},"seriesRevisions":{"power":1,"spectrum":1,"pd":1},"measurements":{"power":[{"elapsedS":1.0,"powerW":0.12345678901234566}],"spectrum":{"wavelengthNm":[975.1234567890123],"intensity":[1234.1234567890124],"gridRevision":1}},"pd":{"points":[{"elapsedS":1,"value":0.5}]}})
    }
    #[test]
    fn retains_exact_data_and_merges_grid() {
        let mut cache = SnapshotCache::default();
        let original = snapshot();
        cache.merge(original.clone()).unwrap();
        let mut patch = snapshot();
        patch["measurements"] =
            json!({"spectrum":{"intensity":[1235.1234567890124],"gridRevision":1}});
        patch["seriesPatches"] =
            json!({"power":{"startX":1,"points":[{"elapsedS":2.0,"powerW":0.23456789012345678}]}});
        cache.merge(patch).unwrap();
        assert_eq!(
            cache.value["measurements"]["power"][0],
            original["measurements"]["power"][0]
        );
        assert_eq!(
            cache.value["measurements"]["power"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(
            cache.value["measurements"]["spectrum"]["wavelengthNm"],
            original["measurements"]["spectrum"]["wavelengthNm"]
        );
    }
    #[test]
    fn runtime_change_does_not_reuse_a_grid() {
        let mut c = SnapshotCache::default();
        c.merge(snapshot()).unwrap();
        let mut p = snapshot();
        p["backend"]["runtimeId"] = json!("b");
        p["measurements"]["spectrum"]
            .as_object_mut()
            .unwrap()
            .remove("wavelengthNm");
        assert!(c.merge(p).is_err());
    }
    #[test]
    fn close_requires_export_drain_and_safe_shutdown() {
        assert!(!shutdown_complete(
            &json!({"shutdownConfirmed":true,"exportsDrained":false})
        ));
        assert!(!shutdown_complete(
            &json!({"shutdownConfirmed":false,"exportsDrained":true})
        ));
        assert!(shutdown_complete(
            &json!({"shutdownConfirmed":true,"exportsDrained":true})
        ));
    }
    #[test]
    fn expired_license_keeps_safety_actions() {
        for m in [
            "app.shutdown",
            "app.stopAll",
            "automatic.end",
            "powerSupply.safeRampDown",
        ] {
            assert!(safe_without_license(m));
        }
        for m in [
            "powerSupply.setCurrent",
            "powerSupply.setOutput",
            "automatic.start",
        ] {
            assert!(!safe_without_license(m));
        }
    }
    #[test]
    fn old_command_response_cannot_replace_newer_measurements() {
        let mut c = SnapshotCache::default();
        let mut current = snapshot();
        current["seriesRevisions"]["power"] = json!(3);
        c.merge(current.clone()).unwrap();
        let mut old = snapshot();
        old["seriesPatches"] = json!({"power":{"startX":0,"points":[{"elapsedS":99,"powerW":99}]}});
        c.merge(old).unwrap();
        assert_eq!(
            c.value["measurements"]["power"],
            current["measurements"]["power"]
        );
        assert_eq!(c.value["seriesRevisions"]["power"], 3);
    }
}
