use glob::{MatchOptions, Pattern};
use std::path::Path;

fn main() {
    let patterns: Vec<_> = std::env::args().skip(1).map(|p| Pattern::new(&p).unwrap()).collect();
    let options = MatchOptions {
        require_literal_separator: true,
        require_literal_leading_dot: false,
        ..Default::default()
    };
    let allowed = |path: &str| patterns.iter().any(|pattern| pattern.matches_path_with(Path::new(path), options));
    for path in [
        r"C:\Users\24561\Desktop\test\100W蓝光\HEA415398\熔接封盖后\HEA415398_出货报告.pdf",
        r"C:\Users\24561\Desktop\test\100W蓝光\HEA415398\熔接封盖后\2026_09_10_10_33.xlsx",
        r"D:\Test Reports\SN-002\report.pdf",
        r"\\server\shared\Reports\report.pdf",
    ] {
        assert!(allowed(path), "result should be allowed: {path}");
    }
    assert!(!allowed(r"C:\Windows\System32\cmd.exe"));
    println!("PASS: PDF/Excel paths (Chinese, spaces, other drives, UNC) allowed; executable denied.");
}
