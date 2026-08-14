# ARP Rust realtime core

This crate accelerates calculations that run for every spectrum or PD sample
block while the existing Python process remains the single hardware owner.
It intentionally exposes a small versioned C ABI instead of loading vendor
drivers into the Tauri UI process.

Current native operations:

- spectrum peak, centroid, and FWHM statistics;
- Scripts Runner-compatible PIB calculation;
- PD sample-block summary statistics.

Build the Windows DLL with:

```powershell
cargo build --manifest-path rust-realtime-core/Cargo.toml --release --locked
```

The Python wrapper discovers the development DLL under `target/release` or
`target/debug`. The Tauri packaging script builds the release DLL and embeds it
in the Python device sidecar. Set `ARP_DISABLE_NATIVE_REALTIME=1` to force the
Python reference implementations for diagnosis. Set `ARP_REALTIME_CORE_DLL` to
an explicit DLL path when testing another build.

Loading or ABI validation failures automatically use the Python reference
implementation, so the accelerator cannot prevent the application from
starting.
