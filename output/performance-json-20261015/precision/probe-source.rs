// One-off probe compiled inside the existing Rust test crate, removed after measurement.
use super::*;
#[test]
#[ignore = "explicit JSON precision fixture directory required; no devices"]
fn json_encoder_precision_probe() {
    let directory = PathBuf::from(env::var("ARP_JSON_PRECISION").expect("set ARP_JSON_PRECISION"));
    let read = |name| serde_json::from_slice::<Vec<f64>>(&std::fs::read(directory.join(name)).unwrap()).unwrap();
    let old = read("stdlib.json");
    let candidate = read("orjson.json");
    let original = std::fs::read(directory.join("original.bin")).unwrap();
    assert_eq!(old.len(), candidate.len());
    let mut changed = Vec::new();
    let mut reference_original_mismatches = 0;
    for (index, (a, b)) in old.iter().zip(&candidate).enumerate() {
        let expected = u64::from_le_bytes(original[index * 8..index * 8 + 8].try_into().unwrap());
        if a.to_bits() != expected { reference_original_mismatches += 1; }
        if a.to_bits() != b.to_bits() {
            changed.push(json!({"index": index, "before": a, "candidate": b,
                "before_bits": a.to_bits(), "candidate_bits": b.to_bits(), "original_bits": expected}));
        }
    }
    let report = json!({"finite_double_count": old.len(), "changed_rust_values": changed.len(),
        "existing_reference_vs_original_mismatches": reference_original_mismatches,
        "first_changes": changed.iter().take(10).collect::<Vec<_>>()});
    std::fs::write(directory.join("rust.json"), serde_json::to_string_pretty(&report).unwrap()).unwrap();
    println!("{report}");
}

