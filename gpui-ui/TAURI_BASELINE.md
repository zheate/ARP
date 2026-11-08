# Power Test

Power Test 是面向 Windows 实验室和产线测试电脑的 Tauri 桌面应用，将电源控制、光功率与光谱采集、自动测试、PD 采集、Excel 记录和出货报告整合到一个操作界面中。

Tauri 是唯一的操作员界面。Python sidecar 使用 `tauri_bridge/headless_backend.py` 直接持有硬件和自动测试状态，不加载 Qt，也不存在旧窗口或可切换的兼容后端。

## 环境

- Node.js 20 或更新版本，前端包管理器固定为 npm；唯一锁文件是 `tauri-ui/package-lock.json`。
- Rust stable toolchain。
- Conda 环境 `sth_eb314`，依赖分别记录在 `requirements-runtime.txt`、`requirements-build.txt` 和 `requirements-dev.txt`。
- Windows 真机还需要 OceanDirect、CH341、NI-DAQmx、NI-VISA 及所用功率计的厂商驱动。

在已有 `sth_eb314` 环境中安装开发与构建依赖：

```sh
conda run -n sth_eb314 python -m pip install -r requirements-dev.txt
```

## 常用命令

先安装前端依赖：

```sh
npm --prefix tauri-ui ci
```

从仓库根目录启动桌面应用：

```sh
npm start
```

分层验证：

```sh
npm run test:python
npm run test:frontend
npm run test:tauri
npm run test:realtime
npm run build
```

Python 测试统一使用标准库 `unittest`，不需要额外测试框架。`npm test` 依次执行全部测试，`npm run check` 再追加前端生产构建。按变更影响运行相关本地检查；完整测试、打包和真实设备操作遵循 [AGENTS.md](AGENTS.md) 中的任务授权范围，已有授权无需重复确认。

## 软件授权

Power Test 使用绑定当前电脑的离线 Ed25519 许可证。Tauri/Rust 主进程负责读取设备 UUID、验证产品、机器、有效期和签名；许可证有效前不会启动 Python sidecar，也不会开放仪器命令。公钥随应用发布，私钥只保存在授权电脑的 `.license/arp-private-key.pem`，该目录已排除在 Git 之外，必须单独安全备份。

客户在激活页复制完整 `ARP-...` 机器码后，在授权电脑执行：

```sh
npm run license:generate -- \
  --machine 'ARP-完整机器码' \
  --customer '客户或公司名称' \
  --expires '2027-08-24' \
  --out 'tmp/customer-license.json'
```

将生成的 JSON 文件交给客户导入。许可证只接受产品 `arp-power-test`，不能与 PLM 或 LD Calculator 混用。运行中的许可证若失效，应用禁止开始新测试和修改硬件输出，但继续开放实时状态、急停、安全结束、停止采集以及当前记录重试，避免授权状态破坏安全下电和数据收尾。

当前开发电脑按其 ARP 机器哈希精确列入“本机免授权”名单，不需要导入许可证；该豁免不会扩展到其他电脑。

## Windows 打包

发布版本唯一取自 `tauri-ui/package.json`，Tauri 配置通过 `../package.json` 读取同一版本。

在 Windows 构建机上先运行严格预检：

```powershell
npm run check:windows
```

预检会确认构建命令、`sth_eb314` 依赖、Rust manifests、OceanDirect DLL 和 CH341 DLL 均可用。预检通过后可构建应用包或完整离线安装包。对客户交付使用 `build:windows:full`：

```powershell
npm run build:windows
npm run build:windows:full
```

缺少必要硬件 DLL 或构建依赖时会直接失败，不生成能力不完整的发布包。

完整安装包输出为 `release/full/Power Test_<版本号>_x64-all-drivers-setup.exe`。客户双击后允许管理员权限，按默认选项即可安装 Power Test、WebView2 和全部设备支持；也可取消不使用的组件。LaserPoint/FTDI、CH341/CH343 串口和 Coherent USB 组件会自动保留所需的 NI-VISA。安装使用离线文件，不要求客户安装 Python、Node.js 或 Rust。激活仍使用独立的本机许可证。

安装流程位于 `packaging/install_full_bundle.ps1`，组件与设备安装逻辑位于 `packaging/full_bundle_helpers.ps1`，统一入口为 `packaging/power_test_full_installer.nsi`：

- 先核验完整驱动文件清单及 SHA-256，再按组件安装 WebView2、INF 驱动、Ophir/Coherent 厂商组件和 NI 运行环境，最后安装应用 MSI。WebView2 检测机器级注册信息，已有环境会跳过，安装后再次确认；Ophir 检查 64 位 COM 注册。
- INF 使用 Windows `pnputil /add-driver /install`，保留驱动签名检查。只在 INF 安装中接受 `259`（无匹配设备或已有更高版本驱动），允许未连接仪器时预装；其他安装器仍严格检查自身返回码。厂商或 Windows 安全确认可能仍需用户操作。厂商静默参数必须在所固定的安装文件版本上完成 Windows 验收，不能仅凭进程退出码证明所有硬件可用。
- 本流程使用 NI Package Manager 24.0 或更新版本；已有旧版本时先调用随包 24.0 安装程序升级，升级要求重启时先续装，不执行旧版不支持的离线配置命令。NI 使用独立配置和本地源，关闭自动连接 ni.com 获取缺失依赖，不更改客户现有 NI 源配置。构建阶段检查离线源索引引用的文件；最终依赖解析仍须在干净 Windows 环境验证。
- 普通重启要求返回 `3010`；需要先重启才能继续时返回 `20`，明确显示“安装尚未完成”。安装缓存保存在管理员可写的 `%CommonProgramFiles%\ARP\Setup\<版本号>\<本次安装>`，一次性计划任务在同一管理员账户下次登录时继续安装。已完成组件原子写入检查点，续装会跳过它们，避免重复执行厂商安装程序。续装读取状态文件前即删除任务，状态损坏也不会在以后每次登录时重复执行。最多允许两次续装；失败后由用户查看日志并重新运行完整安装包。
- 缓存按每次安装隔离，避免旧文件混入新包。缓存会占用额外磁盘空间，不自动删除共享驱动或厂商软件。日志位于 `%ProgramData%\Power Test\InstallerLogs`。原生进程退出码不可用时停止，不能当作成功。超时不会强制终止 Windows Installer；记录进程 ID 和启动时间，在该进程仍运行时阻止再次安装，防止超时后重复启动厂商安装器。

安装前检测本产品主程序与独立后端进程；仍在运行时要求从软件正常结束测试并退出，不强制关闭进程。初次安装与重启续装成功后均保存安装目录，供下次安装恢复。

构建机还需原来的 `local_drivers/current_project` 离线驱动集合、`local_drivers/coherent` 压缩包、固定 SHA-256 的 StarLab/NI/WebView2 安装程序和 NSIS。构建脚本会在构建期解压 FTDI 和 Coherent，验证厂商安装程序签名，并为最终驱动文件生成完整性清单。`build:windows` 的独立应用包不含上述统一驱动安装流程。

仅检查安装流程脚本（不会安装驱动、连接设备或打包）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\test_full_bundle.ps1
```

发布前需要在 Windows 10/11 x64 验证：干净离线系统完整安装、已有 WebView2/NI 的重复安装、每类组件自定义选择、中文/带空格安装路径、驱动失败、NI 中途重启续装、开始菜单启动及卸载。再独立完成真实设备识别、测量、Excel 导出和安全下电。macOS 的 PowerShell 测试、NSIS 编译或 dry-run 都不等于这些验收通过。

部署参数依据：[Microsoft WebView2 离线部署](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)、[PnPUtil 返回码](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/pnputil-return-values)、[NI Package Manager CLI](https://www.ni.com/docs/en-US/bundle/package-manager/page/cli-package-manager.html)、[InstallShield 参数](https://docs.revenera.com/installshield/helplibrary/IHelpSetup_EXECmdLine.htm)和 [Inno Setup 参数](https://jrsoftware.org/ishelp/topic_setupcmdline.htm)。

## 目录

- `tauri-ui/`：React 界面和 Tauri Rust 主进程。
- `tauri_bridge/`：JSON-lines 协议、无 Qt Python 后端和持久化设置。
- `combined_test/`：设备、自动测试领域逻辑、光谱计算、存储和报告。
- `rust-realtime-core/`：逐帧光谱和 PD 计算加速库。
- `packaging/`：Windows sidecar、应用安装包和完整驱动安装包脚本。
- `tests/`：无界面后端和共享领域逻辑测试。

桥接协议及安全不变量见 `tauri-ui/BRIDGE_CONTRACT.md`。Windows/WebView2、CH341 设备重连、真实仪器和完整离线安装仍必须在目标机器上验收，macOS 构建或模拟测试不能替代这些结果。

自动测试点先写入本机应用数据目录下长期保留的逐会话 JSONL，再继续下一个电流点；Excel 是后台生成的交付文件，不再是自动状态机的同步边界。测试结束且确认安全下电后，可直接清空当前 SN 和会话结果进入下一台，设备连接与测试配置保持不变。

应用采用单工位运行：自动测试、手动控制和 PD 采集共用一份设备配置。旧版本首次启动时迁移原工位 1 的配置；测试站别字段、历史 Excel 和记录文件继续保留。
