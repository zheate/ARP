# ARP 测试平台架构重构方案：Recipe / Process Model / Safety / Persistence 分层

## 1. 目标

将 ARP 从“自动测试流程集中在单个运行协调器中的测试程序”演进为“可扩展、可追溯、可恢复、具有明确安全边界的工业测试平台”。

本方案不是大爆炸重写，也不是更换设备驱动、测量算法或 UI。核心目标是明确并强制以下边界：

- **Recipe / Test Procedure**：描述“这个产品要怎么测”。
- **Process Model / Run Manager**：描述“任何测试都必须怎样被安全、可追溯、可恢复地执行”。
- **Safety Kernel**：维护不可被 Recipe 绕过的硬安全约束和安全下电流程。
- **Measurement Services**：提供 Power / Spectrum / PD / Stability 等能力。
- **Calculation / Specification**：把原始测量、派生指标和 Pass/Fail 分离。
- **Persistence / Event Log**：保存 Session、Point、Attempt、Raw Fact、Outcome，而不是只围绕 Excel。
- **Device Adapters**：封装 TDK、CH341、功率计、Ocean、NI-DAQ 等设备差异。

最终原则：

> 换产品，不换平台；换 Recipe，不改安全层；换设备，不改 Recipe；换规格，不改原始测量；换报告格式，不改测试步骤。

---

## 2. 当前代码基础与主要耦合点

ARP 已经有较好的基础，不建议推倒重来：

- `combined_test/automation.py` 已包含 `AutomaticTestOrchestrator`、`AutomaticTestState`、电流计划、Ramp 计划、稳定性计时等相对纯的领域逻辑。
- `combined_test/record_store.py` 已具备 `TestSession`、`MeasurementAttempt`、`AttemptValidity`、`SessionStatus` 与 JSONL journal 体系。
- `combined_test/session_journal.py` 已承担逐会话持久化基础能力。
- `tauri_bridge/station_runtime.py` 已把设备、实时读数、自动测试状态、PD 状态等集中在 Station Runtime。

当前最大耦合点是 `tauri_bridge/station_automatic.py`：它同时知道 Recipe 特例、稳定性参数、硬件访问、暂停/重试、安全下电、逐秒采样、JSONL/Excel、导出队列和测试状态推进。

这不是错误实现，而是已经达到应该进一步分层的规模。

---

## 3. 目标架构

```text
React UI
   │
   ▼
Process Model / Run Manager
   │
   ├─ Preflight
   ├─ Run Manifest / Session
   ├─ Lifecycle
   ├─ Pause / Abort / Resume
   ├─ Checkpoint / Recovery
   ├─ Event Log
   ├─ Safe Completion
   └─ Persistence Contract
   │
   ▼
Recipe Engine
   │
   ├─ Current Plan
   ├─ Hold / Dwell
   ├─ Stability Requirements
   ├─ Measurement Plan
   ├─ Retry Policy
   └─ Specification Reference
   │
   ├──────────────┬───────────────┐
   ▼              ▼               ▼
Measurement   Calculation     Safety Kernel
Services      Services
   │              │               │
   └──────────────┴───────┬───────┘
                          ▼
                    Device Adapters
```

依赖方向必须单向：

```text
Recipe -> Execution -> Services -> Device Interfaces
```

禁止出现：

```text
Device driver -> knows recipe == aging
```

或：

```text
Recipe -> calls hardware driver directly
```

---

## 4. Recipe 的职责

Recipe 是**声明式测试意图**，不是任意脚本语言。

Recipe 可以描述：

- Recipe ID / version
- 所需能力（capabilities）
- 电流计划
- 指定 Hold 点
- 每点 dwell
- 是否采功率 / 光谱 / PD
- 稳定性参数
- 需要计算哪些指标
- Retry policy
- Specification reference
- 是否保存逐秒 trace
- 特定测试点额外动作（只能使用平台允许的领域动作）

Recipe 不得控制：

- 设备硬件最大电流
- CH341 最短命令间隔
- 是否执行安全降流
- 是否允许绕过 shutdown verification
- 是否允许关闭 Session Journal
- Fault 后是否继续输出
- 测试时是否允许切换到手动硬件控制
- 设备驱动级重连、安全互锁和资源锁

### 第一阶段实现建议

不要先做 YAML DSL。

先使用强类型 Python：

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
    specification_id: str | None
```

等 Standard / Hold Points / Aging 三类 Recipe 全部稳定后，再评估 JSON 序列化。

---

## 5. Process Model 的职责

Process Model 不关心当前是 5 A、10 A，或正在测 PIB 还是 PD。

它只关心测试是否：

- 合法开始
- 具备所有必需设备与校准条件
- 拥有唯一 Run / Session ID
- 已冻结本次配置快照
- 每个测试点已经持久化后才允许继续
- 可安全 Pause / Retry / Abort
- Fault 时能进入不可绕过的安全路径
- 结束后完成安全下电确认
- 结果与结束原因完整落盘
- 崩溃后可以恢复记录，但不能自动恢复激光输出

建议生命周期：

```text
CREATED
  -> PREFLIGHT
  -> READY
  -> ARMED
  -> RUNNING
  -> STOPPING
  -> SAFE
  -> PERSISTING
  -> COMPLETED
```

异常路径：

```text
RUNNING -> PAUSED -> RUNNING
RUNNING -> FAULT -> SAFE_SHUTDOWN -> ABORTED
```

### Process Invariants

必须成为代码级 invariant：

1. `Point N` 未成功 commit 到 durable journal 前，不能进入 `Point N+1`。
2. Recipe 不能直接调用设备驱动。
3. Recipe 不能绕过 Safety Kernel。
4. Fault / Abort / Exit 路径不得依赖 Excel 是否成功生成。
5. Crash Recovery 只能恢复 Session / 数据 / 待执行计划，不能自动恢复硬件输出。
6. 安全下电成功必须区分：命令已发出、当前回读确认、输出 OFF 确认、最终 shutdown confirmed。

---

## 6. Safety Kernel

Safety Kernel 负责平台级不可覆盖规则，例如：

- Station 最大允许电流
- 各设备 command spacing
- Safe ramp-down
- Fault latch
- Emergency stop
- Pause timeout 后的降流
- 输出 OFF 验证
- 设备断连后的安全行为
- Manual / Automatic 互斥
- Command generation / stale command rejection

Recipe 只能提出目标，例如：

```text
Target current = 20 A
```

Safety Kernel 决定该目标是否允许，并由 Device Adapter 执行。

不建议对非法 Recipe 静默修改。优先在 Preflight 明确拒绝并说明原因。

---

## 7. Capability 模型

Recipe 应声明“需要什么能力”，而不是“必须是什么品牌设备”。

例如：

```text
Recipe requires:
- controllable_current_source
- optical_power_measurement
- spectrum_acquisition
- verified_current_readback
```

Station Capability：

```text
TDK:
- controllable_current_source
- verified_current_readback
- voltage_readback

CH341:
- controllable_current_source
```

如果 Recipe 要求 `verified_current_readback` 而当前是 CH341：

```text
PREFLIGHT = NOT READY
reason = Recipe requires verified current readback
```

这样以后新增设备型号时，不需要在 Recipe 中写品牌分支。

---

## 8. Measurement / Calculation / Specification 分离

### Measurement

保存真实测量事实：

```text
value
unit
sampled_at
quality
source
```

例如：

```text
FWHM = 4.72 nm
quality = valid
```

### Calculation

平台算法负责从 Raw Data 生成：

- Efficiency
- Peak wavelength
- Centroid
- FWHM
- PIB
- SMSR
- Stability metrics

算法实现属于平台；Recipe 只配置允许配置的参数。

### Specification

规格独立版本化：

```text
spec_id = LD-976-100W
revision = F
FWHM <= 5.0 nm
PIB >= 90 %
```

### Verdict

```text
measurement = 4.72 nm
spec = <= 5.00 nm
verdict = PASS
margin = 0.28 nm
```

Raw Measurement 与 Verdict 必须分离。以后规格变化时，可对历史原始结果重新判定，而不修改历史测量事实。

---

## 9. PointRunner

建议提取单个测试点执行器：

```text
PointRunner
  1. validate target
  2. ramp current
  3. wait power stability
  4. wait spectrum stability
  5. acquire measurement snapshot
  6. calculate metrics
  7. quality gate
  8. commit raw facts / attempt
  9. calculate specification verdict
 10. return PointResult
```

PointRunner 不负责：

- 整个 Session 生命周期
- 下一台 SN
- Excel 报告生成
- UI 页面状态
- 软件退出

这样 Standard / Hold / Aging 都能复用同一个 PointRunner 或其少量领域扩展。

---

## 10. Standard / Hold / Aging 的迁移目标

不再把三者当成三个独立大流程。

### Standard

- range current plan
- Power + Spectrum
- standard stability profile
- no long trace

### Hold Points

- custom current plan
- Power + Spectrum
- per-point dwell
- optional TDK 1 Hz trace

### Aging

- controlled ramp
- long hold
- periodic power samples
- spectrum optional

三者共享：

- Process Model
- Preflight
- Safety Kernel
- RecordStore
- Event Log
- Pause / Abort
- Safe Shutdown
- Completion
- Crash Recovery

---

## 11. 建议目录结构

```text
combined_test/
    recipes/
        definitions.py
        standard.py
        hold_points.py
        aging.py

    execution/
        engine.py
        point_runner.py
        run_context.py

    process/
        run_manager.py
        preflight.py
        lifecycle.py
        recovery.py

    safety/
        interlocks.py
        shutdown.py
        limits.py

    measurements/
        power.py
        spectrum.py
        pd.py
        stability.py

    calculations/
        spectrum.py
        pib.py
        efficiency.py

    records/
        session.py
        events.py
        store.py
        export.py

    devices/
        interfaces.py
        power_supply.py
        power_meter.py
        spectrometer.py
        pd.py
```

第一阶段不要求一次性移动所有旧文件。可以先建立新模块，并让旧入口调用新抽象。

---

## 12. 迁移策略：禁止大爆炸重构

### Phase 0 — Characterization / Safety Net

在改结构前：

- 补足 Standard / Hold / Aging 当前行为测试。
- 固化关键安全不变量测试。
- 固化 JSONL / Attempt / Retry / Shutdown / Export 的现有行为。
- 记录当前公开 bridge payload，不允许无意改变协议。

### Phase 1 — `RecipeDefinition`

目标：只把“Recipe 特例”从 `station_automatic.py` 移出。

工作项：

- 新建 `recipes/definitions.py`。
- 把 Standard / Hold / Aging 编译为 `RecipeDefinition`。
- 把 `selectedScriptId == ...` 的稳定性、dwell、trace 等分支逐步迁入 Recipe。
- 现有 `StationAutomaticMixin` 继续执行，不改设备控制。

验收：

- 三种现有测试的电流点、dwell、稳定参数、采集内容和最终结果与当前版本一致。

### Phase 2 — `PointRunner`

目标：抽离“一个测试点”的执行生命周期。

工作项：

- 建立 `execution/point_runner.py`。
- 把 Ramp / Stable / Acquire / Validate / Commit 的点级逻辑抽出。
- 通过接口调用现有 StationRuntime，而不是直接改设备驱动。

验收：

- Retry / Saturated / Weak Signal / Timeout / Persistence failure 行为不变。
- Point commit invariant 保持。

### Phase 3 — Process Model / RunManager

目标：把 Session 生命周期从 Recipe 执行中剥离。

工作项：

- Preflight
- Run Manifest
- Start / Pause / Resume / Abort
- Fault -> Safe Shutdown
- Completion
- Event log
- Crash recovery descriptor

验收：

- 任何 Recipe 都走同一个安全结束路径。
- Excel 失败不阻塞安全下电。
- Crash 恢复不会自动重新上电。

### Phase 4 — Capability + Specification

目标：移除品牌/设备型号向 Recipe 泄漏。

- Capability registry
- Recipe capability requirement
- Preflight compatibility check
- 独立 Specification version / Verdict

### Phase 5 — 清理旧耦合

只有前四阶段稳定后：

- 缩小 `station_automatic.py`
- 移除旧 Recipe ID 分支
- 合并重复稳定性判断
- 再评估目录迁移

---

## 13. 必须保持不变的边界

在本重构中默认禁止：

- 修改 TDK / CH341 / Power Meter / Ocean / NI-DAQ 协议行为
- 修改电流/电压/功率/光谱单位与数值精度
- 修改 PIB / FWHM / SMSR / Efficiency 算法结果
- 修改现有 JSON bridge 方法名或 payload（除非有明确兼容版本）
- 修改现有安全降流和 command timing 规则的物理意义
- 自动恢复硬件输出
- 将 Excel 重新变成自动测试的同步状态机边界
- 为追求目录漂亮进行无收益的大规模 rename

---

## 14. 测试策略

每个 Phase 必须至少覆盖：

1. **纯领域测试**：Recipe compile、Current plan、Capability、Spec verdict。
2. **状态机测试**：Run lifecycle、Pause、Retry、Abort、Fault。
3. **安全测试**：max current、command interval、shutdown confirmation、stale command。
4. **持久化测试**：point commit、invalid attempt、retry history、journal failure。
5. **兼容测试**：旧配置、旧 JSONL、现有 bridge contract。
6. **回归测试**：Standard / Hold / Aging 最终输出与当前主线一致。

测试尽量使用 Fake / Simulation adapter，不要求真实硬件。

---

## 15. 完成标准

重构完成后，应满足：

- 新增一个普通产品 Recipe 不需要修改 `station_automatic.py`。
- 新增一个 Specification 不需要修改 Measurement / Device 代码。
- 新增一个满足既有 Capability 的电源适配器不需要修改 Recipe。
- 所有 Recipe 共享同一个 Session / Safety / Persistence 生命周期。
- Recipe 不能绕过最大电流、command timing、safe shutdown 与 durable journal。
- Pass/Fail 与原始 Measurement 独立存储和版本化。
- Crash 后能识别未完成 Session，并提供安全的数据恢复入口，但不会自动恢复激光输出。
- Standard / Hold / Aging 行为与重构前保持功能等价，除非另有明确变更说明。

---

## 16. 首个实现 PR 建议范围

第一个 PR 只做 **Phase 0 + Phase 1**：

- 增加 characterization tests。
- 建立 `RecipeDefinition`、`StabilityProfile`、`MeasurementPlan`、`RetryPolicy`。
- 将 Standard / Hold / Aging 的 Recipe 特例迁移到定义层。
- 保持 `StationAutomaticMixin` 作为执行器。
- 不移动设备驱动。
- 不更改 bridge contract。
- 不改变结果数值。

这个范围足够验证架构方向，又不会把安全和硬件风险同时引入。