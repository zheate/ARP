# Design QA

- Source visual truth: `C:\Users\24561\AppData\Local\Temp\codex-clipboard-bf86ff9d-772d-4eea-8876-2fd73352d84b.png`
- Source pixels: 603 x 173
- Implementation: `http://127.0.0.1:1420/?demo=1`
- Implementation screenshot: Codex in-app browser capture from the current local preview (inline capture; no local screenshot path exposed by the browser)
- Browser viewport: 1280 x 720 CSS px
- Device pixel ratio: 2.2649998664855957; the browser capture was reviewed at its displayed viewport size
- State: dark theme, automatic-test demo, live metric cards visible

## Full-view comparison evidence

The four live metric cards preserve the existing typography, colors, borders, radii, and content. Each title is aligned to the upper-right of its card, while each value-and-unit row is centered across the card's content width.

DOM geometry confirmed that all four value rows have the same horizontal center as their cards (`centeredDelta: 0`) and that all four title rows use `justify-content: flex-end`.

## Focused region comparison evidence

A separate crop was not needed because all four card labels, values, and units were legible in the 1280 x 720 full-view capture. The live-power card was also checked independently before applying the same rule to the other three cards.

## Required fidelity surfaces

- Fonts and typography: unchanged from the existing workbench theme; numeric and unit weights remain intact.
- Spacing and layout rhythm: values and units are centered as one group; titles share one right-aligned edge within their respective cards.
- Colors and visual tokens: unchanged; semantic metric colors and muted units remain intact.
- Image quality and asset fidelity: no image assets are present in the modified card region.
- Copy and content: unchanged (`实时功率`, `中心波长`, `FWHM`, `PIB` and their units).

## Comparison history

- Earlier finding (P2): only the live-power card used the requested alignment, leaving the other three cards inconsistent.
- Fix: generalized the alignment selectors from the power card to every card inside `.automatic-metric-grid`.
- Post-fix evidence: browser capture shows all four titles at upper-right and all four value/unit groups centered; computed geometry reports zero horizontal center offset for every value row.

## Findings

No remaining P0, P1, or P2 visual mismatches for the requested card alignment.

## Interaction and console checks

- No interaction behavior was changed by this CSS-only adjustment.
- Browser console errors: none.

final result: passed

---

## 2026-09-14 · Desktop instrument UI / UX audit

The alignment review above is historical and limited to its original card-alignment request. It is not whole-application acceptance. The following review uses `DESIGN.md` as the product baseline and extends the existing browser-demo QA.

### Scope and preserved boundaries

Reviewed automatic test, manual configuration, PD acquisition, data browser, shipping reports, appearance/settings, shared inputs/selects/buttons, measurement readouts, canvas charts, device settings, specifications, script saving, profile management, pause/completion/protection/exit dialogs. The implemented changes are presentation, input accessibility, focus management, layout and QA only. Device protocols, Rust/Python code, acquisition algorithms, data formats, test sequencing, precision and export algorithms are unchanged. `DESIGN.md` and product dependencies are unchanged.

### Findings → implementation

| Priority | Finding and evidence | Actual change / rationale |
|---|---|---|
| P0 | Device modal Tab navigation escaped to the underlying application in all 9 baseline size/device combinations. `aria-modal` alone did not prevent background interaction. | Shared `DialogPortal`: isolate background with `inert`, contain forward/backward keyboard focus, restore trigger focus, suppress page-navigation shortcuts while a modal is open. Existing command, close, acknowledgement and save policies remain with callers. |
| P0 | `PowerProtectionDialog` unconditionally said the device had entered powered-off protection, although this component receives a notice message rather than a confirmed shutdown result. | Replace the unconditional claim with “保护已触发；请核对电源实际输出状态”. Do not infer verified safe shutdown from a protection notice. Actual shutdown-result presentation and force-exit confirmation are retained. |
| P1 | At 1366×768 the baseline spectrum canvas began at y=724.1 and ended at y=1006.1, mostly below the visible work area. | Reduce secondary chrome and metadata density; keep one-row setup above 1100 logical pixels; height-aware plot allocation; reserve spectrum height at 1080p; at extremely short viewports put measurements/spectrum before secondary history. |
| P1 | PD start control was below the 768px viewport (y=827–859). Runtime parameters could be edited while capture was running. | Keep acquisition action and status outside the scrolling configuration region. Disable the parameters as one labelled fieldset during acquisition. Preserve the existing start/stop/save command. Fix container-query column spans for that fieldset; retain stop outside it. |
| P1 | Form labels were visually present but not associated with composite inputs. Existing standard `aria-label` callers were ignored by the custom select. | Shared `Field` context, unique label IDs, Input and NativeSelect association, support both existing `ariaLabel` and standard `aria-label`. |
| P1 | Local dropdowns always opened downwards and could be clipped by a scrolling dialog. | Measure viewport and clipping ancestors, choose available direction/height, retain a local absolute listbox, close on focus leaving and when disabled. No portal or normal-flow popup that moves the footer. |
| P1 | Pass/fail and live readings competed in colour; stale/missing data could not be distinguished with a textual verdict. Variable-length numbers could move adjacent units. | Neutral primary live numbers, compact semantic icon + text verdicts, explicit missing/stale/invalid states, stable numeric/current widths. No changed numeric formatting or limit comparisons. |
| P2 | Control/radius values and theme focus treatment diverged; changing digits and start/phase indicators used unnecessary glow/motion. | Shared 28/32/36px control and 4/6/8px radius tokens; visible keyboard outline in both themes; remove decorative glow, respect reduced motion and keep only a brief change indication. Shipping mode buttons expose their selected state. |

### Measured before/after checkpoint

Baseline: main `452847718cacb36e028928bfae1b77e9e55e4107`, browser-only demo. Initial patched checkpoint: `56f0bc958352f47b33455e994b3433340a5e072b` (subsequently tightened by the PD container-query and spectrum-height fixes).

| Measurement | Baseline | Initial verified patch |
|---|---:|---:|
| 1366×768 spectrum canvas vertical range | 724.1–1006.1 px | 534.8–741.0 px |
| 1366×768 PD start button vertical range | 827–859 px | 175–207 px |
| Device-modal Tab escapes, 3 devices × 3 viewports | 9 / 9 | 0 / 9 |
| Initial patched browser captures / interaction checks | — | 63 / 145 |

These figures are checkpoints, not a claim that later commits were visually accepted without rerunning. The final workflow records the exact tested commit, screenshots, geometry, assertions and logs together. The 1080p/1440p spectrum acceptance now explicitly requires at least 320 logical pixels of canvas height; 1366×768 requires at least 180 and an entirely visible canvas.

### Reproducible acceptance

`npm test` and `npm run build` are necessary but not sufficient. `scripts/live-current.test.mjs` needed an esbuild CSS-empty loader for server rendering; this fixes the test harness, not an acquisition algorithm. New render tests cover field naming, select naming and PD running-state locks; unit tests cover measurement validity/verdicts.

Run the production browser preview on loopback. With Playwright available (CI installs an isolated pinned copy, not a product dependency):

```sh
cd tauri-ui
npm ci
npm test
npm run build
npm run preview -- --host 127.0.0.1 --port 1420
# In another terminal, with ARP_PLAYWRIGHT_MODULE set when installed outside this project:
ARP_UI_QA_ASSERT=1 node scripts/ui-audit.mjs
node scripts/dialog-qa.mjs
```

`ui-review.mjs` distinguishes the static in-progress `?demo=1` fixture from the idle `?demo=1&run=1` fixture; manual/report navigation being locked in the former is expected, not a failed route. It checks all six workspaces, visible PD controls and non-overlapping fields, plot geometry, numeric-width stress, modal focus, local-select Escape/dimensions, detect-footer stability, disabled navigation and light-theme focus. Synthetic numeric stress changes DOM text only, then restores the original nodes; it is not a measurement simulation.

`dialog-qa.mjs` compiles the real dialog components into disposable test-only fixtures, links the production CSS, and checks 1920×1080, 1366×768 and 960×540@2x. It covers protection, pause, incomplete shutdown/completion, pending shutdown, profile management and save-script validation/failure. Callbacks are mocks; no native IPC, hardware command or real file export is invoked. It checks exact first/last focus wrapping, long-message footer reachability, required physical confirmation for force exit, non-dismissible acknowledgement/pause states, disabled pending controls and preserved inputs after a save error. Fixtures are removed from `dist` afterwards.

### Resolution and DPI interpretation

The browser matrix is 1920×1080@1x, 1366×768@1x, 2560×1440@1x, 1536×864@1.25x, 1280×720@1.5x and 960×540@2x. DPR and logical viewport are recorded separately. The last three emulate the available logical area on a 1920×1080 panel; they are **not native Windows/WebView2 DPI acceptance**. At a 540px logical height, secondary content deliberately scrolls rather than shrinking measurement text to unreadable sizes.

### Remaining work / release boundary

| Priority | Remaining work | Acceptance needed |
|---|---|---|
| P0 release gate | Hardware safe-stop and shutdown confirmation under real disconnect/timeout; native modal interactions with actual instruments. | Bench test on Windows; this browser review does not certify hardware safety. |
| P1 | Native 100/125/150/200% Windows scaling, mixed-monitor DPI, title-bar/native dialogs and real instrument-name extremes. | Native Tauri/WebView2 screenshots and keyboard/device tests. |
| P1 | Populated shipping/data tables: keyboard row navigation, sort semantics, large-record performance and successful native export paths. | Controlled Excel/report/record fixtures and real native export; demo error/empty states do not prove these paths. |
| P1 | Manual workspace in short windows and direct readback/source/age labelling across every instrument. | Verify with live-state replay without substituting setpoints for measured current. |
| P2 | Remaining legacy CSS layers, large App module and bundle-size warning. | Incremental consolidation/code splitting with screenshot coverage, not a business-logic rewrite. |

A green browser workflow is evidence for the exercised frontend states only. Review screenshots as well as assertions; do not equate a production build or simulated DPR with complete desktop/hardware acceptance.
