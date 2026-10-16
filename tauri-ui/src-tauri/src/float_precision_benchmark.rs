//! Opt-in precision replay against original Python IEEE-754 binary64 bits.
//! Uses real workbook fixtures without constructing a device backend.

use super::*;
use std::hint::black_box;

#[test]
fn python_response_float_bits_are_preserved() {
    // Actual workbook values: this failed before float_roundtrip was enabled.
    // Integer bit oracles come from Python struct.pack, not another Rust parse.
    for (decimal, expected) in [
        ("100.10484927916119", 0x405906b5d9c0406a_u64),
        ("914.9965972639145", 0x408c97f907fc7e5e),
        ("973.1593463235331", 0x408e6946575d8280),
        ("901.1893788129465", 0x408c2983d90a0149),
        ("11.196121987951809", 0x4026646a19e88f1e),
        ("973.1465812759973", 0x408e692c32cdd4ea),
        ("-0.0", 0x8000000000000000),
        ("5e-324", 1),
        ("-5e-324", 0x8000000000000001),
        ("2.2250738585072014e-308", 0x0010000000000000),
        ("1.7976931348623157e308", 0x7fefffffffffffff),
        (
            "1.00000000000000011102230246251565404236316680908203125",
            0x3ff0000000000000,
        ),
        (
            "1.00000000000000033306690738754696212708950042724609375",
            0x3ff0000000000002,
        ),
    ] {
        let wire =
            format!(r#"{{"v":2,"id":"precision","ok":true,"result":{{"sample":{decimal}}}}}"#);
        let parsed: Value = serde_json::from_str(&wire).unwrap();
        assert_eq!(
            parsed["result"]["sample"].as_f64().unwrap().to_bits(),
            expected,
            "{decimal}"
        );
        let encoded = serde_json::to_string(&stream_envelope(
            "precision",
            snapshot_stream::snapshot_message(parsed["result"].clone()),
        ))
        .unwrap();
        let forwarded: Value = serde_json::from_str(&encoded).unwrap();
        assert_eq!(
            forwarded["message"]["snapshot"]["sample"]
                .as_f64()
                .unwrap()
                .to_bits(),
            expected
        );
    }
}

#[test]
fn float_parser_preserves_control_protocol_types_and_errors() {
    let wire = r#"{"v":2,"id":"control","ok":true,"result":{"enabled":false,"currentA":1,"revision":18446744073709551615,"optional":null,"label":"976 nm"}}"#;
    let parsed: Value = serde_json::from_str(wire).unwrap();
    assert_eq!(
        serde_json::to_string(&parsed)
            .unwrap()
            .parse::<Value>()
            .unwrap(),
        parsed
    );
    assert_eq!(parsed["result"]["revision"].as_u64(), Some(u64::MAX));
    assert!(parsed["result"]["currentA"].is_u64());
    assert_eq!(parsed["result"]["enabled"].as_bool(), Some(false));
    assert!(parsed["result"]["optional"].is_null());
    assert_eq!(parsed["result"]["label"], "976 nm");
    for wire in ["NaN", "Infinity", "1e309", "01", "1.", "[1,]"] {
        assert!(serde_json::from_str::<Value>(wire).is_err(), "{wire}");
    }
}

fn read_through_production_bridge(input: &std::path::Path) -> Value {
    let python = env::var("ARP_BENCHMARK_PYTHON").expect("set ARP_BENCHMARK_PYTHON");
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut child = Command::new(python)
        .args(["-m", "tools.inspect_json_float_precision", "--serve"])
        .arg(input)
        .current_dir(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
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
    let result = bridge.request("app.snapshot", json!({"view": "data"}));
    let mut child = bridge.child.lock().unwrap();
    child.kill().unwrap();
    child.wait().unwrap();
    result.unwrap()
}

fn write_forwarded(output: &std::path::Path, name: &str, result: Value) {
    let envelope = stream_envelope(
        "precision-replay",
        snapshot_stream::snapshot_message(result),
    );
    let encoded = serde_json::to_string(&envelope).unwrap();
    let wide: Vec<u16> = encoded.encode_utf16().collect();
    assert_eq!(String::from_utf16(&wide).unwrap(), encoded);
    std::fs::write(output.join(format!("{name}.forwarded.json")), encoded).unwrap();
}

#[test]
#[ignore = "explicit precision fixture/output paths required; no hardware"]
fn replay_python_float_precision() {
    let directory = PathBuf::from(env::var("ARP_FLOAT_FIXTURES").expect("set ARP_FLOAT_FIXTURES"));
    let output = PathBuf::from(env::var("ARP_FLOAT_OUTPUT").expect("set ARP_FLOAT_OUTPUT"));
    std::fs::create_dir_all(&output).unwrap();
    let pipe_output = output.join("pipe");
    std::fs::create_dir_all(&pipe_output).unwrap();
    let cases: Vec<Value> =
        serde_json::from_slice(&std::fs::read(directory.join("cases.json")).unwrap()).unwrap();
    let mut reports = Vec::new();
    let mut total_changes = 0;
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let wire = std::fs::read_to_string(directory.join(format!("{name}.input.json"))).unwrap();
        let pointers: Vec<String> = serde_json::from_slice(
            &std::fs::read(directory.join(format!("{name}.pointers.json"))).unwrap(),
        )
        .unwrap();
        let bits = std::fs::read(directory.join(format!("{name}.bits.bin"))).unwrap();
        let started = Instant::now();
        let parsed: Value = serde_json::from_str(&wire).unwrap();
        let parse_ms = started.elapsed().as_secs_f64() * 1000.0;
        let from_pipe =
            read_through_production_bridge(&directory.join(format!("{name}.input.json")));
        let mut changes = Vec::new();
        let mut max_absolute_error = 0.0_f64;
        let mut max_ulp = 0_u64;
        let mut path_counts = serde_json::Map::new();
        for (index, pointer) in pointers.iter().enumerate() {
            let expected_bits =
                u64::from_le_bytes(bits[index * 8..index * 8 + 8].try_into().unwrap());
            let actual = parsed.pointer(pointer).unwrap().as_f64().unwrap();
            assert_eq!(
                from_pipe
                    .pointer(pointer.strip_prefix("/result").unwrap())
                    .unwrap()
                    .as_f64()
                    .unwrap()
                    .to_bits(),
                actual.to_bits(),
                "actual response reader must agree with isolated parser: {name}{pointer}"
            );
            if actual.to_bits() != expected_bits {
                let expected = f64::from_bits(expected_bits);
                let ordered = |value: u64| {
                    if value >> 63 == 1 {
                        !value
                    } else {
                        value | (1 << 63)
                    }
                };
                let ulp = ordered(expected_bits).abs_diff(ordered(actual.to_bits()));
                max_ulp = max_ulp.max(ulp);
                max_absolute_error = max_absolute_error.max((actual - expected).abs());
                let category = if pointer.contains("/wavelengthNm/") {
                    "wavelength"
                } else if pointer.contains("/intensity/") {
                    "intensity"
                } else {
                    "other"
                };
                let count = path_counts
                    .get(category)
                    .and_then(Value::as_u64)
                    .unwrap_or(0);
                path_counts.insert(category.to_owned(), json!(count + 1));
                if changes.len() < 8 {
                    changes.push(json!({"pointer": pointer, "expected": expected, "actual": actual,
                        "expected_bits": format!("{expected_bits:016x}"), "actual_bits": format!("{:016x}", actual.to_bits()),
                        "absolute_error": (actual - expected).abs(), "ulp": ulp}));
                }
            }
        }
        let mismatch_count: u64 = path_counts
            .values()
            .map(|value| value.as_u64().unwrap())
            .sum();
        total_changes += mismatch_count;
        // Actual production wrapping/encoding, then consumed by the JS replay.
        write_forwarded(&output, name, parsed.get("result").unwrap().clone());
        write_forwarded(&pipe_output, name, from_pipe);
        reports.push(json!({"name": name, "float_count": pointers.len(), "mismatch_count": mismatch_count,
            "path_counts": path_counts, "max_absolute_error": max_absolute_error, "max_ulp": max_ulp,
            "first_changes": changes, "parse_ms_single_sample": parse_ms}));
    }
    let report = json!({"cases": reports, "total_mismatches": total_changes});
    std::fs::write(
        output.join("rust.json"),
        serde_json::to_string_pretty(&report).unwrap(),
    )
    .unwrap();
    println!("{report}");
    assert_eq!(
        total_changes, 0,
        "Rust must preserve every original Python binary64 bit"
    );
}

#[test]
#[ignore = "explicit frozen precision fixture/output paths required; no hardware"]
fn benchmark_float_precision_parse() {
    let directory = PathBuf::from(env::var("ARP_FLOAT_FIXTURES").expect("set ARP_FLOAT_FIXTURES"));
    let output = PathBuf::from(env::var("ARP_FLOAT_OUTPUT").expect("set ARP_FLOAT_OUTPUT"));
    std::fs::create_dir_all(&output).unwrap();
    let mut reports = Vec::new();
    for name in [
        "real-0-full",
        "real-1-full",
        "real-2-full",
        "8192-dense-live-delta",
        "24x8192-full-spectra",
    ] {
        let wire = std::fs::read_to_string(directory.join(format!("{name}.input.json"))).unwrap();
        let mut samples = Vec::new();
        for index in 0..65 {
            let start = Instant::now();
            black_box(serde_json::from_str::<Value>(black_box(&wire)).unwrap());
            if index >= 5 {
                samples.push(start.elapsed().as_secs_f64() * 1000.0);
            }
        }
        samples.sort_by(f64::total_cmp);
        reports.push(
            json!({"name": name, "bytes": wire.len(), "iterations": samples.len(),
            "median_ms": samples[30], "p95_ms": samples[57]}),
        );
    }
    std::fs::write(
        output.join("parse-timing.json"),
        serde_json::to_string_pretty(&reports).unwrap(),
    )
    .unwrap();
    println!("{}", json!(reports));
}
