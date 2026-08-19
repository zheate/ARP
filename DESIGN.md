# ARP Precision Measurement Design System

> A precision-first engineering interface for laser characterization and optical measurement systems.

## 1. Design Philosophy

ARP is a desktop engineering application for semiconductor-laser characterization, optical-power monitoring, spectrum acquisition, and controlled test execution. Its interface exists to make measurements, equipment state, limits, and safe actions legible at a glance. It is not a marketing surface or a generic administration dashboard.

The visual language is a calm dark instrument workbench: near-black canvas, layered graphite surfaces, fine technical rules, DIN-led numerical readouts, and a restrained amber-orange operating accent. Colour has meaning; decoration does not compete with data.

### Existing identity to retain

- Dark graphite application shell and compact desktop density.
- Operating accent: **ARP orange `#FF7A1A`**.
- `DIN` / `DIN 2014` / `Bahnschrift` numerical fallback chain and `MiSans` Chinese UI fallback.
- Hairline-separated cards, a persistent title bar, a compact sidebar, and instrument-oriented dialogs.
- Existing automatic, manual configuration, PD acquisition, and shipping-report workspaces.

## 2. Core Principles

1. **Measurement first.** The current value, unit, validity, and test state precede visual styling.
2. **Data hierarchy first.** One clear primary result per region; supporting detail stays subordinate.
3. **Low visual noise.** Use graphite surfaces, fine borders, and whitespace before adding colour or shadow.
4. **Stable geometry.** Live values must not resize cards, shift neighbouring controls, or cause visual jitter.
5. **Clear operational state.** Connection, readiness, acquisition, alarm, and safe-stop conditions are explicit.
6. **Dense, not crowded.** Use grouping and a 4 px rhythm to fit engineering data without giving every item equal weight.
7. **Precision over decoration.** No ornamental gradients, glass effects, glow, or animation that reduces measurement clarity.

## 3. Visual Theme & Atmosphere

The default operating environment is a dark, warm-graphite measurement bench. It should feel composed and technically reliable during long test runs:

- Near-black is the canvas, not a decorative background.
- Slightly lifted graphite surfaces form regions for controls, charts, and readings.
- Orange identifies the current operating focus or primary start action; it is never a generic highlight.
- Green, amber, red, and blue communicate semantic state only.
- Borders are intentionally visible but quiet; page-level cards do not need drop shadows.

## 4. Color System

All new UI must consume semantic tokens. Do not introduce raw colours in component code except for a documented chart-series token.

### 4.1 Neutral tokens (default dark mode)

| Token | Hex | Use |
|---|---:|---|
| `canvas` | `#0F1112` | Application background; unused chart exterior |
| `surface-1` | `#17191A` | Sidebar and fixed chrome |
| `surface-2` | `#181A1B` | Default card and work area |
| `surface-3` | `#1D1F20` | Nested panel, input, popover, hover lift |
| `surface-elevated` | `#242728` | Menu, dialog, and other floating layers |
| `border` | `#292C2E` | Standard hairline divider and card boundary |
| `border-strong` | `#474B4E` | Hovered, selected, or important separation |
| `text-primary` | `#F1F2F3` | Primary labels, headings, values |
| `text-secondary` | `#C2C4C6` | Supporting content and active secondary data |
| `text-muted` | `#94989C` | Labels, metadata, inactive navigation |
| `text-disabled` | `#616568` | Disabled control text; pair with disabled treatment |

### 4.2 Brand and operating accent

| Token | Hex | Use |
|---|---:|---|
| `primary` | `#FF7A1A` | Start measurement/test, active navigation, operating focus |
| `primary-hover` | `#FF8D3A` | Hovered primary control |
| `primary-active` | `#D9600B` | Pressed primary control |
| `primary-subtle` | `rgba(255, 122, 26, 0.13)` | Selected surface and restrained active tint |
| `focus-ring` | `rgba(142, 142, 142, 0.32)` | Outer focus halo; retain a 1 px visible outline |

`primary` remains the current ARP orange. It is not a pass/fail colour and must not be used to indicate warning or alarm.

### 4.3 Semantic tokens

| Token | Main | Subtle background | Meaning |
|---|---:|---:|---|
| `success` | `#36C977` | `rgba(54, 201, 119, 0.12)` | Ready, connected, passed, stable |
| `warning` | `#F5A623` | `rgba(245, 166, 35, 0.12)` | Caution, approaching limit, needs attention |
| `error` | `#F05252` | `rgba(240, 82, 82, 0.12)` | Fault, failed validation, unsafe or incomplete action |
| `info` | `#60A5FA` | `rgba(96, 165, 250, 0.12)` | Reference, instruction, non-blocking system information |

Semantic colours always appear with an icon and text. Do not rely on a dot, colour, or an icon alone.

### 4.4 Measurement tokens

Measurement colours distinguish role and data quality, not physical quantity. A wavelength, current, and power reading do not each receive a different decorative colour.

| Token | Colour | Use |
|---|---:|---|
| `measurement-primary` | `#F1F2F3` | Valid current reading and primary trace label |
| `measurement-secondary` | `#C2C4C6` | Derived value, secondary readback, non-primary trace |
| `measurement-reference` | `#60A5FA` | Reference record, target, or comparison curve |
| `measurement-limit` | `#F5A623` | Warning limit line or near-limit value |
| `measurement-warning` | `#F5A623` | Suspect but still interpretable data |
| `measurement-invalid` | `#94989C` | Missing, stale, invalid, or unavailable data |

### 4.5 Chart-series tokens

Use no more than three simultaneously prominent curves unless the task explicitly requires comparison. The existing warm series palette is retained:

| Token | Hex | Recommended role |
|---|---:|---|
| `chart-live` | `#FF7A1A` | Current live curve / selected measurement |
| `chart-secondary` | `#D5A98B` | Secondary or historical comparison |
| `chart-reference` | `#4CA4FF` | Reference / baseline curve |
| `chart-pass` | `#70A58A` | Pass envelope or verified reference marker |
| `chart-grid` | `rgba(71, 75, 78, 0.42)` | Grid and low-priority axes |

## 5. Typography

### 5.1 Font stack

```css
font-family: "ARP Interface", "DIN", "DIN 2014", "DIN Alternate",
  "Bahnschrift", "Roboto Condensed", "MiSans", "PingFang SC",
  "Microsoft YaHei UI", sans-serif;
```

Use the interface face for all UI. Use the DIN-oriented portion of the stack for live numbers, device identifiers, timestamps, and compact technical labels. Do not add a proprietary display font or a separate marketing typeface.

### 5.2 Type scale

| Token | Size / line height | Weight | Use |
|---|---:|---:|---|
| `app-title` | `16 / 22 px` | 650 | Title bar product name |
| `page-title` | `20 / 28 px` | 650 | Page title only; no page hero |
| `section-title` | `16 / 22 px` | 650 | Major work-area section |
| `card-title` | `14 / 20 px` | 600 | Chart, card, and device-section title |
| `body` | `14 / 20 px` | 400 | Default UI content |
| `body-strong` | `14 / 20 px` | 600 | Important labels and selected values |
| `label` | `13 / 18 px` | 500 | Field labels and measurement labels |
| `caption` | `12 / 16 px` | 400 | Metadata, helper copy, status detail |
| `micro` | `11 / 14 px` | 500 | Dense table metadata only |
| `button` | `13 / 16 px` | 600 | Standard button label |
| `numeric` | `13–16 / 18–22 px` | 600 | Table figures, current setpoint, device ID |

Use sentence case Chinese labels. Avoid persistent all caps. Letter spacing remains neutral for Chinese; for compact DIN data, `0.02em–0.04em` is permitted.

## 6. Numeric & Measurement Typography

Every numerical readout uses stable-width figures:

```css
font-variant-numeric: tabular-nums;
font-feature-settings: "tnum" 1;
```

| Element | Size / weight | Rule |
|---|---:|---|
| Measurement value | `28–32 px / 700` | Default primary card value; use `24 px` in dense strips |
| Unit | `13–16 px / 600` | Baseline-aligned, lower visual weight, `8 px` gap from value |
| Measurement label | `12–13 px / 500` | Secondary text; do not place above every digit group unnecessarily |
| Secondary readback | `12 px / 400` | Timestamp, stability, range, or source |
| Table number | `13–14 px / 600` | Right aligned, tabular figures |
| Device ID / serial / port | `12–13 px / 500` | DIN/monospaced-style fallback; never truncate without a copy affordance |

Example hierarchy: **`1064.27`** `nm` then `中心波长`.

### Numeric rules

- Values are right aligned in columns; labels are left aligned.
- Reserve enough character width for the largest expected valid value, sign, decimal point, and unit. Do not autosize a card to its current reading.
- Preserve the instrument-provided precision. Do not silently round a live value merely to make the layout neater.
- When presentation rounding is needed, state it in the column header or export context. Typical UI precision: power `0.001 W` / `0.1 mW`, wavelength `0.01 nm`, temperature `0.1 °C`, current `0.001 A` or `1 mA`, voltage `0.001 V`.
- `—` means unavailable; `Invalid` or `数据无效` means received but unusable; `Stale · 2.4 s` means out of date. These states are not interchangeable.

## 7. Spacing

The spacing base is 4 px. Apply only the following scale unless a platform control requires a measured exception.

| Token | Value | Typical use |
|---|---:|---|
| `space-1` | `4 px` | Icon-to-text micro gap, compact table cell inset |
| `space-2` | `8 px` | Field label gap, dense row gap |
| `space-3` | `12 px` | Card-grid gap, standard control group |
| `space-4` | `16 px` | Card interior section spacing |
| `space-6` | `24 px` | Major panel padding / split regions |
| `space-8` | `32 px` | Page-level grouping only |

- Default compact card: `12 px` padding; standard settings card: `16 px`; complex dialog section: `16–24 px`.
- Default work-area gap: `12 px`.
- Do not introduce arbitrary `13`, `17`, `19`, or `27 px` gaps.

## 8. Border, Radius & Surface Elevation

### 8.1 Borders and radius

| Token | Value | Use |
|---|---:|---|
| `border-hairline` | `1 px solid border` | Cards, inputs, rows, panel divisions |
| `radius-xs` | `2 px` | Indicator chip and tiny marker |
| `radius-sm` | `4 px` | Table selection, compact control, small popover |
| `radius-md` | `6 px` | Buttons, inputs, cards, dialogs |
| `radius-lg` | `8 px` | Large dialog shell only |

Do not use pills for standard buttons, inputs, or metric cards. A status chip may use a pill only when it improves scanning in a dense table.

### 8.2 Surface ladder

| Level | Treatment | Use |
|---|---|---|
| 0 | `canvas`, no shadow | Page background and chart exterior |
| 1 | `surface-1` + hairline | Sidebar, fixed chrome |
| 2 | `surface-2` + hairline | Main cards, charts, settings groups |
| 3 | `surface-3` + hairline | Inputs, nested controls, selected inactive region |
| 4 | `surface-elevated` + hairline + restrained shadow | Dropdown, popover, dialog |
| Focus | 1 px outline + 3 px `focus-ring` | Keyboard focus; never colour-only focus |

Primary layout cards use no shadow. Floating layers may use `0 16px 48px rgba(0,0,0,.45)`; never add glow.

## 9. Layout

### 9.1 Desktop-first geometry

ARP is designed primarily for `1920 × 1080`, then scales down to `1366 × 768` and up to `2560 × 1440`. The desktop work area is the primary product, not a mobile page.

- Persistent title bar: `50 px` target height.
- Persistent sidebar: `220–240 px` expanded; never compress labels into icon-only navigation without an explicit user action.
- Main work area: minimum `960 px` useful width; `12–24 px` outer padding according to window size.
- Charts receive the largest flexible region. The spectrum plot should normally have at least `320 px` vertical drawing space in a 1080 px window.
- At 1366 × 768, collapse secondary setting bands, not chart labels, safety state, or the primary measurement.
- Use CSS grid with `minmax(0, …)` and stable columns; do not allow long device names or live data to push controls off screen.

### 9.2 Page priority

Arrange each workspace in this order unless a safety state takes precedence:

1. Device and current test state
2. Primary live measurement / spectrum plot
3. Core result cards and critical limits
4. Test controls and core parameters
5. Secondary information, history, and export

Large logos, decorative artwork, page heroes, and non-essential summaries must never displace live data.

### 9.3 Responsive and high-DPI rules

- Preserve minimum interactive targets of `28 px`; prefer `32 px` for frequent controls.
- Use device pixels only inside canvas rendering; keep CSS layout in logical pixels.
- Verify 100%, 125%, 150%, and 200% Windows scaling. Text must reflow or scroll inside its intended region, not overlap a chart or footer.
- On narrow windows, stack grid panels deliberately and retain explicit labels; do not hide status by default.

## 10. Navigation

### Sidebar

The sidebar is the single primary page-navigation system: 自动测试, 手动配置, PD 测试, and 出货报告. Each destination has an icon, Chinese text label, and optional keyboard shortcut.

- Active item: `primary-subtle` background, `primary` icon or 2 px leading rule, and `text-primary` label.
- Inactive item: `text-muted`; hover uses a neutral surface lift, not orange fill.
- Disabled item: clear reason in tooltip or inline helper text when state-dependent.
- Do not create a second page-level nav in the top bar.

### Top bar, tabs, and secondary navigation

- The title bar holds product identity, compact current state, pause/save actions when applicable, and a clearly separated emergency-stop action.
- Tabs switch peer views inside one bounded region (for example, station 1 / station 2 or chart modes). They do not duplicate page navigation.
- Active tabs use text, a 2 px underline or restrained surface selection, and the primary accent. Inactive tabs never look disabled.

## 11. Measurement Cards

A measurement card presents one answer, not a collection of equal-priority metrics.

### Required anatomy

1. `label` — short, unambiguous measurement name.
2. `current value` — the primary visual element.
3. `unit` — visually subordinate and baseline-aligned.
4. `secondary information` — optional stability, timestamp, source, or range.
5. `status` — optional text + icon when quality or readiness changes meaning.
6. `trend` — optional compact delta / arrow; never replace the value.
7. `limit` — optional target/range stated as text and, where relevant, a subtle rule.

### Rules

- Default card height: `72–96 px` in a compact metric strip; no gratuitous blank space.
- Use a neutral surface and hairline border. Do not make each card a different colour.
- One card has one primary reading. Place related values in a table or stacked group instead of adding competing large numbers.
- Give invalid and no-data values a fixed width placeholder so the card remains stable.
- A clickable card must disclose the destination or action through hover/focus affordance; it must not look like a static metric.

## 12. Chart & Spectrum Visualization

Charts are measurement instruments, not decoration. Canvas and SVG implementations must prioritise stable frame time, full valid data, and readable axes.

### 12.1 Spectrum chart anatomy

- Plot background: `canvas` or `surface-2`; no gradient or area fill by default.
- Plot boundary: subtle `border` hairline where it helps define the drawing region.
- Grid: `chart-grid`, 1 px; major grid only, behind data.
- Axes and ticks: `text-muted`, 11–12 px, DIN-oriented figures for ticks.
- Axis labels: `text-secondary`, 12 px; include physical units, e.g. `Wavelength (nm)`.
- Live curve: `chart-live`, 1.5–2 px, opaque and visually highest priority.
- Reference curve: `chart-reference`, 1–1.5 px, optionally dashed; identify it in the legend.
- Secondary curve: `chart-secondary`, lower visual priority.
- Peak marker: a clear small marker + text label, positioned to avoid covering the peak.
- Centre wavelength marker: thin vertical rule with explicit `中心 λ` label; do not confuse it with the peak marker.
- Threshold/limit: dashed `measurement-limit` rule with a text label and visible direction of pass/fail.
- Cursor: 1 px high-contrast crosshair; tooltip reports exact coordinate, unit, and series name.

### 12.2 Chart behaviour

- Keep full valid native spectrum samples. Rendering cadence may be bounded, but acquisition data must not be thinned merely for visual smoothness.
- No ornamental entrance animation, interpolated curve morph, or filled gradient below a trace.
- Tooltip and zoom selection must not obscure essential values; Escape clears temporary selection, and Reset View is an explicit secondary action.
- Disabled data is a muted empty plot with a factual message, e.g. `等待光谱仪开始采集`; invalid data shows the reason and last valid timestamp if available.
- Legends identify curve role and unit. Never encode a series identity with colour alone.
- Chart frames must remain stable during a 5 Hz UI refresh. UI refresh cadence is independent from hardware acquisition cadence.

## 13. Form Controls & Parameter Setting

Forms configure physical equipment. Labels, units, constraints, and invalid states must therefore be legible before an action is executed.

### 13.1 Control anatomy and states

| Control | Normal | Hover | Focus | Disabled | Error |
|---|---|---|---|---|---|
| Text / numeric input | `surface-3`, `border` | `border-strong` | 1 px `primary` outline + focus ring | muted surface + reason | `error` border + adjacent message |
| Select | Same as input + chevron | Same | Same | Same | Same |
| Toggle / checkbox / radio | Neutral outline | Neutral lift | Focus ring | Muted, non-interactive | Error label/message |
| Stepper | Numeric field + `+/-` buttons | Button hover only | Field focus | Full group disabled | Field error |

All control states must preserve text contrast. A disabled control should look unavailable, not empty.

### 13.2 Numeric and unit input

Use a composed control for physical values:

```text
[ 1200                 ][ mA ]
```

- The numerical value is editable; the unit is a fixed suffix region unless unit conversion is an explicit feature.
- Separate suffix with a hairline or clear spacing so it is never mistaken for editable text.
- Right-align the editable numeric value when it improves scanning across a form; retain the label on the left or above.
- Display min/max, step, and permitted range as concise helper text when consequential: `范围 0–2000 mA · 步进 1 mA`.
- Mouse-wheel changes must not silently alter a focused physical setpoint; require an intentional editing interaction.

### 13.3 Form layout

- Group fields by instrument or task, not merely by control type.
- Use 2–3 columns only when labels and units stay readable; never create a four-column grid of dense physical settings.
- Put hardware address/resource and calibration inputs near their device state and validation result.
- Destructive reset and connection controls are visually separate from normal save/apply controls.

## 14. Buttons

| Variant | Treatment | Use |
|---|---|---|
| Primary | `primary` fill, light text | One key forward action: 开始测量 / 开始测试 |
| Secondary | neutral surface + hairline | Save, Export, Refresh, Apply, Reset View |
| Tertiary | text + optional icon | Low-impact contextual action |
| Ghost | no resting border/fill | Toolbar and row actions where discoverability is already established |
| Danger | `error` subtle fill + error border/text | Disconnect, Clear Data, confirmed force exit |
| Emergency stop | dedicated error treatment, always labelled | Immediate safety interruption; never share styling with ordinary Stop |
| Icon button | icon + accessible name / tooltip | Compact standard actions only |

- Standard height: `32 px`; dense toolbar: `28 px`; prominent primary action: `36 px`.
- Use action verbs: `开始测试`, `暂停测试`, `保存设置`, `导出数据`, `重置视图`.
- Only one primary action should be dominant inside a bounded panel. Export, save, and refresh are not primary by default.
- Stop and emergency stop are semantically distinct: Stop ends a normal operation; Emergency stop invokes the safety path.

## 15. Tables & Recorded Data

Tables support result review, history, and configuration verification. They must be scannable in long-running engineering work.

- Header: `surface-3`, `12 px / 600`, sticky when the row list scrolls.
- Body: `13–14 px`; default row height `32–36 px`; selected row uses `primary-subtle` plus text/icon state.
- Use quiet hairlines between rows; zebra striping is optional and must be lower contrast than selection.
- Text is left aligned; numeric data is right aligned; units live in header where a column has a single unit.
- Timestamps use stable, sortable format: `YYYY-MM-DD HH:mm:ss`.
- Invalid/failed rows state the reason in a dedicated status column; do not colour the entire row red.
- Preserve measured precision in exports and record detail. The compact table view may state its display precision explicitly.

## 16. Status & Device State

### 16.1 Canonical device states

| State | Display treatment | Meaning |
|---|---|---|
| Disconnected | `○ 未连接` + muted icon/text | No active device session |
| Connecting | spinner + `正在连接` | Connection request in progress |
| Connected | link/check icon + `已连接` | Session open; not necessarily ready |
| Ready | check icon + `就绪` + success | Configured and safe to begin |
| Measuring | activity icon + `测量中` + primary | Acquiring or executing workflow |
| Warning | triangle icon + `注意` + warning | Attention required; workflow meaning is stated |
| Error | error icon + `错误` + error | Fault, failed command, or unsafe condition |

All state badges use **icon + text + colour**. A dot is optional only as a supporting cue. Status detail belongs adjacent to the affected device or workflow, not in an unrelated global banner.

### 16.2 Operational clarity checklist

At every time, the main workspace must answer:

- Which station is active?
- Are required devices connected and ready?
- Is acquisition/test running, paused, stopped, or completing?
- Is there a warning or safety condition requiring action?
- What is the latest primary result, including its validity?

## 17. Notifications, Warnings & Errors

### Notification levels

| Level | Placement | Persistence | Example |
|---|---|---|---|
| Info confirmation | Non-blocking toast | Auto-dismiss after ~3 s | `设置已保存` |
| Warning | Inline callout near affected control; toast only as supplement | Until resolved or dismissed | `接近功率上限` |
| Error | Inline error plus persistent event feedback | Until acknowledged / condition changes | `光谱仪连接失败` |
| Alarm / safety | Blocking alert dialog and persistent page state | Explicit acknowledgement required | `无法确认安全下电` |

- State what happened, consequence, and the safe next action.
- Do not use a generic `操作失败` when an instrument or protocol error is available.
- Never turn the entire app background red. Contain urgency to the affected dialog, panel, and status rail.
- Preserve safety-critical messages until acknowledged; a transient toast is insufficient.

## 18. Dialog, Popover & Tooltip

### Dialog

- Use for consequential settings, confirmations, safety alarms, and tasks that cannot be completed in place.
- Width: normally `480–704 px`; wide configuration dialog: up to `1088 px`; maximum `calc(100vw - 32px)`.
- `surface-elevated`, 1 px `border`, `radius-md` (or `radius-lg` for a major dialog), and one restrained floating shadow.
- Header contains title, purpose, and close action; footer contains concise consequence/help text and ordered actions.
- Initial focus goes to the safest useful control. Escape closes only non-destructive dialogs; safety alarms require their explicit action.

### Popover and tooltip

- Popovers contain compact task controls or detail that must remain in context.
- Tooltips explain icon-only controls, abbreviations, or disabled reasons; they do not hold critical error information.
- Avoid tooltip-only labels for common controls. The primary measurement and safety state are always visible.

## 19. Interaction States & Motion

| State | Required feedback |
|---|---|
| Hover | Border/surface change; no large transform or colour flash |
| Focus | Visible 1 px outline + focus ring on keyboard focus |
| Active / pressed | Brief darker primary or neutral surface state |
| Pending | Disable duplicate command, preserve label or show concise `保存中…` state |
| Success | Status text/icon; do not over-animate |
| Disabled | Muted treatment + reason where the condition is not obvious |

- Keep micro-transitions to `120–160 ms`; they may clarify state change but must never delay a hardware command.
- Do not animate continuously except for a truthful progress/connecting indicator.
- Do not animate live numeric value changes, chart lines, or error banners merely for effect.
- Respect `prefers-reduced-motion` and maintain usable focus treatment without motion.

## 20. Data Density Guidelines

ARP is a medium-high-density professional application.

- Use borders to establish group boundaries, typography to establish rank, and 8–16 px gaps to separate related data.
- Keep labels short and consistently placed. Repeated label positions enable rapid scanning.
- Prefer an aligned readback grid or table over many individually framed micro-cards.
- Reserve coloured accents for current action, semantic state, reference, or limit. Most text stays neutral.
- Favour progressive disclosure for rare calibration or diagnostic parameters; do not hide current equipment state, live readings, or safety controls.
- Empty states are factual and compact: state what data is missing and the action that makes it appear.

## 21. Accessibility

- Meet WCAG AA contrast for text and interactive controls. Verify semantic colours on `surface-2` and `surface-elevated`.
- Never use colour as the only error, status, selection, or chart-series identifier.
- All icon buttons require an accessible name; all fields require a visible label or programmatic label.
- Keyboard order follows the visual task order. Focus never disappears beneath a sticky title bar, modal, or scroll container.
- Dialogs trap focus, restore it to the launching control, and identify their purpose to assistive technology.
- Charts provide a text summary of primary result, axes, active curves, and limit status for non-visual use.
- Do not communicate a live reading through flashing alone; use a labelled status and current value.

## 22. Do’s

- Keep the ARP orange as the operating accent and use it sparingly.
- Make the current live reading and chart easy to identify within one second.
- Use tabular figures, fixed numerical widths, and explicit units.
- Pair every device state with icon, text, and semantic colour.
- Use hairline borders and graphite surface changes to organise dense content.
- Show physical limits, validity, stale-data state, and safety consequence in plain language.
- Verify desktop layouts at 1366×768, 1920×1080, 2560×1440, and Windows high-DPI scaling.

## 23. Don’ts

- Do not add landing-page heroes, testimonials, pricing, CTAs, logo walls, or oversized promotional headings.
- Do not use large gradients, neon grids, glassmorphism, excessive glow, or decorative 3D effects.
- Do not give every metric card a different colour or make colour stand in for a label.
- Do not use oversized 12/16/24 px SaaS-style rounding across the workbench.
- Do not make normal Stop and Emergency Stop look equivalent.
- Do not hide a unit, change displayed precision silently, or round away meaningful measurement data.
- Do not use animations that reduce chart performance, obscure data, or suggest a measurement trend that the instrument did not report.

## 24. Agent Implementation Guide

### Before changing UI

1. Inspect the active desktop surface, not only JSX/CSS, at an idle state and—when hardware is available—a real running state.
2. Identify the affected semantic token, component contract, workflow state, and existing responsive behaviour.
3. Preserve actual instrument bindings, acquisition data, test-state semantics, and safe-stop paths. UI cadence is not acquisition cadence.
4. Reuse the existing shadcn/Tailwind primitives and ARP tokens before creating a new component or adding a dependency.

### Implementation rules

- Create or map named tokens before applying a repeated colour, spacing, radius, or typography value.
- Use `font-variant-numeric: tabular-nums` for all live values, tables, timestamps, and setpoints.
- Build measurement cards, device state, unit inputs, and chart annotation as reusable component patterns only after a real page needs them.
- Prefer surface/border changes over shadow. Restrict substantial shadow to popovers and dialogs.
- Preserve every valid spectrum sample. Optimise transport, rendering, caching, and lifecycle separately from measurement data.
- Make errors recoverable: state the failure, affected device/action, retained safe state, and next valid action.
- Do not add a compatibility switch, alternate theme path, or redundant navigation layer for an obsolete UI treatment.

### Verification checklist

- [ ] Current primary reading, unit, status, and data-validity state are visible without scrolling.
- [ ] Numeric changes do not shift layout or change card width.
- [ ] Keyboard focus is visible on every interactive control.
- [ ] Device states read correctly without colour perception.
- [ ] Chart curves, limits, markers, and axes remain legible on the actual canvas.
- [ ] Automatic, manual, PD, and reporting pages retain their navigation and safety behaviour.
- [ ] 1366×768, 1920×1080, 2560×1440, and high-DPI desktop states have been inspected.
- [ ] No code path has thinned valid spectrum data or turned UI refresh cadence into an acquisition limit.
- [ ] The real Tauri/WebView target has been checked for the changed flow; a browser-only preview is not final acceptance.
