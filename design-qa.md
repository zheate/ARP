# Design QA

- Source visual truth: `/var/folders/pj/_thskkm14c333y_j9nd_dk8h0000gn/T/codex-clipboard-9f70f579-bb7a-49fb-beb5-c774c819a77f.png`
- Implementation screenshot: `/Users/zh/.codex/visualizations/2026/07/19/019f7a17-c8bc-7c52-968b-b0b10952da4b/arp-charts-aligned-after.png`
- Viewport: 1280 × 720 CSS pixels
- State: 详细配置页面，浏览器预览模式，后端为空状态

## Comparison

The source screenshot shows the two side-by-side chart cards with matching outer bounds but different inner plot vertical positions. The implementation gives the shared `ChartPanel` header a consistent minimum height, so both chart areas now start and end on the same horizontal baselines.

Measured post-fix evidence: both chart cards are `top=486`, `bottom=732`; both inner plot regions are `top=555`, `bottom=703`, `height=148`.

Fonts, spacing/layout rhythm, colors/tokens, image/assets, and copy were checked. No image assets are involved in this change.

## Findings

No actionable P0, P1, or P2 findings.

## Comparison history

- Initial comparison: the left 功率实时 plot started 8px lower than the right 功率 / 效率 plot because its header contained status and value content.
- Fix: set the shared `ChartPanel` header to `min-h-9` so headers with and without a right-side metric block occupy the same height.
- Post-fix evidence: both plot regions measure the same top, bottom, and height in the rendered page.

## Implementation Checklist

- [x] Aligned the two chart card headers.
- [x] Aligned the inner plot top edges.
- [x] Aligned the inner plot bottom edges.
- [x] Preserved chart data and header content.
- [x] TypeScript and production build pass.

## Follow-up Polish

None required for this scoped change.

final result: passed

## Device settings dialog QA

- Source visual truth: `/var/folders/pj/_thskkm14c333y_j9nd_dk8h0000gn/T/codex-clipboard-9f38c507-06d8-4c91-b49e-dc50fedb6603.png`
- Implementation screenshot: `/Users/zh/Documents/test/ARP/.codex/device-settings-cards.png`
- Focused dialog screenshot: `/Users/zh/Documents/test/ARP/.codex/power-settings-dialog.png`
- Viewport: 1280 × 720 CSS pixels
- State: 自动测试页，示例数据模式；卡片截图为无弹窗状态，弹窗截图为电源设置打开状态

### Comparison

参考图中的三张深色设备卡片在实现中保持了三列排列、图标容器、标题、状态说明、边框和状态点；本次新增的可点击/键盘入口不改变卡片的主要视觉结构。弹窗截图确认了居中的深色设置面板、设备专属标题、滚动内容区和底部保存/关闭操作。

### Findings

No actionable P0, P1, or P2 findings.

### Interaction evidence

- 电源卡片打开 `电源设置`，包含控制器、TDK 串口、电压、电流和电源控制操作。
- 功率计卡片打开 `功率计设置`，包含串口资源、校准波长、软件增益、采集和相对调零操作。
- 光谱仪卡片打开 `光谱仪设置`，包含 Ocean Insight 设备资源、积分时间、刷新间隔、自动积分和光谱操作。
- 三个弹窗均可通过关闭按钮、遮罩点击和 Escape 关闭；弹窗使用现有配置保存命令。
- 构建通过；浏览器控制台未发现本次代码产生的错误，仅有图表首次布局测量时的既有尺寸警告。

### Fidelity surfaces

- Fonts and typography: 复用现有 Geist / 中文回退字体与现有字号层级。
- Spacing and layout rhythm: 复用现有卡片间距、圆角和深色工作台节奏；弹窗在 1280 × 720 下保持居中并可滚动。
- Colors and visual tokens: 复用现有背景、边框、输入框、蓝色主操作和状态点 token。
- Image quality and asset fidelity: 无新增位图资产；设备图标使用现有 lucide-react 图标库。
- Copy and content: 三个弹窗标题及字段按设备角色区分，保留现有中文操作文案。

final result: passed
