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

Python 测试统一使用标准库 `unittest`，不需要额外测试框架。`npm test` 依次执行全部测试，`npm run check` 再追加前端生产构建。运行完整测试、打包或长时间验证前，遵循 `agent.md` 的授权要求。

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

预检会确认构建命令、`sth_eb314` 依赖、Rust manifests、OceanDirect DLL 和 CH341 DLL 均可用。预检通过后构建应用安装包或完整驱动安装包：

```powershell
npm run build:windows
npm run build:windows:full
```

缺少必要硬件 DLL 或构建依赖时会直接失败，不生成能力不完整的发布包。

## 目录

- `tauri-ui/`：React 界面和 Tauri Rust 主进程。
- `tauri_bridge/`：JSON-lines 协议、无 Qt Python 后端和持久化设置。
- `combined_test/`：设备、自动测试领域逻辑、光谱计算、存储和报告。
- `rust-realtime-core/`：逐帧光谱和 PD 计算加速库。
- `packaging/`：Windows sidecar、应用安装包和完整驱动安装包脚本。
- `tests/`：无界面后端和共享领域逻辑测试。

桥接协议及安全不变量见 `tauri-ui/BRIDGE_CONTRACT.md`。Windows/WebView2、双 CH341、真实仪器和完整离线安装仍必须在目标机器上验收，macOS 构建或模拟测试不能替代这些结果。

自动测试点先写入本机应用数据目录下长期保留的逐会话 JSONL，再继续下一个电流点；Excel 是后台生成的交付文件，不再是自动状态机的同步边界。测试结束且确认安全下电后，可直接清空当前 SN 和会话结果进入下一台，设备连接与测试配置保持不变。
