# 自动测试控制区局部调整验证

产品改动只涉及 AutomaticTestControlCard 的展示，以及 automatic-test-progress / automatic-test-control-card 对应样式。

- 状态与记录在上，开始、停止在下；结束后只显示终态标题。
- 已确认下电的提前结束使用中性文字；未确认下电与异常结束保留危险色。
- 按钮禁用条件、开始与停止回调、共享进度计算和后端均未修改。
- TypeScript 无输出通过；进度与安全状态局部测试 24/24 通过。
- verification.json：实际组件、8 种状态的布局和回调、4 种窗口宽度、2 种主题、3 种密度，共 82 项检查通过。
- after-light / after-dark / after-2x：已人工查看的渲染截图。截图工具未报告横向溢出和运行错误。
- 验证使用浏览器与无硬件回调；不代表 Windows WebView、驱动或真实仪器验收。
- 960px 的原有结束汇总条仍可能拥挤。本次仅调整控制区，未修改该区域。

source-before 保留开始本次实现前的文件。fixture/before-controls.tsx 与 before.css 仅还原控制区的改前状态，保留验证期间其他工作对光谱区的并行修改，以免混入控制区的对比结论。没有回滚其他工作。

如需重现，可从项目根目录运行 node output/playwright/automatic-control-20261015/build-fixture.mjs，然后运行同目录的 verify.mjs。浏览器要求现有 localhost:1420 开发服务；生成的 tauri-ui/.automatic-control-qa-20261015 是可删除的临时验证目录。
