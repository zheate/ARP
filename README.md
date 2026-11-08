# Power Test — GPUI

本分支的桌面入口为 GPUI 原生应用。Python 硬件后端、协议版本 2、采集规则、计算和记录格式沿用现有实现。

```sh
npm start
npm run demo
npm run build
npm run test:gpui
```

`npm run demo` 使用原 Tauri 演示夹具，不启动 Python 后端，不连接仪器。真实工作模式继续使用 `sth_eb314` 环境，也可以用 `ARP_PYTHON_EXECUTABLE` 指定解释器。OceanDirect 的工作目录仍保持在项目根目录。

- `gpui-ui/src/main.rs`：窗口、操作状态和系统退出请求。
- `gpui-ui/src/views.rs`：六个主页面与三类设备设置窗口。
- `gpui-ui/src/charts.rs`：原生曲线绘制；像素坐标转换不改变原始数据。
- `gpui-ui/src/model.rs`：完整曲线、增量更新、光谱网格与版本检查。
- `gpui-ui/src/controller.rs`：许可证校验、内部命令锁和安全关闭。
- `gpui-ui/src/transport.rs`：现有 Python 进程协议的原生实现；命令超时不自动重放。
- `tauri_bridge/`、`combined_test/`：保留原有后端与设备实现。

许可证继续使用原应用数据目录 `com.changguanghuaxin.arp`，机器码和签名算法保持不变。首次有效授权前不创建硬件后端。授权失效后，现有后端仍保留安全停止入口。原生进程锁阻止第二个硬件界面实例启动。

关闭窗口或从系统退出时，必须同时确认安全下电和导出队列完成。失败时保留重试、取消和经操作员确认的强制退出。不得把超时当作下电成功。

## Windows

`npm run build:windows` 构建 GPUI 可执行文件与 Python sidecar，输出到 `release/gpui/Power Test`。`npm run build:windows:full` 构建包含设备驱动的完整安装器。MSI/NSIS 继续使用既有打包工具和升级身份，但打包的主程序为 GPUI 二进制；不会编译或启动旧 Tauri 界面。安装计划跳过 WebView2，完整载荷也排除 WebView2。构建产物写入独立目录并记录哈希，完整安装器拒绝复用迁移前的 Tauri MSI。安装脚本的 44 项模拟测试和 8 个 PowerShell 文件的语法检查通过。Windows 编译、离线安装、VISA/COM、OceanDirect、CH341/TDK/RU 和实际仪器验收仍须在目标系统完成。macOS 演示不支持这些结论。

GPUI 依赖固定在 `gpui-kit 0.9.0`。`vendor/gpui-pre-macos` 的三处等价兼容改动见 `gpui-ui/vendor/README.md`。

迁移前版本保存在 GitHub 分支 `audit/arp-ui-final-20260914`，提交 `9534e1e074c35edafb58a925272364137caf0a45`。原 Tauri 架构文档见 `gpui-ui/TAURI_BASELINE.md`，仅供对照。
