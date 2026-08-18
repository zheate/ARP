# Design QA — 自动测试页 4:3 参考图还原

- source visual truth path: `C:\Users\24561\.codex\attachments\bbf47711-4e57-45f1-aaa4-92c9af658df7\image-1.png`
- implementation screenshot path: `E:\工作\长光华芯\软件\泵驱一体\auto_run_Lpowe2\.codex-ui-qa\automatic-ui-v2.png`
- responsive tall screenshot path: `E:\工作\长光华芯\软件\泵驱一体\auto_run_Lpowe2\.codex-ui-qa\automatic-ui-tall.png`
- side-by-side comparison: `E:\工作\长光华芯\软件\泵驱一体\auto_run_Lpowe2\.codex-ui-qa\reference-vs-automatic-ui-v2.png`
- viewport: `1448 × 1086` CSS px, device scale factor `1`
- source pixels: `1448 × 1086`
- implementation pixels: `1448 × 1086`
- density normalization: none; both artifacts are `1×` and compared at identical pixel dimensions
- source state: 自动测试空闲态，三台设备未连接，无曲线数据
- implementation state: 浏览器只读空闲态，三台设备未连接，无曲线数据

## State boundary

The production app is a Tauri desktop application. The browser-only capture cannot start the native Python/backend bridge, so it shows a transient `系统错误` notification in the upper-right corner. That notification is excluded from layout fidelity findings because it is specific to the browser verification environment and is not present in the normal Tauri runtime. All persistent app-owned regions remain visible underneath it.

## Findings

- No actionable P0 / P1 / P2 visual mismatch remains.
- P3: the global status-icon row uses the closest matching icons already available in the project's icon library; individual glyphs differ slightly from the operating-system icons shown in the reference.
- P3: live state wording is data-driven, so controller/station/device copy can differ from the static reference while keeping the same size and alignment.

## Full-view comparison evidence

The combined comparison confirms the same major composition at `1448 × 1086`: a 50 px global title bar, a 64 px page header, a 188 px sidebar, a 96 px setup panel, a three-card device row, a three-track workflow panel, a three-column chart row, five equal metric cards, and a persistent footer. The implementation's main content begins at the same horizontal and vertical position as the reference to within a few pixels.

At the reference height, the metric cards end above the footer with an approximately 40 px empty band. At `1448 × 1200`, `.automatic-page-spacer` grows by the additional viewport height while setup, device, workflow, chart, and metric cards retain their dimensions. This verifies that the space below the cards is adaptive rather than created with a fixed margin.

## Focused region comparison evidence

The full side-by-side image is `2896 × 1086`, so navigation labels, field labels, device states, workflow steps, chart axes, empty-state icons, metric values, and footer copy are readable without a separate enlargement.

- Header: brand, five menu labels, station pills, controller badge, save action, and emergency action follow the reference hierarchy.
- Setup and device rows: field order, widths, icon treatment, orange spectrum switch, card heights, and spacing match the reference.
- Workflow: summary, six steps, and stability values retain the reference's three-column proportions.
- Charts: power, efficiency, and spectrum cards use the reference's near-equal proportions; empty charts keep visible axes/grid plus a centered library icon and message.
- Metrics and footer: five cards preserve equal widths, orange title rules, large numeric hierarchy, and an independent elastic blank region above the footer.

## Required fidelity surfaces

- Fonts and typography: Geist Variable with Chinese system fallbacks is retained. Brand, page title, navigation, device titles, card labels, DIN-style measurements, units, weights, and truncation follow the reference hierarchy.
- Spacing and layout rhythm: section heights, 9 px vertical rhythm, asymmetric content insets, card radii, borders, and the bottom elastic spacer match the source composition. The default Tauri window height is now `960` so all major bands fit on a typical desktop while taller windows gain blank space below the cards.
- Colors and visual tokens: neutral black canvas, charcoal sidebar/cards, orange primary state, blue selected station, yellow secondary station/spectrum state, and semantic green/red states map to the reference.
- Image quality and asset fidelity: the source contains no photography or illustrations. UI imagery uses the existing icon library and high-DPI Canvas charts; no placeholder drawings or rasterized UI were introduced.
- Copy and content: reference navigation, setup labels, device names, workflow steps, chart titles, result specifications, version, time, and test guidance are represented. Runtime values remain truthful to the connected hardware state.

## Comparison history

1. Earlier P1: the existing 16:9 implementation had only one header band, six navigation entries, station switching inside the form, a spectrum-side summary, and fixed content density optimized for `1440 × 810`.
   - Fix: restored the two-level header, four-item navigation, header-level station pills, three pure chart cards, five result cards, and a desktop height that fits the full workflow.
2. Earlier P2: the first pass used smaller type, grey disconnected device icons, solid empty-chart panels, 10 px section gaps, and symmetric content padding.
   - Fix: matched the reference's typography scale, orange device-icon treatment, chart grid/axes with centered empty-state icons, 9 px rhythm, and measured asymmetric insets.
3. Responsive validation: the `1448 × 1200` capture confirms the new flex spacer absorbs additional height below the metric cards without stretching or separating the cards themselves.

## Verification

- `npm run build`: passed.
- `npm test`: passed (4 / 4).
- `git diff --check`: passed.
- Browser-rendered implementation captured at `1448 × 1086` and compared side-by-side against the source.
- Tall viewport captured at `1448 × 1200` to verify adaptive blank space.

final result: passed
