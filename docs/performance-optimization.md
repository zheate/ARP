# 技术栈整理与性能基线

## 2026-10-13：CH341 等待加电期间的实时刷新

问题来自自动测试点之间的 CH341 命令间隔等待。上一点读取电压后，下一点设置电流需要满足 1.1 秒最小命令间隔；原先 `set_current()` 在持有运行状态锁时等待，功率采样回调和界面快照也需要该锁，导致读数和曲线更新一起暂停。无硬件模拟中，即使测量期间不写文件，也能复现这个阻塞。

修复限定在 `tauri_bridge/station_runtime.py`：计算剩余间隔后释放状态锁与 I/O 锁再等待，醒来后按原有“状态锁 → I/O 锁”顺序重新检查停止代次、连接状态和命令间隔；仅在时隙可用时预留并执行写入。多个等待者不能共用同一时隙，急停会唤醒并取消待执行命令，断开后即使更换控制器也不会发送旧命令。手动操作仍对过快命令立即报错。设备 I/O 本身的锁范围不在本轮调整范围内。

以下对照在同一 Python 3.11.15 环境使用模拟控制器，调用实际运行时、功率采样回调和快照构建方法；基线为提交 `87ffd988` 中的旧方法，未连接真实设备。

| 指标 | 修复前 | 修复后 |
| --- | ---: | ---: |
| 等待期间首次接收功率读数并生成快照 | 1105.36 ms | 0.13 ms |
| 等待期间连续 5 次新读数能否在加电前返回 | 否 | 是，单次 0.13–0.58 ms |
| 电流设置命令总耗时（含间隔等待） | 1105.08 ms | 1104.74 ms |

加电仍按原来的安全节奏执行，优化的是等待期间采样和显示的阻塞。逐点 JSONL 保存、`fsync`、保存失败暂停、自动测试流程及 1.1 秒常量均未修改。新增的完整自动流程测试刻意阻塞真实日志 `fsync`：完成同步前不接受该点、不设置下一点电流；放行后才继续。

验证通过：6 项新增回归及相关安全、运行时、实时电流、日志、设备接口和通信测试，共 178 项。新增的实时刷新测试在旧方法上因 300 ms 内拿不到快照而失败，修复后通过。没有执行全套测试、打包或真实 CH341 操作；上述毫秒值不代表 Windows 机器的磁盘或 USB 驱动延迟。

```sh
/Users/zh/miniconda3/envs/sth_eb314/bin/python -m unittest tests.test_ch341_command_wait tests.test_safety_regressions tests.test_station_runtime tests.test_live_current tests.test_session_journal tests.test_device_interfaces tests.test_tauri_bridge -v
```

证据：[前后计时](../output/performance-20261013/ch341-wait/timing.json)、[178 项测试日志](../output/performance-20261013/ch341-wait/tests.txt)。

## 2026-10-13：第二轮，实时曲线与页面切换

产品改动仅在 `canvas-line-chart.tsx`。未改变样式、动画参数、刷新频率、插值方法、采样点数量或页面状态生命周期。

- 固定可选标注数组的默认引用。原先每次悬停状态改变都会创建一个新的空数组，使底图绘制 effect 重新执行；现在提示框和十字线更新时只重画覆盖层。
- 图表初始尺寸和后续尺寸直接使用 `ResizeObserverEntry.contentRect`。移除图表挂载时及观察器回调中的同步 `getBoundingClientRect()`，避免切页时逐图强制读取布局。观察器、绘制帧的卸载清理及后台窗口同步绘制路径保留。

浏览器使用项目 Vite 服务的生产 React 运行时。固定合成输入为 601 个功率点和 8192 个完整光谱点；窗口 1440×960，Chromium 施加 4 倍 CPU 降速。切页测量为“手动配置 → PD 采集 → 系统设置 → 自动测试”两轮，保留原有入场动画。

| 测量项 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 鼠标移动 60 次并移出：底图重画次数 | 61 | 0 |
| 同一悬停过程脚本耗时 | 105.24 ms | 79.70 ms |
| 8 次切页中的页面尺寸读取次数 | 42 | 2 |
| 切页点击至第二个动画帧的中位耗时 | 36.30 ms | 30.30 ms |
| 8 次切页的浏览器主线程任务累计耗时 | 779.78 ms | 705.27 ms |

时间值为本机单轮对照，包含测试驱动成本并受浏览器调度影响；第二个动画帧不表示入场动画已结束。确定性收益是去除了 61 次多余底图绘制和 40 次尺寸读取，不据此宣称所有页面或目标 Windows 设备已无卡顿。

连续更新另用真实图表组件、固定 5 Hz、30 帧测试：前后均完成两张实时图各 30 次绘制，帧间隔 P95 约 16.8 ms，未测到该负载下的明显掉帧或持续更新提速。最终完整光谱 Canvas 图像完全一致。

验证：

- 深色/浅色 × 1440×960/1180×720：4 张整页截图逐像素一致，12 张 Canvas 图像完全一致，无横向溢出。
- 独立浏览器回归通过：悬停不重画底图、提示框与十字线保留、移出清除覆盖层、单帧两张实时图各重画一次、模拟隐藏窗口时仍同步绘制、主题改变重画、DPR 1/1.5/2 尺寸正确。
- 六个页面各切换两轮，共 12 次切换，通过；返回自动测试页后 3 张图均正常显示，无脚本异常或横向溢出。
- 单调插值、完整光谱、峰值标注、增量合并、外观配置共 20 项聚焦测试通过；TypeScript 检查和前端生产构建通过。构建仍提示主 JS 包大于 500 kB，本轮没有改动加载结构。
- 未执行全套测试、打包或真实设备操作；未验证 Windows WebView2/macOS 原生 Tauri 窗口。工作区同时出现的设备设置与样式修改不属于本轮改动。

独立回归的复现方式（先在 `tauri-ui` 运行 `npm run dev`）：

```sh
playwright-cli --session arp-chart-check open 'http://localhost:1420/scripts/fixtures/chart-performance.html'
playwright-cli --session arp-chart-check run-code --filename tauri-ui/scripts/verify-chart-performance.cjs
```

完整应用测试夹具为 `tauri-ui/scripts/fixtures/app-performance.html?demo=1`，只使用模拟数据。测试夹具不进入产品入口。原始指标和前后对照见 [汇总 JSON](../output/playwright/perf-round2/summary.json)、[切页检查](../output/playwright/perf-round2/navigation.txt)、[浏览器回归](../output/playwright/perf-round2/regression.txt)；测量驱动保存在同目录的 `profile.cjs` 和 `live.cjs`。

## 2026-10-13：数据浏览读取与归档刷新优化

本轮优化 Python 数据浏览后端的两处重复工作。普通详情原先完整读取并解析光谱和原始测量尝试，再删除这些字段；现在由 SQLite 在返回结果前排除这些字段，减少 Python 解码和对象分配。请求完整光谱时仍使用原读取路径。SQLite 缺少 JSON 函数时回退到旧路径，其他数据库错误正常上报。数据库结构和已存原文均不修改。

刷新本地数据库列表时，原先每个会话文件都建立一次 SQLite 连接并检查版本。现在每次扫描先读取一次版本索引，逐文件核对修改时间和大小，只对新增或变化的文件执行原有归档流程。索引只在本次调用内使用；文件变化后的稳定读取检查和失败重试仍然保留。

同一台 Apple Silicon Mac、Python 3.11.15、SQLite 3.53.3，固定合成数据，预热后重复 9 次取中位数。每次公开接口调用都重新建立连接，文件系统缓存已预热。所有数据库、会话文件均由工具在临时目录生成，不读取用户记录，不接触仪器。

| 后端操作 | 改动前 | 改动后 | 耗时降低 |
| --- | ---: | ---: | ---: |
| 普通详情：20 个电流点 × 3648 个光谱点，约 2 MB JSON | 15.17 ms | 2.80 ms | 81.5% |
| 普通详情：100 个电流点 × 8192 个光谱点，约 22.5 MB JSON | 167.14 ms | 27.63 ms | 83.5% |
| 大记录完整光谱读取 | 167.30 ms | 164.31 ms | 基本持平 |
| 扫描 200 个未变化会话文件 | 62.94 ms | 3.94 ms | 93.7% |

大记录普通详情读取的 `tracemalloc` 峰值由 93.27 MiB 降到约 0.03 MiB。该值仅反映 Python 分配，不包括 SQLite 原生内存，不能解释为整个应用内存减少同样数值。SQLite 仍需读取并解析完整 JSON；本轮未优化磁盘冷读或完整光谱页面。

第二次执行得到大记录普通详情 27.15 ms、未变化会话扫描 4.07 ms。4 组新旧返回对象等值对比全部通过，覆盖两个数据规模和普通/完整光谱两种请求。37 项聚焦测试通过，覆盖数据浏览、数据归档、会话日志和无界面后端，包括原文保留、变化会话重新导入、坏文件重试、可选字段、SQLite 兼容回退与错误传播。两个新增回归检查在修改前失败，修改后通过：不需要的数组不再进入 Python 解码器；多个未变化日志共用一次索引读取。

这些数字表示后端操作耗时，不是界面点击到绘制完成的耗时。本轮未修改界面、实时刷新频率、光谱点数或计算公式；未运行全套测试、打包、原生 Tauri 窗口或真实设备验收。

复现：

```sh
/Users/zh/miniconda3/envs/sth_eb314/bin/python -m tools.benchmark_data_library --output output/performance-20261013/repeat.json
/Users/zh/miniconda3/envs/sth_eb314/bin/python -m unittest tests.test_data_library tests.test_data_browser tests.test_session_journal tests.test_headless_backend -v
```

原始记录：[改动前](../output/performance-20261013/before.json)、[改动后](../output/performance-20261013/after.json)、[复测](../output/performance-20261013/repeat.json)、[测试日志](../output/performance-20261013/tests.txt)。

## 2026-09-18：技术栈整理

日期：2026-09-18。提交分支：`audit/arp-ui-final-20260914`。

本轮保留 Tauri、React、TypeScript 和 Python，整理前端职责、强化前后端通信约束，并建立 Python/Rust 计算对照。没有迁移 Vue，也没有更改 Windows 安装包形式。

## 已完成的改动

- `App.tsx` 从 5318 行缩减至约 770 行，保留应用入口、导航、授权、全局关闭与操作协调。自动测试、手动配置、设备设置、图表、报告、PD 采集和系统设置移入 `src/features/`。
- 配置草稿由 `use-configuration-editor.ts` 和 `configuration-draft.ts` 管理。后台刷新不覆盖未保存修改，较早发出的保存响应不能清除较新的修改，后端拥有的功率计系数字段通过专门路径合并。
- `command-contract.ts` 统一命令名、参数和结果类型。编译检查覆盖错误命令名、必填参数缺失、参数类型错误和结果类型错误。界面入口使用有类型的命令参数组合。
- Python 的明确业务拒绝保留错误码，Rust 将错误分类、请求标识、后端会话标识传递给前端。超时和通信中断保留“执行结果未知”，电源设置失败提示先读取状态或查看实体面板。普通操作不会因这些错误自动重放；急停保留独立通道。
- 握手检查协议版本，核心响应检查必要字段，跨后端会话的旧命令响应不会覆盖新会话状态。三种语言共享 `tests/fixtures/bridge-contract.json`。
- 增加 Python 导入、初始化和就绪耗时，以及 Rust 握手、命令等待耗时日志。这些日志为后续定位 Windows 启动等待提供依据，本轮没有测得 Windows 启动耗时。
- 原生计算库增加加载状态与按失败类别去重的回退日志。修正 macOS 动态库扩展名，增加非有限数输入检查。真实加载原生库后，修复等高峰选点、稀疏积分区域和非正积分处理与 Python 不一致的问题。

Python 继续拥有设备操作、自动测试状态与数据保存；Rust 继续承担桌面进程通信和已有数值计算。本轮未增加新的 Rust 算法迁移。

## 实测结果

### 界面

在同一台机器、同一浏览器窗口大小、相同演示数据下，观察开始测试后 6 秒的 React 根节点提交次数：

| 指标 | 改动前 | 改动后 |
| --- | ---: | ---: |
| 根节点提交次数 | 70 | 70 |
| 横向溢出 | 无 | 无 |

这是开发模式的演示负载，仅用于检查重构是否额外触发刷新，不能推断真实仪器采集的帧率或 Windows 启动时间。本轮没有证据证明前端速度提升。生产构建仍存在主 JavaScript 文件约 830 KB 的体积提示，未将文件拆分误报为按需加载优化。

### Python/Rust 计算

运行环境：Apple Silicon、Python 3.11.15、NumPy 2.4.6、SciPy 1.17.1，Rust 原生库使用 release 构建。12 组固定种子合成光谱覆盖 1024、3648、8192 个点以及单峰、多峰、带背景噪声、平坦信号。每项预热 10 次，采集 15 批，每批执行 10 次。记录批次平均耗时的中位数和第 95 百分位数，不代表单次调用尾延迟。

代表性结果为 3648 点单峰光谱，中位数单位为微秒，包含公开 Python 接口中的数据转换与校验：

| 路径 | 改动前 | 改动后 |
| --- | ---: | ---: |
| 光谱统计，Python 参考实现 | 641.4 | 629.1 |
| 光谱统计，原生计算路径 | 464.1 | 470.3 |
| 指定波段功率占比，Python 参考实现 | 334.8 | 331.7 |
| 指定波段功率占比，原生计算路径 | 296.9 | 302.6 |
| 两个数组转换与校验 | 85.8 | 90.7 |

改动前为比较原生路径，显式指定了动态库位置；改动后已能通过默认路径找到 macOS 动态库。两次运行均真实加载原生库。

改动后的光谱统计原生路径相对 Python 参考实现约快 1.34 倍；这是已有原生算法的对照，不是本轮相对旧版本的提速。本轮增加输入检查后，代表性原生耗时略升。3648 点带背景噪声的功率占比计算，原生路径为 354.1 微秒，Python 为 338.7 微秒，原生路径反而略慢。因此暂不扩大算法迁移。

12 组对照均满足 `rtol=1e-12, atol=1e-9`。额外测试覆盖等高峰、零信号、动态中心波长、稀疏积分区域、非法输入和加载失败。这里的正确性只覆盖已列用例，不等同于全部光谱或真实设备验收。

原始记录：

- [改动前计算数据](../output/performance-optimization/realtime-before.json)
- [改动后计算数据](../output/performance-optimization/realtime-after.json)
- [改动前界面记录](../output/performance-optimization/ui-baseline.txt)
- [改动后界面记录](../output/performance-optimization/ui-current.txt)
- [设备弹窗检查](../output/performance-optimization/dialog-check.txt)

复现计算对照：

```sh
cargo build --manifest-path rust-realtime-core/Cargo.toml --release --locked
/Users/zh/miniconda3/envs/sth_eb314/bin/python -m tools.benchmark_realtime --output output/performance-optimization/realtime-repeat.json
```

可设置 `ARP_DISABLE_NATIVE_REALTIME=1` 禁用原生路径；此时性能对照工具会明确失败，不会以跳过原生执行代替验证。

## 验证范围

- 前端生产构建通过，包含命令类型的编译期反例检查。
- 前端聚焦测试 27 项通过，覆盖配置草稿、协议、历史增量合并、光谱数据、暂停动作和安全显示。
- Python 聚焦测试 93 项通过，没有跳过原生库用例；覆盖通信、无界面后端、光谱和安全回归。
- Rust 通信聚焦测试 7 项通过：错误不触发重启、共享错误契约、状态流 5 项；Rust 数值库测试 4 项通过。
- 浏览器演示中六个页面均可切换，未出现脚本异常或横向溢出；手动页读取电压操作正常。
- 1440×960 和 1180×720 两种窗口下，三个设备弹窗均检查资源选择、识别、保存、重新打开、Escape 和布局稳定性。测试中的电源未连接状态通过浏览器测试夹具提供，未操作硬件。

上述实现与验证阶段没有运行全套测试、Windows 打包、真实 Tauri 原生窗口或真实仪器测试，也没有更改安装包。GitHub 提交不代表完成这些验收。

## 后续 Windows 验收

在同一台 Windows 测试机记录首次启动和再次启动，区分进程创建、打包程序启动、Python 导入、后端初始化、协议就绪、首帧可操作六个时间点。安装版进程启动前的解包或系统扫描等待，不在 Python 日志覆盖范围，需要结合外层时间戳判断。

先取得启动基线，再决定是否调整打包或延迟加载。现有“等待后端”体验不能仅凭 React 文件拆分或 macOS 数值测试判定解决。Windows 上还应复验三个设置弹窗、关闭与未确认下电提示、断线恢复、真实光谱和原生库加载；这些是后续独立验收项。


## 2026-10-13：共用表单、页面按需加载与实时更新范围

按“共用组件 → 按需加载 → 实时更新隔离”的顺序实施。保留开始本轮时已有的设备设置、图表及后端改动。本轮没有修改采集命令、控制权限、数值算法、光谱抽样策略或原生刷新间隔。当前源码自动/手动为 100 ms，PD/数据浏览/报告为 500 ms；旧记录中的 200 ms 不代表当前运行配置。

### 实现

- 新增 `ControlRow`、`ControlGrid`、`ParameterControl`，将设备设置、PD 采集、出货报告的资源行、输入/操作行及参数网格收敛到共用规则。字段标签及本地资源下拉层保留，按钮不采用位移反馈。相关按钮的 `transition-all` 改为颜色过渡。
- 数据浏览、出货报告及其样式按需加载，在导航悬停/聚焦时预加载。数据浏览首次打开后保留实例，切换页面不重置筛选状态。等待和异常仅占当前内容区。
- 生产构建故障注入发现普通 dynamic import 重试仍命中失败结果，因此增加资源清单和独立页面重试。重试重新加载同一构建资源及所需样式，不重载应用，不影响全局急停入口。
- 自动测试顶部配置表单、设备信息卡、标题栏操作区、侧栏分别建立更新边界。设备卡比较全部可见字段及回调；设备状态、错误、输出和采集状态变化仍更新。快照合并只复用内容相同的配置/脚本对象，测量数组、安全状态和设备状态继续处理每帧。

### 性能对照

Apple Silicon Mac、Chromium、生产 React 运行时、1440×900、4 倍 CPU 降速。实际应用注入模拟快照：每 100 ms 一次、601 个功率点、8192 个完整光谱点。前后各执行 3 组、每组 30 帧；下表为每组结果的中位数。基线由本轮开始时的 Git 状态加当时未提交的 UI 修改重建，使用独立 Vite 缓存，原工作区没有重置。

| 指标 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 主入口 JavaScript，未压缩 | 850,223 B | 732,756 B |
| 每 30 帧脚本累计耗时 | 799.72 ms | 622.52 ms |
| 每 30 帧主线程任务累计耗时 | 1644.38 ms | 1520.40 ms |
| 顶部 4 个输入框累计更新次数 | 120 | 0 |
| 三张设备信息卡累计更新次数 | 90 | 0 |
| 显示菜单累计更新次数 | 30 | 0 |
| 每组功率/光谱底图绘制次数 | 各 30 | 各 30 |
| 帧间隔 P95 的组间中位数 | 17.6 ms | 17.6 ms |

脚本耗时中位数降低约 22.2%，主入口减小约 13.8%。帧间隔未测到明确改善；收益是减少重复工作，不是提高采集频率。计时包含浏览器驱动与模拟数据生成成本，不能外推 Windows 真机启动或所有负载。最终光谱 Canvas 图像逐字节一致。生产构建确认首页未请求数据浏览/报告模块；主入口仍超过 500 kB，构建保留体积提示。

### 验证

- 59 项聚焦测试通过：静态配置引用复用、配置编辑/删除/后端重启、连续 30 帧全量光谱、快照增量合并、草稿保存、安全状态、真实电流展示、稳定状态、系数配置和峰值标注。修正了旧测试将当前界面的 `5.0 秒` 误写为 `5 秒` 的断言，产品计时文案未改变。
- 明暗主题 × 1440×900 / 1180×720 / 760×700，六个主页面与三个设备弹窗共 54 组截图和布局检查通过。检查卡片同排等高、表单/按钮对齐、横向溢出及弹窗边界。整页包含动态绘图/提示状态，不宣称整页逐像素一致。
- TDK、CH341、功率计和光谱仪资源下拉框均可选择；Escape 先关闭下拉框，再关闭弹窗。4 次模拟识别期间底部按钮文字、颜色、尺寸与位置保持不变。
- 生产资源加载失败后可局部重试；急停入口仍可用。数据筛选、未保存配置跨页面保留；添加/编辑脚本入口及直接进入数据页通过。
- TypeScript、生产构建、`git diff --check` 通过。未执行完整测试套件、打包、原生 Tauri WebView、Windows 系统缩放或真实仪器验收。

原始结果及驱动：[汇总](../output/playwright/ui-stack-optimization/summary.json)、[实时对照前](../output/playwright/ui-stack-optimization/profile-before.json)、[实时对照后](../output/playwright/ui-stack-optimization/profile-after.json)、[布局](../output/playwright/ui-stack-optimization/layout.json)、[交互](../output/playwright/ui-stack-optimization/interactions.json)、[页面状态与故障恢复](../output/playwright/ui-stack-optimization/page-regression.json)。同目录保存修改前后截图、构建输出及可由 Playwright CLI `run-code` 执行的驱动。

## 2026-10-15：四个维度的保守优化

本轮约束：功能、界面布局、操作流程、测量精度、完整光谱、刷新间隔、设备控制时序和可靠落盘规则保持不变。没有修改 Python/Rust 后端、设备命令、自动测试状态机、计算指标、导出格式或 CSS。优化范围限于前端的重复工作和资源加载。

### 诊断与实现

| 维度 | 发现 | 处理 | 成本与边界 |
| --- | --- | --- | --- |
| 渲染重绘 | 设置页跟随测量快照更新，四张静态外观预览反复绘制 | `SystemSettingsPage` 使用 React 默认 `memo` 比较全部属性 | 一个组件边界；外观上下文、本地状态、许可证和回调变化仍正常更新，不手写可能遗漏字段的比较器 |
| 内存管理 | 每次单调曲线插值创建宽度、斜率、切线三个数组，并为每段建立闭包 | 根据相邻两段流式计算切线，保留原运算顺序和全部绘图指令 | 插值辅助存储由 O(N) 降为 O(1)；输入点、Canvas 和图表其余存储仍存在，不代表整个绘图过程只需常量内存 |
| 计算复杂度 | `comparisonRows` / `metricComparisonRows` 对同一电流分组反复展开复制已有数组；大量重复电流时最坏 O(N²) | 只向函数内部新建的分组数组追加，不修改传入数组或测量点 | 分组部分最坏 O(N)；最终电流排序仍是 O(U log U)，U 为不同电流数；重复点的歧义标记和缺测规则不变 |
| 资源体积 | 正常设备入口静态导入演示快照与回放时间轴 | 只有 `demo=1` 分支动态加载这两个模块；正常入口的演示引用保持为空 | 没有给正常页面增加延迟加载界面；演示模式仍加载原模块，安装包总量不会随首屏资源等比例下降 |

缓存与生命周期的只读检查：数据浏览详情缓存已有 120 份普通详情、10 份完整光谱上限；图表的 ResizeObserver、MutationObserver 和动画帧已有清理。没有用删除历史数据、降低刷新频率或缩短可靠保存流程换取性能。本轮未建立长时间 Windows 运行的内存泄漏结论。

### 对照方法

- 插值：固定的 1024 / 3648 / 8192 点光谱。预热后测量 15 批、每批 100 次，报告每批平均耗时的中位数。路径接收器只记录调用次数，不包含真实 Canvas 栅格化。
- 分组：每组 601 / 4000 个点，分别测试唯一电流和重复电流；每轮一起执行功率对比与指标对比。重复电流是压力场景，不能当作普通测试记录的普遍提速。
- 内存：7 次独立 GC 后单次调用的 `heapUsed` 增量中位数。这是临时分配探针，不是进程常驻内存、峰值内存或内存泄漏证据。
- 浏览器：实际应用组件、生产 React 运行时、Chromium、1440×900、4 倍 CPU 降速。固定 601 个功率点、8192 个完整光谱点，每 100 ms 一帧，前后各 3 组、每组 30 帧。旧代码放在临时目录，使用独立 Vite 缓存；没有重置工作区。
- 等值检查：三种光谱长度的全部绘图指令哈希、四种分组输入的完整结果哈希；另外用固定种子 128 组不规则网格/断点测试锁定旧绘图指令。

可复现计算基准：

```sh
node --expose-gc --experimental-strip-types tauri-ui/scripts/benchmark-render-data.mjs
```

可选第二参数是包含旧 `monotone-curve.ts` 和 `data-browser.ts` 的目录。必须串行比较，避免与构建或浏览器性能测试争用 CPU。`p95BatchMs` 表示 15 批中最高的批均值，不能解读为单次调用的 P95 尾延迟。

### 实测结果与验证

旧版本基线提交为 `0c152d67`。同机最终串行对照结果如下；计时取前述批均值的中位数。

| 项目 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 设置页每 30 帧的重复渲染，三组中位数 | 30 次 | 0 次 |
| 设置页外观示例底图重绘，三组中位数 | 120 次 | 0 次 |
| 设置页每组脚本累计耗时，三组中位数 | 76.48 ms | 13.35 ms |
| 8192 点曲线插值计算 | 0.164 ms | 0.113 ms |
| 8192 点插值单次调用堆增量探针 | 1,371,152 B | 648,096 B |
| 4000 个重复电流点，两类对比一起执行 | 26.42 ms | 0.83 ms |
| 601 个唯一电流点，两类对比一起执行 | 0.568 ms | 0.546 ms |
| 主入口 JavaScript，压缩后、未 gzip | 692,798 B | 682,153 B |
| 全部 JavaScript，压缩后、未 gzip | 819,792 B | 820,180 B |

插值计算约减少 31%，堆增量探针约减少 53%。重复电流压力场景约快 32 倍；唯一电流场景多次运行有波动，不作明确提速结论。主入口减少约 1.5%；全部 JavaScript 增加 388 B，是分块的少量开销，不能宣称安装体积减少。生产构建仍提示主入口超过 500 kB。

实时页面每 30 帧的脚本累计耗时中位数从 202.37 ms 变为 191.19 ms，主线程任务累计耗时从 397.55 ms 变为 393.94 ms；两者帧间隔 P95 都约 16.8 ms。因此只确认计算和重复工作的减少，不宣称实时帧率或硬件采集速度提高。功率/光谱每组各绘制 30 次，8192 个光谱点完整保留，最终光谱 Canvas 完全相同。

验证结果：

- 65 项聚焦测试通过，覆盖原曲线约束、128 组旧指令快照、4000 个重复点与冻结输入、数据分析、演示回放、外观、数值显示、历史合并、完整光谱、配置草稿和安全状态。三种光谱长度与四种对比数据的结果哈希全部一致。
- 自动、手动、设置、数据列表四个页面，在明暗主题 × 1440×900 / 1180×720 下共 16 组前后截图和布局对照。40 张 Canvas 图像全部相同；有卡片页面的卡片位置和尺寸相同，所有页面无横向溢出。13 组整页截图完全相同，另 3 组分别有 49 / 5 / 40 个像素不同，不宣称全部整页截图逐像素一致。
- 生产资源请求确认：正常浏览器入口只请求主入口和 Tauri 共享模块，不请求演示模块；`demo=1` 才请求演示快照和回放模块。演示开始/停止、设置页主题、线型、画布上下文更新、恢复默认值和许可证导入错误状态更新通过，页面无未捕获异常。浏览器不支持原生许可证导入，该项仅验证错误显示及按钮恢复，不代表完成原生导入。
- TypeScript、生产构建和 `git diff --check` 通过。没有运行完整测试套件、Windows 打包、原生 Tauri WebView 或真实仪器。本轮也没有验证长时间运行的常驻内存趋势。

原始证据与驱动位于 `output/playwright/performance-20261015/`：[汇总](../output/playwright/performance-20261015/summary.json)、[计算前](../output/playwright/performance-20261015/compute-before.json)、[计算后](../output/playwright/performance-20261015/compute-after.json)、[设置页前](../output/playwright/performance-20261015/settings-before.json)、[设置页后](../output/playwright/performance-20261015/settings-after.json)、[截图对照](../output/playwright/performance-20261015/visual-comparison.json)、[布局](../output/playwright/performance-20261015/layout-check.json)、[生产交互](../output/playwright/performance-20261015/production-check.json)。基准脚本为 `tauri-ui/scripts/benchmark-render-data.mjs`；浏览器驱动为同目录 `profile*.cjs`、`visual*.cjs`、`layout-check.cjs`、`production-check.cjs`，通过 Playwright CLI 的 `run-code --filename` 执行。

## 2026-10-15：大光谱文件连续浏览

本轮先建立固定文件基线，再只修改已确认的内存/返回开销和大光谱显示异常。没有修改设备控制、测量计算、采集频率、保存规则、数据库结构、Excel 文件内容、页面布局或交互流程。此前工作区的优化保留。

### 确认的瓶颈

1. Excel 完整详情缓存原先保留每个采样值对应的 Python 对象和列表指针；每份文件又重复保存同一波长网格。缓存虽然有 32 份上限，但大型记录的驻留成本仍然很高。缓存命中后，原实现还会逐项深拷贝全部光谱数组。
2. 多电流叠加的坐标轴通过 `Math.min(...values)` / `Math.max(...values)` 计算范围。24 条 × 8192 点产生 196,608 个数据行，真实页面夹具稳定复现 `RangeError: Maximum call stack size exceeded`，导致页面无法显示。这是确认的 P1 显示问题。
3. 浏览器侧在 12 条 × 8192 点、连续 24 次查看操作下，GC 后堆占用约 30 MiB，没有观察到该负载内持续失控的增长。本轮没有调整前端详情缓存、选择状态或操作流程。

### 改动及代价

- Python 缓存的光谱采样值使用标准库双精度数值缓冲区。同一工作簿内只共享逐位相同的波长网格，不做插值、降采样、归一化、舍入或有损压缩。
- 接口仍返回原来的普通 JSON 列表。每次请求、每条光谱都获得独立列表；元数据和标量点继续复制，缓存不会被调用方修改。文件版本检查、错误消息和按需读取语义保留。
- 缓存同时保留最多 32 份及 64 MiB 详情对象预算。该预算包括对象容器和数值缓冲区，按对象引用去重估算；它不是进程 RSS 上限，不包含当前响应、活动图表、解析器及分配器保留空间。超过预算时只淘汰可重读的缓存；单份超过预算的文件仍可完整读取，且不会挤掉全部近期缓存。
- 坐标轴改用逐项最小/最大值计算，保留原有边界、零点和留白规则，避免把全部采样点展开为函数参数。绘图仍保留每一个有效采样点。

代价是增加内部缓冲区打包和缓存容量统计。改动主要集中于 `combined_test/data_browser.py`，没有增加运行依赖或持久化格式。冷读解析成本没有解决；这轮收益集中在驻留内存和缓存命中返回。

### 方法与结果

环境为本机 Apple Silicon/macOS、Python 3.11、Chromium 生产 React。输入是生成的真实 `.xlsx` 文件，避免改动用户记录。36 个不同路径的工作簿，每份 24 条光谱，每条 8192 点，共 393,216 个波长/强度数值。前后分别在独立 Python 进程连续读取 36 份、重复查看末份 7 次、查看最近 5 份、重新打开最早 3 份；每次完整核对点数、数值及元数据摘要哈希。RSS 通过 `ps`/`resource` 采样，因此该 RSS 工具适用于本次 macOS/Unix 环境。

| 指标 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 连续浏览 Python 峰值 RSS | 572.1 MiB | 131.0 MiB |
| 最终缓存份数 | 32 | 32 |
| 后端缓存详情对象占用 | 未单独统计 | 50.8 MiB |
| 最近 5 份的实际 Excel 重读次数 | 0 | 0 |
| 独立进程测得的缓存命中详情中位耗时，未含 JSON 编码 | 95.21 ms | 4.48 ms |

峰值 RSS 降低约 77%，该工作负载下没有通过减少缓存份数换内存。缓存清空后分配器仍可能保留 RSS；这不是对长期内存泄漏的证明或否定。前后 36 份完整结果哈希全部相同。

两轮独立进程的冷读差异明显，但 XML 解析代码没有改动，因此不把这部分差异算作收益。另在同一进程按交错顺序比较两套实现，使用相同文件、3 次冷读和 9 次缓存命中。编码使用与桥接相同的 JSON 分隔符、Unicode 设置和换行；不包含管道 I/O、Rust 或 WebView 渲染。

| 同进程交错对照，中位数 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 缓存命中，详情构建 | 44.73 ms | 2.42 ms |
| 缓存命中，详情构建 + JSON 编码 | 123.08 ms | 73.69 ms |
| 冷读，详情构建 + JSON 编码 | 734.43 ms | 750.30 ms |

包含 JSON 编码的缓存命中返回约减少 40%。冷读仍约 0.7–0.8 秒，三次小样本中没有测得可归因的冷读提速。所有响应的 UTF-8 JSON 字节完全相同，不能把独立进程冷读的变化外推成原生端到端加速。

浏览器对照使用实际 `DataBrowserPage`，数据传输由固定合成夹具替代。16 条记录，每条 12 条 × 8192 点光谱，共 24 次“查看记录 → 选择多电流光谱”操作，包含当前产品原有的切换后回到指标曲线规则。GC 放在每次计时之后。单组切换中位耗时为 194 → 144 ms，GC 后堆峰值为 29.72 → 29.68 MiB；这项只有一组前后对照且不含 Python，作为界面局部样本，不作原生总体性能结论。前后最终 Canvas、选中电流和完整光谱请求序列相同。

### 回归验证与复现

- 28 项 Python 聚焦测试通过。新增内存回归先在旧实现失败，随后通过；另覆盖预算淘汰、近期命中、超大项可读且不清空其他缓存、不规则/重复波长、负强度、负零、文件版本失效、空光谱、返回列表隔离和清空容量统计。
- 41 项前端聚焦测试通过。实际浏览器大光谱回归现可显示 24 条完整光谱，保留全部 196,608 个输入行；记录到 196,599 次光谱 Canvas `lineTo` 调用（包含坐标轴），无页面异常。没有用抽稀绕开参数栈限制。
- 现有图表回归通过：悬停不重绘底图、提示/十字线、移出清空、数据刷新、隐藏窗口同步更新、DPR 1 / 1.5 / 2、主题重绘。
- 仓库内 3 个实际工作簿分别比较普通详情和完整光谱，共 6 组新旧 JSON 完全相同，源文件 SHA-256 不变。实际样本为每条 3648 点，不能替代 8192 点压力用例。
- TypeScript、生产构建、`git diff --check` 通过。未运行全套测试、安装包、原生 Tauri WebView、真实仪器或长时间 Windows RSS 趋势验收。

复现连续浏览：

```sh
/Users/zh/miniconda3/envs/sth_eb314/bin/python -m tools.benchmark_spectrum_browser \
  --fixtures output/playwright/spectrum-browse-20261015/fixtures \
  --output output/playwright/spectrum-browse-20261015/python-repeat.json
```

相同命令增加 `--protocol-baseline output/playwright/spectrum-browse-20261015/source-before/data_browser.py` 可运行同进程交错对照。持续内存与计时测试应串行执行，避免与构建、其他性能测试争用 CPU。浏览器回归启动 `npm --prefix tauri-ui run dev` 后，从项目根目录使用 Playwright CLI `run-code --filename tauri-ui/scripts/verify-large-spectrum.cjs`；该夹具使用合成数据，不启动产品后端或连接仪器。

原始证据：[汇总](../output/playwright/spectrum-browse-20261015/summary.json)、[Python 前](../output/playwright/spectrum-browse-20261015/python-before.json)、[Python 后](../output/playwright/spectrum-browse-20261015/python-after.json)、[交错接口对照](../output/playwright/spectrum-browse-20261015/protocol-paired.json)、[浏览器前](../output/playwright/spectrum-browse-20261015/browser-before.json)、[浏览器后](../output/playwright/spectrum-browse-20261015/browser-after.json)、[实际文件等值](../output/playwright/spectrum-browse-20261015/real-workbook-parity.json)、[大光谱回归](../output/playwright/spectrum-browse-20261015/maintained-large-regression.json)。

## 2026-10-15：原生实时快照的锁占用与通信处理

本机无硬件基准没有复现 P1 级实时阻塞。确认的局部热点是 Rust 对完整 JSON 数据的重复复制，尤其是 PD 首次加载或重新同步 13,000 个历史点时。本轮只消除这些复制，保留 Python 状态锁边界、完整数据、协议、刷新间隔和设备控制时序。

### 先测锁占用和编码

使用独立临时配置，构造与 `SpectrumAverager` 发布结果相同的 Python 浮点列表。覆盖 3,648 / 8,192 点光谱、1,200 个功率点和 13,000 个 PD 点，分别测全量、增量和未变化快照。Python 测量走实际 `BridgeService` / `SnapshotBuilder`，Rust release 测量走实际侧边进程任务队列、标准输入输出管道、响应读取线程、协议验证和 `run_snapshot_stream`。夹具只接受快照请求，禁止其他方法，并替换设备发现入口，不连接仪器。

8,192 点实时增量快照在两轮独立测量中，状态锁占用中位数为 0.23–0.50 ms，JSON 编码为 0.60–1.56 ms。Python 生产代码在两轮之间没有修改，这些差异属于运行波动。另以实际 100 ms 间隔获取 30 次快照，同时用配置为 5 ms 间隔的线程探测状态锁：8,192 点用例记录 487 次取锁，等待中位数 0.0065 ms，P95 0.0115 ms，最大 0.0259 ms。这只测探针线程获得调度后的取锁等待，不代表设备回调总延迟或 Windows 调度保证。

锁内还有增量游标线性查找，其耗时不能归到光谱转换上。现有证据不支持扩大状态锁重构。Python JSON 编码仍是通信中的主要开销之一；本轮没有更换编码器或协议来换取速度。

### 消除已确认的重复复制

- Python 响应读取线程直接移交自己拥有的 `result`，不再复制完整结果。缺少 `result`、远端错误、请求编号匹配和错误分类规则保持原样。
- 实时快照消息及订阅信封直接接收已有 JSON 值。原先 `json!` 对插入的 `Value` 按引用序列化，会重新分配整段数组。
- PD 状态指纹先排除 `points` 再复制其余字段。原先先复制完整历史，再删除 `points`。其他字段及非对象 PD 值继续遵循原规则。

### 同一输入的 Rust 对照

为隔离 Python 和操作系统调度波动，在同一 release 进程中交替运行原算法与修改后的算法，每种输入各 100 次。计时包含 Rust JSON 解码、结果提取、协议验证、指纹、消息包装、JSON 编码及 UTF-16 转换，不包含 Python、管道 I/O 或实际 WebView 发送。

| 同进程固定输入，中位数 | 修改前 | 修改后 | 减少 |
| --- | ---: | ---: | ---: |
| 8,192 点实时全量快照 | 1.427 ms | 0.913 ms | 36.0% |
| 8,192 点实时增量快照 | 0.404 ms | 0.277 ms | 31.5% |
| 13,000 点 PD 全量快照 | 9.143 ms | 2.965 ms | 67.6% |

全部对照的 JSON 字节、UTF-16 内容和状态指纹一致。完整光谱和历史点数没有减少，消息体积没有缩减。上述百分比只适用于 Rust 处理段，不能当成设备采集、页面帧率或总体响应速度的提升。

另保存两份修改前后 release 测试程序，按“前后、后前、前后”串行运行三轮真实管道对照。每份程序覆盖两种光谱长度，以及实时增量、PD 增量、PD 重新订阅全量三种流程，每种 100 帧。PD 全量流程整体呈改善，实时增量流程的前后结果受运行波动影响，没有稳定的端到端提速结论。计时替换了流循环的等待函数以隔离处理耗时，但逐次验证了请求的原有 100 / 500 ms 间隔；没有修改产品等待函数。

### 验证与复现

- 53 项 Python 聚焦测试通过，覆盖桥接契约、完整光谱、饱和处理、自动测试安全和实时电流。
- 17 项不同的 Rust 聚焦测试通过，覆盖消息内容与数组所有权、额外字段、负零、小数、PD 非对象值、协议验证、发送错误、刷新间隔、超时退避、恢复全量和取消后丢弃晚到响应。新增两项数组所有权测试先在旧实现失败，修改后通过。
- 17 项前端契约和快照合并测试通过。`cargo check --release --locked --lib` 通过，仅保留原有未构造 `Backend` 变体警告。
- 快照构建器、运行状态与设备控制代码的本轮前后文件一致。等待函数、流超时处理分支及刷新间隔函数逐字一致。JSON 编码、写出队列、设备计算和 CH341 最小命令间隔均未修改。
- 没有运行全套测试、打包、实际 Tauri WebView、Windows WebView2 发送或真实设备验收。

从项目根目录复现 Python 快照及锁探针：

```sh
/Users/zh/miniconda3/envs/sth_eb314/bin/python -m tools.benchmark_realtime_transport \
  --output output/performance-realtime-20261015/repeat
```

再复现 Rust 固定输入对照和真实管道流程：

```sh
ARP_BENCHMARK_PYTHON=/Users/zh/miniconda3/envs/sth_eb314/bin/python \
ARP_BENCHMARK_FIXTURES="$PWD/output/performance-realtime-20261015/repeat" \
ARP_BENCHMARK_OUTPUT="$PWD/output/performance-realtime-20261015/rust-repeat" \
cargo test --release --locked --manifest-path tauri-ui/src-tauri/Cargo.toml \
  --lib realtime_transport_benchmark -- --ignored --nocapture
```

测量时串行执行，避免与编译或其他基准争用 CPU。Rust 基准默认忽略，必须显式选择，并提供 Python 与输出路径；不会进入产品构建。

原始证据：[汇总](../output/performance-realtime-20261015/summary.json)、[Python 初测](../output/performance-realtime-20261015/before/python.json)、[锁探针与复测](../output/performance-realtime-20261015/lock-check/python.json)、[Rust 固定输入对照](../output/performance-realtime-20261015/final/rust.json)、[回归先失败](../output/performance-realtime-20261015/red.txt)、[回归通过](../output/performance-realtime-20261015/ownership-green.txt)。三轮交错对照、两份程序 SHA-256 和各层测试记录保存在汇总及同目录中。

## 2026-10-15：大光谱 JSON 编码与管道通信

确认的热点是大量双精度数值的 JSON 格式化。24 条 × 8,192 点的固定合成光谱响应为 7,155,936 字节，初测 `json.dumps` 中位数 93.39 ms，换行字符串复制 0.27 ms、UTF-8 转换 2.50 ms、预编码数据的管道写出及刷新 3.06 ms。各项分别计时，不能简单相加当作完整响应耗时。8,192 点密集变化的光谱编码约 1.96 ms，未变化的小快照约 0.024 ms。

复用标准库编码器没有稳定的大消息收益；隔离安装的 `simplejson 4.2.0` 在大光谱上也未提速。`orjson 3.13.0` 有明显收益，但不能直接替换：其非有限值处理和部分浮点数字符写法与标准库不同。参考 [orjson 官方说明](https://github.com/ijl/orjson#float)和 [simplejson 编码选项](https://simplejson.readthedocs.io/en/latest/)。候选安装在忽略目录中测试，没有更新其他运行依赖。

### 加速范围与不变条件

新增 `tauri_bridge/json_codec.py`，仅对至少 1,024 点的光谱快照或完整历史光谱响应尝试原生编码。纯 PD 历史、小型控制消息和同时携带 PD 历史的响应继续使用标准库，避免兼容性检查反而增加这类消息的耗时。

原生路径先检查完整响应：仅允许普通 JSON 基础类型、字符串键，以及两种编码器均使用相同定点写法的浮点值，即正负零或 `1e-4 <= abs(x) < 1e16`。科学计数法范围、NaN / Infinity、共享容器、自定义类型等保留原编码；超出原生整数或嵌套能力、无效 Unicode 等输入也交回原编码处理。这些范围只决定内部编码路径，不限制数据输入、保存或传输，也不进行舍入、截断、抽稀或数值替换。

标准库路径保留原来的参数与 UTF-8 换行。原生路径一次生成带换行的 UTF-8 字节。`_write_response` 的输出锁、单次完整写出和刷新逻辑保持原样，编码仍在输出锁外完成。任务队列、请求分类、设备状态锁、控制命令、刷新间隔和停止代次检查均未改动。

运行依赖固定为 `orjson==3.13.0`，当前开发环境已安装。缺少原生库时仍使用原编码器，应用可以按原方式启动。Windows 构建预检增加原生库导入检查；已确认 CPython 3.11 / Windows amd64 wheel 可用，下载体积为 121,287 字节，原生 `.pyd` 为 223,744 字节。这是依赖文件体积，不能当作已测得的安装包增量。

### 同进程交错对照

使用本轮开始时保存的原 `_write_response` 与当前实现，交替处理同一输入。计时包含兼容性检查。每种小响应各 60 次，大光谱各 12 次，另有 3 次预热。管道由持续读取并校验 SHA-256 的线程消费，双方完整输出逐字节一致。

| Python 编码、UTF-8 与操作系统管道写出，中位数 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 8,192 点全量快照，包含功率历史 | 3.212 ms | 1.403 ms |
| 8,192 点增量快照，平滑光谱夹具 | 0.639 ms | 0.431 ms |
| 8,192 点增量快照，密集变化的浮点值 | 2.064 ms | 0.485 ms |
| 24 条 × 8,192 点完整光谱 | 99.696 ms | 22.909 ms |
| 13,000 点 PD 全量，保留标准库路径 | 6.182 ms | 6.185 ms |

密集光谱及完整大光谱的该段耗时约减少 76%–77%。仅编码到内存接收端的大光谱耗时为 96.659 → 20.178 ms。该收益不包含 Excel 冷读、快照构建、Rust 解码、实际 WebView 发送或页面绘制，不能当作点击到曲线出现的整体提速。

实际工作簿的 6 组普通/完整详情响应均与旧编码逐字节相同，源文件 SHA-256 不变。三份完整详情中，两份可使用原生路径；一份在光谱尾部包含定点兼容范围外的微小强度值，保留标准库路径。本轮没有为了扩大加速范围改变这些数值，因此不能宣称所有文件都会提速。

### 精度、异常与时序验证

- 用固定种子生成 1,048,576 个可进入原生路径的双精度样本，包含不同符号、指数和尾数。64 组响应与标准库 JSON 字节完全相同，Python 回读的双精度位模式也完全相同；另测试定点范围边界、负零、极小值、最大有限值和非有限值的原路径行为。
- 新增 15 项传输测试；其中“大光谱不得调用慢速浮点格式化”的回归先在旧实现失败，再在当前实现通过。覆盖字段及顺序、完整数组、输入不变、Unicode/转义、任意精度整数、非字符串键、命名元组、共享/循环容器、深层嵌套、异常类型和消息、缺少原生库、文本输出、并发完整行与单次刷新、写出失败不被吞掉。
- 本轮共 68 项 Python 聚焦测试、17 项前端契约与快照合并测试通过。现有 release Rust/Python 管道基准完成 6 种流程共 600 帧，保留完整点数、修订号/游标规则，并验证传入等待函数的原有 100 / 500 ms 间隔。基准替换等待以隔离处理耗时，不代表实际仪器或 WebView 连续刷新验收。
- 本轮前后，入口中除编码调用外的全部函数 AST 相同；写出函数中的锁及 I/O 块 AST 相同。运行状态和快照构建器文件与本轮前一致。设备控制时序、CH341 间隔、持久化写出和停止逻辑没有调整。
- 源码编译和 `git diff --check` 通过。没有运行全套测试、打包、Windows 执行、真实 WebView2 或真实设备验收。

另有一次覆盖 100,000 个全范围有限浮点值的探索性 Rust 探针：更换候选编码后的解析值与原编码一致，同时发现现有 Rust 默认解析与 Python 原始位模式之间已有边界差异。该探针涉及的完整浮点范围大部分不进入本轮原生编码路径；没有借本轮修改 Rust 解析规则。当前精度保证是编码前后线路字节不变，不能据此宣称修复了原有解析精度问题。探针源码和结果保存在证据目录，临时探针已从产品测试模块移除。

该解析精度问题随后完成独立复现和修复，见下方“Rust 浮点解析精度修复”。

复现基线拆测、同进程对照和精度语料：

```sh
/Users/zh/miniconda3/envs/sth_eb314/bin/python -m tools.benchmark_json_transport \
  --output output/performance-json-20261015/repeat-baseline

/Users/zh/miniconda3/envs/sth_eb314/bin/python -m tools.benchmark_json_transport \
  --baseline-source outputs/json-transport-20261015-baseline/main.py \
  --output output/performance-json-20261015/repeat-paired --iterations 60

/Users/zh/miniconda3/envs/sth_eb314/bin/python -m tools.benchmark_json_transport \
  --precision-corpus --output output/performance-json-20261015/repeat-precision
```

基线拆测命令会测当前生产写出器；原实现的对照必须传入本轮开始时保存的源文件。测量应串行执行。升级原生依赖时，应重新验证逐字节语料及目标 Windows 环境。

原始证据：[汇总](../output/performance-json-20261015/summary.json)、[基线拆测](../output/performance-json-20261015/baseline/baseline.json)、[同进程交错对照](../output/performance-json-20261015/paired-1/paired.json)、[百万双精度语料](../output/performance-json-20261015/precision-guarded/precision.json)、[实际工作簿](../output/performance-json-20261015/real-workbooks.json)、[控制路径一致性](../output/performance-json-20261015/unchanged-control-paths.json)、[Rust 管道回归](../output/performance-json-20261015/native-regression/rust.json)、[依赖体积](../output/performance-json-20261015/dependency-footprint.json)。

## 2026-10-15：Rust 浮点解析精度修复

先复现，再修复。该问题不只涉及极端指数：当前三份实际工作簿的完整详情，共 65,775 个 Python 双精度浮点字段，其中 4,130 个在 Rust 默认解析后偏移 1 ULP，即变为相邻的双精度值。按字段分为波长数组 3,510 个、强度数组 605 个、指标和其他数值 15 个。实际波长最大绝对差异为 `1.1368683772161603e-13 nm`，实际强度的最大绝对差异为 `1.1368683772161603e-13`（原强度单位）。普通详情也有同源差异，不应将两种详情的重复字段相加当作独立数据量。

例如 Python 原始峰值波长 `914.9965972639145`，经过默认 Rust 解析后变为 `914.9965972639144`。直接让 JavaScript 解析原 Python JSON，所有检查字段的二进制位模式都与编码前相同；经过 Rust 后，偏差继续传到 JavaScript。根因在首次解析，未发现 Python 编码或 Rust 转发编码额外引入误差。

回放实际前端的汇总、三位小数格式化、电流匹配、光谱曲线数据、游标和区间拟合函数：三份样本的最大功率点、最大效率点、光谱最大强度像素索引、电流匹配和三位小数显示均未变化；部分游标与拟合计算的末位发生变化，最大绝对差异约 `3.53e-13`。曲线坐标仍存在上述原始值偏差，不能用“显示看不出来”代替精度验证。这是这三份文件的结果，不代表所有可能输入都不会改变选择或边界判断。科学指标和设备状态判断在 Python 上游完成，Rust 不负责重新计算 PIB / SMSR / FWHM。

### 修复范围

`serde_json` 保持锁定的 `1.0.150`，在 Cargo 依赖上启用 `float_roundtrip`，使用库自带的精确浮点解析器。此前默认路径先将十进制有效数字转换为 `f64`，再乘除十的幂，可能产生中间舍入；该现象和精确解析选项由[上游作者说明](https://github.com/serde-rs/json/issues/536)，当前版本的默认特性和可选项见[版本特性清单](https://docs.rs/crate/serde_json/1.0.150/features)。本地同时核对了实际锁定版本的 `de.rs` 两条解析路径。

不增加十进制字符串绕行、手工舍入、数值裁剪或自写浮点解析器。`Cargo.lock` 未变化，没有升级库或增加依赖。Cargo 特性对该原生构建中统一使用的 `serde_json` 生效，因此同时回归整数类型、负零、边界有限值、非 JSON 数值拒绝和控制消息字段。

产品修改仅为依赖选项；另增加测试模块入口和离线诊断工具。Python 编码、设备控制、状态锁、队列、持久化和前端功能实现均未因本轮修复而调整。原有 100 / 500 ms 快照等待、超时、重试、取消及 CH341 命令间隔保持原值。

### 精度回归结果

| 输入 | 修改前 Rust 位差异 | 修改后 Rust / JavaScript 位差异 |
| --- | ---: | ---: |
| 三份完整光谱详情，65,775 个浮点字段 | 4,130，均为 1 ULP | 0 / 0 |
| 三份普通详情，84 个浮点字段 | 12，均为 1 ULP | 0 / 0 |
| 固定种子全范围有限双精度语料，100,000 个值 | 29,625，最多 2 ULP | 0 / 0 |

Python 在编码前用 `struct.pack('<d')` 导出独立位模式预期值。回放包含真实 `_write_response`、操作系统管道、生产 `PythonBridge::start_response_reader`、快照封装、JSON 和 UTF-16 编码，再由 JavaScript `JSON.parse` 接收。Rust 逐字段对比位模式；JavaScript 同时逐字段检查浮点位模式，并深度比较整个接收结果，包含所有数组、整数、字符串、空值和字段。全量采样点均保留。该回放不执行 WebView 消息发送或设备后端。

最小回归使用真实工作簿数值，在修复前明确失败；启用选项后通过。补充负零、最小次正规数、最小正规数、最大有限值、长小数恰处于两个浮点值中间时的舍入、64 位无符号整数和非法 JSON 边界。三份源工作簿 SHA-256 与全部冻结输入 JSON 的 SHA-256 前后一致。

### 解析耗时成本

在本机 macOS arm64、Rust 1.95.0 的 release 构建中，分别保留修改前后测试二进制及哈希；同一冻结输入进行三轮串行对照，各轮每种输入预热 5 次、测量 60 次，轮间交替执行前后版本。表中取三轮中位数的中位数。仅计 JSON 解析、分配和结果释放，不含 Python、管道、Excel 或页面绘制。

| 响应 | 修改前 | 修改后 | 增量 |
| --- | ---: | ---: | ---: |
| 实际 18:02 工作簿，4 条 × 3,648 点 | 0.623 ms | 0.687 ms | 0.064 ms |
| 实际 18:37 工作簿，1 条 × 3,648 点 | 0.168 ms | 0.179 ms | 0.011 ms |
| 实际 18:45 工作簿，4 条 × 3,648 点 | 0.618 ms | 0.679 ms | 0.061 ms |
| 合成 8,192 点密集浮点快照，298,167 字节 | 0.332 ms | 0.449 ms | 0.117 ms |
| 合成 24 条 × 8,192 点，7,155,942 字节 | 8.076 ms | 10.868 ms | 2.792 ms |

这是恢复数值精度的明确成本，不是解析提速。保留精确解析，不用有偏差的旧解析路径换取更低耗时。不同阶段的独立计时不能简单相加为整条链路的延迟，也不能证明真实设备命令的墙钟执行时刻完全相同。

19 项 Rust 聚焦回归、45 项 Python 通信/CH341 等待回归、44 项前端数据分析/快照/协议测试通过。另重跑 6 种模拟流程、共 600 帧真实 Python/Rust 管道回归，验证点数、修订号、游标和传入等待函数的原有间隔；基准替换实际等待，不代表真实设备刷新验收。`cargo check --release --locked --lib` 通过，只有原有 `PythonBridgeError::Backend` 未使用警告。没有运行全套测试、安装包构建、Windows/WebView2 或真实设备验收。

复现当前修复：

```sh
/Users/zh/miniconda3/envs/sth_eb314/bin/python -m tools.inspect_json_float_precision \
  --output output/float-precision-20261015/repeat-fixtures

ARP_FLOAT_FIXTURES="$PWD/output/float-precision-20261015/repeat-fixtures" \
ARP_FLOAT_OUTPUT="$PWD/output/float-precision-20261015/repeat" \
ARP_BENCHMARK_PYTHON=/Users/zh/miniconda3/envs/sth_eb314/bin/python \
cargo test --release --locked --manifest-path tauri-ui/src-tauri/Cargo.toml \
  --lib replay_python_float_precision -- --ignored --nocapture

ARP_REQUIRE_EXACT=1 node --experimental-strip-types tauri-ui/scripts/inspect-float-precision.mjs \
  output/float-precision-20261015/repeat-fixtures output/float-precision-20261015/repeat/pipe
```

解析计时使用同一测试模块中的 `benchmark_float_precision_parse`（同样需要 `ARP_FLOAT_FIXTURES` 和 `ARP_FLOAT_OUTPUT`，使用 `--ignored` 显式运行）。计时应串行执行；修改前二进制保存在忽略目录 `outputs/float-precision-20261015-baseline`，不能用已修复的当前构建冒充旧版本。

证据：[汇总](../output/float-precision-20261015/summary.json)、[修复前位差异](../output/float-precision-20261015/before/rust.json)、[修复后位差异](../output/float-precision-20261015/after/rust.json)、[前端接收及计算回放](../output/float-precision-20261015/after/pipe/javascript.json)、[三轮解析计时](../output/float-precision-20261015/paired-timing.json)、[文件一致性](../output/float-precision-20261015/integrity.json)、[600 帧管道回归](../output/float-precision-20261015/stream-regression/rust.json)。
