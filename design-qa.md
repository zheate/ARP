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

## 2026-08-18 光谱外置四项指标

- source visual truth: `/var/folders/pj/_thskkm14c333y_j9nd_dk8h0000gn/T/codex-clipboard-fe2504a9-60c5-4dcf-8773-8e4931edd57a.png`，以及用户明确要求将原三张光谱指标卡移出波长曲线卡片，并在首位增加“实时功率”。
- implementation screenshot: `output/playwright/automatic-metrics-four-cards.png`
- viewport: `2880 × 1800` CSS px，device scale factor `1`。
- source pixels: `2394 × 136`；implementation pixels: `1072 × 44`。源图仅包含三张卡，实施图按指令扩展为四张；两者的卡片横向节奏、紧凑读数层级和深色令牌保持一致。
- state: 示例数据模式；功率计、光谱仪在线，当前测试点为 `8 A`。

### Findings

- 已修复 P1：中心波长、FWHM、PIB 从光谱卡片内部移至曲线区域上方的独立指标行，避免挤占波长曲线。
- 已修复 P1：新增实时功率首卡，显示 `0.860 W @ 8 A`；功率数值使用现有橙色告警令牌，电流保持辅助层级。
- 已修复 P2：四张卡使用等宽单行网格；三张光谱卡仍保留原有绿/红/灰判定语义和规格设置入口。
- 首次预览发现实时功率数值未应用橙色；已增加 `is-warning` 令牌映射并重新截图复核。

### Verification

- `npm --prefix tauri-ui run build`: passed.
- `git diff --check`: passed.
- Playwright 示例数据页：四卡顺序、数据文案、橙色功率读数和独立光谱卡：passed；点击 Centroid 仍可打开规格设置。
- 唯一 console error 为 Vite 预览的 `/favicon.ico` 404，与应用界面无关。

final result: passed

## 2026-08-18 光谱指标卡内容居中

- source visual truth: `/var/folders/pj/_thskkm14c333y_j9nd_dk8h0000gn/T/codex-clipboard-444c5591-6772-46e6-abfc-4f264e95f332.png`，用户明确要求三个卡片内的文字整体居中。
- implementation screenshot: `output/playwright/automatic-spectrum-metrics-centered.png`
- viewport: `2880 × 1800` CSS px，device scale factor `1`。
- state: 示例数据模式；三项光谱指标均通过。

### Findings

- 已修复 P2：每张指标卡中的名称、DIN 数值、单位作为同一信息组在卡片水平与垂直中心对齐。
- 聚焦截图确认三个卡片的内容组均居中，数值颜色、单行排布和紧凑高度保持不变。

### Verification

- `npm --prefix tauri-ui run build`: passed.
- `git diff --check`: passed.
- Playwright 虚拟状态页：console errors `0`；聚焦截图：passed。

final result: passed

## 2026-08-18 光谱指标卡单行读数

- source visual truth: `/var/folders/pj/_thskkm14c333y_j9nd_dk8h0000gn/T/codex-clipboard-aa78cf93-4d31-4820-86a0-587e6dd39032.png`，以及用户对更小高度、去除黄色竖条、单行排布、DIN 数值格式的明确要求。
- implementation screenshot: `output/playwright/automatic-spectrum-metrics-inline.png`
- viewport: `2880 × 1800` CSS px，device scale factor `1`。
- state: 示例数据模式；三项光谱指标均通过。

### Findings

- 已修复 P1：指标卡高度压缩至单行读数高度，给下方波长曲线留出更多空间。
- 已修复 P1：三个指标名称前的黄色竖条已移除；名称、DIN 数值和单位在同一行，数值为视觉主层级。
- 已修复 P2：数值继续使用绿色通过、红色失败、灰色未出结果的结果语义；卡片点击设置规格的交互保持不变。
- 聚焦截图确认三张卡片等宽、内容没有换行或溢出，且没有遗留规格、状态文字、齿轮或黄色竖条。

### Verification

- `npm --prefix tauri-ui run build`: passed.
- `git diff --check`: passed.
- Playwright 虚拟状态页：console errors `0`；聚焦截图：passed。

final result: passed

## 2026-08-18 光谱指标卡精简

- source visual truth: `/var/folders/pj/_thskkm14c333y_j9nd_dk8h0000gn/T/codex-clipboard-0f527480-a5ff-4725-a580-9a2ebbe49972.png`，以及用户对紧凑高度、可点击规格、数值色彩语义的明确要求。
- implementation screenshot: `output/playwright/automatic-spectrum-metrics-compact.png`
- viewport: `2880 × 1800` CSS px，device scale factor `1`。
- source pixels: `2402 × 324`；implementation pixels: `2618 × 77`。两者均为 CSS 密度截图；高度差是本次将卡片从详情型改为紧凑读数型的预期结果。
- state: 示例数据模式；三项光谱指标均通过。

### Findings

- 已修复 P1：卡片仅保留名称、数值与单位，移除了规格、PASS/FAIL 文案和右上角齿轮，使指标区不再挤占波长曲线高度。
- 已修复 P1：通过值以绿色显示，失败值以红色显示，未出结果保持灰色；颜色本身承担结果表达。
- 已修复 P2：整张卡片仍可通过鼠标、Enter 或 Space 打开规格设置，交互可访问名称为“点击设置规格”。
- 全视图比对确认三个等宽卡片仍嵌在光谱外卡片内；聚焦截图确认没有遗留规格、状态或图标内容。

### Verification

- `npm --prefix tauri-ui run build`: passed.
- `git diff --check`: passed.
- Playwright 虚拟状态页：console errors `0`；点击“中心波长”可打开规格设置：passed。

final result: passed

## 2026-08-18 监测区重排

- source visual truth: 用户提供的三图并列截图，以及“上方两个卡片对半分、下方全宽光谱、中心波长/FWHM/PIB 嵌入光谱卡片”的明确布局指令。
- implementation screenshot: `output/playwright/automatic-spectrum-internal-metrics.png`
- viewport: `2880 × 1800` CSS px, device scale factor `1`
- state: 示例数据模式；功率计、光谱仪均在线，自动测试进行中。

### Findings

- 无 P0 / P1 / P2 差异。
- 上层实时功率和功率/效率两张图使用相同的列宽；下层光谱图横跨完整内容宽度。
- 中心波长、FWHM、PIB 三张等宽指标卡位于光谱卡片内部的标题下方、波长曲线上方；实时功率保留为实时功率图标题内的当前点读数，避免额外占用卡片。
- 自动测试控制卡只保留等宽的开始、停止按钮，并在卡片高度内居中。

### Verification

- `npm --prefix tauri-ui run build`: passed.
- `git diff --check`: passed.
- Playwright 示例数据截图、可访问性快照与控制状态检查：passed；开始按钮禁用、停止按钮可用，符合进行中状态。

final result: passed
