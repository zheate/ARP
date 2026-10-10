# CH341 Mini Controller

面向客户的独立 Python/Tkinter 手动电流控制器。仅使用 CH341 I²C，Windows 10/11 x64；Python、Tcl/Tk 和 CH341DLLA64.DLL 随 EXE 封装，客户不需要安装 Python、WebView2、Qt、NI/VISA、功率计或光谱仪运行库。

## 功能

- 使用中性的 CH341 Mini Controller 名称及通用芯片图标，无公司标志、公司名称或厂商背书。手动电流控制、四项回读及可滚动操作记录按共享网格对齐，适配 100%、125%、150% 缩放。
- 手动识别/选择 CH341。I²C 7 位地址固定 `0x41`、通信速度固定 20 kHz（CH341 模式 `0`，沿用 `tauri_bridge/station_runtime.py` 实际连接设置），客户界面不提供这两个参数。
- 手动下发 0–20 A 电流，最小分辨率 0.01 A；没有自动加电、扫描电流、采集或记录工作簿功能。
- 四个独立按钮手动回读输入电压、输出电压、电流和模块温度，无自动轮询。
- 启动和连接不写电流，不恢复上次输出。下发成功表示通信成功，实际输出以回读为准。
- 设置电流和回读共用 1.1 秒命令保护间隔，与 `StationRuntime._reserve_power_supply_command()` 相同。手动请求过快时拒绝，不排队、不延迟自动执行。一次读事务中的写/读仍保留原驱动 1 ms 等待。
- 停止、断开及退出沿用原 CH341 下电逻辑：每步减少 5 A，间隔 1.1 秒，最终下发 `0 A`。例如 18 A 按 `13 → 8 → 3 → 0 A` 下电。不会在下电期间新增回读命令；发送成功不代表实际输出回读确认。通信失败保留连接并给出重试、取消或确认物理断电后强制退出。
- 一条后台通信队列，界面不因 USB 通信或归零等待而阻塞。软件同一 Windows 登录会话内仅允许一个实例。

## 开发与打包

在仓库根目录使用 `sth_eb314` 环境和 PowerShell 7（`pwsh`，仅开发/构建机需要）：

```powershell
npm run mini:prepare
npm run mini:start
npm run mini:test
npm run mini:build
```

打包需已有 PyInstaller、NSIS 及 Pillow（仅构建机用于生成通用芯片图标，客户 EXE 不包含 Pillow）。构建脚本只读取 `local_drivers/current_project/01_ch341_i2c/CH341PAR.EXE`，验证固定 SHA-256 和驱动 CAT 签名，提取完整且未改动的 WCH INF 载荷。该同一签名包中 INF 引用的 CH347 DLL 也保留，以免破坏 Windows 驱动包完整性；不会添加其他设备驱动。

输出：`release/ch341-mini/CH341Mini_0.1.0_x64-setup.exe`。双击并允许 Windows 管理员提示后，选择安装目录并点击“安装”，统一安装 CH341 驱动、软件及快捷方式。安装目录支持直接输入及浏览文件夹，默认位于 Program Files；程序、快捷方式和卸载程序均使用所选目录。客户无需单独运行厂商驱动安装器，也无需联网。安装失败会停止并显示错误，需要重启时提示重启。安装器不打开或控制硬件。卸载软件保留共享 CH341 驱动。

驱动使用微软支持的 [`pnputil /add-driver … /install`](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/pnputil-command-syntax) 安装。构建环境和签名清单写入 `build-manifest.json`。`CH341Mini.exe --self-check REPORT_JSON` 只检查冻结运行时、Tcl 和 DLL 符号，不扫描或打开设备。

## 验证范围

14 项单元测试对照原有 `combined_test/core.py` 的全部 2001 个 0.01 A 电流点，以及 `combined_test/automation.py` 的所有对应下电序列；覆盖连接不加电、参数拒绝、读写失败、加电和回读共用 1.1 秒保护间隔、下电及释放顺序。构建自动运行冻结 Python/Tcl/Tk/DLL 检查和包内容审计。模拟界面检查覆盖三种尺寸及连接、下发、回读、停止、关闭失败/取消流程。真实加电、干净客户机离线安装和重启验收需在对应硬件/系统上完成；构建成功不能替代这些验收。

完整 Power Test 仍使用原有 Tauri 产品。Mini 版不加载其后端、配置或测量设备，不安装或变更 NI/LabVIEW。

`tests/ui_smoke.py` 使用假驱动，检查 100%、125%、150% Tk 缩放下各三种窗口尺寸、面板对齐、记录区域和按钮位置；`tests/frozen_ui_smoke.py` 打开实际 EXE 并正常关闭，全程不识别、不连接或控制硬件。程序图标在冻结自检中验证，包审计拒绝旧公司标志资源。

`tests/installer_ui_smoke.py` 仅预览安装目录页面，验证默认目录、自定义含空格及中文的路径输入、浏览文件夹打开及取消；不点击“安装”，不执行驱动或软件安装。
