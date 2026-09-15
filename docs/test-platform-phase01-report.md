# ARP Phase 0 + Phase 1：Recipe 领域层实施与验收报告

日期：2026-09-15  
仓库：`zheate/ARP`  
目标分支：`refactor/test-platform-process-model`  
范围：行为基线测试、强类型 RecipeDefinition、现有执行器的渐进接入。**未实施 Phase 2，未进行硬件验收。**

## 版本与交付范围

- 原始产品基线：`0b6f46ac92ffcb880ad8a140fade4b997f2cf926`。
- 本次继续工作时的分支：`90b4ef3fa95611cb5cef0a92bc3459553fc2f069`。当时已经存在 Recipe 模型、运行时接入和 29 项专项测试，另有独立的离线打包提交。此次在现有实现上继续完成验证与补全，未覆盖或回退上述提交。
- 此次补全：复验既有 Phase 0/1；修复所选 Recipe 的 trace 意图未被消费的问题；增加 5 项真实运行时回归；补充源文件校验、基线/候选行为对照和本报告。
- 最终产品实现：`9802fb1bfe609ccd06b5275d663e1882339eb9f5`。报告提交不改变产品行为。
- [完整 Python 验证运行](https://github.com/zheate/ARP/actions/runs/34930084320)；[基线与候选的对照 CI](https://github.com/zheate/ARP/actions/runs/34929628712)。

没有合并 main；分支原有打包工作保留，但不属于本报告的实现或验收范围。

## A. 原架构诊断：实际代码与方案假设

### 职责现状

`AutomaticTestOrchestrator` 已经负责设备无关的点序列、dwell、当前索引、就绪标记和状态，不需要再重写一套。`StationAutomaticMixin` 同时承担点执行、稳定条件选择、暂停/重试、安全收尾、记录和导出协调。真正需要切开的是“测试参数/意图”与“设备及运行生命周期”，不是把所有方法搬到新目录。

`SessionRecordStore` 已保存 Session / Point / Attempt 和选中状态。`queue()` 先调用 JSONL append/fsync，成功后才替换选中的 Attempt 和更新内存记录。自动点循环只有在提交成功后才进入下一个点；Excel 由终态触发后台导出，不能重新成为点推进条件。

以下内容与原方案中的概念示例不同，本期以实际代码和 Bridge Contract 为准：

| 事项 | 实际基线行为 | 本期处理 |
|---|---|---|
| 普通测试是否必须有光谱 | 仅启动时光谱仪已就绪才使用；曾配置资源不代表必须启动光谱仪 | 以 `WHEN_READY_AT_START` 表达，不新增必需仪器门槛 |
| Standard / Hold 的电流模式 | Station 配置支持 Standard+custom、Hold+range；持久化 preset 的校验规则更严格 | 保留两个入口各自的原规则，不用显示名称强行改计划 |
| CH341 / TDK custom | CH341 合并常规网格、停留点和目标；TDK 的 custom ramp 路径只以指定停留点作为测量点 | 保留 `custom_hold_ramp_enabled` 的平台判定和原 planner |
| Aging | CH341-only；2→20 A、2 A 步长，7200 s；每 300 s 记录新鲜功率；不做功率/光谱判稳，不采光谱 | 固化为原样的 typed intent；不是示例中的 30 s 采样 |
| Aging 的记录生命周期 | 不创建普通点测 Session JSONL/Excel | 保留；统一老化持久化属于后续功能变更，不能声称已完成 |
| TDK 启动状态 | 既有流程要求操作者先启用输出，并满足零电流检查 | 不改为“开始测试自动上电”或统一要求全部 Output OFF |
| 电流与命令限值 | CH341 20 A、1.1 s guard；TDK 现有 planner 的 maximum 为 None | 保留原设备约束，不凭示例给 TDK 新增 20 A 限制 |
| Retry | 点重试由操作者介入；设备传输重试是另一层职责 | 不引入自动重复测量次数，不修改恢复动作或超时 |

不能把“每个普通测量点先 durable commit”的现有保证，扩张成“所有测试模式已有完整的统一 Session 生命周期”。

## B. 新增领域模型及依赖关系

| 模型 | 职责 |
|---|---|
| `RecipeDefinition` / `RecipeTemplate` | 配方身份、版本、执行种类、所需能力与测试意图；实例为 frozen dataclass |
| `CurrentPlan` / `CurrentHold` | 电流计划和停留点；复制旧配置中的可变行到不可变 tuple |
| `StabilityProfile` / `PointStability` | 根据电流解析窗口、绝对功率跨度、波长容差 |
| `CurrentBand` / `CurrentMatch` / `StabilityOverride` | 有限、显式的区间/近似匹配规则；每个字段按首个有效匹配覆盖 |
| `MeasurementPlan` | 启动就绪时才采光谱、是否请求 custom 逐秒 trace、稳定性意图 |
| `TimedPowerHold` | 老化持续时间、记录间隔、请求的升流等待时间 |
| `RetryPolicy` | 保留“操作者介入/不适用、自动点重试为 0”的声明 |
| `Capability` | current source、optical power、spectrum、current/voltage readback 的最小枚举骨架 |

依赖方向：

```text
旧 configuration / selectedScriptId
    -> tauri_bridge.recipe_configuration（兼容编译与查询）
    -> combined_test.recipes.catalog / definitions（纯值对象）

StationAutomaticMixin / SnapshotBuilder
    -> 读取 Recipe 的计划与参数
    -> 调用原 planner、设备端口、安全约束、RecordStore
```

Recipe 不调用执行器，也不持有设备、文件、UI 或安全控制权限。领域模块仅依赖标准库与自己的 definitions；独立 Python `-I -S` 子进程已验证：不安装 site-packages 也能导入并编译，不会加载设备或运行时模块。

**最小骨架不等于已上线的能力系统：**本期没有 Capability Registry、真实设备 capability probing、新 Preflight 或通用重试引擎。`RetryPolicy` 和 capability 字段不接管硬件执行权限。`specification_id` 仍为预留引用，不代表已完成规格版本化。

兼容编译器保留原转换、默认值和大小写/空白差异。未知 ID 的辅助编译保留旧参数行为，但不会因此被加入可执行白名单；`automatic_readiness` 仍拒绝未知脚本。

## C. 实际迁移与补全

### Standard

固定 5 s detector window 和低电流规则从 `station_automatic.py` 的参数分支移至 catalog/profile。原 `StableHoldTimer`、`WavelengthStabilityDetector`、`build_test_currents`、`build_test_dwells` 不变。配置仍决定 range/custom，现有电流点量化与最终点规则不变。

### Hold Points

配置窗口、custom holds 和 dwell 经兼容编译器进入 `CurrentPlan`。设备是否走 custom ramp 仍由执行层决定。逐秒 trace 是 Recipe 请求，但是否允许附加采样仍受 TDK 设备路径约束。

### Aging

固定 2→20 A、2 A step、7200 s / 300 s / requested 1 s 移至 `AGING_CURRENT_PLAN` / `AGING_HOLD`。`station_support.py` 保留旧常量名称作为兼容别名；`_run_aging_cycle()` 消费 `TimedPowerHold`。CH341-only 准入、命令 guard、安全降流及现有不建立普通点测 Session 的行为不变。

### 运行时与 snapshot 共用参数

`_automatic_settings()` 消费编译后的计划与测量意图；`_begin_automatic_measurement_window()`、`_wait_for_automatic_stability()`、`SnapshotBuilder` 从同一解析层取得点级稳定参数，避免执行条件和界面说明各有一份数值规则。Bridge 字段和 payload 没有新增、删除或改名。

### 此次继续工作修复的实际缺口：trace 意图

原 Phase 1 接入中，`_begin_recording_session()` 读取全局 `MEASURED_POINT_MEASUREMENTS`。当测试用的已注册模板声明 `TracePolicy.NONE` 时，执行器仍创建逐秒 recorder。

新增测试先复现 **2 项失败**，再将逻辑改为：

```text
原 Session 创建与序列登记
    -> 关闭上次 recorder、清空旧路径
    -> 原 TDK guard
    -> 读取所选 RecipeTemplate.measurement_plan
    -> 根据传入 settings.current_plan_mode 判断 trace 请求
    -> 原 AutomaticSampleRecorder 创建
```

所选 Recipe 的关闭意图现在生效。CH341 仍不创建该 recorder；实际传入 settings 的 mode 仍是判定来源，大小写/空白敏感行为保持；关闭 trace 不会关闭 Session JSONL。三种现有配方的已支持执行行为没有变化。

## D. 明确没有迁移的职责

| 层 | 保留内容及原因 |
|---|---|
| Safety / Device | 最大电流、CH341 1.1 s、TDK/CH341 IO、command generation、锁顺序、safe ramp-down、shutdown confirmation。Recipe 不能提供覆盖这些规则的字段或回调 |
| Process / Runtime | 开始、暂停、恢复、重试许可、结束、当前 Session、资源所有权、设备恢复及线程模型。完整 RunManager 不在本轮 |
| Persistence | Session / Attempt / selected attempt、JSONL fsync、重试历史、Excel materialization、导出错误及后台队列 |
| Measurement / Calculation | 功率及波长判稳算法、采样新鲜度、饱和、PIB/FWHM/SMSR、效率、单位、精度 |
| Public contracts | Tauri/Rust、React、许可证、Bridge method/payload、已有配置和 preset 字段、Excel 格式、依赖与锁文件 |

针对 23 个受保护文件做了基线/候选逐字节比较，全部相同；包括 `automation.py`、`record_store.py`、`session_journal.py`、`devices.py`、`device_interfaces.py`、`tdk_power_supply.py`、`core.py`、`spectrum_math.py`、`station_runtime.py`、`headless_backend.py`、`command_safety.py`、`export_queue.py`、`BRIDGE_CONTRACT.md` 和依赖文件。

`StationAutomaticMixin` 的 42 个方法中，35 个原文未变，包含点执行、暂停/重试、原始数据提交及安全降流路径。文件/方法一致性是限定改动范围的证据，不是物理安全证明。

## E. 行为兼容与验证证据

### 验证总览

| 验证 | 结果 | 覆盖边界 |
|---|---|---|
| 原始基线完整 Python CI | 470 项执行：468 通过、2 跳过 | 原始产品代码、完整仓库和既有便携依赖 |
| 原始基线 + characterization 测试覆盖层 | 11/11 通过 | 只把行为测试放到基线源码旁执行；不移入 Recipe 产品实现 |
| 最终完整 Python CI | 504 项执行：502 通过、2 跳过，0 失败/错误 | 验证产品提交 `9802fb1…`，不是仅验证目录或构建 |
| 本地相关领域/运行时/安全/记录测试 | 204/204 通过 | 含 34 项 Recipe 专项测试；不可再重复相加统计 |
| 本地 Bridge 测试 | 24/24 通过 | 不代表原生 Tauri 或真实串口验收 |
| Recipe 专项 | 34/34 通过 | 原有 29 项 + 本次新增 5 项 trace 回归 |
| 独立行为矩阵 | 1440/1440 与基线一致 | 816 组 settings/planner 返回相同、624 组拒绝类型/信息相同；不是 1440 项 unittest |
| 静态边界 | 23 个保护文件相同；无新增 import cycle | Recipe 模块没有设备/持久化/安全依赖 |

两项跳过均为未构建 Rust realtime DLL 的本机加速对照测试；不是新增 skip，也不算通过。未修改 Rust 或计算算法。

初次本地全量测试尝试未通过：该文本源码副本缺少 PyVISA 和报表 PNG，导致导入/文件错误；失败日志保留，没有删除测试或以 mock 资源伪造通过。最终全量结论来自完整 checkout、按仓库依赖安装的 CI。本地只对具备运行条件的相关集合另行复验。

### 重点输入与输出

| 场景 | 基线与最终行为 |
|---|---|
| Standard detector | 固定 5 s；不是把 configured window 或长 dwell 当作该值 |
| 低于 2 A | 功率 10 s 窗口、0.1 W span；正常波长容差 0.2 nm |
| 2 A 近似匹配 | `math.isclose` 保留绝对 1e-9 A、相对 0；波长 0.5 nm，功率 span 0.2 W |
| `2 A - 5e-10` | 功率仍取低电流 band：10 s / 0.1 W；波长仍匹配 2 A：0.5 nm |
| 确认时钟 | 普通点 5 s，最后点 10 s，单点也是 10 s；不替代 detector 或 dwell |
| CH341 25 A / 0.1 s | 原 planner/guard 拒绝；Recipe 替换不能放宽 |
| TDK custom trace | 仅传入 mode 精确为 custom 且模板请求时创建；CH341 永不由该请求获得附加轮询权限 |
| JSONL 第一点失败 | 不设置下一点电流；retry_persist 重试同一点提交；成功后才进入下一点 |
| 失败后人工结束 | 不设置下一点，沿原安全结束路径收尾 |
| Attempt 替换写入失败 | 原 selected attempt 不被删除；之后成功重试仍保留 [1,2,3] 历史和 rejected attempt |
| Aging shutdown 失败 | 仍为 shutdown_unconfirmed，不被 Recipe 分派转换为成功 |

1440 组离线矩阵组合了脚本 ID、range/custom 的原字符串形式、两类电源、光谱就绪标记、目标电流、稳定窗口和超时。对照检查的是既有 settings/planner API；未知 ID 的辅助解析一致，不表示通过 `automatic_readiness` 的准入。所有设备为 Fake，命令写入列表为零。

## F. 文件变化

以下为整个 Phase 0/1 相对原始基线的配方工作，不包含分支原有打包提交：

| 文件 | 职责 |
|---|---|
| `combined_test/recipes/__init__.py` | 独立领域包入口 |
| `combined_test/recipes/definitions.py` | 不可变强类型计划、条件、测量和重试意图 |
| `combined_test/recipes/catalog.py` | Standard / Hold / Aging 定义及数值规则 |
| `tauri_bridge/recipe_configuration.py` | 旧配置到领域对象的唯一兼容入口 |
| `tauri_bridge/station_automatic.py` | 消费 Recipe 参数；此次补全所选 trace 意图；保留执行与安全边界 |
| `tauri_bridge/snapshot_builder.py` | 使用同一稳定参数解析结果，wire schema 不变 |
| `tauri_bridge/station_support.py` | 老化常量的兼容别名取自领域目录 |
| `tests/test_recipe_characterization.py` | 11 项基线行为与持久化顺序测试 |
| `tests/test_recipe_definitions.py` | 10 项纯模型、独立导入、不可变与稳定边界测试 |
| `tests/test_recipe_integration.py` | 8 项运行时、snapshot、限值与安全收尾接入测试 |
| `tests/test_recipe_trace_integration.py` | 本次新增 5 项 trace 意图与设备限制回归 |
| `.github/workflows/recipe-phase01-tests.yml` | 只读基线/候选完整 Python 回归，附源文件 SHA256 证据 |
| `docs/test-platform-phase01-report.md` | 本报告 |

临时应用精确补丁的工作流已从最终文件树删除；保留的回归 workflow 仅有 `contents: read`，不提交代码。

## G. 剩余分支与架构债务

### 分支计数口径

在生产 `combined_test/`、`tauri_bridge/`、`tools/` 中，扫描引用 selectedScriptId / selected_script_id / script_id / AGING_TEST_SCRIPT_ID 的 AST Compare 节点。测试、文档、目录声明和字典查询不计入。

- 原始基线 9 个；最终 8 个，其中 **1 个已是 ExecutionKind 比较**。
- 因此直接按脚本标识比较为 **9→7**。`station_automatic.py` 的两处内置 ID 行为特例已移出；剩下的通用 ID 查询仍用于准入，不是新的配方特例。
- 前端精确比较内置 Recipe ID 的表达式 **13 个，未变**；另有 runContext 的一般相等性比较，不算内置配方分支。完整匹配位置保留在 evidence JSON。

| 最终保留位置 | 保留理由 |
|---|---|
| `station_automatic.py`：readiness 按 ID 找脚本 | 保持未知脚本拒绝，领域模板不是执行授权 |
| `station_automatic.py`：`_aging_test_selected()` | 兼容方法名保留，内部按解析后的 `ExecutionKind.TIMED_POWER_HOLD` 分派 |
| `station_runtime.py:102` | 已保存配置启动时的老化迁移/控制器兼容 |
| `station_runtime.py:409` | Aging + 非 CH341 的后端拒绝规则 |
| `station_runtime.py:455` | 配置更新时保留 Aging 固定参数 |
| `test_script_presets.py:28,30,56` | 既有 preset 的白名单、计划模式和老化归一化契约 |
| 前端 13 个比较 | 老化默认值、曲线/阶段展示、控制器禁用与 preset 兼容；本轮禁止改 React |

这些计数没有把所有 selectedScriptId 引用误报成分支，也没有为了得到零而把安全限制藏进映射。

### 边界检查

Recipe 包和编译入口没有导入设备、RecordStore、Safety 或 UI，关键设备模块未发现 Recipe ID 判断。静态 import 图没有新增循环；仓库原本的 `shipping_report` / `shipping_report_headless` 相互引用仍然存在，与本轮无关。

现有配置接口不能声明跳过降流、停用 journal 或覆盖设备限值。实际执行仍经过原验证与安全端口；这不是针对任意恶意 Python 程序的沙箱或安全认证。

### 尚未完成

`StationAutomaticMixin` 仍协调 Session、点执行、暂停/重试、采样和导出。此次没有伪装成完整 Process Model，也没有完成 Run Manifest、Crash Recovery、Capability Registry 或独立 Specification Engine。

对于使用现有执行种类和参数能力的新普通 Recipe，不需要在点执行方法里新增按名称的分支；但正式上架仍要更新既有脚本准入、preset 规则和必要的前端呈现。新增不同物理执行语义仍需后续工作，不能宣称现在已经支持任意插件式流程。

## H. Phase 2：PointRunner 方案（未实施）

建议先以 `_run_automatic_point()` 为切口，提取“一个点从请求到持久化确认”的执行边界，而不是移动所有设备或改写线程模型。

1. **输入值对象**：point index、target current、已解析的 dwell/stability/measurement intent；平台 timing、取消和安全权限不来自 Recipe。
2. **服务端口**：通过原 StationRuntime 提供受保护的 set-current、采样/稳定等待、原数据质量判断和提交接口。驱动、锁顺序、命令代际保持原 owner。
3. **结果区分**：采样结果与 durable-commit receipt 分开；只返回 captured 值不能让外层推进下一点。Receipt 至少关联 session/point/attempt 身份。
4. **重试分开**：retry_point 允许按原策略重新测量并保留 Attempt；retry_persist 重写原已采集点，不能变成重新升流或重新测量。
5. **外层保留**：SN/Session、序列推进、全局 Stop/Fault、最终安全下电、Excel 排队、Next UUT 仍留在运行协调层。
6. **渐进范围**：先迁普通 measured-point 路径；Aging 当前没有同样的持久化生命周期，不能为了复用 PointRunner 而暗中改变老化行为。
7. **验收**：重复本期 JSONL 失败/恢复轨迹、取消/代际/锁测试、弱信号与饱和/超时、5/10 s 确认、TDK/CH341 行为，以及完整 Python suite；新增独立 fake-clock/fault-injection 验证。

旧与新执行路径仅可在 Fake 环境对照。不能对真实电源“并行影子执行”两套命令来验证等价。

本期到此停止：**不创建 PointRunner、不重写 Process Model、不操作真实仪器、不制作或发布安装包。**
