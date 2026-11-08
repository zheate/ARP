# First operation-motion pass — 2026-10-15

Implemented against the existing dirty working tree at `ddce8df750f3e3919b043b9e36849cb3a7880a02`, branch `audit/arp-ui-final-20260914`. The implementation and builds ran in an isolated copy. Two concurrent input styling tasks were observed; none of their files was edited. No branch switch, commit, push, app restart, native sidecar, device connection or real business-data write was performed.

## Exact product changes

- `tauri-ui/src/operation-motion.css`: colour feedback for known ordinary settings close/cancel/save, display/theme controls and local dropdown buttons is 100ms. Other buttons default to immediate colour feedback, including start/stop/output/discovery commands. Pressed buttons retain their target geometry; keyboard focus and disabled changes are immediate. Existing running/output button pulses are removed.
- Sidebar selection uses the existing background and leading pseudo-element for 140ms feedback. Its icon no longer scales. Existing page, title, header and sidebar entrances/stagger are disabled so controls are present immediately; the original page keys, navigation callbacks and mount/subscribe lifetimes remain intact.
- Settings panels use one 140ms opacity `0.96 → 1` / vertical `4px → 0` entrance; menus use one 140ms opacity / `-2px → 0` entrance. Backdrops and close/unmount are immediate. Local menus remain local; Radix owns its existing Portal, focus and Escape lifecycle.
- Lifecycle/status and alert dialogs are fully visible immediately, including protection, pause, shutdown failure and completion. Reduced motion removes every transition/entrance introduced in this pass.
- `tauri-ui/src/main.tsx`: one import after the existing styles. No product JavaScript or component event handler was added or changed.

No dependency changes. Installed versions checked: React 19.2.7, Motion 13.1.0, Radix 1.6.2, Tailwind 4.3.3, Tauri API 2.11.1. CSS alone was sufficient; no additional animation engine was used. NumberFlow, readout data and the chart drawing path remain unchanged.

## Verification

Added `scripts/operation-motion-qa.mjs` and `scripts/fixtures/operation-motion.tsx`. The harness mounts actual product controls, device settings, safety dialogs, Radix Select, metrics and canvas components; command/save ports collect in-memory results. It rejects non-loopback URLs. It never launches Tauri. The baseline and enabled runs share the same emitted fixture JavaScript, differing only in production CSS.

- Frontend suite: baseline and final **142/142**.
- Explicit TypeScript check and production build: passed. Existing >500kB chunk warning remains.
- Motion browser QA: **167/167**, zero `pageerror` events. 101 deterministic replay frames preserve sequence/index, revisions, all raw measurement arrays/device values, verdicts, automatic state, in-memory saved JSON bytes and command IDs/order/parameters. The two replay JSON artifacts are byte-identical.
- Start/stop command recording occurs in the same JavaScript event task, once per action. Disabled controls stay disabled. Discovery button geometry stays stable after the panel's entrance completes. Tests use synthetic discovery, never hardware.
- Three device dropdowns: local list, first Escape closes list, second Escape closes panel, background inert and focus restoration pass. Interrupted panel openings are exercised during replay; 36 actual App navigation changes are selected by the next frame, including rapid reversals. The original keyed page and subscription source are hash-verified unchanged.
- 1920×1080, 1366×768 and 960×540 × dark/light × normal/reduced: panel bounds, keyboard focus containment, target geometry, single entrance, Radix Escape/focus restoration and immediate protection/shutdown panels pass. Screenshots were visually checked at the large dark and small light sizes.
- Existing full UI audit: **454/455 checks**, 120 screenshots, zero recorded browser script errors. The existing `1920x1080-dpr1-spectrum-visible` assertion expects at least 320px; both baseline CSS and final CSS produce the identical spectrum rectangle `x=207, y=735, width=1694, height=314, bottom=1049`. This pre-existing layout/threshold issue is outside this motion pass. No check or threshold was relaxed.

## Measured timing

One sequential baseline/enabled run in headless Chromium 1228 on this Mac, production CSS and fixture bundle. No CPU throttling; these are small samples with warmup/order effects. Callback measurement times the DOM click through the synchronous in-memory command port; render measurement includes replay merge and a forced React commit, not GPU paint or backend acquisition. Navigation measures click to the next animation frame carrying the new `aria-current`.

| Measurement | Baseline median / p95 | Enabled median / p95 | Samples each |
|---|---:|---:|---:|
| Start/stop callback | 0.10 / 0.40 ms | 0.10 / 0.10 ms | 8 |
| Replay merge + React commit | 0.70 / 1.80 ms | 0.60 / 1.80 ms | 101 |
| Actual App navigation to next frame | 14.10 / 18.30 ms | 14.10 / 15.20 ms | 18 |

Replay maximum increased from 2.30 to 2.80ms in this run. These measurements do not establish zero overhead or a performance improvement. Colour transitions repaint; only opacity/transform entrances can be compositor accelerated. Production CSS grew from 178,999 to 181,735 bytes before gzip.

## Evidence and rerun

Evidence is under `output/playwright/operation-motion/`: `results.json`, identical `replay-baseline.json` / `replay-enabled.json`, `source-invariants.json`, baseline CSS, test/build logs, 13 screenshots and `spectrum-baseline.json`. The wider audit is retained under the task's isolated `qa-evidence/` and its log is copied into motion evidence.

From `tauri-ui`, after `npm test` and `npm run build`, start a dedicated loopback production preview on an unused port (for example 1438). Run:

```sh
ARP_MOTION_QA_URL='http://127.0.0.1:1438/?demo=1' \
ARP_MOTION_BASELINE_CSS='../output/playwright/operation-motion/baseline.css' \
ARP_PLAYWRIGHT_MODULE='/absolute/path/to/playwright/index.mjs' \
node scripts/operation-motion-qa.mjs
```

Set `ARP_CHROMIUM_EXECUTABLE` only when selecting an already installed browser binary. The script adds fixture files under `dist/__motion-qa`; run the normal production build afterward to remove QA fixtures before distribution. Existing UI audit runs with the repository's documented `ARP_UI_QA_URL`, `ARP_PLAYWRIGHT_MODULE`, and `ARP_UI_QA_ASSERT=1` variables.

## Limits

Browser replay and saved JSON comparison do not validate hardware sampling cadence, physical power-off, native file dialogs, real Excel/JSONL persistence or device-driver timing. This pass did not execute new backend writes. Windows/WebView2 native DPI, lower-spec hardware/GPU behaviour, and physical instruments remain pending release acceptance. In-memory command fixtures cannot establish instrument command timing. No real device or running test App was used.
