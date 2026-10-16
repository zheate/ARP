//! Opt-in, hardware-free benchmark of the production Rust/Python stream path.
//! ARP_BENCHMARK_PYTHON=/path/to/python ARP_BENCHMARK_OUTPUT=/absolute/path
//! cargo test --release --lib realtime_transport_benchmark -- --ignored --nocapture

use super::*;
use std::hint::black_box;

fn legacy_fingerprint(snapshot: &Value) -> Value {
    let Some(root) = snapshot.as_object() else {
        return snapshot.clone();
    };
    let mut fingerprint = serde_json::Map::new();
    for (key, value) in root {
        if ["capturedAt", "measurements", "seriesPatches"].contains(&key.as_str()) {
            continue;
        }
        let mut value = value.clone();
        if key == "pd" {
            if let Some(pd) = value.as_object_mut() {
                pd.remove("points");
            }
        }
        fingerprint.insert(key.clone(), value);
    }
    Value::Object(fingerprint)
}

// Fixed-input differential replay isolates Rust work from Python/OS scheduling.
// The reference is the original reader/fingerprint/wrapping algorithm.
fn transform(payload: &str, legacy: bool) -> (Value, String, Vec<u16>) {
    let mut response: Value = serde_json::from_str(payload).unwrap();
    let snapshot = if legacy {
        response.get("result").cloned().unwrap()
    } else {
        response.as_object_mut().unwrap().remove("result").unwrap()
    };
    validate_columnar_spectrum(&snapshot).unwrap();
    let fingerprint = if legacy {
        legacy_fingerprint(&snapshot)
    } else {
        snapshot_stream::snapshot_state_fingerprint(&snapshot)
    };
    let message = if legacy {
        json!({"snapshot": snapshot})
    } else {
        snapshot_stream::snapshot_message(snapshot)
    };
    let envelope = if legacy {
        json!({"__arpSnapshotStream": "benchmark", "message": message})
    } else {
        stream_envelope("benchmark", message)
    };
    let encoded = serde_json::to_string(&envelope).unwrap();
    let wide = encoded.encode_utf16().chain(std::iter::once(0)).collect();
    (fingerprint, encoded, wide)
}

fn measure(mut action: impl FnMut(), iterations: usize) -> Value {
    for _ in 0..5 {
        action();
    }
    let mut samples = Vec::with_capacity(iterations);
    for _ in 0..iterations {
        let start = Instant::now();
        action();
        samples.push(start.elapsed().as_secs_f64() * 1000.0);
    }
    samples.sort_by(f64::total_cmp);
    json!({"median_ms": samples[iterations / 2], "p95_ms": samples[(iterations * 95 / 100).min(iterations - 1)]})
}

#[test]
#[ignore = "requires explicit benchmark Python/output paths; never connects hardware"]
fn realtime_transport_benchmark() {
    let python = env::var("ARP_BENCHMARK_PYTHON").expect("set ARP_BENCHMARK_PYTHON");
    let output = PathBuf::from(env::var("ARP_BENCHMARK_OUTPUT").expect("set ARP_BENCHMARK_OUTPUT"));
    let fixture_dir = PathBuf::from(
        env::var("ARP_BENCHMARK_FIXTURES")
            .unwrap_or_else(|_| output.to_string_lossy().into_owned()),
    );
    std::fs::create_dir_all(&output).unwrap();
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let iterations = 100;
    let mut paired = Vec::new();
    for scenario in ["live_full", "live_delta", "pd_full"] {
        let payload =
            std::fs::read_to_string(fixture_dir.join(format!("8192-{scenario}.json"))).unwrap();
        let expected = transform(&payload, true);
        assert_eq!(expected, transform(&payload, false));
        let mut samples = [Vec::new(), Vec::new()];
        for iteration in 0..iterations {
            let order = if iteration % 2 == 0 { [0, 1] } else { [1, 0] };
            for version in order {
                let start = Instant::now();
                let actual = transform(black_box(&payload), version == 0);
                samples[version].push(start.elapsed().as_secs_f64() * 1000.0);
                assert_eq!(
                    actual, expected,
                    "full JSON, UTF-16 and fingerprint must match"
                );
            }
        }
        for values in &mut samples {
            values.sort_by(f64::total_cmp);
        }
        paired.push(json!({"points": 8192, "scenario": scenario, "iterations_per_version": iterations,
            "before": {"median_ms": samples[0][iterations/2], "p95_ms": samples[0][iterations*95/100]},
            "after": {"median_ms": samples[1][iterations/2], "p95_ms": samples[1][iterations*95/100]},
            "wire_and_fingerprint_equal": true,
            "scope": "Rust parse/result extraction/validation/fingerprint/wrap/JSON/UTF-16 only; alternating same input in one process; excludes Python/pipe/WebView"}));
    }
    let mut components = Vec::new();
    for points in [3648, 8192] {
        for scenario in [
            "live_full",
            "live_delta",
            "live_unchanged",
            "pd_full",
            "pd_delta",
        ] {
            let payload =
                std::fs::read_to_string(fixture_dir.join(format!("{points}-{scenario}.json")))
                    .unwrap();
            let response: Value = serde_json::from_str(&payload).unwrap();
            let snapshot = &response["result"];
            let parse = measure(
                || {
                    black_box(serde_json::from_str::<Value>(black_box(&payload)).unwrap());
                },
                iterations,
            );
            let result_clone = measure(
                || {
                    black_box(response.get("result").cloned().unwrap());
                },
                iterations,
            );
            let validate = measure(
                || {
                    validate_columnar_spectrum(black_box(snapshot)).unwrap();
                },
                iterations,
            );
            let fingerprint = measure(
                || {
                    black_box(snapshot_stream::snapshot_state_fingerprint(black_box(
                        snapshot,
                    )));
                },
                iterations,
            );
            let message = json!({"snapshot": snapshot});
            let envelope = stream_envelope("benchmark", message.clone());
            let message_wrap = measure(
                || {
                    black_box(snapshot_stream::snapshot_message(black_box(
                        snapshot.clone(),
                    )));
                },
                iterations,
            );
            let envelope_wrap = measure(
                || {
                    black_box(stream_envelope("benchmark", black_box(message.clone())));
                },
                iterations,
            );
            let stringify = measure(
                || {
                    black_box(serde_json::to_string(black_box(&envelope)).unwrap());
                },
                iterations,
            );
            let encoded = serde_json::to_string(&envelope).unwrap();
            let utf16 = measure(
                || {
                    black_box(
                        encoded
                            .encode_utf16()
                            .chain(std::iter::once(0))
                            .collect::<Vec<_>>(),
                    );
                },
                iterations,
            );
            components.push(json!({"points": points, "scenario": scenario, "parse": parse,
                "legacy_result_clone_reference": result_clone, "validate": validate, "fingerprint": fingerprint,
                "message_wrap_including_input_clone": message_wrap, "envelope_wrap_including_input_clone": envelope_wrap,
                "json_encode": stringify, "utf16": utf16}));
        }
    }
    let mut pipelines = Vec::new();
    for points in [3648, 8192] {
        let mut child = Command::new(&python)
            .args([
                "-m",
                "tools.benchmark_realtime_transport",
                "--serve",
                "--points",
                &points.to_string(),
            ])
            .current_dir(&root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let stdin = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let pending = Arc::new(Mutex::new(HashMap::new()));
        PythonBridge::start_response_reader(stdout, Arc::clone(&pending)).unwrap();
        let bridge = PythonBridge {
            child: Mutex::new(child),
            stdin: Mutex::new(BufWriter::new(stdin)),
            pending,
            next_request_id: AtomicU64::new(1),
            shutdown_confirmed: AtomicBool::new(true),
            force_exit_requested: AtomicBool::new(false),
        };
        for (view, resubscribe) in [("automatic", false), ("pd", false), ("pd", true)] {
            let interval = snapshot_interval_ms(view);
            let first = bridge
                .request("app.snapshot", json!({"view": view}))
                .unwrap();
            let expected_intensity = first.pointer("/measurements/spectrum/intensity").cloned();
            let mut elapsed_ms = Vec::new();
            let request_started = std::cell::Cell::new(Instant::now());
            let mut waits = Vec::new();
            // Production request, response reader, validation, revision/cursor logic,
            // and envelope serialization; WebView dispatch is outside this benchmark.
            let mut stream = || {
                run_snapshot_stream(
                    view,
                    Duration::from_millis(interval),
                    |params| {
                        request_started.set(Instant::now());
                        bridge
                            .request("app.snapshot", params)
                            .map_err(|e| StreamRequestError::Fatal(e.message()))
                    },
                    |message| {
                        if view == "automatic" {
                            assert_eq!(
                                message.pointer("/snapshot/measurements/spectrum/intensity"),
                                expected_intensity.as_ref()
                            );
                        } else if resubscribe || elapsed_ms.is_empty() {
                            assert_eq!(
                                message["snapshot"]["pd"]["points"]
                                    .as_array()
                                    .unwrap()
                                    .len(),
                                13_000
                            );
                        } else {
                            assert_eq!(
                                message["snapshot"]["seriesPatches"]["pd"]["points"]
                                    .as_array()
                                    .unwrap()
                                    .len(),
                                1
                            );
                        }
                        let payload =
                            serde_json::to_string(&stream_envelope("benchmark", message)).unwrap();
                        black_box(
                            payload
                                .encode_utf16()
                                .chain(std::iter::once(0))
                                .collect::<Vec<_>>(),
                        );
                        elapsed_ms.push(request_started.get().elapsed().as_secs_f64() * 1000.0);
                        Ok(())
                    },
                    |delay| {
                        if delay.is_zero() {
                            return true;
                        }
                        waits.push(delay.as_millis());
                        !resubscribe && waits.len() < iterations
                    },
                )
            };
            if resubscribe {
                for _ in 0..iterations {
                    stream();
                }
            } else {
                stream();
            }
            assert_eq!(waits, vec![interval as u128; iterations]);
            assert_eq!(elapsed_ms.len(), iterations);
            let mut sorted = elapsed_ms[5..].to_vec();
            sorted.sort_by(f64::total_cmp);
            pipelines.push(json!({"points": points, "view": view, "resubscribe_each_sample": resubscribe,
            "samples_ms": elapsed_ms, "median_ms": sorted[sorted.len()/2],
            "p95_ms": sorted[sorted.len()*95/100], "frame_count": iterations, "requested_interval_ms": interval,
            "note": "no sleep to isolate processing; actual sidecar queues/OS pipe/production Rust reader; excludes WebView dispatch"}));
        }
        bridge.child.lock().unwrap().kill().unwrap();
        bridge.child.lock().unwrap().wait().unwrap();
    }
    let report = json!({"components": components, "pipelines": pipelines, "paired_rust": paired});
    std::fs::write(
        output.join("rust.json"),
        serde_json::to_string_pretty(&report).unwrap(),
    )
    .unwrap();
    println!(
        "{}",
        serde_json::to_string_pretty(&report["pipelines"]).unwrap()
    );
}
