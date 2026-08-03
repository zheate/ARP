# Power Test Tauri UI

Power Test 程序的桌面界面，技术栈为 Tauri 2、React、TypeScript、Vite、Tailwind CSS 和 shadcn/ui。

## 已接入功能

- 自动测试：任务信息、完整电流/超时/安全下电参数、准备门禁、开始、重试、结束并下电、返回设置、实时进度、功率与光谱。
- 详细配置：CH341/TDK 选择与连接、电流/电压/输出控制、电压/电流/温度读取、功率计识别/采集/调零、光谱仪识别/采集/自动积分/CSV、三张实时曲线同页显示。
- PD 采集：NI-DAQ 识别、通道/接线/量程/采样/标定/保存设置、开始/停止、统计量与实时趋势。
- 安全：所有设备命令、自动状态、Excel 导出和下电继续由现有 Python 控制器负责；Tauri 关闭前会请求紧急停止，并等待后台采集/导出线程结束。

现有 PySide6 程序仍可通过仓库根目录的 `python main.py` 独立运行，作为迁移期回退入口。

## 本地运行

```sh
npm install
npm run tauri dev
```

解释器选择顺序：`ARP_PYTHON_EXECUTABLE`、当前 Conda 环境、项目常用的 `sth_eb314` 环境、系统 Python。若运行环境无法加载 PySide6，界面会诚实退回只读模式，不扫描或控制硬件。

```sh
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
```

协议与安全边界见 [BRIDGE_CONTRACT.md](./BRIDGE_CONTRACT.md)。

## Windows 离线安装包

在装有项目驱动、Conda `sth_eb314`、Node.js、Rust 和 PyInstaller 的 Windows 构建机上运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build_tauri_windows.ps1
```

脚本先生成单文件 `arp-python` sidecar，再通过 Windows 专用 Tauri 配置将其装入安装包。安装后的 Tauri 主进程优先使用同目录 sidecar，不依赖目标电脑预装 Python。OceanDirect、CH341、VISA 和 NI-DAQmx 仍必须在 Windows 真机上用实际硬件完成最终验收。

若仓库中没有 `assets/libs/ocean_direct/OceanDirect.dll`，脚本会明确警告并继续构建；这样的安装包不能用于 Ocean Insight 光谱仪验收。
