import { busyInteraction } from "@/components/busy-interaction"
import { ControlRow, ControlGrid, ParameterControl } from "@/components/control-layout"
import type { CommandName } from "@/lib/command-contract"
import { DeviceDetectButton } from "@/components/device-detect-button"
import { DialogPortal } from "@/components/dialog-portal"
import { Field } from "@/components/field"
import { NativeSelect } from "@/components/native-select"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { BUILTIN_TEST_SCRIPT_OPTIONS, UpdateConfig, applyTestScript } from "@/features/configuration/model"
import { NumberField, NumberInput, RunCommand, StatusDot, formatDuration, formatMeasurementAge, formatNumber, formatTrimmedNumber } from "@/features/shared/controls"
import { type AppConfiguration, type BackendSnapshot, type DeviceSnapshot, type PowerMeterCoefficientLibrary, type PowerMeterCoefficientRevision } from "@/lib/backend"
import { powerMeterCoefficientLibraryForResource, powerMeterIdentityForResource, powerMeterResourceValue } from "@/lib/power-meter-resource"
import { ChevronRight, Gauge, Play, Plus, Power, Radio, RotateCcw, Save, Settings, SlidersHorizontal, Square, X, type LucideIcon } from "lucide-react"
import { Tooltip } from "radix-ui"
import { memo, useEffect, useRef, useState } from "react"

export const I2C_CURRENT_LIMIT_A = 20

export const MAX_CUSTOM_CURRENT_POINTS = 50

export const defaultSpectrometerResourceLabel = "请选择光谱仪"

export function Ch341PortSelector({
  config,
  snapshot,
  active,
  pending,
  layout = "stacked",
  update,
  onRefresh,
}: {
  config: AppConfiguration
  snapshot: BackendSnapshot | null
  active: boolean
  pending: boolean
  layout?: "stacked" | "inline"
  update: UpdateConfig
  onRefresh: () => void | Promise<unknown>
}) {
  const psu = snapshot?.devices?.powerSupply
  const isConnected = Boolean(psu?.connected && config.powerSupplyKind === "ch341")
  const detecting = Boolean(psu?.detecting)
  const ch341 = psu?.ch341
  const discoveryState = ch341?.discoveryState ?? "not_scanned"
  const ports = ch341?.ports ?? []

  const selectedLoc = config.ch341SelectedLocationPath || ""
  const currentPort = ports.find((p) => p.locationPath === selectedLoc)

  const handleSelectPort = (loc: string) => {
    update("ch341SelectedLocationPath", loc)
  }

  return (
    <div className="flex flex-col gap-2">
      <Field label="CH341 设备" layout={layout}>
        <ControlRow>
          <NativeSelect
            disabled={!active || pending || detecting || isConnected}
            value={currentPort ? selectedLoc : ""}
            onChange={handleSelectPort}
          >
            {discoveryState === "not_scanned" && <option value="">请先点击识别 CH341</option>}
            {discoveryState === "empty" && <option value="">未检测到 CH341 设备</option>}
            {discoveryState === "error" && <option value="">扫描失败：{ch341?.error || "驱动异常"}</option>}
            {discoveryState === "ready" && ports.length === 0 && <option value="">无可用端口</option>}
            {discoveryState === "ready" && !currentPort && <option value="">请选择 CH341 设备</option>}
            {ports.map((port) => {
              return (
                <option key={port.locationPath} value={port.locationPath}>
                  Device {port.deviceIndex} · 可用
                </option>
              )
            })}
          </NativeSelect>
          <DeviceDetectButton
            detecting={detecting}
            disabled={!active || pending || detecting || isConnected}
            onClick={onRefresh}
          />
        </ControlRow>
      </Field>
    </div>
  )
}

export type DeviceSettingsKind = "powerSupply" | "powerMeter" | "spectrometer"

export const DeviceCard = memo(function DeviceCard({ title, icon: Icon, device, model, details, summary, visibleSummary, disabled = false, onOpenSettings }: { title: string; icon: LucideIcon; device?: DeviceSnapshot; model: string; details: Array<[string, string]>; summary?: string; visibleSummary?: string; disabled?: boolean; onOpenSettings: () => void }) {
  const connectionLabel = device?.detecting ? "识别中"
    : device?.state === "connected" ? "在线"
    : device?.state === "connecting" ? "连接中"
    : device?.state === "error" ? "异常" : "未连接"
  const activityLabel = device?.outputEnabled ? "输出中"
    : device?.running ? (title === "功率计" ? "测量中" : "采集中") : ""
  return <Tooltip.Provider delayDuration={250}>
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <button
          aria-disabled={disabled}
          aria-label={`${title}信息${disabled ? "，测试期间设置已锁定" : "，点击打开设置"}`}
          className="device-rail-item"
          onClick={() => { if (!disabled) onOpenSettings() }}
          type="button"
        >
          <span className="device-rail-item__icon" data-active={device?.outputEnabled}><Icon className="size-4" /></span>
          <span className="device-rail-item__copy"><span><strong>{title}</strong><small title={model}>{model}</small></span><span className="device-rail-item__state"><StatusDot state={device?.detecting ? "connecting" : device?.state} />{connectionLabel}{activityLabel && <> · {activityLabel}</>}</span><span aria-hidden={!visibleSummary} className={`device-rail-item__summary ${visibleSummary ? "" : "is-empty"}`} title={visibleSummary}>{visibleSummary || "占位"}</span></span>
          <ChevronRight className="device-rail-item__chevron size-4" />
        </button>
      </Tooltip.Trigger>
      <Tooltip.Portal><Tooltip.Content className="device-info-tooltip" side="bottom" sideOffset={8} collisionPadding={12}>
        <strong>{title} · {model}</strong>
        <dl>{details.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || "未配置"}</dd></div>)}</dl>
        {device?.state === "error" && <p className="text-destructive">{device.detail}</p>}
        <p>{disabled ? "测试期间仅查看参数" : "点击设备栏进入设置"}</p>
        <span className="sr-only">{summary}</span>
        <Tooltip.Arrow className="device-info-tooltip__arrow" />
      </Tooltip.Content></Tooltip.Portal>
    </Tooltip.Root>
  </Tooltip.Provider>
}, (previous, next) => (
  previous.title === next.title
  && previous.icon === next.icon
  && previous.model === next.model
  && previous.summary === next.summary
  && previous.visibleSummary === next.visibleSummary
  && previous.disabled === next.disabled
  && previous.onOpenSettings === next.onOpenSettings
  && previous.device?.state === next.device?.state
  && previous.device?.detail === next.device?.detail
  && previous.device?.detecting === next.device?.detecting
  && previous.device?.running === next.device?.running
  && previous.device?.outputEnabled === next.device?.outputEnabled
  && previous.details.length === next.details.length
  && previous.details.every(([label, value], index) => label === next.details[index][0] && value === next.details[index][1])
))

export function CustomCurrentPlanEditor({ config, update, disabled, maximumCurrentA }: {
  config: AppConfiguration
  update: UpdateConfig
  disabled: boolean
  maximumCurrentA?: number
}) {
  const holdCurrentMaximum = Math.min(maximumCurrentA ?? Number.POSITIVE_INFINITY, config.targetCurrentA)
  const updatePoint = (index: number, key: "currentA" | "dwellS", value: number) => {
    const points = config.customCurrentPoints.map((point, pointIndex) => (
      pointIndex === index ? { ...point, [key]: value } : point
    ))
    update("customCurrentPoints", points)
  }
  const addPoint = () => {
    if (config.customCurrentPoints.length >= MAX_CUSTOM_CURRENT_POINTS) return
    const previous = config.customCurrentPoints[config.customCurrentPoints.length - 1]
    const proposedCurrent = (previous?.currentA ?? 0) + 1
    const currentA = Math.max(config.initialCurrentA, Math.min(proposedCurrent, holdCurrentMaximum))
    update("customCurrentPoints", [
      ...config.customCurrentPoints,
      { currentA, dwellS: previous?.dwellS ?? 300 },
    ])
  }
  const removePoint = (index: number) => {
    if (config.customCurrentPoints.length <= 1) return
    update("customCurrentPoints", config.customCurrentPoints.filter((_point, pointIndex) => pointIndex !== index))
  }
  const totalDwellS = config.customCurrentPoints.reduce((total, point) => total + Math.max(0, point.dwellS), 0)

  return (
    <div className="custom-current-plan">
      <div className="custom-current-plan__header">
        <span>顺序</span><span>指定电流 (A)</span><span>停留时间 (s)</span><span aria-hidden="true" />
      </div>
      <div className="custom-current-plan__rows">
        {config.customCurrentPoints.map((point, index) => (
          <div className="custom-current-plan__row" key={index}>
            <span className="custom-current-plan__index">{index + 1}</span>
            <NumberInput aria-label={`第 ${index + 1} 个指定停留点`} disabled={disabled} max={holdCurrentMaximum} min={config.initialCurrentA} onChange={(val) => updatePoint(index, "currentA", val)} step="0.1" value={point.currentA} />
            <NumberInput aria-label={`第 ${index + 1} 个电流点停留时间`} disabled={disabled} min={0.1} onChange={(val) => updatePoint(index, "dwellS", val)} step="1" value={point.dwellS} />
            <button aria-label={`删除第 ${index + 1} 个电流点`} className="custom-current-plan__remove" disabled={disabled || config.customCurrentPoints.length <= 1} onClick={() => removePoint(index)} type="button"><X className="size-4" /></button>
          </div>
        ))}
      </div>
      <div className="custom-current-plan__footer">
        <p>{config.customCurrentPoints.length} 个停留点 · 合计停留 {formatDuration(totalDwellS)}</p>
        <Button disabled={disabled || config.customCurrentPoints.length >= MAX_CUSTOM_CURRENT_POINTS} onClick={addPoint} size="sm" type="button" variant="outline"><Plus className="size-3.5" /><span className="device-settings-button__label">增加停留点</span></Button>
      </div>
      <p className="custom-current-plan__hint">自动测试仍按起始、目标和步长逐点判稳；指定电流点会插入序列，判稳后按停留时间每 1 s 记录完整数据。</p>
    </div>
  )
}

export function TestScriptSettingsForm({ snapshot, config, update, active, pending, onScriptSelect }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean
  onScriptSelect?: (id: string) => void
}) {
  const agingMode = config.selectedScriptId === "aging"
  return (
    <section className="power-settings-section power-settings-section--automatic">
      <div className="power-settings-section-heading">
        <p className="power-settings-section-title">测试脚本参数</p>
        <p className="power-settings-section-description">选择参数脚本，修改升流节拍和安全下电参数后保存</p>
      </div>
      <div className="power-settings-fields">
        <Field label="参数脚本">
          <NativeSelect
            aria-label="参数脚本"
            disabled={!active || pending}
            value={config.selectedScriptId}
            onChange={(scriptId) => {
              if (onScriptSelect) {
                onScriptSelect(scriptId)
              } else {
                const scripts = snapshot?.testScripts ?? BUILTIN_TEST_SCRIPT_OPTIONS
                const script = scripts.find((item) => item.id === scriptId)
                if (script) {
                  const next = applyTestScript(config, script)
                  for (const [key, val] of Object.entries(next)) {
                    update(key as keyof AppConfiguration, val as never)
                  }
                }
              }
            }}
          >
            {(snapshot?.testScripts ?? BUILTIN_TEST_SCRIPT_OPTIONS)
              .filter((script) => !script.powerSupplyKinds || script.powerSupplyKinds.includes(config.powerSupplyKind))
              .map((script) => <option key={script.id} value={script.id}>{script.name}</option>)}
          </NativeSelect>
        </Field>
        <ControlGrid className="power-settings-parameter-grid">
          <NumberField disabled={agingMode} label="起始电流 (A)" value={config.initialCurrentA} onChange={(v) => update("initialCurrentA", v)} max={currentInputMaximum(config, snapshot)} />
          <NumberField disabled={agingMode} label="目标电流 (A)" value={config.targetCurrentA} onChange={(v) => update("targetCurrentA", v)} max={currentInputMaximum(config, snapshot)} />
          <NumberField disabled={agingMode} label="电流间隔 (A)" value={config.currentStepA} onChange={(v) => update("currentStepA", v)} max={currentInputMaximum(config, snapshot)} />
        </ControlGrid>
        {agingMode ? <div className="custom-current-plan">
          <p className="custom-current-plan__hint">CH341 从 2 A 开始，每步增加 2 A；每步等待 1 s，硬件命令保护下实际下发间隔不短于 1.1 s。到达 20 A 后不判稳，立即开始 2 小时老化计时。</p>
        </div> : config.currentPlanMode === "custom" && <CustomCurrentPlanEditor config={config} disabled={pending} maximumCurrentA={currentInputMaximum(config, snapshot)} update={update} />}
        <ControlGrid className="power-settings-parameter-grid power-settings-parameter-grid--safety">
          <NumberField label="单点超时 (s)" value={config.pointTimeoutS} onChange={(v) => update("pointTimeoutS", v)} />
          <NumberField label="下电步长 (A)" value={config.rampDownStepA} onChange={(v) => update("rampDownStepA", v)} max={currentInputMaximum(config, snapshot)} />
          <NumberField label="下电间隔 (s)" value={config.rampDownIntervalS} onChange={(v) => update("rampDownIntervalS", v)} step="0.1" />
        </ControlGrid>
      </div>
    </section>
  )
}

export function PowerSupplySettingsForm({ snapshot, config, update, active, pending, run, onScriptSelect, mode = "auto" }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
  onScriptSelect?: (id: string) => void; mode?: "auto" | "manual"
}) {
  const psu = snapshot?.devices.powerSupply
  const resources = psu?.resources ?? []
  const serialSupply = config.powerSupplyKind !== "ch341"
  const ruSupply = config.powerSupplyKind === "ru"
  const shutdownUnconfirmed = ruSupply && snapshot?.safety.shutdown.requested === true
    && !snapshot.safety.shutdown.confirmed
  const serialLabel = ruSupply ? "VARIED RU" : "TDK"
  const resourceKey = ruSupply ? "ruResource" : "tdkResource"
  const voltageKey = ruSupply ? "ruVoltageV" : "tdkVoltageV"
  const selectedResource = config[resourceKey].trim()
  const agingMode = config.selectedScriptId === "aging"
  return (
    <div className="power-settings-layout" data-mode={mode}>
      <section className="power-settings-section">
        <div className="power-settings-section-heading">
          <p className="power-settings-section-title">{mode === "manual" ? "硬件配置与读取" : "手动控制"}</p>
          <p className="power-settings-section-description">{mode === "manual" ? "选择控制器与硬件资源，并执行状态读取" : "连接设备并执行输出控制与状态读取"}</p>
        </div>
        <div className="power-settings-fields">
          <Field label="控制器"><NativeSelect disabled={!active || pending || psu?.detecting || psu?.connected} value={config.powerSupplyKind} onChange={(v) => updatePowerSupplyKind(config, update, v as AppConfiguration["powerSupplyKind"])}><option value="ch341">CH341 I²C</option><option disabled={agingMode} value="tdk">{agingMode ? "TDK RS232（AU 老化测试不可用）" : "TDK RS232"}</option><option disabled={agingMode} value="ru">{agingMode ? "VARIED RU RS232（AU 老化测试不可用）" : "VARIED RU RS232"}</option></NativeSelect></Field>
          {serialSupply ? <Field label={`${serialLabel} 串口`}>
            <ControlRow>
              <NativeSelect disabled={!active || pending || psu?.detecting || psu?.connected} value={selectedResource} onChange={(value) => update(resourceKey, value)}>
                <option value="">请点击识别电源</option>
                {selectedResource && !resources.includes(selectedResource) && <option value={selectedResource}>{selectedResource}</option>}
                {resources.map((resource) => <option key={resource} value={resource}>{resource}</option>)}
              </NativeSelect>
              <DeviceDetectButton detecting={psu?.detecting} disabled={!active || pending || psu?.detecting || psu?.connected} onClick={() => run("device.refresh", { device: "powerSupply" }, true)} />
            </ControlRow>
          </Field> : (
            <Ch341PortSelector
              config={config}
              snapshot={snapshot}
              active={active}
              pending={pending}
              layout="stacked"
              update={update}
              onRefresh={() => run("device.refresh", { device: "powerSupply" }, true)}
            />
          )}
          {ruSupply && (
            <ControlGrid className="power-settings-value-grid">
              <Field label="波特率"><NativeSelect disabled={!active || pending || psu?.detecting || psu?.connected} value={String(config.ruBaudRate)} onChange={(value) => update("ruBaudRate", Number(value))}>
                {[1200, 2400, 4800, 9600, 14400, 19200, 38400, 43000, 57600, 115200].map((baud) => <option key={baud} value={baud}>{baud}</option>)}
              </NativeSelect></Field>
              <NumberField label="电源地址 (1–255)" value={config.ruAddress} onChange={(value) => update("ruAddress", value)} min={1} max={255} step="1" disabled={!active || pending || psu?.detecting || psu?.connected} />
            </ControlGrid>
          )}
          {mode === "manual" ? (
            <>
              <ControlGrid className="power-settings-action-grid">
                <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputVoltage" })} size="sm" variant="outline"><span className="device-settings-button__label">读取输出电压</span></Button>
                <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputCurrent" })} size="sm" variant="outline"><span className="device-settings-button__label">读取输出电流</span></Button>
                {config.powerSupplyKind === "ch341" && (
                  <>
                    <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "inputVoltage" })} size="sm" variant="outline"><span className="device-settings-button__label">读取输入电压</span></Button>
                    <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "temperature" })} size="sm" variant="outline"><span className="device-settings-button__label">读取模块温度</span></Button>
                  </>
                )}
              </ControlGrid>
            </>
          ) : (
            <>
              {serialSupply ? <>
                <ControlGrid className="power-settings-action-grid">
                  <Button aria-disabled={psu?.detecting || undefined} disabled={!active || pending} onClick={() => void run(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)} variant={psu?.connected ? "outline" : "default"}><span className="device-settings-button__label">{psu?.connected ? "安全断开" : `连接 ${serialLabel}`}</span></Button>
                  <Button className={psu?.outputEnabled ? "btn-power-active" : ""} disabled={!active || pending || !psu?.connected || (shutdownUnconfirmed && !psu?.outputEnabled)} onClick={() => void run("powerSupply.setOutput", { enabled: !psu?.outputEnabled })} variant={psu?.outputEnabled ? "destructive" : "outline"}>{psu?.outputEnabled && <span className="size-2 rounded-full bg-white animate-pulse shrink-0 mr-1.5 inline-block" />}<span className="device-settings-button__label">{psu?.outputEnabled ? "关闭输出" : "开启输出"}</span></Button>
                </ControlGrid>
                <ControlGrid className="power-settings-value-grid">
                  <ParameterControl>
                    <NumberField label="输出电压 (V)" value={config[voltageKey]} onChange={(v) => update(voltageKey, v)} step="0.1" />
                    <Button disabled={!active || pending || !psu?.connected || shutdownUnconfirmed} onClick={() => void run("powerSupply.setVoltage", { voltageV: config[voltageKey] }, true)} variant="outline"><span className="device-settings-button__label">设置电压</span></Button>
                  </ParameterControl>
                  <ParameterControl>
                    <NumberField label="设定电流 (A)" value={config.setCurrentA} onChange={(v) => update("setCurrentA", v)} max={currentInputMaximum(config, snapshot)} step="0.1" />
                    <Button disabled={!active || pending || !psu?.connected || (shutdownUnconfirmed && config.setCurrentA > 0)} onClick={() => void run("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)} variant="outline"><span className="device-settings-button__label">设置电流</span></Button>
                  </ParameterControl>
                </ControlGrid>
              </> : <>
                <div className="power-settings-connection">
                {(() => {
                  const selectedLoc = config.ch341SelectedLocationPath
                  const port = snapshot?.devices?.powerSupply?.ch341?.ports?.find((p) => p.locationPath === selectedLoc)
                  const isCh341ReadyToConnect = Boolean(
                    port?.online &&
                    snapshot?.devices?.powerSupply?.ch341?.discoveryState === "ready"
                  )
                  const btnText = psu?.connected
                    ? "安全断开"
                    : port?.online
                    ? `连接 Device ${port.deviceIndex}`
                    : "连接电源"
                  return (
                    <Button
                      aria-disabled={psu?.detecting || undefined} disabled={!active || pending || (!psu?.connected && !isCh341ReadyToConnect)}
                      onClick={() => void run(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)}
                      variant={psu?.connected ? "outline" : "default"}
                    >
                      <span className="device-settings-button__label">{btnText}</span>
                    </Button>
                  )
                })()}
                </div>
                <Field label="设定电流 (A)">
                  <ControlRow className="gap-2.5">
                    <NumberInput value={config.setCurrentA} onChange={(v) => update("setCurrentA", v)} max={currentInputMaximum(config, snapshot)} step="0.1" />
                    <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)}><span className="device-settings-button__label">设置电流</span></Button>
                  </ControlRow>
                </Field>
                <ControlGrid className="power-settings-action-grid">
                <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputVoltage" })} size="sm" variant="outline"><span className="device-settings-button__label">读取输出电压</span></Button>
                <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputCurrent" })} size="sm" variant="outline"><span className="device-settings-button__label">读取输出电流</span></Button>
                <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "inputVoltage" })} size="sm" variant="outline"><span className="device-settings-button__label">读取输入电压</span></Button>
                <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "temperature" })} size="sm" variant="outline"><span className="device-settings-button__label">读取模块温度</span></Button>
                <p className="col-span-full text-xs text-muted-foreground">
                  回读：输入 {formatNumber(psu?.inputVoltageV, " V", 2)} · 输出 {formatNumber(psu?.outputVoltageV, " V", 2)} · 电流 {formatNumber(psu?.outputCurrentA, " A", 2)} · 温度 {formatNumber(psu?.temperatureC, " °C", 2)}
                </p>
                </ControlGrid>
              </>}
            </>
          )}
        </div>
      </section>
      {mode === "auto" && <TestScriptSettingsForm snapshot={snapshot} config={config} update={update} active={active} pending={pending} onScriptSelect={onScriptSelect} />}
    </div>
  )
}

export type PowerMeterProfileDraftIntent = "new" | "copy" | "save"

export interface PowerMeterProfileDraft {
  name: string
  softwareGain: number
  wavelengthNm: number
  notes: string
  operator: string
  reason: string
}

export function selectedPowerMeterRevision(library: PowerMeterCoefficientLibrary | null): PowerMeterCoefficientRevision | null {
  if (!library) return null
  const profile = library.profiles.find((item) => item.id === library.selectedProfileId)
  return profile?.versions.find((revision) => revision.version === library.selectedVersion)
    ?? profile?.versions[profile.versions.length - 1]
    ?? null
}

export function formatProfileTimestamp(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value || "时间未知" : date.toLocaleString("zh-CN", { hour12: false })
}

export function PowerMeterProfileEditor({ snapshot, config, active, pending, run, compact = false, onManage }: {
  snapshot: BackendSnapshot | null
  config: AppConfiguration
  active: boolean
  pending: boolean
  run: RunCommand
  compact?: boolean
  onManage?: () => void
}) {
  const meter = snapshot?.devices.powerMeter
  const library = powerMeterCoefficientLibraryForResource(
    meter,
    config.powerMeterResource,
    snapshot?.configuration?.powerMeterResource,
  )
  const identity = powerMeterIdentityForResource(
    meter,
    config.powerMeterResource,
    snapshot?.configuration?.powerMeterResource,
    { driverKind: config.powerMeterDriverKind, serialNumber: config.powerMeterSerialNumber },
  )
  const { driverKind, serialNumber } = identity
  const selectedProfile = library?.profiles.find((profile) => profile.id === library.selectedProfileId) ?? null
  const selectedRevision = selectedPowerMeterRevision(library)
  const [draftIntent, setDraftIntent] = useState<PowerMeterProfileDraftIntent | null>(null)
  const [draft, setDraft] = useState<PowerMeterProfileDraft>({
    name: "",
    softwareGain: config.softwareGain,
    wavelengthNm: config.powerMeterWavelengthNm,
    notes: "",
    operator: "",
    reason: "",
  })
  const [draftTarget, setDraftTarget] = useState<{ profileId: string; expectedVersion: number } | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const probeIdentity = `${powerMeterResourceValue(config.powerMeterResource)}:${driverKind}:${serialNumber}`
  const draftTargetProfile = draftTarget ? library?.profiles.find((profile) => profile.id === draftTarget.profileId) : null
  const draftTargetLatestVersion = draftTargetProfile?.versions[draftTargetProfile.versions.length - 1]?.version
  const draftStale = Boolean(
    draftIntent === "save"
    && draftTarget
    && (draftTargetLatestVersion === undefined || draftTargetLatestVersion !== draftTarget.expectedVersion),
  )
  const automaticActive = Boolean(snapshot && !["idle", "completed"].includes(snapshot.automaticTest.state))
  const pendingManualPoints = snapshot?.manualTest?.pendingCount ?? 0
  const unavailable = !active || Boolean(meter?.detecting || meter?.running) || automaticActive || pendingManualPoints > 0
  const mutationLocked = unavailable || pending || busy
  const profileCommandLock = useRef(false)
  const lockReason = !serialNumber
    ? "请先识别并选择具有探头 SN 的功率计"
    : meter?.running
      ? "请先停止功率采集"
      : automaticActive
        ? "自动测试进行中，不能切换或修改方案"
        : pendingManualPoints > 0
          ? `请先保存 ${pendingManualPoints} 个未保存的手动测试点`
          : ""

  useEffect(() => {
    setDraftIntent(null)
    setDraftTarget(null)
    setHistoryOpen(false)
    setError("")
  }, [probeIdentity])

  const beginDraft = (intent: PowerMeterProfileDraftIntent) => {
    const source = selectedRevision
    setDraft({
      name: intent === "new" ? "" : intent === "copy" ? `${source?.name ?? "系数方案"} 副本` : source?.name ?? "",
      softwareGain: source?.softwareGain ?? config.softwareGain,
      wavelengthNm: source?.wavelengthNm ?? config.powerMeterWavelengthNm,
      notes: source?.notes ?? "",
      operator: "",
      reason: "",
    })
    setHistoryOpen(false)
    setError("")
    setDraftTarget(intent === "save" && selectedProfile && selectedRevision
      ? { profileId: selectedProfile.id, expectedVersion: selectedProfile.versions[selectedProfile.versions.length - 1]?.version ?? selectedRevision.version }
      : null)
    setDraftIntent(intent)
  }

  const perform = async (method: Extract<CommandName, `powerMeter.profile.${string}`>, params: Record<string, unknown>) => {
    if (mutationLocked || profileCommandLock.current || !driverKind || !serialNumber) return
    profileCommandLock.current = true
    setBusy(true)
    try {
      const result = await run(method, { driverKind, serialNumber, ...params }, true)
      if (!result) throw new Error("操作未执行，请等待当前设备命令完成后重试")
      setError("")
      setDraftIntent(null)
      setDraftTarget(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      throw reason
    } finally {
      profileCommandLock.current = false
      setBusy(false)
    }
  }

  const selectProfile = async (profileId: string) => {
    if (!library || profileId === library.selectedProfileId) return
    const profile = library.profiles.find((item) => item.id === profileId)
    const latest = profile?.versions[profile.versions.length - 1]
    if (!latest) return
    await perform("powerMeter.profile.select", { profileId, expectedVersion: latest.version }).catch(() => undefined)
  }

  const saveDraft = async () => {
    if (!draftIntent) return
    if (!draft.name.trim()) {
      setError("请填写方案名称")
      return
    }
    if (![draft.softwareGain, draft.wavelengthNm].every((value) => Number.isFinite(value) && value > 0)) {
      setError("功率系数和校准波长必须是大于零的数值")
      return
    }
    const values = {
      name: draft.name.trim(),
      softwareGain: draft.softwareGain,
      wavelengthNm: draft.wavelengthNm,
      notes: draft.notes.trim(),
      operator: draft.operator.trim(),
      reason: draft.reason.trim(),
    }
    if (draftIntent === "save") {
      if (!draftTarget) {
        setError("原方案版本已不可用，请取消后重新编辑")
        return
      }
      await perform("powerMeter.profile.save", {
        profileId: draftTarget.profileId,
        expectedVersion: draftTarget.expectedVersion,
        ...values,
      }).catch(() => undefined)
      return
    }
    await perform("powerMeter.profile.create", values).catch(() => undefined)
  }

  const restoreRevision = async (revision: PowerMeterCoefficientRevision) => {
    if (!selectedProfile) return
    const latest = selectedProfile.versions[selectedProfile.versions.length - 1]
    if (!latest || revision.version === latest.version) return
    await perform("powerMeter.profile.restore", {
      profileId: selectedProfile.id,
      expectedVersion: latest.version,
      restoreVersion: revision.version,
      reason: `恢复 v${revision.version}`,
    }).catch(() => undefined)
  }

  const updateDraft = <K extends keyof PowerMeterProfileDraft>(key: K, value: PowerMeterProfileDraft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  if (onManage) return <>
    <Field className="manual-device-inline-field" label="当前方案" layout="inline">
      <ControlRow>
        <NativeSelect ariaLabel="手动测试系数方案" disabled={mutationLocked || !serialNumber || !library?.profiles.length} value={library?.selectedProfileId ?? ""} onChange={(value) => void selectProfile(value)}>
          {library?.profiles.length ? library.profiles.map((profile) => {
            const revision = profile.versions[profile.versions.length - 1]
            return <option key={profile.id} value={profile.id}>{revision?.name ?? "未命名方案"} · v{revision?.version ?? 0}</option>
          }) : <option value="">{serialNumber ? "暂无可用方案" : "未识别探头"}</option>}
        </NativeSelect>
        <Button disabled={!active || pending} onClick={onManage} variant="outline"><SlidersHorizontal className="size-3.5" />方案管理</Button>
      </ControlRow>
    </Field>
    {error && <p className="text-xs text-destructive" role="alert">{error}</p>}
  </>

  return (
    <section aria-busy={busy} {...busyInteraction(() => pending || profileCommandLock.current)} className={`power-meter-profile ${compact ? "power-meter-profile--compact" : ""}`}>
      <div className="power-meter-profile__heading">
        <div>
          <p className="power-meter-profile__title">系数方案</p>
          <p className="power-meter-profile__identity">{serialNumber ? `${driverKind || "未知驱动"} · 探头 SN ${serialNumber}` : "当前资源没有可用的探头身份"}</p>
        </div>
        {selectedRevision && <Badge variant="outline">v{selectedRevision.version}</Badge>}
      </div>

      {library?.profiles.length ? <Field label="当前方案">
        <NativeSelect
          ariaLabel="系数方案"
          disabled={unavailable || draftIntent !== null}
          value={library.selectedProfileId}
          onChange={(value) => void selectProfile(value)}
        >
          {library.profiles.map((profile) => {
            const revision = profile.versions[profile.versions.length - 1]
            return <option key={profile.id} value={profile.id}>{revision?.name ?? "未命名方案"} · v{revision?.version ?? 0}</option>
          })}
        </NativeSelect>
      </Field> : <p className="power-meter-profile__empty">此探头尚无可选方案，可新建第一套方案。</p>}

      {selectedRevision && !draftIntent && <div className="power-meter-profile__summary">
        <dl>
          <div><dt>功率系数</dt><dd>× {formatTrimmedNumber(selectedRevision.softwareGain, 6)}</dd></div>
          <div><dt>校准波长</dt><dd>{selectedRevision.wavelengthNm == null ? "待补充" : `${formatTrimmedNumber(selectedRevision.wavelengthNm, 3)} nm`}</dd></div>
        </dl>
        {selectedRevision.notes && <p>{selectedRevision.notes}</p>}
        {selectedRevision.wavelengthNm == null && <p className="power-meter-profile__warning">旧方案没有校准波长，首次使用前请保存修改并补充波长。</p>}
      </div>}

      {draftIntent && <div className="power-meter-profile__draft">
        <div className="power-meter-profile__draft-title">
          <strong>{draftIntent === "save" ? "修改当前方案" : draftIntent === "copy" ? "另存为新方案" : "新建方案"}</strong>
          <span>保存前不会改变当前测量参数</span>
        </div>
        <Field label="方案名称"><Input disabled={unavailable} maxLength={60} value={draft.name} onChange={(event) => updateDraft("name", event.target.value)} /></Field>
        <ControlGrid className="device-settings-field-grid">
          <NumberField disabled={unavailable} label="功率系数" min={0.000001} step="0.001" value={draft.softwareGain} onChange={(value) => updateDraft("softwareGain", value)} />
          <NumberField disabled={unavailable} label="校准波长 (nm)" min={0.000001} step="0.1" value={draft.wavelengthNm} onChange={(value) => updateDraft("wavelengthNm", value)} />
        </ControlGrid>
        <Field label="条件备注"><textarea className="power-meter-profile__textarea" disabled={unavailable} maxLength={300} rows={2} value={draft.notes} onChange={(event) => updateDraft("notes", event.target.value)} /></Field>
        <ControlGrid className="device-settings-field-grid">
          <Field label="修改人（可选）"><Input disabled={unavailable} maxLength={60} value={draft.operator} onChange={(event) => updateDraft("operator", event.target.value)} /></Field>
          <Field label="修改原因（可选）"><Input disabled={unavailable} maxLength={120} value={draft.reason} onChange={(event) => updateDraft("reason", event.target.value)} /></Field>
        </ControlGrid>
        <div className="power-meter-profile__draft-actions">
          <Button aria-disabled={busy} onClick={() => { setDraftIntent(null); setDraftTarget(null); setError("") }} size="sm" variant="outline">取消</Button>
          <Button className="w-28" aria-busy={busy} disabled={unavailable} onClick={() => void saveDraft()} size="sm"><Save className="size-3.5" />{busy ? "保存中…" : "保存方案"}</Button>
        </div>
        {draftStale && <p className="power-meter-profile__message is-warning">此方案在草稿打开后已有新版本。草稿仍保留，保存时会由后端阻止覆盖；建议取消后查看最新版本。</p>}
      </div>}

      {!draftIntent && <div className="power-meter-profile__actions">
        <Button disabled={unavailable || !serialNumber} onClick={() => beginDraft("new")} size="sm" variant="outline"><Plus className="size-3.5" />新建</Button>
        <Button disabled={unavailable || !selectedRevision} onClick={() => beginDraft("copy")} size="sm" variant="outline">另存为</Button>
        <Button disabled={unavailable || !selectedRevision} onClick={() => beginDraft("save")} size="sm" variant="outline">保存修改</Button>
        <Button disabled={!selectedProfile} onClick={() => setHistoryOpen((current) => !current)} size="sm" variant="outline">{historyOpen ? "收起历史" : "查看历史"}</Button>
      </div>}

      {historyOpen && selectedProfile && <div className="power-meter-profile__history">
        {[...selectedProfile.versions].reverse().map((revision, index) => (
          <article className="power-meter-profile__history-item" key={revision.version}>
            <div>
              <strong>v{revision.version} · {revision.name}</strong>
              <span>{formatProfileTimestamp(revision.createdAt)}</span>
            </div>
            <p>× {formatTrimmedNumber(revision.softwareGain, 6)} · {revision.wavelengthNm == null ? "波长待补充" : `${formatTrimmedNumber(revision.wavelengthNm, 3)} nm`}</p>
            {(revision.operator || revision.reason) && <p>{[revision.operator && `修改人：${revision.operator}`, revision.reason && `原因：${revision.reason}`].filter(Boolean).join(" · ")}</p>}
            <Button disabled={unavailable || index === 0} onClick={() => void restoreRevision(revision)} size="sm" variant="ghost"><RotateCcw className="size-3.5" />{index === 0 ? "当前版本" : "恢复此版本"}</Button>
          </article>
        ))}
      </div>}

      {(lockReason || error) && <p aria-live="polite" className={`power-meter-profile__message ${error ? "is-error" : ""}`}>{error || lockReason}</p>}
    </section>
  )
}

export function PowerMeterSettingsForm({ snapshot, config, update, active, pending, run, mode = "auto" }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
  mode?: "auto" | "manual"
}) {
  const meter = snapshot?.devices.powerMeter
  const resources = meter?.resources ?? []
  const selectedResource = config.powerMeterResource.trim()
  const selectedResourceAvailable = resources.some((item) => powerMeterResourceValue(item) === selectedResource)
  const displayedResource = selectedResourceAvailable ? selectedResource : ""
  const selectResource = (resource: string) => {
    update("powerMeterResource", resource)
  }
  const measurementSummary = meter?.quality === "live"
    ? `实时功率 ${formatNumber(meter.powerW, " W")} · ${meter.stable ? "已稳定" : "稳定中"}`
    : meter?.quality === "stale"
      ? `最后读数 ${formatNumber(meter.powerW, " W")} · ${formatMeasurementAge(meter.ageMs)}`
      : meter?.quality === "saturated"
        ? "功率计读数已饱和"
        : "功率数据不可用"
  return (
    <div className="device-settings-form">
      <Field label="串口资源">
        <ControlRow>
          <NativeSelect disabled={!active || pending || meter?.detecting || meter?.running} value={displayedResource} onChange={selectResource}>
            <option value="">请选择串口资源</option>
            {resources.map((item) => <option key={item} value={powerMeterResourceValue(item)}>{item}</option>)}
          </NativeSelect>
          <DeviceDetectButton detecting={meter?.detecting} disabled={!active || pending || meter?.detecting || meter?.running} onClick={() => run("device.refresh", { device: "powerMeter" }, true)} />
        </ControlRow>
      </Field>
      <PowerMeterProfileEditor active={active} config={config} pending={pending} run={run} snapshot={snapshot} />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <label className="device-settings-checkbox"><input checked={config.powerSmoothing ?? true} onChange={(e) => update("powerSmoothing", e.target.checked)} type="checkbox" />功率曲线平滑</label>
        <span className="text-xs text-muted-foreground">仅影响显示，不改变测量数据</span>
      </div>
      {mode === "auto" && (
        <>
          <ControlGrid className="device-settings-action-grid"><Button aria-disabled={meter?.detecting || undefined} disabled={!active || pending || meter?.running || !selectedResourceAvailable} onClick={() => void run("powerMeter.setRelativeZero", { enabled: true }, true)} variant="outline">相对调零</Button><Button aria-disabled={meter?.detecting || undefined} disabled={!active || pending || meter?.running || !selectedResourceAvailable} onClick={() => void run("powerMeter.setRelativeZero", { enabled: false }, true)} variant="outline">取消调零</Button></ControlGrid>
          <Button className={`w-full transition-colors ${meter?.running ? "btn-running-pulse" : ""}`} aria-disabled={meter?.detecting || undefined} disabled={!active || pending || (!meter?.running && !selectedResourceAvailable)} onClick={() => void run(meter?.running ? "powerMeter.stop" : "powerMeter.start", {}, true)} variant={meter?.running ? "destructive" : "default"}>{meter?.running ? <><Square className="size-4" />停止采集</> : <><Play className="size-4" />开始采集</>}</Button>
          <p className="text-xs text-muted-foreground">{measurementSummary}</p>
        </>
      )}
    </div>
  )
}

export function SpectrometerSettingsForm({ snapshot, config, update, active, pending, run, mode = "auto", compact = false }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
  mode?: "auto" | "manual"
  compact?: boolean
}) {
  const spectrum = snapshot?.devices.spectrometer
  const resources = spectrum?.resources ?? []
  const selectedResourceAvailable = resources.includes(config.spectrometerResource)
  const displayedResource = selectedResourceAvailable ? config.spectrometerResource : ""
  const measurementSummary = spectrum?.quality === "live"
    ? `中心 ${formatNumber(spectrum.peakWavelengthNm, " nm")} · FWHM ${formatNumber(spectrum.fwhmNm, " nm")}`
    : spectrum?.quality === "stale"
      ? `最后光谱 ${formatMeasurementAge(spectrum.ageMs)}`
      : spectrum?.quality === "saturated"
        ? "光谱已饱和，指标无效"
        : "光谱数据不可用"
  return (
    <div className="device-settings-form">
      <Field className={compact ? "manual-device-inline-field" : undefined} label="设备资源" layout={compact ? "inline" : "stacked"}>
        <ControlRow>
          <NativeSelect disabled={!active || pending || spectrum?.detecting || spectrum?.running} value={displayedResource} onChange={(v) => update("spectrometerResource", v)}>
            <option value="">{defaultSpectrometerResourceLabel}</option>
            {resources.map((item) => <option key={item} value={item}>{item}</option>)}
          </NativeSelect>
          <DeviceDetectButton detecting={spectrum?.detecting} disabled={!active || pending || spectrum?.detecting || spectrum?.running} onClick={() => run("device.refresh", { device: "spectrometer" }, true)} />
        </ControlRow>
      </Field>
      <ControlGrid className="device-settings-field-grid">
        <NumberField label="积分时间 (μs)" value={config.integrationTimeUs} onChange={(v) => update("integrationTimeUs", v)} />
        <NumberField label="刷新间隔 (ms)" value={config.spectrometerIntervalMs} onChange={(v) => update("spectrometerIntervalMs", v)} />
        <NumberField label="平均帧数" min={1} max={20} value={config.spectrumAverageFrames} onChange={(v) => update("spectrumAverageFrames", Math.max(1, Math.min(20, Math.round(v))))} />
        <Field label="采集模式"><label className="device-settings-checkbox"><input checked={config.autoIntegration} onChange={(e) => update("autoIntegration", e.target.checked)} type="checkbox" />自动积分</label></Field>
        <NumberField label="PIB 中心波长 (nm)" step="0.1" value={config.pibCenterWavelengthNm} onChange={(v) => update("pibCenterWavelengthNm", v)} />
        <NumberField label="PIB 半带宽 (nm)" step="0.1" value={config.pibHalfRangeNm} onChange={(v) => update("pibHalfRangeNm", v)} />
      </ControlGrid>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <label className="device-settings-checkbox"><input checked={config.spectrumSmoothing ?? true} onChange={(e) => update("spectrumSmoothing", e.target.checked)} type="checkbox" />光谱曲线平滑</label>
        <span className="text-xs text-muted-foreground">仅影响显示，不改变测量数据</span>
      </div>
      {mode === "auto" && (
        <>
          <Button className={`w-full transition-colors ${spectrum?.running ? "btn-running-pulse" : ""}`} aria-disabled={spectrum?.detecting || undefined} disabled={!active || pending || (!spectrum?.running && !selectedResourceAvailable)} onClick={() => void run(spectrum?.running ? "spectrometer.stop" : "spectrometer.start", {}, true)} variant={spectrum?.running ? "destructive" : "default"}>{spectrum?.running ? <><Square className="size-4" />停止采集</> : <><Play className="size-4" />开始采集</>}</Button>
          <p className="text-xs text-muted-foreground">{measurementSummary}</p>
        </>
      )}
    </div>
  )
}

export function DeviceSettingsDialog({ kind, open, onClose, onSave, onScriptSelect, snapshot, config, update, active, pending, run, mode = "auto" }: {
  kind: DeviceSettingsKind; open: boolean; onClose: () => void; onSave: () => Promise<void>; onScriptSelect?: (id: string) => void; snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand; mode?: "auto" | "manual"
}) {
  const [saving, setSaving] = useState(false)
  const commandLockRef = useRef(false)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const body = document.body
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = body.style.overflow
    const previousPaddingRight = body.style.paddingRight
    const focusFrame = window.requestAnimationFrame(() => closeButtonRef.current?.focus())
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) onClose()
    }
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    body.style.overflow = "hidden"
    const hasStableScrollbarGutter = getComputedStyle(document.documentElement).scrollbarGutter.includes("stable")
    if (scrollbarWidth > 0 && !hasStableScrollbarGutter) body.style.paddingRight = `${scrollbarWidth}px`
    document.addEventListener("keydown", handleKeyDown)
    return () => {
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPaddingRight
      window.cancelAnimationFrame(focusFrame)
      previousFocus?.focus()
      document.removeEventListener("keydown", handleKeyDown)
    }
  }, [open])

  if (!open) return null
  const details = {
    powerSupply: { title: "电源设置", description: mode === "manual" ? "选择电源设备并读取状态；电压和电流设置位于主页面的电源控制卡片" : "配置电源控制器、输出参数并执行安全控制", icon: <Power className="size-5" /> },
    powerMeter: { title: "功率计设置", description: "选择功率计资源并管理探头系数方案", icon: <Gauge className="size-5" /> },
    spectrometer: { title: "光谱仪设置", description: "配置光谱仪硬件资源与光谱采集参数", icon: <Radio className="size-5" /> },
  }[kind]
  const handleSave = async () => {
    if (!active || saving || pending || commandLockRef.current || snapshot?.devices[kind]?.detecting) return
    commandLockRef.current = true
    setSaving(true)
    try {
      await onSave()
      onClose()
    } finally {
      commandLockRef.current = false
      setSaving(false)
    }
  }

  const runDialogCommand: RunCommand = async (...args) => {
    if (commandLockRef.current || pending || snapshot?.devices[kind]?.detecting) return
    commandLockRef.current = true
    try {
      return await run(...args)
    } finally {
      commandLockRef.current = false
    }
  }

  return (
    <DialogPortal>
      <div className="dialog-overlay motion-overlay-enter" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
        <div aria-labelledby={`${kind}-settings-title`} aria-modal="true" className="device-settings-dialog motion-dialog-enter" data-kind={kind} data-mode={mode} role="dialog">
          <div className="device-settings-dialog__header">
            <div className="device-settings-dialog__identity">
              <div className="device-settings-dialog__icon">{details.icon}</div>
              <div className="min-w-0">
                <h2 className="device-settings-dialog__title" id={`${kind}-settings-title`}>{details.title}</h2>
                <p className="device-settings-dialog__description">{details.description}</p>
              </div>
            </div>
            <button aria-label="关闭设置弹窗" className="device-settings-dialog__close" onClick={onClose} ref={closeButtonRef} type="button"><X className="size-[18px]" /></button>
          </div>
          <div className="device-settings-dialog__body" {...busyInteraction(() => commandLockRef.current || pending || saving || Boolean(snapshot?.devices[kind]?.detecting))}>
            {kind === "powerSupply" && <PowerSupplySettingsForm active={active} config={config} mode={mode} onScriptSelect={onScriptSelect} pending={false} run={runDialogCommand} snapshot={snapshot} update={update} />}
            {kind === "powerMeter" && <PowerMeterSettingsForm active={active} config={config} mode={mode} pending={false} run={runDialogCommand} snapshot={snapshot} update={update} />}
            {kind === "spectrometer" && <SpectrometerSettingsForm active={active} config={config} mode={mode} pending={false} run={runDialogCommand} snapshot={snapshot} update={update} />}
          </div>
          <div className="device-settings-dialog__footer">
            <p className="device-settings-dialog__hint">{kind === "powerMeter" ? "普通设置不会覆盖已保存的系数方案" : "修改内容将保存到当前测试配置"}</p>
            <div className="device-settings-dialog__actions">
              <Button className="device-settings-dialog__cancel" onClick={onClose} variant="outline"><span className="device-settings-button__label">关闭</span></Button>
              <Button aria-busy={saving} aria-disabled={!active || pending || saving || snapshot?.devices[kind]?.detecting} className="device-settings-dialog__save" disabled={!active || saving} onClick={() => void handleSave()}><span className="device-settings-button__label">{saving ? "保存中…" : "保存设置"}</span></Button>
            </div>
          </div>
        </div>
      </div>
    </DialogPortal>
  )
}

export function PowerMeterProfileDialog({
  open,
  onClose,
  snapshot,
  config,
  active,
  pending,
  run,
}: {
  open: boolean
  onClose: () => void
  snapshot: BackendSnapshot | null
  config: AppConfiguration
  active: boolean
  pending: boolean
  run: RunCommand
}) {
  const commandLockRef = useRef(false)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const body = document.body
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = body.style.overflow
    const previousPaddingRight = body.style.paddingRight
    const focusFrame = window.requestAnimationFrame(() => closeButtonRef.current?.focus())
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) onClose()
    }
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    body.style.overflow = "hidden"
    const hasStableScrollbarGutter = getComputedStyle(document.documentElement).scrollbarGutter.includes("stable")
    if (scrollbarWidth > 0 && !hasStableScrollbarGutter) body.style.paddingRight = `${scrollbarWidth}px`
    document.addEventListener("keydown", handleKeyDown)
    return () => {
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPaddingRight
      window.cancelAnimationFrame(focusFrame)
      previousFocus?.focus()
      document.removeEventListener("keydown", handleKeyDown)
    }
  }, [open, onClose])

  if (!open) return null

  const runDialogCommand: RunCommand = async (...args) => {
    if (commandLockRef.current || pending) return
    commandLockRef.current = true
    try {
      return await run(...args)
    } finally {
      commandLockRef.current = false
    }
  }

  return (
    <DialogPortal>
      <div
        className="dialog-overlay motion-overlay-enter"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) onClose()
        }}
      >
        <div
          aria-labelledby="power-meter-profile-dialog-title"
          aria-modal="true"
          className="device-settings-dialog motion-dialog-enter"
          data-kind="powerMeter"
          role="dialog"
        >
          <div className="device-settings-dialog__header">
            <div className="device-settings-dialog__identity">
              <div className="device-settings-dialog__icon">
                <SlidersHorizontal className="size-5" />
              </div>
              <div className="min-w-0">
                <h2 className="device-settings-dialog__title" id="power-meter-profile-dialog-title">
                  功率计系数方案
                </h2>
                <p className="device-settings-dialog__description">
                  管理探头系数方案、校准波长与历史版本
                </p>
              </div>
            </div>
            <button
              aria-label="关闭系数方案弹窗"
              className="device-settings-dialog__close"
              onClick={onClose}
              ref={closeButtonRef}
              type="button"
            >
              <X className="size-[18px]" />
            </button>
          </div>
          <div className="device-settings-dialog__body">
            <PowerMeterProfileEditor
              active={active}
              config={config}
              pending={pending}
              run={runDialogCommand}
              snapshot={snapshot}
            />
          </div>
          <div className="device-settings-dialog__footer">
            <p className="device-settings-dialog__hint">
              方案修改与切换会即时生效并保存到探头配置库
            </p>
            <div className="device-settings-dialog__actions">
              <Button className="min-w-[80px]" onClick={onClose} variant="default">
                完成
              </Button>
            </div>
          </div>
        </div>
      </div>
    </DialogPortal>
  )
}

export function AutomaticSpecificationsDialog({ config, update, active, pending, onClose, onSave, editScript = false, snapshot = null }: {
  config: AppConfiguration
  update: UpdateConfig
  active: boolean
  pending: boolean
  onClose: () => void
  onSave: () => Promise<void>
  editScript?: boolean
  snapshot?: BackendSnapshot | null
}) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const saveLockRef = useRef(false)
  const onCloseRef = useRef(onClose)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const [draft, setDraft] = useState(() => ({ ...config }))
  onCloseRef.current = onClose

  useEffect(() => {
    const body = document.body
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = body.style.overflow
    const previousPaddingRight = body.style.paddingRight
    const focusFrame = window.requestAnimationFrame(() => closeButtonRef.current?.focus())
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return
      if (event.key === "Escape" && !saveLockRef.current) {
        event.preventDefault()
        onCloseRef.current()
      }
      if (event.key === "Tab") {
        const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]') ?? []).filter((element) => element.getClientRects().length > 0)
        const first = controls[0]
        const last = controls[controls.length - 1]
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault()
          last?.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          first?.focus()
        }
      }
    }
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    body.style.overflow = "hidden"
    const hasStableScrollbarGutter = getComputedStyle(document.documentElement).scrollbarGutter.includes("stable")
    if (scrollbarWidth > 0 && !hasStableScrollbarGutter) body.style.paddingRight = `${scrollbarWidth}px`
    document.addEventListener("keydown", handleKeyDown)
    return () => {
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPaddingRight
      window.cancelAnimationFrame(focusFrame)
      previousFocus?.focus()
      document.removeEventListener("keydown", handleKeyDown)
    }
  }, [])

  const updateDraft = <K extends keyof typeof draft>(key: K, value: (typeof draft)[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  const handleSave = async () => {
    if (saveLockRef.current || pending || !active) return
    saveLockRef.current = true
    setSaving(true)
    try {
      if (!Number.isFinite(draft.pibHalfRangeNm) || draft.pibHalfRangeNm <= 0) {
        throw new Error("PIB 半带宽必须大于 0")
      }
      if (editScript) {
        const keys = ["selectedScriptId", "currentPlanMode", "initialCurrentA", "targetCurrentA", "currentStepA", "customCurrentPoints", "pointTimeoutS", "rampDownStepA", "rampDownIntervalS"] as const
        for (const key of keys) update(key, draft[key])
      }
      update("centerWavelengthSpecNm", Math.max(0, draft.centerWavelengthSpecNm))
      update("centerWavelengthToleranceNm", Math.max(0, draft.centerWavelengthToleranceNm))
      update("fwhmMaximumNm", Math.max(0, draft.fwhmMaximumNm))
      update("pibMinimumPercent", Math.max(0, Math.min(100, draft.pibMinimumPercent)))
      update("pibHalfRangeNm", draft.pibHalfRangeNm)
      await onSave()
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      saveLockRef.current = false
      setSaving(false)
    }
  }

  return (
    <DialogPortal>
      <div className="dialog-overlay motion-overlay-enter" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onClose() }}>
        <div {...busyInteraction(() => saveLockRef.current)} aria-busy={saving} aria-labelledby="automatic-specifications-title" aria-modal="true" className={`device-settings-dialog automatic-specification-dialog motion-dialog-enter${editScript ? " test-configuration-dialog" : ""}`} ref={dialogRef} role="dialog">
          <div className="device-settings-dialog__header">
            <div className="device-settings-dialog__identity">
              <div className="device-settings-dialog__icon"><Settings className="size-5" /></div>
              <div className="min-w-0">
                <h2 className="device-settings-dialog__title" id="automatic-specifications-title">{editScript ? "编辑测试设置" : "设置测试规格"}</h2>
                <p className="device-settings-dialog__description">{editScript ? "编辑测试脚本参数与测试规格，保存后应用于当前测试配置" : "配置自动测试指标的判定范围，保存后应用于当前工位"}</p>
              </div>
            </div>
            <button aria-label="关闭设置弹窗" className="device-settings-dialog__close" aria-disabled={saving} onClick={onClose} ref={closeButtonRef} type="button"><X className="size-[18px]" /></button>
          </div>
          <div className="device-settings-dialog__body">
            <fieldset {...busyInteraction(pending)} disabled={!active} className={editScript ? "test-configuration-layout" : "min-w-0"} aria-disabled={saving}>
              {editScript && <TestScriptSettingsForm active={active} config={draft} pending={false} snapshot={snapshot} update={updateDraft} />}
              <div className={`automatic-specification-form${editScript ? " power-settings-section" : ""}`}>
                <div className="automatic-specification-form__intro">
                  <div>
                    <p className={editScript ? "power-settings-section-title" : "text-sm font-semibold"}>{editScript ? "测试规格" : "光谱指标"}</p>
                    <p className={editScript ? "power-settings-section-description" : "mt-1 text-xs leading-5 text-muted-foreground"}>{editScript ? "设置光谱指标的合格判定范围" : "点击指标卡即可再次打开此设置。"}</p>
                  </div>
                  <Badge variant="outline">当前工位</Badge>
                </div>
                <ControlGrid className="device-settings-field-grid">
                  <NumberField label="中心波长规格 (nm)" step="0.1" value={draft.centerWavelengthSpecNm} onChange={(value) => updateDraft("centerWavelengthSpecNm", value)} />
                  <NumberField label="中心波长允许偏差 (± nm)" min={0} step="0.1" value={draft.centerWavelengthToleranceNm} onChange={(value) => updateDraft("centerWavelengthToleranceNm", value)} />
                  <NumberField label="FWHM 上限 (nm)" min={0} step="0.01" value={draft.fwhmMaximumNm} onChange={(value) => updateDraft("fwhmMaximumNm", value)} />
                  <NumberField label="PIB 下限 (%)" min={0} max={100} step="0.1" value={draft.pibMinimumPercent} onChange={(value) => updateDraft("pibMinimumPercent", value)} />
                  <NumberField label="PIB 半带宽 (nm)" min={0} step="0.1" value={draft.pibHalfRangeNm} onChange={(value) => updateDraft("pibHalfRangeNm", value)} />
                </ControlGrid>
              </div>
            </fieldset>
            {error && <p className="mt-3 text-xs text-destructive" role="alert">保存失败：{error}</p>}
          </div>
          <div className="device-settings-dialog__footer">
            <p className="device-settings-dialog__hint">{editScript ? "脚本参数和测试规格统一保存到当前测试配置" : "规格保存到当前工位配置"}</p>
            <div className="device-settings-dialog__actions">
              <Button className="device-settings-dialog__cancel" aria-disabled={saving} onClick={onClose} variant="outline">取消</Button>
              <Button aria-busy={saving} className="device-settings-dialog__save" disabled={!active} aria-disabled={pending || saving || !active} onClick={() => void handleSave()}>{saving ? "保存中…" : editScript ? "保存设置" : "保存规格"}</Button>
            </div>
          </div>
        </div>
      </div>
    </DialogPortal>
  )
}

export function currentInputMaximum(config: AppConfiguration, snapshot: BackendSnapshot | null): number | undefined {
  const connectedKind = snapshot?.devices.powerSupply.connected
    ? snapshot.configuration?.powerSupplyKind ?? config.powerSupplyKind
    : config.powerSupplyKind
  if (connectedKind === "ch341") return I2C_CURRENT_LIMIT_A
  return connectedKind === "ru" && snapshot?.configuration?.powerSupplyKind === "ru"
    ? snapshot.devices.powerSupply.maximumCurrentA ?? undefined : undefined
}

export function updatePowerSupplyKind(config: AppConfiguration, update: UpdateConfig, kind: AppConfiguration["powerSupplyKind"]) {
  if (config.selectedScriptId === "aging" && kind !== "ch341") return
  update("powerSupplyKind", kind)
  if (kind !== "ch341") return
  update("setCurrentA", Math.min(config.setCurrentA, I2C_CURRENT_LIMIT_A))
  update("initialCurrentA", Math.min(config.initialCurrentA, I2C_CURRENT_LIMIT_A))
  update("targetCurrentA", Math.min(config.targetCurrentA, I2C_CURRENT_LIMIT_A))
  update("currentStepA", Math.min(config.currentStepA, I2C_CURRENT_LIMIT_A))
  update("customCurrentPoints", config.customCurrentPoints.map((point) => ({
    ...point,
    currentA: Math.min(point.currentA, I2C_CURRENT_LIMIT_A),
  })))
  update("rampDownStepA", Math.min(config.rampDownStepA, I2C_CURRENT_LIMIT_A))
}
