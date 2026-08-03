---
name: diagnose-tauri-webview2-memory
description: Diagnose and fix sustained or apparent memory growth in Windows Tauri WebView2 applications, especially React/Vite UIs with live charts, IPC streams, polling, or hardware telemetry. Use when users report that WebView2, a Tauri window, the renderer process, or a "Power Test"-style long-running UI keeps increasing memory; when debug and release builds behave differently; when Task Manager screenshots show growing WebView2 memory; or for Chinese reports such as "内存持续增长", "还是在增加", "WebView2 管理器内存变大", and "Power Test 内存泄漏".
---

# Diagnose Tauri WebView2 Memory

Diagnose with matched workloads before changing code. Distinguish a true leak from delayed garbage collection and from development-runtime allocation pressure.

## Workflow

1. Preserve hardware safety. Do not start, stop, connect, configure, or command instruments merely to reproduce UI memory growth. Prefer synthetic snapshots that exercise only the frontend.
2. Read repository instructions and inspect the working tree. Preserve unrelated user changes.
3. Identify the exact running executable and WebView2 child tree. Record whether it is `target/debug` or `target/release`, the renderer PID, WebView2 runtime version, and effective browser arguments.
4. Run `scripts/sample-webview2-memory.ps1` for a baseline. Measure the renderer separately from the GPU and browser processes. Treat Task Manager's grouped total as supporting evidence, not the primary metric.
5. Reproduce with a bounded, changing dataset at the real UI refresh rate. Keep hardware sampling unchanged. If real acquisition is unavailable, inject a temporary sinusoidal chart series capped to the production history length.
6. Compare the same workload in development and production React runtimes. React StrictMode and the development runtime can create much higher transient allocation pressure during repeated commits.
7. If needed, enable a temporary CDP port and collect:
   - renderer working set and private bytes;
   - `Runtime.getHeapUsage`;
   - `Memory.getDOMCounters`;
   - document, event-listener, and canvas counts;
   - before/after `HeapProfiler.collectGarbage` values.
8. Isolate one layer at a time: transport only, transport plus state update, fixed references versus changing arrays, Canvas disabled versus enabled, development versus production runtime.
9. Implement the smallest fix supported by the comparison. Remove every diagnostic flag, synthetic series, remote-debugging port, and temporary script before final validation.
10. Validate with at least five minutes of changing data and report the range and low-water trend, not one screenshot.

## Interpretation Matrix

| Observation | Likely cause | Next action |
|---|---|---|
| DOM nodes grow continuously | Detached DOM/component leak | Take heap snapshots and trace retainers |
| JS heap and private bytes both grow; forced GC does not lower either | Retained JS graph | Inspect subscriptions, closures, caches, and unbounded histories |
| JS heap falls after GC but private bytes do not | Native/external allocation or committed heap pages | Compare transport, Canvas, and runtime modes |
| Messages arrive but memory is stable until React state commits | Render/runtime allocation pressure | Reduce commit scope/rate and compare production React |
| Canvas disabled has the same slope | Canvas is not the root cause | Stop modifying chart drawing and isolate state/runtime |
| Release is stable but `tauri dev` grows | React/Vite development runtime | Serve production React during Tauri development or test the release executable |
| GPU is flat while renderer grows | Main renderer path | Focus on JS, React, IPC, and DOM rather than GPU flags |
| Memory rises and periodically returns to the same low water | Delayed collection, not an unbounded leak | Tune thresholds only if operationally necessary |

## Proven Tauri/React Pattern

For continuous telemetry UIs where release is stable but `tauri dev` grows:

- Remove root `React.StrictMode` if duplicate development renders are unacceptable for this long-running desktop workload.
- Start Vite with `NODE_ENV=production` before importing Vite, while retaining its server and file watching. A small Node launcher using `createServer()` is cross-platform.
- Verify the served dependency chunk contains production-runtime markers and no `react.development` or `jsxDEV` markers.
- Keep the normal snapshot rate bounded; 2 Hz is usually enough for a power chart even when hardware sampling is faster.
- Suppress snapshots whose business fingerprint and series revisions are unchanged.
- Bound all displayed histories and use append patches/cursors rather than repeatedly replacing large full histories.
- On Windows, prefer a native WebView2 message path for frequent live snapshots when profiling shows Tauri channel delivery overhead. Use a unique subscription ID and always remove the matching listener.
- A small V8 old-generation limit and periodic GC may keep transient allocations bounded, but use them only after proving objects are collectible. They do not repair a retained-object leak.

Do not claim the issue is fixed merely because memory is lower. Require a plateau under a changing workload in the same startup mode the user actually uses.

## Commands

Run the bundled read-only sampler from PowerShell:

```powershell
& "$HOME/.codex/skills/diagnose-tauri-webview2-memory/scripts/sample-webview2-memory.ps1" `
  -AppName tauri-ui -SampleCount 21 -IntervalSeconds 15
```

Typical validation commands for this project class:

```powershell
npm run build
cargo test
python -m pytest tests/test_tauri_bridge.py -q
git diff --check
```

## Reporting

State:

- the executable mode and exact renderer PID tested;
- test duration, update rate, and whether data changed;
- private-memory and working-set ranges;
- whether low water rose, plateaued, or returned after GC;
- diagnostics removed;
- builds/tests run;
- any remaining difference between development and release behavior.
