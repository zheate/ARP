# ARP UI / UX / Frontend Audit — 2026-09-14

## Scope and evidence boundary

Design baseline: `DESIGN.md`. The earlier root `design-qa.md` is a narrowly scoped metric-card alignment review, not evidence of whole-application acceptance.

Reviewed the frontend structure, shared controls, styling layers, automatic testing, manual configuration, PD acquisition, data browsing, shipping reports, appearance/settings, canvas charts, device/settings dialogs and safety/status presentation. The changes preserve the graphite desktop instrument workbench, ARP Orange for operating focus, existing fonts and numerical precision. No Rust/Python, device protocol, measurement algorithm, test sequence or backend data structure was changed.

- Baseline: `452847718cacb36e028928bfae1b77e9e55e4107`.
- Verified implementation: `58fd953d6a0d70fcb62bb66106be17936b8b905d`.
- GitHub Actions tested PR merge ref: `442d4e3438deb3d0588d43487c0fe10aebaa9d12`.
- PR: https://github.com/zheate/ARP/pull/10
- Successful verification run: https://github.com/zheate/ARP/actions/runs/34849750054

This is a presentation-layer review, not certification of the laser system's physical safety or measurement correctness.

## Findings and actual changes

| Priority | Finding / evidence | Implemented change | Why |
|---|---|---|---|
| P0 — operational risk | A device-settings modal did not contain keyboard focus. Baseline browser checks reproduced nine Tab-containment failures: three device dialogs at three viewports. Background controls and navigation shortcuts were not an adequate modal boundary. | Shared `DialogPortal` owns forward/reverse Tab containment, background inertness and focus restoration. Page-navigation shortcuts do not run while a modal is open. Existing callers retain save guards, initial-focus choice, acknowledgement requirements and hardware callbacks. | Prevent an operator's keyboard action from landing on a background control while a modal is being used. No physical-device accident was simulated or claimed. |
| P0 — data interpretation, defensive edge case | Numeric formatting already maps non-finite values to `--`, while the old FWHM limit color could still be successful for a non-finite value such as negative infinity (`typeof value === number` and `value <= limit`). This is a code-path finding, not an observed instrument output. | A shared presentation-only verdict requires finite live data before displaying success/failure. Invalid, stale and missing data have distinct text states. | A placeholder or invalid reading must not imply a passed measurement. Existing valid-value comparisons, units and precision remain unchanged. |
| P1 | At 1366×768, secondary sample/configuration bands displaced most of the spectrum below the viewport. | Height-aware chart allocation; more compact secondary bands and controls; retain primary metrics and explicit navigation labels. At very short logical viewports, spectrum precedes secondary historical power plots and the workspace scrolls vertically. | Allocate scarce height to measured values, plots and operating state rather than metadata. |
| P1 | The PD acquisition action was below the initial 1366×768 viewport. Several acquisition/calibration/unit fields could be edited while acquisition was running. | Move existing start/stop action and status above the independently scrolling configuration body; disable the configuration fieldset during acquisition; keep the existing stop action outside it. | Keep the stop control accessible and prevent displayed draft settings from being mistaken for active acquisition settings. Command names, payloads and acquisition behavior are unchanged. |
| P1 | Local dropdowns had no shared available-space placement rule. Composite fields often lacked a programmatic name; standard `aria-label` callers were not consumed by the local select. | Extract `Field` and `NativeSelect`; label context reaches nested inputs/comboboxes; unique list linkage; position dropdowns above/below within viewport and clipping ancestors; preserve local absolute positioning. | Improve keyboard identification and prevent clipping without moving dialog footers or introducing a second control language. |
| P1 | Valid live power used warning/accent emphasis; metric verdicts relied too heavily on color. Tabular digits alone did not reserve geometry across magnitude changes. | Neutral primary live values, semantic text/icon verdicts, reserved numeric and current-readback widths. | Keep Orange associated with operation/focus, distinguish missing data from a failed limit, and stabilize units and adjacent content. |
| P1 | The existing frontend test command failed when its esbuild SSR harness encountered a CSS import. | Add the CSS-empty loader to that harness; keep rendering actual application components and retain assertions. Add shared-field, select, PD-running and measurement-verdict tests. | Restore the existing test gate rather than bypassing it. |
| P2 | Control heights, corner radii and keyboard focus treatment were inconsistent; some numeric/start/phase treatments were decorative. | Shared 28/32/36 px control-height and 4/6/8 px radius tokens; explicit keyboard outlines in both themes; remove affected glow/looping treatments; respect reduced motion. Shipping mode buttons expose `aria-pressed`. | Improve consistency and long-session readability without redesigning the app as a web dashboard. |

## Measured layout outcomes

All dimensions below are CSS pixels from the demo, including its existing notification UI; they are not physical monitor pixels.

| Surface | Baseline | Final |
|---|---:|---:|
| 1366×768 automatic spectrum top / bottom | about 724 / 1006 | about 535 / 741 |
| 1366×768 automatic spectrum drawing height | 282, mostly below viewport | 206, entirely inside viewport |
| 1920×1080 spectrum drawing height | about 288 | 322 |
| 1366×768 PD start-action top | 827 | 175 |

This is not a claim that every plot fits on a 960×540 logical viewport. At that size the workspace intentionally scrolls vertically; current readings and operating controls retain usable geometry, and spectrum comes before secondary history.

### Regression found by screenshot inspection

The first implementation introduced a PD fieldset whose children no longer matched an existing direct-child container-query selector. This produced implicit grid columns and misaligned fields even though the build and initial tests passed. The selector was corrected in `index.css`, and explicit field-width/overlap assertions were added. The 1080p spectrum minimum was also raised after measuring actual canvas height rather than assuming card height equalled drawing height.

## Verification

- `npm test`: **97 passed, 0 failed, 0 skipped**.
- `npm run build`: **TypeScript and Vite production build passed**.
- Browser workspace/dialog suite: **63 captures, 152 assertions, 0 failed assertions, 0 console/page errors**.
- Additional actual-component state fixtures: **15 captures, 81 assertions, 0 failed assertions, 0 console/page errors**.
- Combined browser evidence: **78 captures and 233 assertions**. These counts exclude the 97 frontend tests.
- No document-level horizontal overflow detected in the captured workspace suite.
- Existing large JavaScript chunk warning remains; the warning threshold was not increased to hide it.

### Viewport matrix

| Logical viewport | Device pixel ratio | Meaning |
|---|---:|---|
| 1920×1080 | 1 | Primary desktop workspace |
| 1366×768 | 1 | Smaller desktop workspace |
| 2560×1440 | 1 | Larger desktop workspace |
| 1536×864 | 1.25 | Browser emulation of reduced logical area at 125% |
| 1280×720 | 1.5 | Browser emulation of reduced logical area at 150% |
| 960×540 | 2 | Browser emulation of reduced logical area at 200% |

The six workspaces were captured in idle mode, and automatic testing in its running fixture. Device dialogs, local dropdowns, script editing, save-script dialog, focus restoration, detection/footer stability and light-theme focus were checked. Additional fixtures mount the real protection, pause, shutdown-failure, PD and readout components with inert callbacks: long warnings scroll inside a bounded dialog; footer actions remain visible; required acknowledgements are retained; unconfirmed forced exit stays disabled; running PD parameters are disabled while stop stays enabled; actual React readout updates from `9.999` through `1000.000`, placeholders and negative values retain geometry and respect reduced motion.

No real instrument was connected. Native file dialogs, actual workbook ingestion, batch report generation, data-journal recovery and real hardware shutdown transitions were not exercised end to end. Data-browser empty/error presentation was captured; a populated large-record table was not visually load-tested.

## Files

Presentation:
- `tauri-ui/src/App.tsx`
- `tauri-ui/src/components/dialog-portal.tsx`
- `tauri-ui/src/components/field.tsx`
- `tauri-ui/src/components/native-select.tsx`
- `tauri-ui/src/components/measurement-state.tsx`
- `tauri-ui/src/components/numeric-readout.tsx`
- `tauri-ui/src/components/save-script-dialog.tsx`
- `tauri-ui/src/components/ui/input.tsx`
- `tauri-ui/src/lib/measurement-verdict.ts`
- `tauri-ui/src/index.css`
- `tauri-ui/src/workbench-appearance.css`

Verification:
- `tauri-ui/src/lib/measurement-verdict.test.ts`
- `tauri-ui/scripts/workbench-ui.test.mjs`
- `tauri-ui/scripts/live-current.test.mjs`
- `tauri-ui/scripts/ui-audit.mjs`
- `tauri-ui/scripts/ui-review.mjs`
- `tauri-ui/scripts/ui-state-review.mjs`
- `.github/workflows/frontend-qa.yml`

Playwright is installed in an isolated CI tools directory, not added to the shipped application's dependencies. The final QA workflow has read-only repository permissions; temporary audit transfer/apply workflows are not part of the final change set.

## Remaining work / acceptance gates

**P0 release-acceptance gate, not a newly confirmed backend defect:** validate real over-power protection, lost communication, stale data and unconfirmed shutdown on the supported Windows/WebView2 runtime with the physical instrument. Browser focus/layout tests do not establish that electrical output is actually off.

**P1:** native Windows 100/125/150/200% scaling, mixed-DPI monitor moves, window decorations and minimum window size still need native acceptance. Complete shipping single/batch report flows and populated data-browser table/long-record scenarios with representative local files. Extend isolated fixtures to completion/export failure and coefficient-profile history before claiming exhaustive modal-state coverage.

**P2:** consolidate more legacy CSS only where measured benefit justifies risk; measure page-level code splitting before changing the large bundle; continue reducing repeated settings markup without restructuring the backend or test workflow. Do not fill deliberate empty states with extra decorative cards.
