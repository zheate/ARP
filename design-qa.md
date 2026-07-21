# Design QA — 功率 / 效率点线图

- source visual truth path: `/var/folders/pj/_thskkm14c333y_j9nd_dk8h0000gn/T/codex-clipboard-30e98a1b-d153-4ff3-a234-81cdf65da84a.png`
- implementation screenshot path: `/Users/zh/.codex/visualizations/2026/07/20/019f7f3f-4eec-7263-a043-bfb0916f966d/arp-power-efficiency-hollow-points.png`
- full-view implementation screenshot: `/Users/zh/.codex/visualizations/2026/07/20/019f7f3f-4eec-7263-a043-bfb0916f966d/arp-automatic-no-spectrum-full.png`
- side-by-side comparison: `/Users/zh/.codex/visualizations/2026/07/20/019f7f3f-4eec-7263-a043-bfb0916f966d/power-curve-side-by-side.png`
- viewport: `1280 × 720`, device pixel ratio `2`
- state: 自动测试示例数据；“同时采集光谱并判断波长稳定”未勾选；功率 / 效率图可见
- primary interaction tested: 取消光谱采集后，右侧图表由光谱切换为功率 / 效率
- console errors checked: 无 error / warning

## Full-view comparison evidence

自动测试页面保留原有两列布局、标题层级、卡片间距和双坐标轴；取消光谱后，右侧卡片按既有布局显示功率 / 效率图，没有溢出或遮挡。

## Focused region comparison evidence

已将参考截图的“功率趋势”区域与 ARP 功率 / 效率区域放入同一张对比图。两者的关键点线处理一致：实线连接、圆形标记、圆心为图表底色、外圈使用曲线同色描边。ARP 同时显示两条数据，因此保留功率绿色和效率橙色以及双坐标轴，这是既有产品约束，不属于设计漂移。

## Required fidelity surfaces

- Fonts and typography: 本次未改字体、字号、字重或标签文案；沿用 ARP 现有设计系统。
- Spacing and layout rhythm: 卡片、图表内边距与自动测试两列布局保持不变；无可见溢出。
- Colors and visual tokens: 点中心使用 ARP 的 `--card` 底色，描边使用各曲线颜色，避免硬编码参考项目的卡片灰色。
- Image quality and asset fidelity: 图表继续使用高分屏 Canvas 矢量绘制；未新增或替换产品图片资产。
- Copy and content: “功率 / 效率”、图例与单位文案保持不变。

## Comparison history

1. Earlier finding: `[P1]` 效率使用三角形且点为实心，与参考截图单曲线空心圆风格不一致。
   - Fix: 两条曲线统一为 `8px` 圆形标记；圆心使用卡片底色；使用 `2px` 曲线同色描边；连线保持 `1.7px` 实线。
   - Post-fix evidence: `power-curve-side-by-side.png` 显示功率和效率均为同样的空心圆点线样式。
2. Final comparison: 未发现可执行的 P0 / P1 / P2 差异。

## Findings

- 无 P0 / P1 / P2 问题。

## Open Questions

- 无。

## Implementation Checklist

- [x] 功率曲线使用空心圆点。
- [x] 效率曲线使用空心圆点，不再使用三角形。
- [x] 点描边与曲线同色，圆心跟随卡片底色。
- [x] 自动测试取消光谱后显示功率 / 效率图。
- [x] 生产构建通过，浏览器控制台无错误。

## Follow-up Polish

- 无阻塞项。

final result: passed
