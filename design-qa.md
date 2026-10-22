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

## 2026-09-14 — Full workbench review

The record above is the historical card-alignment review, not a claim that every workspace or safety state was validated. The current systematic audit, findings, priority decisions, measured geometry and limitations are in [the workbench audit](docs/ui-audit-2026-09-14.md). `DESIGN.md` remains the design baseline.

The reusable entry point is `tauri-ui/scripts/ui-audit.mjs`; CI is `.github/workflows/frontend-qa.yml`. Run the existing tests and production build before browser review. Use a loopback demo only; the QA refuses external hosts and does not start Tauri or communicate with hardware.

Evidence includes screenshots plus JSON checks for six logical-viewport/DPR combinations, real numeric updates, modal focus/background isolation, local dropdown geometry, failed/pending saves, long diagnostic text, safe-exit guards and PD running controls. Real React components and the production CSS are used in synthetic state fixtures. No product dependency was added for browser QA.

The first accepted source revision passed 97 frontend tests and 455 browser checks with 120 screenshots. Consult the artifact's commit.txt for the exact source revision of every subsequent run; build success alone is not visual acceptance.

Windows/WebView2 native 100/125/150/200% DPI, physical devices and real report/export data remain explicit release-validation requirements. At a 960×540 logical viewport, intentional vertical scrolling is retained; DPR emulation is not native Windows certification.

---

## 2026-10-15 — Automatic test proportions and progress

Scope: implement the selected automatic-test design and the user's later proportion rules in the existing product. Manual-test layout and device commands are outside this change.

- Visual target: `/Users/zh/.codex/generated_images/01a13e04-5e36-7473-8a07-46d6a20dfd58/exec-2c999ffa-68e1-4dec-a18a-9017e995debc.png`.
- The later approved rule supersedes the mockup's pixel heights: device rail, power-chart row, metrics and spectrum share one grid in the ratio **1.5:4:1:4**, after subtracting other content and gaps. Device columns are equal width. The no-spectrum workflow retains its existing conditional content.
- Comparison evidence: `output/playwright/automatic-proportions-20261015/design-comparison.png`, combining source and implementation at 1586×992. Both show a dark automatic-test running screen. The source uses illustrative point 1/6 and measurements; the implementation uses the existing deterministic point 4/6 fixture. These data differences and the demo-only banner are intentional, not evidence of acquisition correctness. Existing chart rendering, fonts, controls and measurement values were retained.
- An initial P2 issue was found in distant density: the legacy two-column metrics rule produced two rows inside the new proportional metric area. The automatic-only selector now keeps four equal metric columns and one row. Post-fix evidence is in `dark-远看-1180.png` and `light-远看-1180.png`; no numeric-card or progress overflow remains in those captures.
- The progress stage also distinguishes waiting for wavelength stability from waiting for power stability; point index and durable recorded count remain separate. Elapsed time comes from the backend snapshot and uses no UI-generated clock or completion estimate.
- Final geometry matrix: `output/playwright/automatic-proportions-20261015/layout-results.json`. All 18 combinations passed: dark/light × compact/comfortable/distant × 1180×720, 1440×900 and 1920×1080. Device widths differ by less than one subpixel, height ratios differ from the approved values by less than 0.001, and all rows fit without horizontal overflow or clipped numeric/progress content.
- Actual page evidence: `final-running.png`, `completed-1180.png`, `replay-updated.png` in the same evidence directory. The spectrum and its wavelength axis are visible at the minimum supported window size. Three device settings dialogs still open and close. A demo stop showed the recorded count and confirmed-shutdown presentation. A fresh demo replay advanced from point 1/3, recorded 0, time 00:00 to completed, recorded 3, time 00:35. No real device was involved.
- Verification: 25 focused tests passed (`automatic-presentation.test.ts` and `workbench-ui.test.mjs`); `npm run build` and `git diff --check` passed. The build retains the existing large-chunk warning. No browser console errors were observed in the final preview.
- No outstanding P0/P1/P2 design differences within this scope. Native Windows/WebView2, display scaling and real-device operation remain unverified. The proportional desktop layout targets the application's supported minimum width of 1180; narrower web previews retain the existing fallback layout.

final result: passed


---

## 2026-10-15 — Manual current control and power-button labels

Scope: apply the selected compact current-control design in the existing manual page. The user subsequently specified `断开连接` and `归零&关闭`; these labels supersede the image. Existing command handlers, safety conditions, settings dialogs, other cards and charts are outside the change.

- Source visual truth: `/Users/zh/.codex/generated_images/01a13fe4-9ebb-75c0-933c-af810cb2d057/exec-5dcf0c1e-de8f-4292-b109-8bca04bc6286.png` (1586×992). Normalize to 1440×900 for comparison. The explicit compact brief is a 104 px input, 80 px apply button and 8 px gap; these sizes take precedence over approximate generated-image geometry.
- Implementation: `http://127.0.0.1:1449/?demo=1&run=1`, using an isolated production build of the existing app. In-app browser capture: `output/playwright/manual-compact-20261015/dark-1440.jpg` (1440×900 pixels, 1440×900 CSS viewport). State: dark/comfortable, manual page, TDK connected with output off, draft 8 A, applied 0 A. No hardware is connected.
- Full-view comparison: `output/playwright/manual-compact-20261015/design-comparison.jpg`; source on the left, implementation on the right. Three device-card edges, chart rows, metrics and spectrum retain the existing layout.
- Focused comparison: `output/playwright/manual-compact-20261015/focused-comparison.jpg`; the target-current field, apply button and applied-current status now form one compact left-aligned group. Label and helper retain the existing content column.
- Typography: existing product fonts, 14 px numeric input, 12 px button and 11 px applied status retained. The generated image's enlarged text was not copied into the product. Labels and focus feedback remain usable.
- Spacing: input 104×32 px, button 80×32 px, 8 px control gap, 12 px before applied status. At 1180×720 and 1920×1080, all three device cards share their top edge and height; widths differ by less than one CSS pixel. No page horizontal overflow or footer-button text clipping. A separate light-theme check at 1180×720 passed. Evidence: screenshots and `checks.json` in the same directory.
- Colors/tokens: existing dark/light tokens, neutral outline states and orange output action retained.
- Assets: no new raster assets, fonts or icons required; existing chart renderers and number steppers retained.
- Copy: both TDK and CH341 manual branches use `断开连接` and `归零&关闭`; the pending label is `关闭中…`. Operational status/error descriptions retain their existing wording.
- Interaction checks in the TDK browser demo: input 12.3 and ArrowUp produce 12.4 without changing applied current; enabling output preserves the draft; apply changes applied current to 12.4 A; `归零&关闭` restores applied/output current to 0 A while retaining connection; `断开连接` changes the UI to disconnected; reconnect remains available. Disconnect stays disabled while output is enabled. No browser console errors.
- Comparison history: the first completed rendered comparison found no actionable P0/P1/P2 difference within this scope. The compact numeric wrapper was bounded locally during implementation so the existing stepper stays anchored to the input; no shared numeric-control changes were needed. The user's copy and explicit compact sizing are intentional differences from the generated reference.
- Validation: TypeScript and Vite production build passed; 8 focused checks passed in `scripts/workbench-ui.test.mjs` and `src/lib/number-input.test.ts`; scoped `git diff --check` passed. Existing large-chunk build warning remains.
- Limits: browser demo validation only. Native Windows/WebView2, display scaling and real-device acceptance were not performed. No full suite or packaging was run.

final result: passed


---

## 2026-10-15 — Automatic spectrum card with integrated readouts

Scope: implement the user's explicitly attached left-readout/right-plot design in the automatic-test page. The former four-card metric row is removed; power/current and the existing stability status now occupy reserved space in the power-chart header. Manual-test callers retain the previous defaults. No device commands, acquisition calculations, measurement validity rules or persistence behavior were changed.

- Source visual truth: `/Users/zh/.codex/generated_images/01a13e04-5e36-7473-8a07-46d6a20dfd58/exec-be030be9-e60d-426d-9d10-2a1fc5b5dd1c.png`, 2172×724 pixels. The content-card crop is (28,55)-(2147,659), 2119×604 pixels. For comparison this crop is proportionally downsampled to 1251×357; it is not stretched.
- Implementation URL: `http://localhost:1420/?demo=1`. Screenshot: `output/playwright/spectrum-readouts-20261015/final-running.png`, 1440×900 pixels at a 1440×900 CSS viewport (1:1 raster/CSS capture). Spectrum crop: `spectrum-detail.png`, 1251×330 pixels. State: dark/comfortable, demo point 4/6, power 0.860 W at 8 A, center 976.200 nm, FWHM 0.776 nm, PIB 96.85%; values and verdicts match the reference. The demo banner explicitly identifies simulated data.
- Full component comparison: `output/playwright/spectrum-readouts-20261015/design-comparison.png` contains both the selected reference and the actual spectrum-card crop. Full-page screenshot establishes its relation to the power charts. Focused readout comparison: `readouts-comparison.png`.
- Layout/spacing: one outer card, three equal metric tracks in its left column, one full-height right plot, consistent left padding and numeric baselines. The sidebar uses a 20% track with a minimum readable width instead of copying the reference's approximate 18% track. The spectrum and upper power-chart cards remain equal height. Their height comes from a shared 1.5:4.5:4.5 device/power/spectrum grid, superseding the earlier four-row ratio in this report. The removed metric-row space is shared by the two chart rows. The rendered card is slightly shorter than the normalized isolated reference because it shares the existing full-page grid; no content is clipped.
- Fonts/typography: existing product Chinese UI font and DIN numeric readouts are retained. Values are larger than labels and units; normal values are neutral, not green. At 1440×900 comfortable mode values measure 32.886 px. Compact mode caps at 28 px; distant mode reaches 37.971 px at 1440×900 and 42.4 px at 1920×1080. This retains the product's density behavior without fixed card heights.
- Colors/tokens: existing card, border, foreground, muted, success and danger tokens are reused in dark/light themes. FWHM has a red value, textual verdict/icon, and short left accent. The generated mock's background haze and decorative texture are intentionally omitted in favor of the existing flat product surface. No contrast-certification claim is made.
- Assets/copy: existing canvas plot, wavelength axes, peak annotations/legend, numeric animation and Lucide verdict icons are reused. No chart data is rasterized and no illustrative asset was needed. All three indicator names, values, units, status text and limit tooltips remain present. Each indicator retains its accessible button name and specification-settings action.
- Comparison history: initial review found a P2 density inconsistency: distant mode did not enlarge the new spectrum readouts. The final scoped density rules increase values and labels in distant mode while remaining bounded by available card height. Post-fix screenshots are `深色-远看-1180.png`, `浅色-远看-1180.png`, and the corresponding 1440/1920 captures. A further comfortable-mode refinement avoids the older short-window global size cap, keeping the selected design's prominent numbers. The final combined reference/implementation comparison has no actionable P0/P1/P2 differences within the approved layout and existing-token constraints.
- Geometry verification: `layout-results.json` covers all 18 combinations of dark/light, compact/comfortable/distant and 1180×720, 1440×900, 1920×1080. All have no horizontal page overflow or clipped metric content; the upper/lower card heights differ by at most 0.007813 CSS px. All spectrum cards fit inside the viewport. The final six comfortable-mode captures were refreshed after the final size adjustment; compact/distant rules were unchanged by that adjustment.
- Interaction verification in the browser demo: Enter on FWHM opens the existing specification dialog; Escape on its focused close button closes it. Center wavelength and PIB clicks open the same dialog and Cancel closes it. No specification values were saved. A simulated Stop updates the power/current readout to 0 W / 0 A and retains the existing completion summary. `completed-1180.png` shows all three metrics and both chart rows fitting after that summary appears. `manual-unchanged-1180.png` confirms the manual page still has four separate metric cards, no integrated sidebar, and no horizontal overflow. No physical device was used.
- Validation: 27 focused checks passed (`automatic-presentation.test.ts`, `measurement-verdict.test.ts`, `workbench-ui.test.mjs`). TypeScript/Vite production build and `git diff --check` passed. Existing >500 kB chunk warning remains. No browser console errors were observed.
- Limits: browser demo verification only. Native Windows/WebView2, OS display scaling and real-device acceptance remain unverified. No full suite, package, commit, push or deployment was performed. Existing unrelated worktree changes and earlier report entries were preserved.

final result: passed


### Follow-up — remove visible automatic spectrum verdict labels

The user requested removal of “达标” and “超限”. The automatic spectrum sidebar now omits the verdict badge (icon and text) for all three metrics. Numeric colors, units, existing accessible verdict information, click-to-configure behavior and manual-page defaults remain intact. Browser evidence: `output/playwright/spectrum-readouts-20261015/without-verdict-labels.png`, 1440×900 CSS/pixels. The rendered sidebar contains zero `.measurement-state` elements, its three titles contain only their metric names, and the page has no horizontal overflow. TypeScript `npx tsc --noEmit` and `git diff --check` passed. No measurement logic changed.

final result: passed


### Follow-up — narrow automatic spectrum readout column

The user requested less unused width after verdict labels were removed. The integrated sidebar now uses a bounded 14% track (`clamp(10rem, 14%, 13rem)`) instead of a minimum 11.5rem/20% track. At the 1440×900 CSS viewport the comfortable-mode column shrinks from approximately 250 to 175 CSS px, returning approximately 75 px to the spectrum plot without shrinking the numbers. Final screenshot: `output/playwright/spectrum-readouts-20261015/narrow-readouts.png`. Geometry evidence: `narrow-readouts-checks.json`; compact/comfortable checks include actual CSS widths 983, 1200 and 1600 during a transient preview zoom, and distant checks cover actual CSS widths 1440 and 1920. All recorded checks show no clipped metric content or horizontal overflow. The final screenshot returns to a 1440×900 CSS viewport. This CSS-only follow-up preserves chart height, values and behavior. `git diff --check` passed; no additional behavior tests were needed.

final result: passed


---

## 2026-10-15 — Seven-segment manual current controls

Scope: implement the accepted three-column power-card design with right-aligned target digits and the new `当前电流：` feedback readout. No device command, acquisition cadence, backend, safety guard, or other-page numeric style was changed.

- Source visual truth: `/Users/zh/.codex/generated_images/01a13fe4-9ebb-75c0-933c-af810cb2d057/exec-4f19883d-b9a0-48be-9a5c-3a117c99a314.png` (1461×1076). This is the selected card with the user's final right-alignment correction.
- Implementation: `http://127.0.0.1:1459/?demo=1&run=1&page=manual`, using an isolated production build of the existing app. Final browser screenshot: `output/playwright/manual-seven-segment-20261015/dark-1440.jpg` (1440×900 pixels/CSS px, DPR 1). Focused card: `power-card.jpg` in the same directory (403×298 pixels). State: dark/comfortable, TDK connected, output off, draft 8 A and feedback 0 A.
- Full view: the existing three device cards remain equal-height and top-aligned. The input, apply button and feedback occupy three equal grid tracks; the same tracks align with the TDK footer. The control height is now 36 px, matching footer controls. Other manual page components retain their existing design.
- Focused comparison: `output/playwright/manual-seven-segment-20261015/card-comparison.jpg`, source left and implementation right. Card images are contained proportionally in equal presentation slots without warping; generated margins are not treated as exact geometry.
- Typography: genuine DSEG7 Classic Bold WOFF2 is bundled locally; both numeric controls use 18 px, weight 700, without synthesized bold. Target text is right-aligned, with 24 px on the arrow side and 6 px on the left. Chinese labels and the A unit retain normal product fonts. Source: https://github.com/keshikan/DSEG/releases/download/v0.46/fonts-DSEG_v046.zip. OFL notice ships in `public/licenses/DSEG-LICENSE.txt` and the built output.
- Spacing/layout: at 1180×720, 1440×900 and 1920×1080, all three device-card tops/heights align and each current-control track matches its TDK footer column within one subpixel. No horizontal page overflow. Narrow feedback may wrap its label above its value inside a fixed 36 px row, so a decimal value does not move the surrounding layout.
- Colors/tokens: dark digits use restrained amber with a very light halo; light-theme digits use a darker amber and no halo. Existing card, helper, button and status tokens are preserved. A light-theme 1180×720 check is saved as `light-1180.jpg`.
- Asset fidelity: existing icons, native editable text and stepper controls remain real UI; seven-segment digits use the bundled font rather than a generated screenshot or handcrafted drawing.
- Copy/content: `已下发` is replaced with `当前电流：`; only its numeric value uses the seven-segment font. The value reads existing `outputCurrentA` feedback, never the draft or `activeCurrentA`. Disconnected, missing and nonfinite readings display `--`, not a fabricated zero. No hardware polling was added; CH341 without an existing feedback reading also shows `--`.
- Interactions: browser demo verified decimal entry, ArrowUp (12.3 → 12.4), feedback remaining zero before apply, feedback changing to 12.4/12.34 after apply, zero after close, and `--` after disconnect. Existing output/disconnect locks remain. Browser console errors: none.
- Comparison history: the initial comparison found the regular font too thin relative to the selected segmented design; it was replaced with the actual bold font. A P2 was then found in the 1180 px decimal check: 12.34 exceeded the input's available text width. Local padding was reduced, keeping 18 px digits and the arrow gap. Final `decimal-card.jpg` shows the full 12.34; DOM measurements report clientWidth=scrollWidth=89 and scrollLeft=0. The final card comparison has no actionable P0/P1/P2 finding.
- Validation: TypeScript/Vite build passed; 9 focused checks passed in `manual-current.test.mjs`, `workbench-ui.test.mjs`, and `number-input.test.ts`. The feedback test covers TDK and CH341, zero, decimals, unavailable/nonfinite readings and disconnect, with a distinct target value to prevent accidental setpoint substitution. Scoped diff whitespace check passed. Existing large-chunk build warning remains.
- Limits: browser demo and rendered source verification only. Native Windows/WebView2, display scaling and physical-device acceptance are not established. Full suites and packaging were not run.

final result: passed


### Follow-up — duplicate TDK identity

The manual-card summary prefixed `TDK RS232` to the configured resource. The demo resource was also `TDK RS232`, producing the duplicate. The display now removes that exact duplicate while retaining distinct VISA resources and the empty-resource hint. Verified after rebuilding in the in-app browser: `.manual-device-card__identity` is `TDK RS232`. Evidence: `output/playwright/manual-seven-segment-20261015/power-card-identity-fixed.jpg`. The existing manual-current check and TypeScript/Vite build passed; scoped diff check passed. No device command or stored configuration was changed.

final result: passed


### Follow-up — remove duplicate current and restyle output feedback

The user's updated brief supersedes the earlier right-hand current readout: remove it and give the existing TDK output-voltage/current pair the same seven-segment style. The current input/action row and feedback row now use the same two-column grid. Only digits use the bundled 18 px amber DSEG font; labels and V/A units retain normal UI type. Labels remain `输出电压` / `输出电流`, matching their actual data sources. The CH341 four-item telemetry row is outside this styling change.

Evidence: `output/playwright/manual-seven-segment-20261015/feedback-card.jpg` and `feedback-dark-1440.jpg` (dark 1440×900), plus `feedback-dark-1180.jpg` and `feedback-light-1180.jpg`. At 1180×720, the two readouts each have clientWidth=scrollWidth=141 px with a 12.34 A value, the three device cards share y=112 and height=348.42 px, and there is no horizontal page overflow. The duplicated `当前电流：` is absent. Browser console errors: none. No actionable P0/P1/P2 issue remains for the updated brief.

TypeScript/Vite build and 5 focused checks passed. The feedback regression covers voltage/current, zero, decimal, unavailable/nonfinite values and disconnection; it also prevents substituting input voltage or target current. Keyboard step props are retained. No backend/hardware commands were changed or real devices operated.

final result: passed


### Follow-up — distinct voltage/current colors across themes

Scope is limited to manual readout color CSS. Voltage uses blue (#67c8ff dark / #0369a1 light); current retains amber (#ffad32 dark / #a85108 light). Semantic colors remain consistent across all six accent palettes. Dark glow now derives from each digit's actual color, so voltage does not inherit an orange halo. Missing readings and disabled inputs retain their existing muted states. No numeric values, source fields, keyboard behavior, dimensions or device commands changed.

Validation uses the real ManualPage and the exact production stylesheet in an isolated, explicitly labeled example-data fixture. Simulated 12.34 V / 2.5 A readings are confined to `output/manual-seven-segment-preview-20261015/__color-qa`; product demo data and real records were not modified. Evidence: `output/playwright/manual-readout-colors-20261015/`, with twelve card screenshots, `results.json`, `dark-light-comparison.jpg` and a narrow 1180×720 capture.

All dark/light × orange/blue/green/purple/rose/graphite combinations have distinct digit colors, the expected bundled seven-segment font and no page horizontal overflow. Measured foreground/background ratios are approximately 9.38:1 for both dark readouts; light voltage is 5.93:1 and light current is 5.46:1. These figures concern these numeric readouts only, not whole-app accessibility certification. Dark/light screenshots were visually inspected. The actual app was refreshed and still displays missing voltage feedback as gray `--`. TypeScript/Vite build and scoped whitespace check passed; existing large-chunk warning remains. Native WebView/display-scaling and real-device testing remain outside this visual change.

final result: passed


---

## 2026-10-15 — Apply the integrated chart layout to manual testing

Scope: apply the user-approved automatic chart layout and subsequent refinements to the manual page. The device cards, current-control sequencing, device settings, save controls, acquisition commands and safety locks retain their existing behavior.

- Visual target: the approved left-metrics/right-spectrum design, as refined by the user to remove verdict badges, narrow the sidebar and remove the duplicate right title. Its current rendered reference is `output/playwright/spectrum-readouts-20261015/single-spectrum-heading.png`, 1440×900 pixels/CSS.
- Before/after full-page comparison: `output/playwright/manual-integrated-charts-20261015/before-after.png`, combining two 1440×900 CSS/pixel captures of the manual idle demo at 1:1 scale. The old capture includes the transient demo notice; that notice is dismissed in the new capture and is unrelated to the layout. The before/after source files are `before.png` and `after-idle.png`.
- Focused comparison: `spectrum-comparison.png` in the same directory combines the refined automatic spectrum card and the manual spectrum card. The reference crop is proportionally scaled to the manual card width, without stretching. Manual device cards occupy more vertical space, so the two manual chart rows share the remaining space equally and can scroll together in short windows. The smaller adaptive readouts in the shorter manual spectrum card are an intentional consequence of preserving the device controls.
- Implementation evidence: `final-manual.png`, dark/comfortable, 1440×900 CSS/pixels, existing populated demo curves following a simulated automatic stop. The instantaneous power/current readout shows 0 W / 0 A after that stop; the power curve retains its previous samples. This is a display check, not evidence of real acquisition or shutdown.
- Typography and colors: reuse the same shared numeric readout, DIN values, Chinese UI labels, units, stability status, neutral normal values and red abnormal values as the automatic page. Three spectrum metric groups remain aligned in equal tracks. Verdict words/icons and the duplicate right heading are absent. Existing chart legend/axis labels remain readable.
- Layout and assets: power/current now occupy reserved power-chart header space; the separate four-card metric row is removed. The full spectrum and peak legend remain in the right plot. The existing chart renderers and icons are reused; no new raster assets or replacement fonts are introduced. Device cards retain equal top edges, widths and heights.
- Reuse: `RealtimePowerReadout` is extracted from the existing automatic JSX and used by both pages. The same live-quality guard, current source, formatting, and manual/automatic stability component remain in place. Shared spectrum metrics are rendered with the existing `spectrumOnly` option. Manual metrics retain their previous non-clickable behavior; configuration remains in the device settings.
- Geometry: `checks.json` covers 18 dark/light × compact/comfortable/distant × 1180×720, 1440×900, 1920×1080 combinations. No horizontal overflow or clipped metric content was found. All upper/lower chart-row height differences were zero in the recorded matrix. There are zero old metric grids, verdict badges, or duplicate right titles. 1180×720 intentionally scrolls vertically; `small-window-scrolled.png` shows the whole spectrum after PageDown, with its bottom at 719.922 CSS px in a 720 px viewport. 1440×900 and 1920×1080 did not need vertical scrolling in the populated fixture.
- Comparison history: initial evidence capture was invalidated by a dev reload returning to the automatic page. It was replaced with a fresh manual capture at the same 1440×900 viewport; the invalid comparison was not accepted. Final full-page and focused comparisons show no actionable P0/P1/P2 difference within this scope. Existing product fonts, adaptive card heights and neutral theme tokens are intentional constraints.
- Validation: 7 focused checks passed (`manual-current.test.mjs`, `workbench-ui.test.mjs`, `measurement-verdict.test.ts`), including output-feedback/null/disconnected numeric cases. TypeScript/Vite production build and `git diff --check` passed. The existing large-chunk warning remains. No browser console errors were observed.
- Limits: browser demo only; native Windows/WebView2, OS display scaling and real devices were not tested. No full suite, packaging, commit, push or deployment was performed. Existing unrelated worktree changes and previous QA entries were preserved.

final result: passed


### Follow-up — remove excess space above the power plot

The user clarified that the issue was the large blank area above the power curve. The earlier stopped-value investigation was discontinued and its exploratory test removed; measurement data, quality handling, and historical samples were not changed. The actual cause was additive vertical spacing: 12 px outer card padding at each end plus a roughly 59.6 px header accommodating two readout lines. The readout and acquisition status now share one line, and only the power-readout card uses the compact outer/header spacing.

Evidence: `output/playwright/power-chart-header-20261015/comparison.png` combines before/after crops from the same 1440×900 CSS/pixel manual-page fixture and identical 614×223 card crops. `manual-after.png` is the full page; `automatic-running-after.png` checks the longer running status on the automatic page. The header measures 40 px, and the canvas begins 41 px below the card top rather than about 72.6 px. At the measured manual viewport the canvas height grows from 130.2 to 173.8 CSS px while the outer card height remains 221.8 px. Numbers, current, status text and chart values retain their existing semantics.

`checks.json` covers automatic/manual × compact/comfortable/distant × 1180×720, 1440×900 and 1920×1080. All 18 checks have no clipped header/readout or horizontal overflow. TypeScript/Vite build, 7 focused checks and `git diff --check` passed; the existing large-chunk build warning remains. Verification used the browser demo only.

final result: passed


### Follow-up — one-decimal manual current display with no caret

The user requested a persistent decimal point with one fractional digit in the manual current input and no visible input caret. The manual current NumberInput now opts into `displayDecimalPlaces={1}`. Its native editing state remains unchanged beneath a pointer-transparent, accessibility-hidden formatted readout, so normal typing and partial decimal input do not jump when the formatted suffix appears. The current value and command payload are not rounded or otherwise changed. The native input retains its accessible label and focus outline; only its text/caret are transparent for this scoped display. The formatted readout reuses the existing seven-segment typography, active color and disabled color. Other numeric fields keep their existing rendering.

Evidence: `output/playwright/fixed-current-input-20261015/one-decimal-card.png` shows the focused 1.0 input at a 1440×900 browser viewport. `checks.json` records 0→0.0, 1→1.0, 12→12.0, Chinese decimal punctuation→1.5, vertical/horizontal arrow steps, consecutive typing, Backspace and both stepper buttons. All recorded display/caret checks passed, with transparent caret color and no formatted-readout overflow. Ctrl+Enter in the demo applied the numeric 1 A value and updated the output feedback while the input continued displaying 1.0. The demo was safely stopped after verification and its temporary test tab closed.

Validation: 7 focused checks passed (`manual-current.test.mjs`, `number-input.test.ts`), including fixed display versus editable numeric-value cases and the existing shortcut/availability cases. TypeScript/Vite build and `git diff --check` passed; the existing large-chunk warning remains. This was browser-demo verification, not real-instrument or native-runtime acceptance.

final result: passed
