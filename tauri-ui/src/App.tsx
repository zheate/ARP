import { useEffect, useState, type ReactNode } from "react"
import {
  Activity,
  Archive,
  BarChart3,
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
  ShieldCheck,
  SlidersHorizontal,
  Square,
} from "lucide-react"
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
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

const navigation = [
  { id: "automatic" as const, label: "自动测试", icon: CircleGauge },
  { id: "manual" as const, label: "手动测试", icon: SlidersHorizontal },
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

function asNumber(value: string): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function formatNumber(value: number | null | undefined, suffix = "", digits = 3): string {
  return value == null || !Number.isFinite(value) ? "--" : `${value.toFixed(digits)}${suffix}`
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
  return (
    <select
      className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm shadow-xs outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 disabled:bg-slate-100"
      disabled={disabled}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {children}
    </select>
  )
}

function StatusDot({ state }: { state?: string }) {
  const color = state === "connected" || state === "running"
    ? "bg-emerald-500"
    : state === "error"
      ? "bg-red-500"
      : state === "connecting"
        ? "bg-amber-400"
        : "bg-slate-300"
  return <span className={`size-2 shrink-0 rounded-full ${color}`} />
}

function DeviceCard({ title, icon, device }: { title: string; icon: ReactNode; device?: DeviceSnapshot }) {
  return (
    <Card className="gap-3 border-slate-200 py-4 shadow-sm">
      <CardContent className="flex items-center gap-3 px-4">
        <div className="grid size-10 place-items-center rounded-xl bg-slate-100 text-slate-600">{icon}</div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2 text-sm font-medium">
            <span>{title}</span><StatusDot state={device?.state} />
          </div>
          <p className="mt-1 truncate text-xs text-slate-500" title={device?.detail}>{device?.detail || "未连接"}</p>
        </div>
      </CardContent>
    </Card>
  )
}

function ChartPanel({ title, data, xKey, lines, empty = "暂无实时数据" }: {
  title: string
  data: Array<Record<string, number | null>>
  xKey: string
  lines: Array<{ key: string; label: string; color: string; yAxisId?: string }>
  empty?: string
}) {
  return (
    <Card className="border-slate-200 shadow-sm">
      <CardHeader className="pb-2"><CardTitle className="text-sm">{title}</CardTitle></CardHeader>
      <CardContent className="h-56 px-3 pb-3">
        {data.length === 0 ? (
          <div className="grid h-full place-items-center rounded-lg bg-slate-50 text-sm text-slate-400">{empty}</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 16, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey={xKey} tick={{ fontSize: 10 }} />
              <YAxis yAxisId="left" tick={{ fontSize: 10 }} />
              {lines.some((line) => line.yAxisId === "right") && <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 10 }} />}
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
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
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

function App() {
  const { snapshot, error, loading, commandPending, refresh, command } = useBackendSnapshot()
  const [page, setPage] = useState<Page>("automatic")
  const [config, setConfig] = useState<AppConfiguration>(emptyConfig)
  const [dirty, setDirty] = useState(false)
  const [selectedSession, setSelectedSession] = useState("")
  const active = snapshot?.backend.mode === "active"

  useEffect(() => {
    if (snapshot?.configuration && !dirty) setConfig(snapshot.configuration)
  }, [snapshot?.capturedAt, snapshot?.configuration, dirty])

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
  const title = navigation.find((item) => item.id === page)?.label ?? "自动测试"

  return (
    <div className="min-h-screen bg-[#f4f7fb] text-slate-950">
      <div className="grid min-h-screen grid-cols-[232px_minmax(0,1fr)]">
        <aside className="flex min-h-screen flex-col bg-[#122033] px-4 py-5 text-white">
          <div className="flex items-center gap-3 px-2">
            <div className="grid size-10 place-items-center rounded-xl bg-blue-500 shadow-lg shadow-blue-950/30"><Activity className="size-5" /></div>
            <div><p className="text-sm font-semibold tracking-wide">ARP 综合测试</p><p className="mt-0.5 text-xs text-slate-400">光电测试工作台</p></div>
          </div>
          <p className="mt-8 px-2 text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">工作区</p>
          <nav className="mt-3 space-y-1.5">
            {navigation.map((item) => (
              <button
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${page === item.id ? "bg-white/10 font-medium text-white ring-1 ring-white/10" : "text-slate-400 hover:bg-white/5 hover:text-slate-200"}`}
                key={item.id}
                onClick={() => setPage(item.id)}
                type="button"
              >
                <item.icon className="size-4" /><span>{item.label}</span>{page === item.id && <span className="ml-auto size-1.5 rounded-full bg-blue-400" />}
              </button>
            ))}
          </nav>
          <div className="mt-auto rounded-xl border border-white/10 bg-white/[0.04] p-3.5">
            <div className="flex items-center gap-2 text-xs font-medium text-slate-200"><ShieldCheck className="size-4 text-emerald-400" />安全控制在 Python</div>
            <p className="mt-2 text-xs leading-5 text-slate-400">{snapshot?.safety.detail ?? "正在连接本地控制器…"}</p>
          </div>
        </aside>

        <main className="min-w-0">
          <header className="flex h-[72px] items-center justify-between border-b border-slate-200/80 bg-white px-6">
            <div>
              <div className="flex items-center gap-2"><h1 className="text-xl font-semibold tracking-tight">{title}</h1><Badge variant="outline" className={active ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-amber-200 bg-amber-50 text-amber-700"}>{active ? "控制器已接入" : "只读兼容模式"}</Badge></div>
              <p className="mt-1 text-xs text-slate-500">{snapshot?.status?.message ?? error ?? (loading ? "正在连接 Python 后端" : "等待后端")}</p>
            </div>
            <div className="flex items-center gap-2">
              {dirty && <Button disabled={!active || commandPending} onClick={() => void saveConfiguration()} size="sm" variant="outline"><Save className="size-4" />保存设置</Button>}
              <Button aria-label="刷新" disabled={loading} onClick={() => void refresh()} size="icon" variant="outline"><RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} /></Button>
              <Button className="bg-red-600 hover:bg-red-700" disabled={!active || commandPending} onClick={() => void run("app.stopAll")} size="sm"><OctagonAlert className="size-4" />紧急停止</Button>
            </div>
          </header>

          <div className="space-y-4 p-5 lg:p-6">
            {error && <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
            {notices.map((notice, index) => <div className={`rounded-lg border px-4 py-3 text-sm ${notice.level === "error" ? "border-red-200 bg-red-50 text-red-700" : "border-amber-200 bg-amber-50 text-amber-800"}`} key={`${notice.title}-${index}`}><b>{notice.title}：</b>{notice.message}</div>)}
            {!active && snapshot && <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">当前 Python 环境未加载 PySide6/设备驱动，因此保留为只读状态。使用项目指定环境启动后，下面的控制会自动开放。</div>}

            {page === "automatic" && <AutomaticPage snapshot={snapshot} config={config} update={update} active={active} pending={commandPending} readyCount={devicesReady} readyTotal={requiredDevices} progress={progress} run={run} />}
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

function AutomaticPage({ snapshot, config, update, active, pending, readyCount, readyTotal, progress, run }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; readyCount: number; readyTotal: number; progress: number; run: RunCommand
}) {
  const auto = snapshot?.automaticTest
  const running = auto && !["idle", "completed", "paused"].includes(auto.state)
  return (
    <>
      <section className="grid grid-cols-3 gap-4">
        <DeviceCard title="电源" icon={<Power className="size-5" />} device={snapshot?.devices.powerSupply} />
        <DeviceCard title="功率计" icon={<Gauge className="size-5" />} device={snapshot?.devices.powerMeter} />
        <DeviceCard title="光谱仪" icon={<Radio className="size-5" />} device={snapshot?.devices.spectrometer} />
      </section>
      <section className="grid grid-cols-[minmax(360px,0.9fr)_minmax(520px,1.35fr)] gap-4">
        <Card className="border-slate-200 shadow-sm">
          <CardHeader><CardTitle className="text-base">测试任务与计划</CardTitle><CardDescription>参数直接交给现有自动测试控制器</CardDescription></CardHeader>
          <CardContent className="grid grid-cols-2 gap-3">
            <Field label="产品 SN" className="col-span-2"><Input value={config.sn} onChange={(e) => update("sn", e.target.value)} /></Field>
            <Field label="产品型号"><Input value={config.productModel} onChange={(e) => update("productModel", e.target.value)} /></Field>
            <Field label="生产批次"><Input value={config.batch} onChange={(e) => update("batch", e.target.value)} /></Field>
            <Field label="测试站别"><Input value={config.station} onChange={(e) => update("station", e.target.value)} /></Field>
            <Field label="输出目录"><Input value={config.outputDir} onChange={(e) => update("outputDir", e.target.value)} /></Field>
            <Separator className="col-span-2 my-1" />
            <NumberField label="起始电流 (A)" value={config.initialCurrentA} onChange={(v) => update("initialCurrentA", v)} />
            <NumberField label="目标电流 (A)" value={config.targetCurrentA} onChange={(v) => update("targetCurrentA", v)} />
            <NumberField label="电流间隔 (A)" value={config.currentStepA} onChange={(v) => update("currentStepA", v)} />
            <NumberField label="单点超时 (s)" value={config.pointTimeoutS} onChange={(v) => update("pointTimeoutS", v)} />
            <NumberField label="下电步长 (A)" value={config.rampDownStepA} onChange={(v) => update("rampDownStepA", v)} />
            <NumberField label="下电间隔 (s)" value={config.rampDownIntervalS} onChange={(v) => update("rampDownIntervalS", v)} step="0.1" />
            <label className="col-span-2 flex items-center gap-2 text-sm"><input checked={config.useSpectrometer} onChange={(e) => update("useSpectrometer", e.target.checked)} type="checkbox" />同时采集光谱并判断波长稳定</label>
            <div className="col-span-2 rounded-lg border bg-slate-50 p-3">
              <div className="flex justify-between text-sm"><b>准备状态</b><span>{readyCount} / {readyTotal}</span></div><Progress className="mt-2 h-1.5" value={readyCount / readyTotal * 100} />
              <p className="mt-2 text-xs text-slate-500">{auto?.settingsError || auto?.detail || "等待配置"}</p>
            </div>
            <div className="col-span-2 grid grid-cols-2 gap-2">
              <Button disabled={!active || pending || !auto?.canStart} onClick={() => void run("automatic.start", config as unknown as Record<string, unknown>)}><Play className="size-4" />开始自动测试</Button>
              <Button disabled={!active || pending || !auto?.canRetry} onClick={() => void run("automatic.retry")} variant="outline"><RefreshCw className="size-4" />重试当前点</Button>
              <Button disabled={!active || pending || !auto?.canEnd} onClick={() => void run("automatic.end")} variant="destructive"><Square className="size-4" />结束并安全下电</Button>
              <Button disabled={!active || pending || running} onClick={() => void run("automatic.reset")} variant="outline"><RotateCcw className="size-4" />返回设置</Button>
            </div>
          </CardContent>
        </Card>
        <div className="space-y-4">
          <Card className="border-slate-200 shadow-sm">
            <CardContent className="grid grid-cols-[1fr_auto] items-center gap-4 py-4">
              <div><div className="flex items-center gap-2"><StatusDot state={auto?.state === "paused" ? "error" : running ? "connected" : "disconnected"} /><b className="text-sm">{auto?.detail || "未开始"}</b></div><Progress className="mt-3 h-2" value={progress} /><p className="mt-2 text-xs text-slate-500">测试点 {Math.max(0, (auto?.currentIndex ?? -1) + 1)} / {auto?.currents?.length ?? 0}</p></div>
              <div className="text-right"><p className="text-2xl font-semibold">{formatNumber(auto?.currentA, " A", 1)}</p><p className="text-xs text-slate-500">当前测试电流</p></div>
            </CardContent>
          </Card>
          <ChartPanel title="实时光功率" data={(snapshot?.measurements?.power ?? []) as Array<Record<string, number>>} xKey="elapsedS" lines={[{ key: "powerW", label: "光功率 (W)", color: "#2563eb" }]} />
          <ChartPanel title="光谱" data={(snapshot?.measurements?.spectrum ?? []) as Array<Record<string, number>>} xKey="wavelengthNm" lines={[{ key: "intensity", label: "强度", color: "#65a30d" }]} />
        </div>
      </section>
    </>
  )
}

function NumberField({ label, value, onChange, step = "1" }: { label: string; value: number; onChange: (value: number) => void; step?: string }) {
  return <Field label={label}><Input type="number" step={step} value={value} onChange={(e) => onChange(asNumber(e.target.value))} /></Field>
}

function ManualPage({ snapshot, config, update, active, pending, run }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
}) {
  const psu = snapshot?.devices.powerSupply
  const meter = snapshot?.devices.powerMeter
  const spectrum = snapshot?.devices.spectrometer
  return (
    <>
      <section className="grid grid-cols-3 gap-4">
        <Card className="border-slate-200 shadow-sm"><CardHeader><CardTitle className="text-base">电源控制</CardTitle></CardHeader><CardContent className="space-y-3">
          <Field label="控制器"><NativeSelect value={config.powerSupplyKind} onChange={(v) => update("powerSupplyKind", v as "ch341" | "tdk")}><option value="ch341">CH341 I²C</option><option value="tdk">TDK RS232</option></NativeSelect></Field>
          {config.powerSupplyKind === "tdk" && <Field label="TDK 串口"><Input value={config.tdkResource} onChange={(e) => update("tdkResource", e.target.value)} /></Field>}
          <NumberField label="设定电流 (A)" value={config.setCurrentA} onChange={(v) => update("setCurrentA", v)} step="0.1" />
          {config.powerSupplyKind === "tdk" && <NumberField label="输出电压 (V)" value={config.tdkVoltageV} onChange={(v) => update("tdkVoltageV", v)} step="0.1" />}
          <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending} onClick={() => void run(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)} variant={psu?.connected ? "outline" : "default"}>{psu?.connected ? "安全断开" : "连接电源"}</Button><Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)}>设置电流</Button></div>
          {config.powerSupplyKind === "tdk" && <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setVoltage", { voltageV: config.tdkVoltageV }, true)} variant="outline">设置电压</Button><Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setOutput", { enabled: !psu?.outputEnabled })} variant={psu?.outputEnabled ? "destructive" : "outline"}>{psu?.outputEnabled ? "关闭输出" : "开启输出"}</Button></div>}
          <div className="grid grid-cols-2 gap-2">
            <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputVoltage" })} size="sm" variant="outline">读取输出电压</Button>
            <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputCurrent" })} size="sm" variant="outline">读取输出电流</Button>
            {config.powerSupplyKind === "ch341" && <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "inputVoltage" })} size="sm" variant="outline">读取输入电压</Button>}
            {config.powerSupplyKind === "ch341" && <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "temperature" })} size="sm" variant="outline">读取模块温度</Button>}
          </div>
          <p className="text-xs text-slate-500">{psu?.detail || "未连接"} · 当前 {formatNumber(psu?.activeCurrentA, " A", 2)}</p>
        </CardContent></Card>

        <Card className="border-slate-200 shadow-sm"><CardHeader><CardTitle className="text-base">功率计</CardTitle></CardHeader><CardContent className="space-y-3">
          <Field label="串口资源"><Input value={config.powerMeterResource} onChange={(e) => update("powerMeterResource", e.target.value)} list="power-resources" /><datalist id="power-resources">{meter?.resources?.map((item) => <option key={item} value={item} />)}</datalist></Field>
          <NumberField label="校准波长 (nm)" value={config.powerMeterWavelengthNm} onChange={(v) => update("powerMeterWavelengthNm", v)} step="0.1" />
          <NumberField label="软件增益" value={config.softwareGain} onChange={(v) => update("softwareGain", v)} step="0.01" />
          <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending} onClick={() => void run("device.refresh", { device: "powerMeter" }, true)} variant="outline"><RefreshCw className="size-4" />识别</Button><Button disabled={!active || pending} onClick={() => void run(meter?.running ? "powerMeter.stop" : "powerMeter.start", {}, true)}>{meter?.running ? "停止采集" : "开始采集"}</Button></div>
          <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending || meter?.running} onClick={() => void run("powerMeter.setRelativeZero", { enabled: true }, true)} variant="outline">相对调零</Button><Button disabled={!active || pending || meter?.running} onClick={() => void run("powerMeter.setRelativeZero", { enabled: false }, true)} variant="outline">取消调零</Button></div>
          <p className="text-xs text-slate-500">实时功率 {formatNumber(meter?.powerW, " W")} · {meter?.stable ? "已稳定" : "稳定中"}</p>
        </CardContent></Card>

        <Card className="border-slate-200 shadow-sm"><CardHeader><CardTitle className="text-base">光谱仪</CardTitle></CardHeader><CardContent className="space-y-3">
          <NumberField label="积分时间 (μs)" value={config.integrationTimeUs} onChange={(v) => update("integrationTimeUs", v)} />
          <NumberField label="刷新间隔 (ms)" value={config.spectrometerIntervalMs} onChange={(v) => update("spectrometerIntervalMs", v)} />
          <label className="flex items-center gap-2 text-sm"><input checked={config.autoIntegration} onChange={(e) => update("autoIntegration", e.target.checked)} type="checkbox" />自动积分</label>
          <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending} onClick={() => void run("device.refresh", { device: "spectrometer" }, true)} variant="outline"><RefreshCw className="size-4" />识别</Button><Button disabled={!active || pending} onClick={() => void run(spectrum?.running ? "spectrometer.stop" : "spectrometer.start", {}, true)}>{spectrum?.running ? "停止采集" : "开始采集"}</Button></div>
          <div className="grid grid-cols-2 gap-2"><Button disabled={!active || pending || !snapshot?.measurements?.spectrum.length} onClick={() => void run("spectrometer.saveCsv")} variant="outline"><Download className="size-4" />保存光谱 CSV</Button><Button disabled={!active || pending} onClick={() => void run("charts.reset")} variant="outline"><RotateCcw className="size-4" />清空曲线</Button></div>
          <p className="text-xs text-slate-500">中心 {formatNumber(spectrum?.centroidNm, " nm")} · FWHM {formatNumber(spectrum?.fwhmNm, " nm")}</p>
        </CardContent></Card>
      </section>
      <section className="grid grid-cols-2 gap-4">
        <ChartPanel title="功率实时" data={(snapshot?.measurements?.power ?? []) as Array<Record<string, number>>} xKey="elapsedS" lines={[{ key: "powerW", label: "功率 (W)", color: "#2563eb" }]} />
        <ChartPanel title="功率 / 效率" data={(snapshot?.measurements?.stable ?? []) as Array<Record<string, number | null>>} xKey="currentA" lines={[{ key: "powerW", label: "功率 (W)", color: "#16a34a" }, { key: "efficiencyPercent", label: "效率 (%)", color: "#f59e0b", yAxisId: "right" }]} />
        <div className="col-span-2"><ChartPanel title="光谱" data={(snapshot?.measurements?.spectrum ?? []) as Array<Record<string, number>>} xKey="wavelengthNm" lines={[{ key: "intensity", label: "强度", color: "#65a30d" }]} /></div>
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
        ].map(([label, value]) => <Card className="border-slate-200 py-4 shadow-sm" key={label}><CardContent><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-xl font-semibold">{value}</p></CardContent></Card>)}
      </section>
      <Card className="border-slate-200 shadow-sm"><CardHeader className="flex-row items-center justify-between"><div><CardTitle className="text-base">本轮测试点</CardTitle><CardDescription>{records?.workbookPath || "尚未创建测试会话"}</CardDescription></div><div className="flex gap-2">{records?.workbookPath && <Button onClick={() => void revealItemInDir(records.workbookPath)} variant="outline">打开所在文件夹</Button>}<Button disabled={!active || pending || !records?.unsavedCount} onClick={() => void run("records.exportCurrent")}><Download className="size-4" />保存 Excel ({records?.unsavedCount ?? 0})</Button></div></CardHeader><CardContent><DataTable rows={records?.current ?? []} /></CardContent></Card>
      <Card className="border-slate-200 shadow-sm">
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
          <div className="overflow-auto rounded-lg border"><table className="w-full text-left text-xs"><thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2">对比</th>{["时间", "SN", "型号", "批次", "站别", "模式", "状态", "导出"].map((item) => <th className="px-3 py-2 font-medium" key={item}>{item}</th>)}</tr></thead><tbody>{records?.history.map((row) => <tr className={`cursor-pointer border-t hover:bg-blue-50 ${selected === row.sessionId ? "bg-blue-50" : ""}`} key={row.sessionId} onClick={() => selectRow(row.sessionId)}><td className="px-3 py-2"><input checked={checked.includes(row.sessionId)} onChange={() => toggleChecked(row.sessionId)} onClick={(e) => e.stopPropagation()} type="checkbox" /></td><td className="whitespace-nowrap px-3 py-2">{new Date(row.startedAt).toLocaleString()}</td><td className="px-3 py-2 font-medium">{row.sn}</td><td className="px-3 py-2">{row.productModel || "--"}</td><td className="px-3 py-2">{row.batch || "--"}</td><td className="px-3 py-2">{row.station || "--"}</td><td className="px-3 py-2">{row.mode === "automatic" ? "自动" : "手动"}</td><td className="px-3 py-2">{row.status}</td><td className="px-3 py-2">{row.exportState}</td></tr>)}</tbody></table>{!records?.history.length && <p className="p-8 text-center text-sm text-slate-400">当前目录还没有历史测试</p>}</div>
          <div className="flex flex-wrap justify-end gap-2"><Button disabled={!active || pending || checked.length === 0} onClick={() => void run("records.compare", { sessionIds: checked })} variant="outline">对比所选 ({checked.length})</Button><Button disabled={!active || pending || !selectedRow || selectedRow.status !== "incomplete"} onClick={() => void run("records.resume", { sessionId: selected })} variant="outline"><RotateCcw className="size-4" />继续未完成测试</Button><Button disabled={!active || pending || !selectedRow} onClick={() => void run("records.reexport", { sessionId: selected })} variant="outline"><Download className="size-4" />重新导出</Button>{selectedRow?.workbookPath && <Button onClick={() => void openPath(selectedRow.workbookPath)} variant="outline">打开 Excel</Button>}{selectedRow?.workbookPath && <Button onClick={() => void revealItemInDir(selectedRow.workbookPath)} variant="outline">打开文件夹</Button>}</div>
        </CardContent>
      </Card>
      {records?.detail && <Card className="border-slate-200 shadow-sm"><CardHeader><CardTitle className="text-base">记录详情 · {String(records.detail.sn ?? "")}</CardTitle><CardDescription>{String(records.detail.terminationReason ?? "") || "查看每个测试点及复测尝试"}</CardDescription></CardHeader><CardContent><AttemptTable rows={records.attempts} /></CardContent></Card>}
      {!!records?.comparison.length && <ComparisonPanel comparisons={records.comparison} />}
    </>
  )
}

function AttemptTable({ rows }: { rows: NonNullable<BackendSnapshot["records"]>["attempts"] }) {
  return <div className="overflow-auto rounded-lg border"><table className="w-full text-left text-xs"><thead className="bg-slate-50"><tr>{["点", "目标电流", "尝试", "状态", "原因", "电压", "功率", "效率", "中心波长", "FWHM"].map((label) => <th className="px-3 py-2 font-medium" key={label}>{label}</th>)}</tr></thead><tbody>{rows.map((row) => <tr className="border-t" key={row.attemptId}><td className="px-3 py-2">{row.sequenceIndex + 1}</td><td className="px-3 py-2">{formatNumber(row.targetCurrentA, " A", 1)}</td><td className="px-3 py-2">{row.attemptNo}{row.selected ? " · 采用" : ""}</td><td className="px-3 py-2">{row.validity}</td><td className="max-w-52 truncate px-3 py-2" title={row.invalidReason}>{row.invalidReason || "--"}</td><td className="px-3 py-2">{formatNumber(row.voltageV)}</td><td className="px-3 py-2">{formatNumber(row.powerW)}</td><td className="px-3 py-2">{formatNumber(row.efficiency)}</td><td className="px-3 py-2">{formatNumber(row.centroidNm)}</td><td className="px-3 py-2">{formatNumber(row.fwhmNm)}</td></tr>)}</tbody></table></div>
}

function ComparisonPanel({ comparisons }: { comparisons: NonNullable<BackendSnapshot["records"]>["comparison"] }) {
  const data = useMemoComparison(comparisons)
  return <ChartPanel title="多轮测试功率对比" data={data.rows} xKey="currentA" lines={data.lines} />
}

function useMemoComparison(comparisons: NonNullable<BackendSnapshot["records"]>["comparison"]) {
  const byCurrent = new Map<number, Record<string, number | null>>()
  comparisons.forEach((comparison, index) => comparison.points.forEach((point) => {
    if (point.currentA == null) return
    const row = byCurrent.get(point.currentA) ?? { currentA: point.currentA }
    row[`session${index}`] = point.powerW
    byCurrent.set(point.currentA, row)
  }))
  const colors = ["#2563eb", "#16a34a", "#f59e0b", "#9333ea", "#e11d48"]
  return { rows: [...byCurrent.values()].sort((a, b) => Number(a.currentA) - Number(b.currentA)), lines: comparisons.map((comparison, index) => ({ key: `session${index}`, label: comparison.label, color: colors[index] })) }
}

function DataTable({ rows }: { rows: Array<Record<string, number | null>> }) {
  const headers = [
    ["currentA", "电流 (A)"], ["voltageV", "电压 (V)"], ["powerW", "功率 (W)"], ["efficiency", "效率"], ["centroidNm", "中心波长"], ["fwhmNm", "FWHM"], ["pib", "PIB"], ["smsrDb", "SMSR (dB)"],
  ] as const
  return <div className="overflow-auto rounded-lg border"><table className="w-full text-left text-xs"><thead className="bg-slate-50 text-slate-500"><tr>{headers.map(([, label]) => <th className="px-3 py-2 font-medium" key={label}>{label}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr className="border-t" key={index}>{headers.map(([key]) => <td className="px-3 py-2" key={key}>{formatNumber(row[key], "", key === "efficiency" || key === "pib" ? 4 : 3)}</td>)}</tr>)}</tbody></table>{rows.length === 0 && <p className="p-8 text-center text-sm text-slate-400">尚无测试点</p>}</div>
}

function PdPage({ snapshot, active, pending, run }: { snapshot: BackendSnapshot | null; active: boolean; pending: boolean; run: RunCommand }) {
  const pd = snapshot?.pd
  const [settings, setSettings] = useState(pd?.settings)
  useEffect(() => { if (!settings && pd?.settings) setSettings(pd.settings) }, [pd?.settings, settings])
  const current = settings ?? { device: "", channel: "", terminal: "DIFF", range: 10, sampleRateHz: 1000, blockSize: 100, scale: 1, offset: 0, unit: "V", save: true, outputDir: "" }
  const set = (key: string, value: unknown) => setSettings({ ...current, [key]: value })
  return (
    <>
      <section className="grid grid-cols-[380px_minmax(520px,1fr)] gap-4">
        <Card className="border-slate-200 shadow-sm"><CardHeader><CardTitle className="text-base">NI-DAQ 采集设置</CardTitle><CardDescription>PD 采集可在电源加电期间独立启动或停止</CardDescription></CardHeader><CardContent className="grid grid-cols-2 gap-3">
          <Field label="采集卡" className="col-span-2"><NativeSelect value={current.device} onChange={(v) => set("device", v)}><option value="">请选择</option>{pd?.devices.map((item) => <option key={item} value={item}>{item}</option>)}</NativeSelect></Field>
          <Field label="输入通道"><NativeSelect value={current.channel} onChange={(v) => set("channel", v)}><option value="">请选择</option>{pd?.channels.map((item) => <option key={item} value={item}>{item}</option>)}</NativeSelect></Field>
          <Field label="接线方式"><NativeSelect value={current.terminal} onChange={(v) => set("terminal", v)}><option value="DIFF">差分 DIFF</option><option value="RSE">参考单端 RSE</option></NativeSelect></Field>
          <Field label="输入量程"><NativeSelect value={String(current.range ?? "")} onChange={(v) => set("range", asNumber(v))}><option value="">自动</option>{pd?.ranges.map((item) => <option key={String(item.value)} value={String(item.value)}>{item.label}</option>)}</NativeSelect></Field>
          <Field label="采样率 (S/s)"><Input type="number" value={current.sampleRateHz} onChange={(e) => set("sampleRateHz", asNumber(e.target.value))} /></Field>
          <Field label="每批点数"><Input type="number" value={current.blockSize} onChange={(e) => set("blockSize", asNumber(e.target.value))} /></Field>
          <Field label="标定比例"><Input type="number" value={current.scale} onChange={(e) => set("scale", asNumber(e.target.value))} /></Field>
          <Field label="标定偏置"><Input type="number" value={current.offset} onChange={(e) => set("offset", asNumber(e.target.value))} /></Field>
          <Field label="显示单位"><Input value={current.unit} onChange={(e) => set("unit", e.target.value)} /></Field>
          <label className="flex items-end gap-2 pb-2 text-sm"><input checked={current.save} onChange={(e) => set("save", e.target.checked)} type="checkbox" />保存原始数据</label>
          <Field label="保存目录" className="col-span-2"><Input value={current.outputDir} onChange={(e) => set("outputDir", e.target.value)} /></Field>
          <div className="col-span-2 grid grid-cols-2 gap-2"><Button disabled={!active || pending || pd?.state === "running"} onClick={() => void run("pd.refresh")} variant="outline"><RefreshCw className="size-4" />识别采集卡</Button><Button disabled={!active || pending || (pd?.state !== "running" && !current.device)} onClick={() => void run(pd?.state === "running" ? "pd.stop" : "pd.start", current as unknown as Record<string, unknown>)}>{pd?.state === "running" ? <><Square className="size-4" />停止并保存</> : <><Play className="size-4" />开始采集</>}</Button></div>
          <p className="col-span-2 text-xs text-slate-500">{pd?.status || "等待识别采集卡"}</p>
        </CardContent></Card>
        <div className="space-y-4">
          <section className="grid grid-cols-3 gap-3">{[["当前值", pd?.currentValue ?? "--"], ["电压", pd?.voltage ?? "--"], ["采样数", pd?.sampleCount ?? "0"]].map(([label, value]) => <Card className="border-slate-200 py-4 shadow-sm" key={label}><CardContent><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-lg font-semibold">{value}</p></CardContent></Card>)}</section>
          <ChartPanel title="PD 实时趋势" data={(pd?.points ?? []) as Array<Record<string, number>>} xKey="elapsedS" lines={[{ key: "value", label: current.unit || "PD", color: "#2563eb" }]} empty="开始采集后显示实时趋势" />
          <Card className="border-slate-200 py-4 shadow-sm"><CardContent className="grid grid-cols-3 gap-4 text-sm"><div><p className="text-xs text-slate-500">批次均值</p><p className="mt-1 font-medium">{pd?.mean ?? "--"}</p></div><div><p className="text-xs text-slate-500">标准差</p><p className="mt-1 font-medium">{pd?.standardDeviation ?? "--"}</p></div><div><p className="text-xs text-slate-500">最小 / 最大</p><p className="mt-1 font-medium">{pd?.rangeText ?? "--"}</p></div></CardContent></Card>
        </div>
      </section>
    </>
  )
}

export default App
