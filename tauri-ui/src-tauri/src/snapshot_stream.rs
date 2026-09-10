use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

pub enum StreamRequestError {
    Timeout(String),
    Fatal(String),
}

pub fn wait_for_generation(active: &AtomicU64, generation: u64, delay: Duration) -> bool {
    let deadline = Instant::now() + delay;
    loop {
        if active.load(Ordering::SeqCst) != generation {
            return false;
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return true;
        }
        std::thread::sleep(remaining.min(Duration::from_millis(100)));
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

pub fn snapshot_state_fingerprint(snapshot: &Value) -> Value {
    let Some(root) = snapshot.as_object() else {
        return snapshot.clone();
    };
    // Revisions/cursors carry history changes without cloning history arrays.
    const IGNORED: [&str; 3] = ["capturedAt", "measurements", "seriesPatches"];
    let mut fingerprint = serde_json::Map::with_capacity(root.len().saturating_sub(3));
    for (key, value) in root {
        if IGNORED.contains(&key.as_str()) {
            continue;
        }
        if key == "pd" {
            let mut pd = value.clone();
            if let Some(pd_object) = pd.as_object_mut() {
                pd_object.remove("points");
            }
            fingerprint.insert(key.clone(), pd);
        } else {
            fingerprint.insert(key.clone(), value.clone());
        }
    }
    Value::Object(fingerprint)
}

// The injected operations keep retry/resync/cancellation testable without a
// Python process, WebView, or connected instruments.
pub fn run_snapshot_stream(
    view: &str,
    interval: Duration,
    mut request: impl FnMut(Value) -> Result<Value, StreamRequestError>,
    mut send: impl FnMut(Value) -> Result<(), String>,
    mut wait: impl FnMut(Duration) -> bool,
) {
    let mut since: Option<Value> = None;
    let mut cursors = serde_json::Map::new();
    let mut last_fingerprint: Option<Value> = None;
    let mut retry_delay = Duration::from_millis(500);
    while wait(Duration::ZERO) {
        let mut params = serde_json::Map::new();
        params.insert("view".to_string(), json!(view));
        if let Some(revisions) = since.clone() {
            params.insert("since".to_string(), revisions);
        }
        if !cursors.is_empty() {
            params.insert("cursors".to_string(), Value::Object(cursors.clone()));
        }
        let result = request(Value::Object(params));
        // A view may have been replaced while the request was blocked.
        if !wait(Duration::ZERO) {
            break;
        }
        match result {
            Ok(snapshot) => {
                retry_delay = Duration::from_millis(500);
                since = snapshot.get("seriesRevisions").cloned();
                update_stream_cursor(&snapshot, "power", "/measurements/power", &mut cursors);
                update_stream_cursor(&snapshot, "pd", "/pd/points", &mut cursors);
                let fingerprint = snapshot_state_fingerprint(&snapshot);
                if last_fingerprint.as_ref() != Some(&fingerprint) {
                    last_fingerprint = Some(fingerprint);
                    if send(json!({ "snapshot": snapshot })).is_err() {
                        break;
                    }
                }
                if !wait(interval) {
                    break;
                }
            }
            Err(StreamRequestError::Timeout(message)) => {
                if send(json!({ "error": message })).is_err() {
                    break;
                }
                // A late response may have advanced backend caches. Recover
                // with a full snapshot, including the spectrum wavelength grid,
                // and deliver it even when its state matches the last frame.
                since = None;
                cursors.clear();
                last_fingerprint = None;
                if !wait(retry_delay) {
                    break;
                }
                retry_delay = (retry_delay * 2).min(Duration::from_secs(5));
            }
            Err(StreamRequestError::Fatal(message)) => {
                let _ = send(json!({ "error": message }));
                break;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    use std::collections::VecDeque;

    #[test]
    fn timeout_recovers_with_full_snapshot_even_when_state_has_not_changed() {
        let snapshot = json!({
            "seriesRevisions": { "power": 2, "spectrum": 1, "pd": 1 },
            "measurements": { "power": [{ "elapsedS": 10.0, "powerW": 1.0 }],
                "spectrum": { "wavelengthNm": [976.0], "intensity": [100.0], "gridRevision": 1 } },
            "pd": { "points": [{ "elapsedS": 5.0, "value": 2.0 }] }
        });
        let mut responses = VecDeque::from([
            Ok(snapshot.clone()),
            Err(StreamRequestError::Timeout("busy".to_string())),
            Ok(snapshot.clone()),
        ]);
        let mut params = Vec::new();
        let mut messages = Vec::new();
        let mut waits = Vec::new();
        let finished = Cell::new(false);
        run_snapshot_stream(
            "automatic",
            Duration::from_millis(200),
            |request| {
                params.push(request);
                responses.pop_front().expect("no extra request")
            },
            |message| {
                messages.push(message);
                finished.set(messages.len() == 3);
                Ok(())
            },
            |delay| {
                if !delay.is_zero() {
                    waits.push(delay);
                }
                !finished.get()
            },
        );
        assert_eq!(params[1]["since"]["power"], 2);
        assert_eq!(params[1]["cursors"]["power"], 10.0);
        assert_eq!(params[1]["cursors"]["pd"], 5.0);
        assert_eq!(params[2], json!({ "view": "automatic" }));
        assert_eq!(
            messages,
            vec![
                json!({ "snapshot": snapshot }),
                json!({ "error": "busy" }),
                json!({ "snapshot": snapshot })
            ]
        );
        assert!(waits.contains(&Duration::from_millis(500)));
    }

    #[test]
    fn repeated_timeouts_back_off_with_a_cap_and_cancel_without_an_extra_request() {
        let mut attempts = 0;
        let mut waits = Vec::new();
        run_snapshot_stream(
            "pd",
            Duration::from_millis(500),
            |_| {
                attempts += 1;
                Err(StreamRequestError::Timeout("busy".to_string()))
            },
            |_| Ok(()),
            |delay| {
                if delay.is_zero() {
                    return true;
                }
                waits.push(delay.as_millis());
                waits.len() < 7
            },
        );
        assert_eq!(attempts, 7);
        assert_eq!(waits, [500, 1000, 2000, 4000, 5000, 5000, 5000]);
    }

    #[test]
    fn a_successful_recovery_resets_the_next_timeout_delay() {
        let mut responses = VecDeque::from([
            Err(StreamRequestError::Timeout("busy".to_string())),
            Err(StreamRequestError::Timeout("busy".to_string())),
            Ok(json!({ "seriesRevisions": {} })),
            Err(StreamRequestError::Timeout("busy again".to_string())),
        ]);
        let mut waits = Vec::new();
        run_snapshot_stream(
            "automatic",
            Duration::from_millis(200),
            |_| responses.pop_front().expect("no extra request"),
            |_| Ok(()),
            |delay| {
                if delay.is_zero() {
                    return true;
                }
                waits.push(delay.as_millis());
                waits.len() < 4
            },
        );
        assert_eq!(waits, [500, 1000, 200, 500]);
    }

    #[test]
    fn fatal_transport_or_backend_errors_are_not_retried() {
        let mut attempts = 0;
        let mut messages = Vec::new();
        run_snapshot_stream(
            "automatic",
            Duration::from_millis(200),
            |_| {
                attempts += 1;
                Err(StreamRequestError::Fatal("broken pipe".to_string()))
            },
            |message| {
                messages.push(message);
                Ok(())
            },
            |_| true,
        );
        assert_eq!(attempts, 1);
        assert_eq!(messages, [json!({ "error": "broken pipe" })]);
    }

    #[test]
    fn cancelling_during_request_drops_the_late_response() {
        let cancelled = Cell::new(false);
        run_snapshot_stream(
            "manual",
            Duration::from_millis(200),
            |_| {
                cancelled.set(true);
                Ok(json!({}))
            },
            |_| panic!("cancelled stream must not send"),
            |_| !cancelled.get(),
        );
        assert!(!wait_for_generation(
            &AtomicU64::new(2),
            1,
            Duration::from_secs(5)
        ));
    }
}
