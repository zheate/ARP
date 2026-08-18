# Design QA — 自动测试工作台真实运行态还原

- source visual truth path: `/Users/zh/.codex/attachments/aa1f7f10-341a-4f79-ac0f-06926f3ae7af/image-1.png`
- implementation screenshot path: `/Users/zh/Documents/test/ARP/.codex-ui-qa/actual-real-v2.png`
- normalized source: `/Users/zh/Documents/test/ARP/.codex-ui-qa/reference-1440x810.png`
- normalized implementation: `/Users/zh/Documents/test/ARP/.codex-ui-qa/actual-real-v2-normalized.png`
- side-by-side comparison: `/Users/zh/Documents/test/ARP/.codex-ui-qa/reference-vs-actual-real-v2.png`
- source pixels: `1672 × 941`, treated as `1×`
- implementation capture: `2972 × 1706` including native window shadow; app content crop `2880 × 1614`, CSS window approximately `1440 × 807`, device scale factor `2`
- normalization: source and cropped implementation normalized to `1440 × 810` for one-to-one composition review
- source state: 自动测试进行中，三台设备在线，8.00 A，第 4 / 6 点
- implementation state: standard `npm start` real Tauri idle state, three devices disconnected, no demo query or injected measurements

## State boundary

The source and implementation do not contain the same measurement state. The source is a populated mid-test design reference; the implementation evidence deliberately uses the real hardware-disconnected idle state that the user actually opens with `npm start`. Data values, PASS results, enabled buttons, traffic-light focus color, and plotted traces are therefore not treated as layout defects. The comparison judges the app-owned frame, region proportions, typography hierarchy, spacing, tokens, empty-state containment, and persistent controls.

## Findings

- No actionable P0 / P1 / P2 visual mismatches remain in the app-owned layout.
- P3: `数据浏览` and `系统设置` are present to match the reference navigation but remain disabled because those product pages do not exist yet. They are not wired to fake routes.
- P3: the reference uses a subtle top-left ambient light treatment; the implementation keeps the existing flat neutral-dark tokens instead of introducing a decorative image or ungrounded asset.

## Full-view comparison evidence

The `2880 × 810` side-by-side comparison shows matching structural bands: one 50 px title bar, approximately 12.7% fixed sidebar, one-row setup panel, equal three-device row, three-part workflow card, `1 : 1 : 1.25` chart grid, five equal result cards, and a persistent three-column footer. The earlier extra page header, oversized station pills, right-side scrollbar gutter, and near-equal spectrum chart width are absent.

## Focused region comparison evidence

The full comparison is saved at double width, so labels, icons, controls, borders, chart headers, and metric copy are readable without a separate enlarged capture.

- Title bar: native macOS traffic lights overlay the same single bar as `Power Test`; save and emergency actions occupy the reference position and scale.
- Setup/device rows: field order and card proportions match. The dual-station product control is contained inside the existing `测试站别` field instead of creating a second header band.
- Workflow: current summary, six-step process, and stability panel use the same three-track proportions.
- Charts: power and efficiency cards are equal width; spectrum is wider and contains its own center-wavelength/FWHM/PIB side summary.
- Footer: version, live clock, and test tip remain visible without page scrolling.

## Required fidelity surfaces

- Fonts and typography: existing Geist Variable with Chinese system fallbacks is retained. Nav, labels, chart headings, DIN-style measurements, units, weights, truncation, and line heights follow the source hierarchy; the real idle values remain `--` rather than fake populated values.
- Spacing and layout rhythm: default Tauri window is now `1440 × 810`; title bar, sidebar, content cards, and footer fit in one 16:9 view. Main horizontal inset is 12 px and section gap is 8 px. No persistent vertical scrollbar appears in the verified view.
- Colors and visual tokens: neutral canvas `#0f1112`, sidebar `#17191a`, card `#181a1b`, orange `#ff7a1a`, efficiency blue `#4ca4ff`, and spectrum yellow `#ffcc00` preserve the source semantic hierarchy.
- Image quality and asset fidelity: the source contains no photography or illustration assets. UI icons use the project's Lucide dependency; charts remain high-DPI Canvas output and preserve all native spectrum points.
- Copy and content: source navigation, setup labels, device names, workflow labels, chart titles, result specifications, version, time, and test tip are represented. Runtime state copy truthfully reflects disconnected hardware.

## Comparison history

1. Earlier `[P1]`: the real `npm start` screen had a native title bar plus a second 56 px page header, oversized dual-station pills, four-item navigation, and a tall 1440 × 900 frame.
   - Fix: use the macOS overlay title bar, move actions into the single app bar, set the default window to `1440 × 810`, add the two reference navigation slots, and move station switching into the station field.
   - Evidence: `/Users/zh/Documents/test/ARP/.codex-ui-qa/actual-real-v1.png`.
2. Earlier `[P2]`: the first revision kept a persistent right gutter/scrollbar, used near-equal chart widths, and flattened chart heading hierarchy.
   - Fix: remove the document scrollbar gutter, use `1 : 1 : 1.25` chart columns, stack chart title/legend, and restore the spectrum-side measurement summary.
   - Evidence: `/Users/zh/Documents/test/ARP/.codex-ui-qa/actual-real-v2.png` and `/Users/zh/Documents/test/ARP/.codex-ui-qa/reference-vs-actual-real-v2.png`.
3. A later unverified micro-adjustment was reverted after macOS capture began returning black frames. The retained implementation is the second revision shown in the valid real-Tauri evidence above.

## Primary interaction and runtime checks

- Standard `npm start` launched Vite on port `1420`, compiled Rust, and ran `target/debug/tauri-ui`.
- Real Tauri title-bar overlay, empty device state, station selector, chart empty states, metrics, and footer rendered together.
- `npm run build`, `npm test`, and `git diff --check` pass.

## Implementation checklist

- [x] One native-overlay title bar matching the reference composition.
- [x] Six-slot sidebar layout without fake routed pages.
- [x] Setup, device, workflow, chart, metric, and footer bands visible in one screen.
- [x] Dual-station control retained without the extra header band.
- [x] Wider spectrum chart with embedded measurement summary.
- [x] Real idle-state Tauri capture and normalized side-by-side comparison.

final result: passed
