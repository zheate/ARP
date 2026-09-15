# GPT-6 Pro Prompt — ARP Test Platform / Process Model Refactor

你现在是一名负责**工业自动测试平台、半导体激光器测试系统、设备控制、安全状态机、Python 架构重构和测试基础设施设计**的资深软件架构师。

请直接在 GitHub 仓库：

`zheate/ARP`

分支：

`refactor/test-platform-process-model`

上工作。

在任何代码修改之前，先完整阅读：

- `AGENTS.md`
- `README.md`
- `PRODUCT.md`
- `DESIGN.md`
- `tauri-ui/BRIDGE_CONTRACT.md`
- `docs/test-platform-process-model-plan.md`
- `combined_test/automation.py`
- `combined_test/record_store.py`
- `combined_test/session_journal.py`
- `combined_test/devices.py`
- `tauri_bridge/headless_backend.py`
- `tauri_bridge/station_runtime.py`
- `tauri_bridge/station_automatic.py`
- 与自动测试、脚本 preset、record store、shutdown、retry、export、bridge contract 相关的 tests

这不是普通 Code Review，也不是简单目录整理。

你的目标是：

> 在不改变现有测试物理行为、设备协议、测量算法、数值精度和安全语义的前提下，把 ARP 当前的自动测试架构向“工业测试平台”演进，使 **Recipe / Process Model / Safety / Persistence / Device Adapter** 的职责边界清晰，并且可以渐进迁移，而不是大爆炸重写。

---

# 一、架构目标

最终依赖方向必须趋向：

```text
UI
 ↓
Process Model / Run Manager
 ↓
Recipe Engine
 ↓
Execution / Point Runner
 ↓
Measurement / Calculation / Safety Services
 ↓
Device Interfaces / Adapters
```

同时 Persistence / Event Log 作为平台基础设施被 Process Model 与 Execution 使用。

必须避免：

```text
Device driver -> knows recipe == aging
Recipe -> calls hardware driver directly
Recipe -> can disable safe shutdown
Recipe -> can bypass durable journal
```

Recipe 是“测试意图声明”，不是任意脚本语言。

---

# 二、先理解当前代码，不要假设方案文档完全正确

请严格检查当前仓库实际实现，再决定最小、安全的重构路径。

尤其关注：

- `AutomaticTestOrchestrator` 当前实际负责什么。
- Standard / Hold Points / Aging 的区别目前分别散落在哪里。
- `selectedScriptId` / script preset / power-supply-kind 的条件分支在哪里。
- `StationAutomaticMixin` 中哪些逻辑是真正 Recipe-specific，哪些是平台级基础设施。
- `SessionRecordStore` / JSONL 的 durable point commit 顺序。
- Retry / invalid attempt / selected attempt 的语义。
- Pause / fault / safe ramp-down / shutdown confirmation 的实际安全路径。
- Excel export 与 JSONL journal 当前的同步/异步关系。
- bridge contract 哪些字段是 UI 或 Tauri 已依赖的稳定接口。

如果发现方案文档中的假设和当前代码不同，以当前代码为准，并在总结中指出。

---

# 三、本轮只实施 Phase 0 + Phase 1

**不要一次性完成整个五阶段重构。**

本轮目标是建立正确的基础抽象，并证明不会改变现有行为。

## Phase 0 — Characterization / Safety Net

先补测试，再重构。

需要明确覆盖当前：

1. Standard Recipe
2. Hold Points / custom current plan
3. Aging Recipe
4. 当前计划电流点生成
5. dwell / hold 规则
6. 稳定性 window / threshold 特例
7. power / spectrum requirement
8. TDK / CH341 当前允许行为差异
9. retry / invalid attempt 基本语义
10. durable point commit 后才推进下一点的重要 invariant

这些测试必须描述“当前行为”，而不是描述你希望未来变成什么。

如果现有测试已经覆盖，不重复造轮子；优先补缺失的 characterization test。

## Phase 1 — RecipeDefinition

建立一个**强类型、纯领域、无硬件副作用**的 Recipe 定义层。

建议但不强制使用以下结构：

```python
@dataclass(frozen=True)
class RecipeDefinition:
    id: str
    version: int
    capabilities: tuple[str, ...]
    current_plan: CurrentPlan
    stability: StabilityProfile
    measurement_plan: MeasurementPlan
    retry_policy: RetryPolicy
    specification_id: str | None = None
```

可以根据当前代码调整字段，但必须满足：

- Recipe Definition 不 import TDK/CH341/Ocean/NI-DAQ 驱动实现。
- Recipe Definition 不做 I/O。
- Recipe Definition 不直接写 JSONL / Excel。
- Recipe Definition 不执行安全下电。
- Recipe Definition 不知道 React / Tauri。
- Recipe Definition 可以被纯单元测试构造和比较。

至少把现有：

- `standard`
- `hold-points`
- `aging`

表示为 RecipeDefinition 或等价的强类型对象。

---

# 四、哪些逻辑应迁移，哪些逻辑本轮禁止迁移

## 本轮应该迁移到 Recipe 层的内容

仅限真正的测试意图，例如：

- current plan mode
- 默认测试点 / custom point semantics
- dwell / hold profile
- 是否需要 spectrometer
- stability profile 的可配置/配方级参数
- 特定 Recipe 是否需要 trace sampling
- Recipe-level measurement requirements
- Retry policy 的声明性部分

尤其需要消除或集中以下类型的散落逻辑：

```python
if selected_script_id == "standard": ...
if selected_script_id == AGING_TEST_SCRIPT_ID: ...
```

但不要为了消除 `if` 强行抽象本来就属于设备能力的判断。

## 本轮禁止迁移到 Recipe 的内容

以下属于平台 / Safety / Device 层，本轮必须保持：

- Station 最大允许电流
- `MIN_POWER_SUPPLY_COMMAND_INTERVAL_S`
- CH341 command timing
- TDK / CH341 实际读写实现
- safe ramp-down
- emergency stop
- command generation / stale command protection
- shutdown verification
- durable journal 是否启用
- fault 后是否继续输出
- Excel export worker
- bridge protocol
- hardware reconnect

Recipe 不得获得绕过这些规则的能力。

---

# 五、稳定性规则的处理原则

当前代码中有 Standard、2 A、<2 A 等特殊稳定参数。

不要简单复制现有 `if` 到新文件里然后宣布完成。

请把：

> 稳定性算法

和：

> Recipe / point-specific stability parameters

分开。

例如：

- `StableHoldTimer` / WavelengthStabilityDetector 属于平台算法。
- `window_s = 5`
- `tolerance_nm = 0.2`
- `current < 2A -> power span 0.1W / 10s`

可以表示成 Recipe 中的 StabilityProfile / rule set。

但本轮必须保证执行结果与现有行为完全一致。

不要顺便“改进”阈值。

---

# 六、Capability 设计：本轮只建立最小骨架

可以建立 capability 类型或字段，例如：

```text
controllable_current_source
optical_power_measurement
spectrum_acquisition
verified_current_readback
voltage_readback
```

但本轮不要重写所有设备发现和 Preflight。

目标只是保证未来 Recipe 描述需要的**能力**，而不是写死：

```text
requires TDK
requires OceanDirect
```

如果完整 Capability Registry 会扩大风险，本轮只定义数据模型和测试，不接管现有运行行为。

---

# 七、当前持久化体系必须保护

当前 README 和代码已经形成非常重要的方向：

> 每个自动测试点先 durable commit 到 JSONL / Session Record，再继续下一点；Excel 是 materialized delivery artifact，而不是自动状态机同步边界。

这个原则不得破坏。

请增加或确认测试：

```text
Point N persistence failure
→ must not silently advance to Point N+1
```

重构过程中：

- `TestSession`
- `MeasurementAttempt`
- `AttemptValidity`
- Retry history
- selected attempt
- `SessionStatus`

语义都必须保留。

不要为了 Recipe 抽象而重做 RecordStore。

---

# 八、禁止改动范围

除非为了修复本轮重构直接暴露的明确 bug 且有测试证明，否则不要修改：

- Rust
- React UI
- Tauri command names
- Python bridge 方法名或 JSON payload
- TDK / CH341 / Power Meter / Ocean / NI-DAQ 协议
- PIB / FWHM / SMSR / Efficiency 数值算法
- 单位
- 数值精度
- Excel 格式
- shutdown 物理策略
- command timing
- package dependency
- licensing

不要做格式化全仓库、rename 大量文件、移动所有设备驱动、重写线程模型。

---

# 九、实现方式要求

## 1. 小步提交

建议分成：

1. characterization tests
2. Recipe domain model
3. existing scripts -> RecipeDefinition mapping
4. StationAutomaticMixin consumes RecipeDefinition
5. cleanup duplicated recipe-specific branches
6. final compatibility tests

每一个 commit 都应可以独立解释。

## 2. 兼容层优先

允许旧代码继续调用：

```python
selectedScriptId
currentPlanMode
customCurrentPoints
```

新 RecipeDefinition 可以由当前 configuration 编译产生。

不要要求一次修改所有调用者。

## 3. 强类型优先

优先 dataclass / Enum / Protocol。

不要使用大型 `dict[str, Any]` 作为新的核心领域模型。

## 4. 不做 YAML DSL

本轮禁止新增可执行 YAML/JSON 流程语言、`goto`、任意表达式、动态 Python eval 或插件脚本系统。

---

# 十、重点测试要求

至少确保以下场景行为等价：

### Standard

- current sequence
- 5 s standard stability behavior
- low-current special power stability behavior
- 2 A special wavelength/power behavior
- final point delay

### Hold Points

- custom points strictly increasing
- dwell retained
- target/end current behavior
- `custom_hold_ramp_enabled`
- TDK trace enable条件保持

### Aging

- CH341 compatibility rule保持
- aging target / duration / interval behavior保持
- 不意外启用 TDK-only sampling path

### Safety / Persistence

- maximum current validation unchanged
- CH341 command spacing unchanged
- persistence failure does not advance point
- retry attempt history preserved
- pause does not bypass shutdown behavior
- final safe ramp-down path unchanged

---

# 十一、架构审计要求

代码完成后，再重新搜索整个仓库，回答：

1. 还剩多少处 `selectedScriptId == ...` 或等价 Recipe ID 分支？
2. 每一处为什么还留着？
3. 是否仍有 Device Adapter 知道 Recipe ID？
4. Recipe 是否有任何路径能绕过 Safety / RecordStore？
5. 是否出现新的 circular dependency？
6. 新增一个普通 Recipe 是否还必须修改 `station_automatic.py`？
7. 如果答案是“是”，下一阶段应该抽哪一层？

不要为了让数字变成 0 而错误迁移职责。

---

# 十二、验证

至少执行仓库中与你改动相关的：

```text
Python domain tests
frontend tests（如果 bridge/model 类型影响前端）
Tauri/bridge tests（如果相关）
full npm test / npm check（按 AGENTS.md 授权与规则）
```

如果完整 Windows / hardware 测试无法在环境中进行，要明确指出，而不是模拟通过。

不得声称：

- 浏览器测试等于真实激光器安全验收
- Mock driver 等于真实设备验证
- Build pass 等于测试物理结果未改变

---

# 十三、最终交付格式

完成后给出一份严格的工程总结：

## A. 当前架构诊断

- 原耦合点
- 为什么需要切分

## B. 新增架构

- RecipeDefinition
- StabilityProfile
- MeasurementPlan
- RetryPolicy
- Capability skeleton
- 依赖方向

## C. 实际迁移内容

逐项说明 Standard / Hold / Aging 哪些逻辑从哪里迁到哪里。

## D. 未迁移内容

说明为什么仍属于 Safety / Device / Process Model。

## E. 行为兼容证据

列出测试以及关键值对比。

## F. 文件变化

逐文件说明。

## G. 剩余耦合

列出仍然存在的 Recipe-specific 分支。

## H. Phase 2 建议

重点评估 `PointRunner` 应该如何切出，但不要在本轮顺手实现，除非 Phase 1 无法安全落地且你能证明必要性。

---

# 十四、最终验收标准

本轮完成后应该满足：

1. Standard / Hold / Aging 都存在明确的强类型 RecipeDefinition。
2. 核心 Recipe 参数可以在无硬件环境下纯测试。
3. 现有测试的电流点、dwell、稳定规则、采集要求没有行为漂移。
4. `StationAutomaticMixin` 中 Recipe ID 特例显著减少，或被集中到单一兼容编译入口。
5. Recipe 不 import 具体设备驱动。
6. Recipe 不直接操作 Persistence / Safety / UI。
7. JSONL point-commit invariant 保持。
8. bridge contract 不变。
9. 没有新增产品运行依赖。
10. 所有相关测试通过。

如果发现实现上述目标必须触碰硬件安全逻辑，请停止扩大修改，保留清晰边界，并在最终报告中给出 Phase 2 方案，而不是冒险重写。

开始时先输出一份简短的“当前代码实际结构与计划偏差”分析，然后直接实施 Phase 0 + Phase 1，不要只停留在建议。