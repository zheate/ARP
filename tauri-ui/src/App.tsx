import { Children, isValidElement, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react"
import {
  Archive,
  BarChart3,
  ChevronDown,
  CircleGauge,
  Download,
  Gauge,
  OctagonAlert,
  Play,
  Power,
  Radio,
  RefreshCw,
  RotateCcw,
  Save,
  SlidersHorizontal,
  Square,
  X,
} from "lucide-react"
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  ReferenceDot,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { useBackendSnapshot } from "@/hooks/use-backend-snapshot"
import type { AppConfiguration, BackendSnapshot, DeviceSnapshot } from "@/lib/backend"

type Page = "automatic" | "manual" | "records" | "pd"

const CH341_CURRENT_LIMIT_A = 20
const POWER_PLOT_HISTORY_S = 60
const POWER_TIME_TICK_INTERVAL_S = 10

const navigation = [
  { id: "automatic" as const, label: "自动测试", icon: CircleGauge },
  { id: "manual" as const, label: "详细配置", icon: SlidersHorizontal },
  { id: "records" as const, label: "当前记录", icon: Archive },
  { id: "pd" as const, label: "PD 采集", icon: BarChart3 },
]

const emptyConfig: AppConfiguration = {
  sn: "",
  productModel: "",
  batch: "",
  station: "",
  outputDir: "",
  powerSupplyKind: "ch341",
  tdkResource: "",
  setCurrentA: 0,
  tdkVoltageV: 0,
  powerMeterResource: "",
  powerMeterWavelengthNm: 976,
  softwareGain: 1,
  powerMeterIntervalMs: 100,
  spectrometerResource: "",
  integrationTimeUs: 1000,
  autoIntegration: false,
  spectrometerIntervalMs: 100,
  stableWindowS: 3,
  stableToleranceW: 0.15,
  initialCurrentA: 1,
  targetCurrentA: 20,
  currentStepA: 3,
  pointTimeoutS: 120,
  rampDownStepA: 5,
  rampDownIntervalS: 1.1,
  pauseRampDownTimeoutS: 30,
  useSpectrometer: true,
}

const configurationKeys = Object.keys(emptyConfig) as Array<keyof AppConfiguration>

function configurationsEqual(left: AppConfiguration, right: AppConfiguration): boolean {
  return configurationKeys.every((key) => left[key] === right[key])
}

function asNumber(value: string): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function formatNumber(value: number | null | undefined, suffix = "", digits = 3): string {
  return value == null || !Number.isFinite(value) ? "--" : `${value.toFixed(digits)}${suffix}`
}

const defaultSpectrometerResourceLabel = "自动选择第一台 Ocean Insight"

function powerMeterResourceValue(label: string): string {
  return label.match(/ASRL\d+::INSTR/i)?.[0] ?? label
}

function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={`space-y-1.5 ${className}`}>
      <Label>{label}</Label>
      {children}
    </div>
  )
}

function NativeSelect({ value, onChange, children, disabled }: {
  value: string
  onChange: (value: string) => void
  children: ReactNode
  disabled?: boolean
}) {
  const emptyValue = "__empty__"
  const options = Children.toArray(children).filter((child): child is ReactElement<{ value?: string; children?: ReactNode }> => isValidElement(child))
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const selectedValue = value || emptyValue
  const selectedOption = options.find((option) => (String(option.props.value ?? "") || emptyValue) === selectedValue)
  const displayValue = selectedOption?.props.children ?? (value || "请选择")

  useEffect(() => {
    if (!open) return
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener("pointerdown", handlePointerDown)
    return () => document.removeEventListener("pointerdown", handlePointerDown)
  }, [open])

  return (
    <div className="relative w-full" ref={rootRef}>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        className="flex h-9 w-full items-center justify-between gap-2 rounded-lg border border-[#3f3f46] bg-[#18181e] px-3 text-left text-sm outline-none transition-colors hover:border-[#52525b] hover:bg-[#1d1d24] focus:border-ring focus:ring-2 focus:ring-ring/30 disabled:cursor-not-allowed disabled:border-[#27272a] disabled:bg-muted disabled:text-muted-foreground"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && open) {
            event.stopPropagation()
            event.preventDefault()
            setOpen(false)
          } else if (event.key === "Enter" || event.key === " " || event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault()
            setOpen(true)
          }
        }}
        role="combobox"
        type="button"
      >
        <span className="min-w-0 truncate">{displayValue}</span>
        <ChevronDown className={`size-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && <div className="absolute left-0 top-full z-[80] mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-[#3f3f46] bg-[#111113] p-1 shadow-xl" role="listbox">
        {options.map((option, index) => {
          const optionValue = String(option.props.value ?? "") || emptyValue
          const selected = optionValue === selectedValue
          return <button
            aria-selected={selected}
            className={`flex w-full items-center rounded-md px-3 py-2 text-left text-sm outline-none transition-colors hover:bg-[#2a2a31] focus:bg-[#2a2a31] ${selected ? "bg-[#252530] text-white" : "text-zinc-300"}`}
            key={`${optionValue}-${index}`}
            onClick={() => {
              onChange(optionValue === emptyValue ? "" : optionValue)
              setOpen(false)
            }}
            role="option"
            type="button"
          >{option.props.children}</button>
        })}
      </div>}
    </div>
  )
}

function StatusDot({ state }: { state?: string }) {
  const color = state === "connected" || state === "running"
    ? "bg-emerald-500"
    : state === "error"
      ? "bg-red-500"
      : state === "connecting"
        ? "bg-amber-400"
        : "bg-zinc-700"
  return <span className={`size-2 shrink-0 rounded-full ${color}`} />
}

type DeviceSettingsKind = "powerSupply" | "powerMeter" | "spectrometer"

function DeviceCard({ title, icon, device, onOpenSettings }: { title: string; icon: ReactNode; device?: DeviceSnapshot; onOpenSettings: () => void }) {
  return (
    <Card
      aria-label={`打开${title}设置`}
      className="cursor-pointer gap-3 py-4 shadow-none transition-colors hover:border-[#3f3f46] focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
      onClick={onOpenSettings}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onOpenSettings()
        }
      }}
      role="button"
      tabIndex={0}
    >
      <CardContent className="flex items-center gap-3 px-4">
        <div className="grid size-10 place-items-center rounded-xl border border-border bg-[#151515] text-zinc-400">{icon}</div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2 text-sm font-medium">
            <span>{title}</span><StatusDot state={device?.state} />
          </div>
          <p className="mt-1 truncate text-xs text-muted-foreground" title={device?.detail}>{device?.detail || "未连接"}</p>
        </div>
      </CardContent>
    </Card>
  )
}

function PowerSupplySettingsForm({ snapshot, config, update, active, pending, run }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
}) {
  const psu = snapshot?.devices.powerSupply
  return (
    <div className="space-y-3">
      <Field label="控制器"><NativeSelect value={config.powerSupplyKind} onChange={(v) => updatePowerSupplyKind(config, update, v as "ch341" | "tdk")}><option value="ch341">CH341 I²C</option><option value="tdk">TDK RS232</option></NativeSelect></Field>
      {config.powerSupplyKind === "tdk" && <Field label="TDK 串口"><Input value={config.tdkResource} onChange={(e) => update("tdkResource", e.target.value)} /></Field>}
      {config.powerSupplyKind === "tdk" && <NumberField label="输出电压 (V)" value={config.tdkVoltageV} onChange={(v) => update("tdkVoltageV", v)} step="0.1" />}
      <NumberField label="设定电流 (A)" value={config.setCurrentA} onChange={(v) => update("setCurrentA", v)} max={currentInputMaximum(config, snapshot)} step="0.1" />
      {config.powerSupplyKind === "tdk" ? <div className="grid grid-cols-2 gap-2">
        <Button disabled={!active || pending} onClick={() => void run(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)} variant={psu?.connected ? "outline" : "default"}>{psu?.connected ? "安全断开" : "连接 TDK"}</Button>
        <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setVoltage", { voltageV: config.tdkVoltageV }, true)} variant="outline">设置电压</Button>
        <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setOutput", { enabled: !psu?.outputEnabled })} variant={psu?.outputEnabled ? "destructive" : "outline"}>{psu?.outputEnabled ? "关闭输出" : "开启输出"}</Button>
        <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)}>设置电流</Button>
      </div> : <>
        <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending} onClick={() => void run(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)} variant={psu?.connected ? "outline" : "default"}>{psu?.connected ? "安全断开" : "连接电源"}</Button><Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)}>设置电流</Button></div>
        <div className="grid grid-cols-2 gap-2">
          <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputVoltage" })} size="sm" variant="outline">读取输出电压</Button>
          <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputCurrent" })} size="sm" variant="outline">读取输出电流</Button>
          <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "inputVoltage" })} size="sm" variant="outline">读取输入电压</Button>
          <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "temperature" })} size="sm" variant="outline">读取模块温度</Button>
        </div>
      </>}
    </div>
  )
}

function PowerMeterSettingsForm({ snapshot, config, update, active, pending, run }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
}) {
  const meter = snapshot?.devices.powerMeter
  const resources = meter?.resources ?? []
  const selectedResource = config.powerMeterResource.trim()
  return (
    <div className="space-y-3">
      <Field label="串口资源">
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
          <NativeSelect value={selectedResource} onChange={(v) => update("powerMeterResource", v)}>
            <option value="">请选择串口资源</option>
            {selectedResource && !resources.some((item) => powerMeterResourceValue(item) === selectedResource) && <option value={selectedResource}>{selectedResource}</option>}
            {resources.map((item) => <option key={item} value={powerMeterResourceValue(item)}>{item}</option>)}
          </NativeSelect>
          <Button disabled={!active || pending} onClick={() => void run("device.refresh", { device: "powerMeter" }, true)} variant="outline"><RefreshCw className="size-4" />识别</Button>
        </div>
      </Field>
      <NumberField label="校准波长 (nm)" value={config.powerMeterWavelengthNm} onChange={(v) => update("powerMeterWavelengthNm", v)} step="0.1" />
      <NumberField label="软件增益" value={config.softwareGain} onChange={(v) => update("softwareGain", v)} step="0.01" />
      <Button className="w-full" disabled={!active || pending} onClick={() => void run(meter?.running ? "powerMeter.stop" : "powerMeter.start", {}, true)}>{meter?.running ? "停止采集" : "开始采集"}</Button>
      <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending || meter?.running} onClick={() => void run("powerMeter.setRelativeZero", { enabled: true }, true)} variant="outline">相对调零</Button><Button disabled={!active || pending || meter?.running} onClick={() => void run("powerMeter.setRelativeZero", { enabled: false }, true)} variant="outline">取消调零</Button></div>
      <p className="text-xs text-muted-foreground">实时功率 {formatNumber(meter?.powerW, " W")} · {meter?.stable ? "已稳定" : "稳定中"}</p>
    </div>
  )
}

function SpectrometerSettingsForm({ snapshot, config, update, active, pending, run }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
}) {
  const spectrum = snapshot?.devices.spectrometer
  return (
    <div className="space-y-3">
      <Field label="设备资源">
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
          <NativeSelect value={config.spectrometerResource} onChange={(v) => update("spectrometerResource", v)}>
            <option value="">{defaultSpectrometerResourceLabel}</option>
            {spectrum?.resources?.filter((item) => item !== defaultSpectrometerResourceLabel).map((item) => <option key={item} value={item}>{item}</option>)}
          </NativeSelect>
          <Button disabled={!active || pending} onClick={() => void run("device.refresh", { device: "spectrometer" }, true)} variant="outline"><RefreshCw className="size-4" />识别</Button>
        </div>
      </Field>
      <NumberField label="积分时间 (μs)" value={config.integrationTimeUs} onChange={(v) => update("integrationTimeUs", v)} />
      <NumberField label="刷新间隔 (ms)" value={config.spectrometerIntervalMs} onChange={(v) => update("spectrometerIntervalMs", v)} />
      <label className="flex items-center gap-2 text-sm"><input checked={config.autoIntegration} onChange={(e) => update("autoIntegration", e.target.checked)} type="checkbox" />自动积分</label>
      <Button className="w-full" disabled={!active || pending} onClick={() => void run(spectrum?.running ? "spectrometer.stop" : "spectrometer.start", {}, true)}>{spectrum?.running ? "停止采集" : "开始采集"}</Button>
      <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending || !snapshot?.measurements?.spectrum.length} onClick={() => void run("spectrometer.saveCsv")} variant="outline"><Download className="size-4" />保存光谱 CSV</Button><Button disabled={!active || pending} onClick={() => void run("charts.reset")} variant="outline"><RotateCcw className="size-4" />清空曲线</Button></div>
      <p className="text-xs text-muted-foreground">中心 {formatNumber(spectrum?.centroidNm, " nm")} · FWHM {formatNumber(spectrum?.fwhmNm, " nm")}</p>
    </div>
  )
}

function DeviceSettingsDialog({ kind, open, onClose, onSave, snapshot, config, update, active, pending, run }: {
  kind: DeviceSettingsKind; open: boolean; onClose: () => void; onSave: () => Promise<void>; snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
}) {
  const [saving, setSaving] = useState(false)
  const commandLockRef = useRef(false)

  useEffect(() => {
    if (!open) return
    const body = document.body
    const previousOverflow = body.style.overflow
    const previousPaddingRight = body.style.paddingRight
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose()
    }
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    body.style.overflow = "hidden"
    const hasStableScrollbarGutter = getComputedStyle(document.documentElement).scrollbarGutter.includes("stable")
    if (scrollbarWidth > 0 && !hasStableScrollbarGutter) body.style.paddingRight = `${scrollbarWidth}px`
    document.addEventListener("keydown", handleKeyDown)
    return () => {
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPaddingRight
      document.removeEventListener("keydown", handleKeyDown)
    }
  }, [open])

  if (!open) return null
  const details = {
    powerSupply: { title: "电源设置", description: "配置电源控制器、输出参数并执行安全控制", icon: <Power className="size-5" /> },
    powerMeter: { title: "功率计设置", description: "配置串口资源、校准波长和采集参数", icon: <Gauge className="size-5" /> },
    spectrometer: { title: "光谱仪设置", description: "配置 Ocean Insight 设备和光谱采集参数", icon: <Radio className="size-5" /> },
  }[kind]

  const handleSave = async () => {
    if (saving || pending) return
    setSaving(true)
    try {
      await onSave()
      onClose()
    } finally {
      setSaving(false)
    }
  }

  const runDialogCommand: RunCommand = async (method, params, sync) => {
    if (commandLockRef.current || pending) return
    commandLockRef.current = true
    try {
      await run(method, params, sync)
    } finally {
      commandLockRef.current = false
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-4 backdrop-blur-[2px]" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div aria-labelledby={`${kind}-settings-title`} aria-modal="true" className="flex max-h-[min(760px,calc(100vh-2rem))] w-[min(520px,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-[#3f3f46] bg-[#0b0b0d] shadow-2xl" role="dialog">
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div className="flex items-start gap-3"><div className="grid size-10 shrink-0 place-items-center rounded-xl border border-border bg-[#151515] text-zinc-400">{details.icon}</div><div><h2 className="text-base font-semibold" id={`${kind}-settings-title`}>{details.title}</h2><p className="mt-1 text-xs leading-5 text-muted-foreground">{details.description}</p></div></div>
          <button aria-label="关闭设置弹窗" className="rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-white/10 hover:text-white" onClick={onClose} type="button"><X className="size-4" /></button>
        </div>
        <div className="min-h-0 overflow-y-auto px-5 py-4">
          {kind === "powerSupply" && <PowerSupplySettingsForm active={active} config={config} pending={false} run={runDialogCommand} snapshot={snapshot} update={update} />}
          {kind === "powerMeter" && <PowerMeterSettingsForm active={active} config={config} pending={false} run={runDialogCommand} snapshot={snapshot} update={update} />}
          {kind === "spectrometer" && <SpectrometerSettingsForm active={active} config={config} pending={false} run={runDialogCommand} snapshot={snapshot} update={update} />}
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-border bg-[#080808] px-5 py-3"><p className="text-xs text-muted-foreground">修改后保存到当前测试配置</p><div className="flex gap-2"><Button className="w-16" onClick={onClose} variant="outline">关闭</Button><Button aria-busy={saving} aria-disabled={!active || pending || saving} className="w-24" disabled={!active || saving} onClick={() => void handleSave()}>{saving ? "保存中…" : "保存设置"}</Button></div></div>
      </div>
    </div>
  )
}

function ChartPanel({ title, data, xKey, lines, empty = "暂无实时数据", heightClassName, headerRight, overlays, stretch = false, xDomain, xTicks }: {
  title: string
  data: Array<Record<string, number | null>>
  xKey: string
  lines: Array<{ key: string; label: string; color: string; yAxisId?: string }>
  empty?: string
  heightClassName?: string
  headerRight?: ReactNode
  overlays?: ReactNode
  stretch?: boolean
  xDomain?: [number, number]
  xTicks?: number[]
}) {
  return (
    <Card className={`shadow-none ${stretch ? "h-full min-h-0" : ""}`}>
      <CardHeader className="flex min-h-9 flex-row items-center justify-between gap-3 pb-2"><CardTitle className="text-sm">{title}</CardTitle>{headerRight}</CardHeader>
      <CardContent className={`${stretch ? "min-h-0 flex-1" : heightClassName || (data.length === 0 ? "h-40" : "h-56")} px-3 pb-3`}>
        {data.length === 0 ? (
          <div className="grid h-full place-items-center rounded-lg border border-border/70 bg-[#080808] text-sm text-zinc-600">{empty}</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 16, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis allowDataOverflow={xDomain !== undefined} axisLine={{ stroke: "var(--border)" }} dataKey={xKey} domain={xDomain ?? ["auto", "auto"]} tick={{ fill: "var(--muted-foreground)", fontSize: 10 }} tickFormatter={xTicks ? (value: number) => value.toFixed(0) : undefined} tickLine={{ stroke: "var(--border)" }} ticks={xTicks} type="number" />
              <YAxis axisLine={{ stroke: "var(--border)" }} yAxisId="left" tick={{ fill: "var(--muted-foreground)", fontSize: 10 }} tickLine={{ stroke: "var(--border)" }} />
              {lines.some((line) => line.yAxisId === "right") && <YAxis axisLine={{ stroke: "var(--border)" }} yAxisId="right" orientation="right" tick={{ fill: "var(--muted-foreground)", fontSize: 10 }} tickLine={{ stroke: "var(--border)" }} />}
              <Tooltip contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--popover-foreground)", fontSize: 12 }} />
              <Legend wrapperStyle={{ color: "var(--muted-foreground)", fontSize: 11 }} />
              {lines.map((line) => (
                <Line
                  dataKey={line.key}
                  dot={false}
                  isAnimationActive={false}
                  key={line.key}
                  name={line.label}
                  stroke={line.color}
                  strokeWidth={1.8}
                  type="monotone"
                  yAxisId={line.yAxisId ?? "left"}
                />
              ))}
              {overlays}
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

function PowerRealtimeChart({ snapshot, stretch = false }: { snapshot: BackendSnapshot | null; stretch?: boolean }) {
  const powerData = (snapshot?.measurements?.power ?? []) as Array<Record<string, number | null>>
  const latestPowerW = powerData[powerData.length - 1]?.powerW
  const latestElapsedS = powerData[powerData.length - 1]?.elapsedS
  const powerTimeDomain: [number, number] = typeof latestElapsedS === "number"
    ? [Math.max(0, latestElapsedS - POWER_PLOT_HISTORY_S), Math.max(10, latestElapsedS)]
    : [0, 10]
  const firstPowerTickS = Math.ceil(powerTimeDomain[0] / POWER_TIME_TICK_INTERVAL_S) * POWER_TIME_TICK_INTERVAL_S
  const powerTimeTicks = Array.from(
    { length: Math.floor((powerTimeDomain[1] - firstPowerTickS) / POWER_TIME_TICK_INTERVAL_S) + 1 },
    (_, index) => firstPowerTickS + index * POWER_TIME_TICK_INTERVAL_S,
  )
  const powerMeter = snapshot?.devices.powerMeter
  const powerStable = powerMeter?.running === true && powerMeter.stable === true
  const powerStabilityLabel = powerMeter?.running ? (powerStable ? "已稳定" : "稳定中") : "未采集"
  const powerStabilityColor = powerStable ? "#5fd07a" : "#2563eb"

  return (
    <ChartPanel
      title="功率实时"
      data={powerData}
      xKey="elapsedS"
      lines={[{ key: "powerW", label: "功率 (W)", color: powerStabilityColor }]}
      stretch={stretch}
      xDomain={powerTimeDomain}
      xTicks={powerTimeTicks}
      headerRight={<div className="flex items-center gap-3"><div className={`flex items-center gap-1.5 text-sm font-medium ${powerStable ? "text-emerald-400" : powerMeter?.running ? "text-blue-300" : "text-muted-foreground"}`}><span className={`size-2 rounded-full ${powerStable ? "bg-emerald-400" : powerMeter?.running ? "bg-blue-400" : "bg-zinc-600"}`} />{powerStabilityLabel}</div><p className="text-lg font-semibold tabular-nums">{formatNumber(latestPowerW, " W")}</p></div>}
    />
  )
}

function SpectrumRealtimeChart({ snapshot, stretch = false }: { snapshot: BackendSnapshot | null; stretch?: boolean }) {
  const spectrumData = (snapshot?.measurements?.spectrum ?? []) as Array<Record<string, number | null>>
  const spectrum = snapshot?.devices.spectrometer
  const spectrumPeaks = snapshot?.measurements?.spectrumPeaks ?? []

  return (
    <ChartPanel
      title="光谱"
      data={spectrumData}
      xKey="wavelengthNm"
      lines={[{ key: "intensity", label: "强度", color: "#65a30d" }]}
      stretch={stretch}
      overlays={spectrumPeaks.flatMap((annotation) => [
        <ReferenceLine
          key={`${annotation.label}-line`}
          label={{ value: `${annotation.label} ${annotation.centroidNm.toFixed(3)} nm`, fill: "#7dd3fc", fontSize: 10, position: "insideTop" }}
          stroke="#7dd3fc"
          strokeDasharray="3 3"
          strokeOpacity={0.65}
          x={annotation.centroidNm}
        />,
        <ReferenceDot
          key={`${annotation.label}-dot`}
          fill="#7dd3fc"
          r={3}
          stroke="#082f49"
          strokeWidth={1}
          x={annotation.centroidNm}
          y={annotation.peakIntensity}
        />,
      ])}
      headerRight={
        <div className="grid min-w-0 flex-1 grid-cols-3 items-center gap-6 text-center">
          <div className="flex flex-col items-center gap-1">
            <p className="text-lg font-semibold leading-none tabular-nums">{formatNumber(spectrum?.centroidNm, " nm")}</p>
            <p className="text-[10px] leading-none text-muted-foreground">中心波长</p>
          </div>
          <div className="flex flex-col items-center gap-1">
            <p className="text-lg font-semibold leading-none tabular-nums">{formatNumber(spectrum?.fwhmNm, " nm")}</p>
            <p className="text-[10px] leading-none text-muted-foreground">FWHM</p>
          </div>
          <div className="flex flex-col items-center gap-1">
            <p className="text-lg font-semibold leading-none tabular-nums">{formatNumber(spectrum?.smsrDb, " dB", 2)}</p>
            <p className="text-[10px] leading-none text-muted-foreground">SMSR</p>
          </div>
        </div>
      }
    />
  )
}

function App() {
  const [page, setPage] = useState<Page>("automatic")
  const { snapshot, error, loading, commandPending, refresh, command } = useBackendSnapshot(page)
  const [config, setConfig] = useState<AppConfiguration>(emptyConfig)
  const [dirty, setDirty] = useState(false)
  const [selectedSession, setSelectedSession] = useState("")
  const active = snapshot?.backend.mode === "active"

  useEffect(() => {
    if (!snapshot?.configuration || dirty) return
    setConfig((current) => configurationsEqual(current, snapshot.configuration!) ? current : snapshot.configuration!)
  }, [snapshot?.configuration, dirty])

  const update = <K extends keyof AppConfiguration>(key: K, value: AppConfiguration[K]) => {
    setConfig((current) => ({ ...current, [key]: value }))
    setDirty(true)
  }

  const saveConfiguration = async () => {
    if (!active) return
    await command("app.configure", config as unknown as Record<string, unknown>)
    setDirty(false)
  }

  const run = async (method: string, params: Record<string, unknown> = {}, sync = false) => {
    if (!active) return
    if (sync && dirty) await saveConfiguration()
    await command(method, params)
  }

  const devicesReady = [
    snapshot?.devices.powerSupply.connected,
    snapshot?.devices.powerMeter.ready,
    config.useSpectrometer && snapshot?.devices.spectrometer.ready,
  ].filter(Boolean).length
  const requiredDevices = config.useSpectrometer ? 3 : 2
  const progress = (snapshot?.automaticTest.progress ?? 0) * 100
  const notices = snapshot?.backend.notices ?? []
  const notifications = [
    ...(error ? [{ key: `request-error:${error}`, level: "error" as const, title: "系统错误", message: error }] : []),
    ...notices.map((notice, index) => ({ ...notice, key: `backend-notice:${notice.level}:${notice.title}:${notice.message}:${index}` })),
    ...(!active && snapshot ? [{ key: "runtime-warning", level: "warning" as const, title: "运行环境提示", message: "当前 Python 环境未加载 PySide6/设备驱动，因此控制功能暂不可用。请使用项目指定环境启动。" }] : []),
  ]
  const [dismissedNotifications, setDismissedNotifications] = useState<Set<string>>(new Set())
  const visibleNotifications = notifications.filter((notification) => !dismissedNotifications.has(notification.key))
  const title = navigation.find((item) => item.id === page)?.label ?? "自动测试"

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div className="grid min-h-screen grid-cols-[232px_minmax(0,1fr)]">
        <aside className="flex min-h-screen flex-col border-r border-border bg-[#050505] px-4 py-5 text-white">
          <p className="px-2 text-xs font-medium uppercase tracking-[0.16em] text-zinc-600">工作区</p>
          <nav className="mt-3 space-y-1.5">
            {navigation.map((item) => (
              <button
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-base transition-colors ${page === item.id ? "bg-[#18181b] font-medium text-white ring-1 ring-white/10" : "text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-200"}`}
                key={item.id}
                onClick={() => setPage(item.id)}
                type="button"
              >
                <item.icon className="size-4" /><span>{item.label}</span>
              </button>
            ))}
          </nav>
        </aside>

        <main className="min-w-0">
          <header className="flex h-[72px] items-center justify-between border-b border-border bg-[#050505] px-6">
            <div>
              <div className="flex items-center gap-2"><h1 className="text-xl font-semibold tracking-tight">{title}</h1><Badge variant="outline" className={active ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400" : "border-amber-500/30 bg-amber-500/10 text-amber-400"}>{active ? "控制器已接入" : "只读兼容模式"}</Badge></div>
              <p className="mt-1 text-xs text-muted-foreground">{snapshot?.status?.message ?? (loading ? "正在连接 Python 后端" : "等待后端")}</p>
            </div>
            <div className="flex items-center gap-2">
              {dirty && <Button disabled={!active || commandPending} onClick={() => void saveConfiguration()} size="sm" variant="outline"><Save className="size-4" />保存设置</Button>}
              <Button aria-label="刷新" disabled={loading} onClick={() => void refresh()} size="icon" variant="outline"><RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} /></Button>
              <Button className="bg-red-600 hover:bg-red-700" disabled={!active || commandPending} onClick={() => void run("app.stopAll")} size="sm"><OctagonAlert className="size-4" />紧急停止</Button>
            </div>
          </header>

          {visibleNotifications.length > 0 && <div className="pointer-events-none fixed right-4 top-4 z-50 flex w-[min(420px,calc(100vw-2rem))] flex-col gap-2">
            {visibleNotifications.map((notification) => {
              const isError = notification.level === "error"
              const isWarning = notification.level === "warning"
              return <div className={`pointer-events-auto flex items-start gap-3 rounded-xl border px-4 py-3 text-sm shadow-2xl backdrop-blur-md ${isError ? "border-red-500/40 bg-[#2a0909]/95 text-red-100" : isWarning ? "border-amber-500/40 bg-[#271b05]/95 text-amber-100" : "border-blue-500/40 bg-[#071a32]/95 text-blue-100"}`} key={notification.key} role="alert">
                <OctagonAlert className={`mt-0.5 size-4 shrink-0 ${isError ? "text-red-400" : isWarning ? "text-amber-400" : "text-blue-400"}`} />
                <div className="min-w-0 flex-1"><p className="font-medium">{notification.title}</p><p className="mt-1 leading-5 text-white/70">{notification.message}</p></div>
                <button aria-label="关闭提示" className="shrink-0 rounded-md p-1 text-white/50 transition-colors hover:bg-white/10 hover:text-white" onClick={() => setDismissedNotifications((current) => new Set(current).add(notification.key))} type="button"><X className="size-4" /></button>
              </div>
            })}
          </div>}

          <div className="space-y-4 p-5 lg:p-6">
            {page === "automatic" && <AutomaticPage snapshot={snapshot} config={config} update={update} active={active} pending={commandPending} readyCount={devicesReady} readyTotal={requiredDevices} progress={progress} run={run} saveConfiguration={saveConfiguration} />}
            {page === "manual" && <ManualPage snapshot={snapshot} config={config} update={update} active={active} pending={commandPending} run={run} />}
            {page === "records" && <RecordsPage snapshot={snapshot} active={active} pending={commandPending} selected={selectedSession} setSelected={setSelectedSession} run={run} />}
            {page === "pd" && <PdPage snapshot={snapshot} active={active} pending={commandPending} run={run} />}
          </div>
        </main>
      </div>
    </div>
  )
}

type UpdateConfig = <K extends keyof AppConfiguration>(key: K, value: AppConfiguration[K]) => void
type RunCommand = (method: string, params?: Record<string, unknown>, sync?: boolean) => Promise<void>

function currentInputMaximum(config: AppConfiguration, snapshot: BackendSnapshot | null): number | undefined {
  const connectedKind = snapshot?.devices.powerSupply.connected
    ? snapshot.configuration?.powerSupplyKind ?? config.powerSupplyKind
    : config.powerSupplyKind
  return connectedKind === "ch341" ? CH341_CURRENT_LIMIT_A : undefined
}

function updatePowerSupplyKind(config: AppConfiguration, update: UpdateConfig, kind: "ch341" | "tdk") {
  update("powerSupplyKind", kind)
  if (kind !== "ch341") return
  update("setCurrentA", Math.min(config.setCurrentA, CH341_CURRENT_LIMIT_A))
  update("initialCurrentA", Math.min(config.initialCurrentA, CH341_CURRENT_LIMIT_A))
  update("targetCurrentA", Math.min(config.targetCurrentA, CH341_CURRENT_LIMIT_A))
  update("currentStepA", Math.min(config.currentStepA, CH341_CURRENT_LIMIT_A))
  update("rampDownStepA", Math.min(config.rampDownStepA, CH341_CURRENT_LIMIT_A))
}

function AutomaticPage({ snapshot, config, update, active, pending, readyCount, readyTotal, progress, run, saveConfiguration }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; readyCount: number; readyTotal: number; progress: number; run: RunCommand; saveConfiguration: () => Promise<void>
}) {
  const auto = snapshot?.automaticTest
  const running = auto && !["idle", "completed", "paused"].includes(auto.state)
  const pendingDatabaseCount = snapshot?.records?.pendingDatabaseCount ?? 0
  const [openSettings, setOpenSettings] = useState<DeviceSettingsKind | null>(null)
  const closeSettings = () => setOpenSettings(null)
  return (
    <>
      <section className="grid grid-cols-3 gap-4">
        <DeviceCard title="电源" icon={<Power className="size-5" />} device={snapshot?.devices.powerSupply} onOpenSettings={() => setOpenSettings("powerSupply")} />
        <DeviceCard title="功率计" icon={<Gauge className="size-5" />} device={snapshot?.devices.powerMeter} onOpenSettings={() => setOpenSettings("powerMeter")} />
        <DeviceCard title="光谱仪" icon={<Radio className="size-5" />} device={snapshot?.devices.spectrometer} onOpenSettings={() => setOpenSettings("spectrometer")} />
      </section>
      {openSettings && <DeviceSettingsDialog active={active} config={config} kind={openSettings} onClose={closeSettings} onSave={saveConfiguration} open pending={pending} run={run} snapshot={snapshot} update={update} />}
      <section className="grid grid-cols-[minmax(360px,0.9fr)_minmax(520px,1.35fr)] gap-4">
        <Card className="shadow-none">
          <CardContent className="grid grid-cols-2 gap-x-4 gap-y-5 pb-6">
            <Field label="输出目录" className="col-span-2"><Input value={config.outputDir} onChange={(e) => update("outputDir", e.target.value)} /></Field>
            <Field label="壳体 SN"><Input value={config.sn} onChange={(e) => update("sn", e.target.value)} /></Field>
            <Field label="测试站别"><Input value={config.station} onChange={(e) => update("station", e.target.value)} /></Field>
            <Separator className="col-span-2 my-2" />
            <NumberField label="起始电流 (A)" value={config.initialCurrentA} onChange={(v) => update("initialCurrentA", v)} max={currentInputMaximum(config, snapshot)} />
            <NumberField label="目标电流 (A)" value={config.targetCurrentA} onChange={(v) => update("targetCurrentA", v)} max={currentInputMaximum(config, snapshot)} />
            <NumberField label="电流间隔 (A)" value={config.currentStepA} onChange={(v) => update("currentStepA", v)} max={currentInputMaximum(config, snapshot)} />
            <NumberField label="单点超时 (s)" value={config.pointTimeoutS} onChange={(v) => update("pointTimeoutS", v)} />
            <NumberField label="下电步长 (A)" value={config.rampDownStepA} onChange={(v) => update("rampDownStepA", v)} max={currentInputMaximum(config, snapshot)} />
            <NumberField label="下电间隔 (s)" value={config.rampDownIntervalS} onChange={(v) => update("rampDownIntervalS", v)} step="0.1" />
            <label className="col-span-2 flex items-center gap-3 py-1 text-sm"><input checked={config.useSpectrometer} onChange={(e) => update("useSpectrometer", e.target.checked)} type="checkbox" />同时采集光谱并判断波长稳定</label>
            <div className="col-span-2 rounded-lg border border-border bg-[#080808] p-4">
              <div className="flex justify-between text-sm"><b>准备状态</b><span>{readyCount} / {readyTotal}</span></div><Progress className="mt-3 h-1.5" value={readyCount / readyTotal * 100} />
              <p className="mt-3 text-xs text-muted-foreground">{auto?.settingsError || auto?.detail || "等待配置"}</p>
            </div>
            <div className="col-span-2 grid grid-cols-2 gap-3">
              <Button disabled={!active || pending || !auto?.canStart} onClick={() => void run("automatic.start", config as unknown as Record<string, unknown>)}><Play className="size-4" />开始自动测试</Button>
              <Button disabled={!active || pending || !auto?.canRetry} onClick={() => void run("automatic.retry")} variant="outline"><RefreshCw className="size-4" />重试当前点</Button>
              <Button disabled={!active || pending || !auto?.canEnd} onClick={() => void run("automatic.end")} variant="destructive"><Square className="size-4" />结束并安全下电</Button>
              <Button disabled={!active || pending || running || pendingDatabaseCount === 0} onClick={() => void run("records.commitCurrent")} variant="outline"><Save className="size-4" />保存数据</Button>
            </div>
          </CardContent>
        </Card>
        <div className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)_minmax(0,1fr)] gap-4">
          <Card className="shadow-none">
            <CardContent className="grid grid-cols-[1fr_auto] items-center gap-4 py-4">
              <div><div className="flex items-center gap-2"><StatusDot state={auto?.state === "paused" ? "error" : running ? "connected" : "disconnected"} /><b className="text-sm">{auto?.detail || "未开始"}</b></div><Progress className="mt-3 h-2" value={progress} /><p className="mt-2 text-xs text-muted-foreground">测试点 {Math.max(0, (auto?.currentIndex ?? -1) + 1)} / {auto?.currents?.length ?? 0}</p></div>
              <div className="text-right"><p className="text-2xl font-semibold">{formatNumber(auto?.currentA, " A", 1)}</p><p className="text-xs text-muted-foreground">当前测试电流</p></div>
            </CardContent>
          </Card>
          <PowerRealtimeChart snapshot={snapshot} stretch />
          <SpectrumRealtimeChart snapshot={snapshot} stretch />
        </div>
      </section>
    </>
  )
}

function NumberField({ label, value, onChange, min, max, step = "1" }: { label: string; value: number; onChange: (value: number) => void; min?: number; max?: number; step?: string }) {
  const handleChange = (rawValue: string) => {
    let nextValue = asNumber(rawValue)
    if (min !== undefined) nextValue = Math.max(min, nextValue)
    if (max !== undefined) nextValue = Math.min(max, nextValue)
    onChange(nextValue)
  }
  return <Field label={label}><Input type="number" min={min} max={max} step={step} value={value} onChange={(e) => handleChange(e.target.value)} /></Field>
}

function ManualPage({ snapshot, config, update, active, pending, run }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
}) {
  const psu = snapshot?.devices.powerSupply
  const meter = snapshot?.devices.powerMeter
  const spectrum = snapshot?.devices.spectrometer
  const powerMeterResources = meter?.resources ?? []
  const selectedPowerMeterResource = config.powerMeterResource.trim()
  return (
    <>
      <section className="grid grid-cols-3 gap-4">
        <Card className="shadow-none"><CardHeader><CardTitle className="text-base">电源控制</CardTitle></CardHeader><CardContent className="space-y-3">
          <Field label="控制器"><NativeSelect value={config.powerSupplyKind} onChange={(v) => updatePowerSupplyKind(config, update, v as "ch341" | "tdk")}><option value="ch341">CH341 I²C</option><option value="tdk">TDK RS232</option></NativeSelect></Field>
          {config.powerSupplyKind === "tdk" && <Field label="TDK 串口"><Input value={config.tdkResource} onChange={(e) => update("tdkResource", e.target.value)} /></Field>}
          {config.powerSupplyKind === "tdk" && <NumberField label="输出电压 (V)" value={config.tdkVoltageV} onChange={(v) => update("tdkVoltageV", v)} step="0.1" />}
          <NumberField label="设定电流 (A)" value={config.setCurrentA} onChange={(v) => update("setCurrentA", v)} max={currentInputMaximum(config, snapshot)} step="0.1" />
          {config.powerSupplyKind === "tdk" ? <div className="grid grid-cols-2 gap-2">
            <Button disabled={!active || pending} onClick={() => void run(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)} variant={psu?.connected ? "outline" : "default"}>{psu?.connected ? "安全断开" : "连接 TDK"}</Button>
            <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setVoltage", { voltageV: config.tdkVoltageV }, true)} variant="outline">设置电压</Button>
            <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setOutput", { enabled: !psu?.outputEnabled })} variant={psu?.outputEnabled ? "destructive" : "outline"}>{psu?.outputEnabled ? "关闭输出" : "开启输出"}</Button>
            <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)}>设置电流</Button>
          </div> : <>
            <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending} onClick={() => void run(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)} variant={psu?.connected ? "outline" : "default"}>{psu?.connected ? "安全断开" : "连接电源"}</Button><Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)}>设置电流</Button></div>
            <div className="grid grid-cols-2 gap-2">
              <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputVoltage" })} size="sm" variant="outline">读取输出电压</Button>
              <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputCurrent" })} size="sm" variant="outline">读取输出电流</Button>
              <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "inputVoltage" })} size="sm" variant="outline">读取输入电压</Button>
              <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "temperature" })} size="sm" variant="outline">读取模块温度</Button>
            </div>
          </>}
        </CardContent></Card>

        <Card className="shadow-none"><CardHeader><CardTitle className="text-base">功率计</CardTitle></CardHeader><CardContent className="space-y-3">
          <Field label="串口资源">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
              <NativeSelect value={selectedPowerMeterResource} onChange={(v) => update("powerMeterResource", v)}>
                <option value="">请选择串口资源</option>
                {selectedPowerMeterResource && !powerMeterResources.some((item) => powerMeterResourceValue(item) === selectedPowerMeterResource) && <option value={selectedPowerMeterResource}>{selectedPowerMeterResource}</option>}
                {powerMeterResources.map((item) => <option key={item} value={powerMeterResourceValue(item)}>{item}</option>)}
              </NativeSelect>
              <Button disabled={!active || pending} onClick={() => void run("device.refresh", { device: "powerMeter" }, true)} variant="outline"><RefreshCw className="size-4" />识别</Button>
            </div>
          </Field>
          <NumberField label="校准波长 (nm)" value={config.powerMeterWavelengthNm} onChange={(v) => update("powerMeterWavelengthNm", v)} step="0.1" />
          <NumberField label="软件增益" value={config.softwareGain} onChange={(v) => update("softwareGain", v)} step="0.01" />
          <Button className="w-full" disabled={!active || pending} onClick={() => void run(meter?.running ? "powerMeter.stop" : "powerMeter.start", {}, true)}>{meter?.running ? "停止采集" : "开始采集"}</Button>
          <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending || meter?.running} onClick={() => void run("powerMeter.setRelativeZero", { enabled: true }, true)} variant="outline">相对调零</Button><Button disabled={!active || pending || meter?.running} onClick={() => void run("powerMeter.setRelativeZero", { enabled: false }, true)} variant="outline">取消调零</Button></div>
        </CardContent></Card>

        <Card className="shadow-none"><CardHeader><CardTitle className="text-base">光谱仪</CardTitle></CardHeader><CardContent className="space-y-3">
          <Field label="串口资源">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
              <NativeSelect value={config.spectrometerResource} onChange={(v) => update("spectrometerResource", v)}>
                <option value="">{defaultSpectrometerResourceLabel}</option>
                {spectrum?.resources?.filter((item) => item !== defaultSpectrometerResourceLabel).map((item) => <option key={item} value={item}>{item}</option>)}
              </NativeSelect>
              <Button disabled={!active || pending} onClick={() => void run("device.refresh", { device: "spectrometer" }, true)} variant="outline"><RefreshCw className="size-4" />识别</Button>
            </div>
          </Field>
          <NumberField label="积分时间 (μs)" value={config.integrationTimeUs} onChange={(v) => update("integrationTimeUs", v)} />
          <NumberField label="刷新间隔 (ms)" value={config.spectrometerIntervalMs} onChange={(v) => update("spectrometerIntervalMs", v)} />
          <label className="flex items-center gap-2 text-sm"><input checked={config.autoIntegration} onChange={(e) => update("autoIntegration", e.target.checked)} type="checkbox" />自动积分</label>
          <Button className="w-full" disabled={!active || pending} onClick={() => void run(spectrum?.running ? "spectrometer.stop" : "spectrometer.start", {}, true)}>{spectrum?.running ? "停止采集" : "开始采集"}</Button>
          <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending || !snapshot?.measurements?.spectrum.length} onClick={() => void run("spectrometer.saveCsv")} variant="outline"><Download className="size-4" />保存光谱 CSV</Button><Button disabled={!active || pending} onClick={() => void run("charts.reset")} variant="outline"><RotateCcw className="size-4" />清空曲线</Button></div>
        </CardContent></Card>
      </section>
      <section className="grid grid-cols-2 gap-4">
        <PowerRealtimeChart snapshot={snapshot} />
        <ChartPanel title="功率 / 效率" data={(snapshot?.measurements?.stable ?? []) as Array<Record<string, number | null>>} xKey="currentA" lines={[{ key: "powerW", label: "功率 (W)", color: "#16a34a" }, { key: "efficiencyPercent", label: "效率 (%)", color: "#f59e0b", yAxisId: "right" }]} />
        <div className="col-span-2"><SpectrumRealtimeChart snapshot={snapshot} /></div>
      </section>
    </>
  )
}

function RecordsPage({ snapshot, active, pending, selected, setSelected, run }: {
  snapshot: BackendSnapshot | null; active: boolean; pending: boolean; selected: string; setSelected: (value: string) => void; run: RunCommand
}) {
  const records = snapshot?.records
  const selectedRow = records?.history.find((item) => item.sessionId === selected)
  const [checked, setChecked] = useState<string[]>([])
  const [filters, setFilters] = useState({ sn: "", productModel: "", batch: "", station: "", mode: "", status: "", dateFrom: "", dateTo: "" })
  const selectRow = (sessionId: string) => {
    setSelected(sessionId)
    if (active) void run("records.select", { sessionId })
  }
  const toggleChecked = (sessionId: string) => setChecked((current) => current.includes(sessionId) ? current.filter((value) => value !== sessionId) : [...current, sessionId].slice(-5))
  const applyFilters = () => {
    const dateFrom = filters.dateFrom ? new Date(`${filters.dateFrom}T00:00:00`).toISOString() : ""
    const dateTo = filters.dateTo ? new Date(`${filters.dateTo}T23:59:59.999`).toISOString() : ""
    void run("records.setFilters", { ...filters, dateFrom, dateTo })
  }
  return (
    <>
      <section className="grid grid-cols-5 gap-3">
        {[
          ["会话", records?.summary.sessions ?? 0],
          ["完成率", records?.summary.completionRate == null ? "--" : `${(records.summary.completionRate * 100).toFixed(1)}%`],
          ["无效尝试", records?.summary.invalidAttemptRate == null ? "--" : `${(records.summary.invalidAttemptRate * 100).toFixed(1)}%`],
          ["复测率", records?.summary.retestRate == null ? "--" : `${(records.summary.retestRate * 100).toFixed(1)}%`],
          ["中位耗时", records?.summary.medianDurationS == null ? "--" : `${(records.summary.medianDurationS / 60).toFixed(1)} min`],
        ].map(([label, value]) => <Card className="py-4 shadow-none" key={label}><CardContent><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-xl font-semibold">{value}</p></CardContent></Card>)}
      </section>
      <Card className="shadow-none"><CardHeader className="flex-row items-center justify-between"><div><CardTitle className="text-base">本轮测试点</CardTitle><CardDescription>{records?.workbookPath || "尚未创建测试会话"}</CardDescription></div><div className="flex gap-2">{records?.workbookPath && <Button onClick={() => void revealItemInDir(records.workbookPath)} variant="outline">打开所在文件夹</Button>}<Button disabled={!active || pending || !records?.pendingDatabaseCount} onClick={() => void run("records.commitCurrent")} variant="outline"><Save className="size-4" />保存数据 ({records?.pendingDatabaseCount ?? 0})</Button><Button disabled={!active || pending || !records?.unsavedCount} onClick={() => void run("records.exportCurrent")}><Download className="size-4" />保存 Excel ({records?.unsavedCount ?? 0})</Button></div></CardHeader><CardContent><DataTable rows={records?.current ?? []} /></CardContent></Card>
      <Card className="shadow-none">
        <CardHeader><CardTitle className="text-base">历史记录</CardTitle><CardDescription>SQLite 本地档案是记录源，Excel 是导出文件；最多对比五轮</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-8 gap-2">
            {(["sn", "productModel", "batch", "station"] as const).map((key) => <Input key={key} placeholder={{ sn: "SN", productModel: "型号", batch: "批次", station: "站别" }[key]} value={filters[key]} onChange={(e) => setFilters({ ...filters, [key]: e.target.value })} />)}
            <NativeSelect value={filters.mode} onChange={(value) => setFilters({ ...filters, mode: value })}><option value="">全部模式</option><option value="automatic">自动</option><option value="manual">手动</option></NativeSelect>
            <NativeSelect value={filters.status} onChange={(value) => setFilters({ ...filters, status: value })}><option value="">全部状态</option><option value="completed">完整完成</option><option value="stopped_by_operator">人工结束</option><option value="aborted_safely">异常中止</option><option value="incomplete">未完成</option></NativeSelect>
            <Input aria-label="开始日期" type="date" value={filters.dateFrom} onChange={(e) => setFilters({ ...filters, dateFrom: e.target.value })} />
            <Input aria-label="结束日期" type="date" value={filters.dateTo} onChange={(e) => setFilters({ ...filters, dateTo: e.target.value })} />
          </div>
          <div className="flex justify-end gap-2"><Button disabled={!active || pending} onClick={() => { setFilters({ sn: "", productModel: "", batch: "", station: "", mode: "", status: "", dateFrom: "", dateTo: "" }); void run("records.setFilters", {}) }} variant="outline">清除筛选</Button><Button disabled={!active || pending} onClick={applyFilters}><RefreshCw className="size-4" />查询</Button></div>
          <div className="overflow-auto rounded-lg border"><table className="w-full text-left text-xs"><thead className="bg-muted/50 text-muted-foreground"><tr><th className="px-3 py-2">对比</th>{["时间", "SN", "型号", "批次", "站别", "模式", "状态", "导出"].map((item) => <th className="px-3 py-2 font-medium" key={item}>{item}</th>)}</tr></thead><tbody>{records?.history.map((row) => <tr className={`cursor-pointer border-t hover:bg-blue-500/10 ${selected === row.sessionId ? "bg-blue-500/10" : ""}`} key={row.sessionId} onClick={() => selectRow(row.sessionId)}><td className="px-3 py-2"><input checked={checked.includes(row.sessionId)} onChange={() => toggleChecked(row.sessionId)} onClick={(e) => e.stopPropagation()} type="checkbox" /></td><td className="whitespace-nowrap px-3 py-2">{new Date(row.startedAt).toLocaleString()}</td><td className="px-3 py-2 font-medium">{row.sn}</td><td className="px-3 py-2">{row.productModel || "--"}</td><td className="px-3 py-2">{row.batch || "--"}</td><td className="px-3 py-2">{row.station || "--"}</td><td className="px-3 py-2">{row.mode === "automatic" ? "自动" : "手动"}</td><td className="px-3 py-2">{row.status}</td><td className="px-3 py-2">{row.exportState}</td></tr>)}</tbody></table>{!records?.history.length && <p className="p-8 text-center text-sm text-zinc-600">当前目录还没有历史测试</p>}</div>
          <div className="flex flex-wrap justify-end gap-2"><Button disabled={!active || pending || checked.length === 0} onClick={() => void run("records.compare", { sessionIds: checked })} variant="outline">对比所选 ({checked.length})</Button><Button disabled={!active || pending || !selectedRow || selectedRow.status !== "incomplete"} onClick={() => void run("records.resume", { sessionId: selected })} variant="outline"><RotateCcw className="size-4" />继续未完成测试</Button><Button disabled={!active || pending || !selectedRow} onClick={() => void run("records.reexport", { sessionId: selected })} variant="outline"><Download className="size-4" />重新导出</Button>{selectedRow?.workbookPath && <Button onClick={() => void openPath(selectedRow.workbookPath)} variant="outline">打开 Excel</Button>}{selectedRow?.workbookPath && <Button onClick={() => void revealItemInDir(selectedRow.workbookPath)} variant="outline">打开文件夹</Button>}</div>
        </CardContent>
      </Card>
      {records?.detail && <Card className="shadow-none"><CardHeader><CardTitle className="text-base">记录详情 · {String(records.detail.sn ?? "")}</CardTitle><CardDescription>{String(records.detail.terminationReason ?? "") || "查看每个测试点及复测尝试"}</CardDescription></CardHeader><CardContent><AttemptTable rows={records.attempts} /></CardContent></Card>}
      {!!records?.comparison.length && <ComparisonPanel comparisons={records.comparison} />}
    </>
  )
}

function AttemptTable({ rows }: { rows: NonNullable<BackendSnapshot["records"]>["attempts"] }) {
  return <div className="overflow-auto rounded-lg border"><table className="w-full text-left text-xs"><thead className="bg-muted/50 text-muted-foreground"><tr>{["点", "目标电流", "尝试", "状态", "原因", "电压", "功率", "效率", "中心波长", "FWHM"].map((label) => <th className="px-3 py-2 font-medium" key={label}>{label}</th>)}</tr></thead><tbody>{rows.map((row) => <tr className="border-t" key={row.attemptId}><td className="px-3 py-2">{row.sequenceIndex + 1}</td><td className="px-3 py-2">{formatNumber(row.targetCurrentA, " A", 1)}</td><td className="px-3 py-2">{row.attemptNo}{row.selected ? " · 采用" : ""}</td><td className="px-3 py-2">{row.validity}</td><td className="max-w-52 truncate px-3 py-2" title={row.invalidReason}>{row.invalidReason || "--"}</td><td className="px-3 py-2">{formatNumber(row.voltageV)}</td><td className="px-3 py-2">{formatNumber(row.powerW)}</td><td className="px-3 py-2">{formatNumber(row.efficiency)}</td><td className="px-3 py-2">{formatNumber(row.centroidNm)}</td><td className="px-3 py-2">{formatNumber(row.fwhmNm)}</td></tr>)}</tbody></table></div>
}

function ComparisonPanel({ comparisons }: { comparisons: NonNullable<BackendSnapshot["records"]>["comparison"] }) {
  const data = useMemoComparison(comparisons)
  return <ChartPanel title="多轮测试功率对比" data={data.rows} xKey="currentA" lines={data.lines} />
}

function useMemoComparison(comparisons: NonNullable<BackendSnapshot["records"]>["comparison"]) {
  return useMemo(() => {
    const byCurrent = new Map<number, Record<string, number | null>>()
    comparisons.forEach((comparison, index) => comparison.points.forEach((point) => {
      if (point.currentA == null) return
      const row = byCurrent.get(point.currentA) ?? { currentA: point.currentA }
      row[`session${index}`] = point.powerW
      byCurrent.set(point.currentA, row)
    }))
    const colors = ["#2563eb", "#16a34a", "#f59e0b", "#9333ea", "#e11d48"]
    return { rows: [...byCurrent.values()].sort((a, b) => Number(a.currentA) - Number(b.currentA)), lines: comparisons.map((comparison, index) => ({ key: `session${index}`, label: comparison.label, color: colors[index] })) }
  }, [comparisons])
}

function DataTable({ rows }: { rows: Array<Record<string, number | null>> }) {
  const headers = [
    ["currentA", "电流 (A)"], ["voltageV", "电压 (V)"], ["powerW", "功率 (W)"], ["efficiency", "效率"], ["centroidNm", "中心波长"], ["fwhmNm", "FWHM"], ["pib", "PIB"], ["smsrDb", "SMSR (dB)"],
  ] as const
  return <div className="overflow-auto rounded-lg border"><table className="w-full text-left text-xs"><thead className="bg-muted/50 text-muted-foreground"><tr>{headers.map(([, label]) => <th className="px-3 py-2 font-medium" key={label}>{label}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr className="border-t" key={index}>{headers.map(([key]) => <td className="px-3 py-2" key={key}>{formatNumber(row[key], "", key === "efficiency" || key === "pib" ? 4 : 3)}</td>)}</tr>)}</tbody></table>{rows.length === 0 && <p className="p-8 text-center text-sm text-zinc-600">尚无测试点</p>}</div>
}

function PdPage({ snapshot, active, pending, run }: { snapshot: BackendSnapshot | null; active: boolean; pending: boolean; run: RunCommand }) {
  const pd = snapshot?.pd
  const [settings, setSettings] = useState(pd?.settings)
  useEffect(() => { if (!settings && pd?.settings) setSettings(pd.settings) }, [pd?.settings, settings])
  const current = settings ?? { device: "", channel: "", terminal: "DIFF", range: 10, sampleRateHz: 1000, blockSize: 100, scale: 1, offset: 0, unit: "V", save: true, outputDir: "" }
  const set = (key: string, value: unknown) => setSettings({ ...current, [key]: value })
  return (
    <>
      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(360px,380px)_minmax(0,1fr)]">
        <Card className="shadow-none"><CardHeader><CardTitle className="text-base">NI-DAQ 采集设置</CardTitle><CardDescription>PD 采集可在电源加电期间独立启动或停止</CardDescription></CardHeader><CardContent className="grid grid-cols-2 gap-3">
          <div className="col-span-2 pt-1"><p className="text-xs font-medium uppercase tracking-[0.12em] text-zinc-400">采集参数</p></div>
          <Field label="采集卡" className="col-span-2">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
              <NativeSelect value={current.device} onChange={(v) => set("device", v)}><option value="">请选择</option>{pd?.devices.map((item) => <option key={item} value={item}>{item}</option>)}</NativeSelect>
              <Button disabled={!active || pending || pd?.state === "running"} onClick={() => void run("pd.refresh")} variant="outline"><RefreshCw className="size-4" />识别</Button>
            </div>
          </Field>
          <Field label="输入通道"><NativeSelect value={current.channel} onChange={(v) => set("channel", v)}><option value="">请选择</option>{pd?.channels.map((item) => <option key={item} value={item}>{item}</option>)}</NativeSelect></Field>
          <Field label="接线方式"><NativeSelect value={current.terminal} onChange={(v) => set("terminal", v)}><option value="DIFF">差分 DIFF</option><option value="RSE">参考单端 RSE</option></NativeSelect></Field>
          <Field label="输入量程"><NativeSelect value={String(current.range ?? "")} onChange={(v) => set("range", asNumber(v))}><option value="">自动</option>{pd?.ranges.map((item) => <option key={String(item.value)} value={String(item.value)}>{item.label}</option>)}</NativeSelect></Field>
          <Field label="采样率 (S/s)"><Input type="number" value={current.sampleRateHz} onChange={(e) => set("sampleRateHz", asNumber(e.target.value))} /></Field>
          <Separator className="col-span-2 my-1" />
          <div className="col-span-2"><p className="text-xs font-medium uppercase tracking-[0.12em] text-zinc-400">标定参数</p></div>
          <Field label="每批点数"><Input type="number" value={current.blockSize} onChange={(e) => set("blockSize", asNumber(e.target.value))} /></Field>
          <Field label="标定比例"><Input type="number" value={current.scale} onChange={(e) => set("scale", asNumber(e.target.value))} /></Field>
          <Field label="标定偏置"><Input type="number" value={current.offset} onChange={(e) => set("offset", asNumber(e.target.value))} /></Field>
          <Field label="显示单位"><Input value={current.unit} onChange={(e) => set("unit", e.target.value)} /></Field>
          <Separator className="col-span-2 my-1" />
          <div className="col-span-2 rounded-lg border border-border/70 bg-[#080808]/70 p-3">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div><p className="text-sm font-medium">保存设置</p><p className="mt-0.5 text-xs text-muted-foreground">可选：保存本次采集的原始数据</p></div>
              <label className="flex shrink-0 items-center gap-2 text-sm"><input checked={current.save} onChange={(e) => set("save", e.target.checked)} type="checkbox" />保存原始数据</label>
            </div>
            <Field label="保存目录"><Input value={current.outputDir} onChange={(e) => set("outputDir", e.target.value)} /></Field>
          </div>
          <div className="col-span-2 space-y-2">
            <div className="flex items-center gap-2 text-xs text-muted-foreground"><span className="grid size-5 place-items-center rounded-full bg-blue-500/15 font-semibold text-blue-300">1</span><span>先识别采集卡，再开始采集</span></div>
            <Button className="w-full" disabled={!active || pending || (pd?.state !== "running" && !current.device)} onClick={() => void run(pd?.state === "running" ? "pd.stop" : "pd.start", current as unknown as Record<string, unknown>)}>{pd?.state === "running" ? <><Square className="size-4" />停止并保存</> : <><Play className="size-4" />开始采集</>}</Button>
          </div>
          <p className="col-span-2 text-xs text-muted-foreground">{pd?.status || "等待识别采集卡"}</p>
        </CardContent></Card>
        <div className="space-y-4">
          <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">{[["当前值", pd?.currentValue ?? "--"], ["电压", pd?.voltage ?? "--"], ["采样数", pd?.sampleCount ?? "0"]].map(([label, value]) => <Card className="py-4 shadow-none" key={label}><CardContent><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold">{value}</p></CardContent></Card>)}</section>
          <ChartPanel heightClassName="h-64" title="PD 实时趋势" data={(pd?.points ?? []) as Array<Record<string, number>>} xKey="elapsedS" lines={[{ key: "value", label: current.unit || "PD", color: "#2563eb" }]} empty="开始采集后显示实时趋势" />
          <Card className="py-4 shadow-none"><CardContent className="grid grid-cols-3 gap-4 text-sm"><div><p className="text-xs text-muted-foreground">批次均值</p><p className="mt-1 font-medium">{pd?.mean ?? "--"}</p></div><div><p className="text-xs text-muted-foreground">标准差</p><p className="mt-1 font-medium">{pd?.standardDeviation ?? "--"}</p></div><div><p className="text-xs text-muted-foreground">最小 / 最大</p><p className="mt-1 font-medium">{pd?.rangeText ?? "--"}</p></div></CardContent></Card>
        </div>
      </section>
    </>
  )
}

export default App
