# GPUI 迁移检查记录

备份分支：`audit/arp-ui-final-20260914`。本地与 GitHub 远端均确认提交为 `9534e1e074c35edafb58a925272364137caf0a45`。当前开发分支：`codex/gpui`。

本轮将默认开发入口切换到 GPUI。保留现有无界面 Python 后端、版本 2 协议、原始 binary64 数据、采集节奏、判稳与下电规则。旧 Tauri 源码作为迁移参照保留。

## 界面方向

- 产品：实验室与产线的电源、功率和光谱测试工具。
- 气质：紧凑、精确、克制。
- 环境：沿用现有工程界面的石墨中性色和橙色重点色。
- 重点：实时测量数字与完整曲线；状态颜色保留业务含义。
- 细节：同排设备卡使用统一尺寸、内边距和底部操作位置。

静态检查与首轮原生界面检查发现并修正：输入框缺少可访问名称；默认组件的重点色与原界面不一致；命令状态行会改变页面高度。旧截图仅作视觉参照，窗口尺寸不同，不构成同视口像素验收。此记录采用 unslop（Damir Sokolovsky，https://heydamir.design/unslop，CC BY 4.0）及 20ui 的检查方法。

## 本地验证

- `npm run build`：通过。GPUI Kit 0.9.0、Rust 1.95.0、macOS arm64，开发构建。日志见 build.txt。
- `npm run test:gpui`：14 项通过。包括许可证签名与机器绑定、过期安全入口、关闭的双重确认、旧快照版本隔离、完整光谱网格，以及真实子进程管道中的 8192 点 binary64 往返。日志见 native-core-tests.txt。
- `conda run -n sth_eb314 python -m unittest tests.test_bridge_contract tests.test_tauri_bridge tests.test_power_meter_profiles -v`：49 项通过。日志见 backend-tests.txt。
- 安装脚本的 44 项副作用隔离模拟测试通过，包含原生计划不安装 WebView2、UTF-8/中文路径、版本读取、旧 MSI/损坏产物拒绝，以及既有 NI 续装/进程保护。使用官方便携版 PowerShell 7.6.9；下载包 SHA-256 与官方 hashes.sha256 一致。日志见 installer-tests.txt。
- 8 个 PowerShell 文件的语法检查通过，日志见 installer-syntax.txt。
- `git diff --check`：通过。
- macOS 原生首轮演示：主页面显示、脚本选择器打开、Escape 收起和系统设置页面切换有本会话的 CUA 证据。演示使用迁移前夹具，未连接仪器。

## 未完成的验收

Mac 在第二轮原生检查时处于锁定状态，CUA 两次报告无法自动解锁。等待用户手动解锁。最终版本的三个设备选择器、一次识别、关闭/取消、布局稳定性与 1180×760/960×640 适配尚未完成运行验收。

Windows 构建脚本迁移为输出 GPUI 应用目录和 Python sidecar。完整安装命令复用既有 MSI/NSIS 工具及升级身份，使用预编译 GPUI 主程序；安装计划和发布载荷不包含 WebView2。原生安装器使用独立构建目录、原生版本和产物哈希记录，拒绝混入旧 Tauri MSI。Windows 构建与实际安装仍未执行。Windows/WebView 替换后的部署、VISA/COM、真实设备、完整离线安装和性能收益未验证。本分支仍是开发迁移分支，不能据此宣称生产版本验收完成。

源文件的 SHA-256 见 source-hashes.json。Python 设备、计算与持久化模块没有改动。恢复迁移前入口时，在干净工作区执行 `git switch audit/arp-ui-final-20260914`；原始测试数据保持在原位置。
