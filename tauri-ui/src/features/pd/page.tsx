import { ControlRow } from "@/components/control-layout"
import { DeviceDetectButton } from "@/components/device-detect-button"
import { Field } from "@/components/field"
import { NativeSelect } from "@/components/native-select"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { ChartPanel, PD_PLOT_HISTORY_S, PD_TIME_TICK_INTERVAL_S, PLM_CHART_SERIES } from "@/features/charts/realtime"
import { asNumber, NumberInput, RunCommand } from "@/features/shared/controls"
import { samePdSection } from "@/features/shared/snapshot-equality"
import { type BackendSnapshot } from "@/lib/backend"
import { open as openDialog } from "@tauri-apps/plugin-dialog"
import { FolderOpen, Play, Square } from "lucide-react"
import { memo, useEffect, useState } from "react"

export const PdPage = memo(function PdPage({ snapshot, active, pending, run }: { snapshot: BackendSnapshot | null; active: boolean; pending: boolean; run: RunCommand }) {
  const pd = snapshot?.pd
  const pdData = (pd?.points ?? []) as Array<Record<string, number>>
  const latestElapsedS = pdData[pdData.length - 1]?.elapsedS
  const pdTimeDomain: [number, number] = typeof latestElapsedS === "number"
    ? [Math.max(0, latestElapsedS - PD_PLOT_HISTORY_S), Math.max(10, latestElapsedS)]
    : [0, 10]
  const firstPdTickS = Math.ceil(pdTimeDomain[0] / PD_TIME_TICK_INTERVAL_S) * PD_TIME_TICK_INTERVAL_S
  const pdTimeTicks = Array.from(
    { length: Math.floor((pdTimeDomain[1] - firstPdTickS) / PD_TIME_TICK_INTERVAL_S) + 1 },
    (_, index) => firstPdTickS + index * PD_TIME_TICK_INTERVAL_S,
  )
  const [settings, setSettings] = useState(pd?.settings)
  useEffect(() => { if (!settings && pd?.settings) setSettings(pd.settings) }, [pd?.settings, settings])
  const current = settings ?? { device: "", channel: "", terminal: "DIFF", range: 10, sampleRateHz: 1000, blockSize: 100, scale: 1, offset: 0, unit: "V", save: true, outputDir: "" }
  const set = (key: string, value: unknown) => setSettings({ ...current, [key]: value })
  const choosePdOutputDir = async () => {
    if (!active || pending || pd?.state === "running") return
    try {
      const selected = await openDialog({
        multiple: false,
        directory: true,
        title: "选择 PD 数据保存目录",
        defaultPath: current.outputDir.trim() || undefined,
      })
      if (typeof selected === "string" && selected) {
        set("outputDir", selected)
      }
    } catch {
      // dialog cancelled or failed
    }
  }
  return (
    <>
      <section className="pd-workspace grid grid-cols-1 gap-4">
        <Card className="pd-settings-card shadow-none"><CardHeader><CardTitle className="text-base">NI-DAQ 采集设置</CardTitle><CardDescription>PD 采集可在电源加电期间独立启动或停止</CardDescription></CardHeader>          <div className="pd-acquisition-actions space-y-2">
            <div className="flex items-center gap-2 text-xs text-muted-foreground"><span className="grid size-5 place-items-center rounded-full bg-primary/15 font-semibold text-primary">1</span><span>先识别采集卡，再开始采集</span></div>
            <Button className="w-full" disabled={!active || pending || pd?.detecting || (pd?.state !== "running" && !current.device)} onClick={() => void run(pd?.state === "running" ? "pd.stop" : "pd.start", current as unknown as Record<string, unknown>)}>{pd?.state === "running" ? <><Square className="size-4" />停止并保存</> : <><Play className="size-4" />开始采集</>}</Button>
          </div>
          <p className="pd-acquisition-status text-xs text-muted-foreground" role="status">{pd?.status || "等待识别采集卡"}</p>
<CardContent className="pd-settings-grid grid grid-cols-2 gap-3"><fieldset aria-label="PD 采集参数" className="contents" disabled={!active || pending || pd?.state === "running"}>
          <div className="col-span-2 pt-1"><p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">采集参数</p></div>
          <Field label="采集卡" className="pd-inline-field col-span-2" layout="inline">
            <ControlRow>
              <NativeSelect disabled={!active || pending || pd?.detecting || pd?.state === "running"} value={current.device} onChange={(v) => set("device", v)}><option value="">请选择</option>{pd?.devices.map((item) => <option key={item} value={item}>{item}</option>)}</NativeSelect>
              <DeviceDetectButton detecting={pd?.detecting} disabled={!active || pending || pd?.detecting || pd?.state === "running"} onClick={() => run("pd.refresh")} />
            </ControlRow>
          </Field>
          <Field label="输入通道" className="pd-inline-field" layout="inline"><NativeSelect value={current.channel} onChange={(v) => set("channel", v)}><option value="">请选择</option>{pd?.channels.map((item) => <option key={item} value={item}>{item}</option>)}</NativeSelect></Field>
          <Field label="接线方式" className="pd-inline-field" layout="inline"><NativeSelect value={current.terminal} onChange={(v) => set("terminal", v)}><option value="DIFF">差分 DIFF</option><option value="RSE">参考单端 RSE</option></NativeSelect></Field>
          <Field label="输入量程" className="pd-inline-field" layout="inline"><NativeSelect value={String(current.range ?? "")} onChange={(v) => set("range", asNumber(v))}><option value="">自动</option>{pd?.ranges.map((item) => <option key={String(item.value)} value={String(item.value)}>{item.label}</option>)}</NativeSelect></Field>
          <Field label="采样率 (S/s)" className="pd-inline-field" layout="inline"><NumberInput value={current.sampleRateHz} onChange={(v) => set("sampleRateHz", v)} /></Field>
          <Separator className="col-span-2 my-1" />
          <div className="col-span-2"><p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">标定参数</p></div>
          <Field label="每批点数" className="pd-inline-field" layout="inline"><NumberInput value={current.blockSize} onChange={(v) => set("blockSize", v)} /></Field>
          <Field label="标定比例" className="pd-inline-field" layout="inline"><NumberInput value={current.scale} onChange={(v) => set("scale", v)} step="0.01" /></Field>
          <Field label="标定偏置" className="pd-inline-field" layout="inline"><NumberInput value={current.offset} onChange={(v) => set("offset", v)} step="0.01" /></Field>
          <Field label="显示单位" className="pd-inline-field" layout="inline"><Input value={current.unit} onChange={(e) => set("unit", e.target.value)} /></Field>
          <Separator className="col-span-2 my-1" />
          <div className="col-span-2 rounded-lg border border-[var(--plm-flat-border)] bg-background/30 p-3">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div><p className="text-sm font-medium">保存设置</p><p className="mt-0.5 text-xs text-muted-foreground">可选：保存本次采集的原始数据</p></div>
              <label className="flex shrink-0 items-center gap-2 text-sm"><input checked={current.save} onChange={(e) => set("save", e.target.checked)} type="checkbox" />保存原始数据</label>
            </div>
            <Field label="保存目录" className="pd-inline-field" layout="inline">
              <div className="flex items-center gap-1.5">
                <Input
                  className="min-w-0 flex-1"
                  value={current.outputDir}
                  onChange={(e) => set("outputDir", e.target.value)}
                  placeholder="留空则使用默认目录"
                />
                <Button
                  className="size-9 shrink-0 px-0"
                  disabled={!active || pending || pd?.state === "running"}
                  onClick={() => void choosePdOutputDir()}
                  size="sm"
                  type="button"
                  variant="outline"
                  title="选择保存目录"
                >
                  <FolderOpen className="size-4" />
                </Button>
              </div>
            </Field>
          </div>
        </fieldset></CardContent></Card>
        <div className="pd-monitor space-y-4">
          <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">{[["当前值", pd?.currentValue ?? "--"], ["电压", pd?.voltage ?? "--"], ["采样数", pd?.sampleCount ?? "0"]].map(([label, value]) => <Card className="py-4 shadow-none" key={label}><CardContent><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 font-din tabular-nums text-2xl font-semibold">{value}</p></CardContent></Card>)}</section>
          <ChartPanel heightClassName="h-64" title="PD 实时趋势" data={pdData} xKey="elapsedS" xDomain={pdTimeDomain} xTicks={pdTimeTicks} lines={[{ key: "value", label: current.unit || "PD", color: PLM_CHART_SERIES.accent }]} empty="开始采集后显示实时趋势" />
          <Card className="py-4 shadow-none"><CardContent className="grid grid-cols-3 gap-4 text-sm"><div><p className="text-xs text-muted-foreground">批次均值</p><p className="mt-1 font-medium">{pd?.mean ?? "--"}</p></div><div><p className="text-xs text-muted-foreground">标准差</p><p className="mt-1 font-medium">{pd?.standardDeviation ?? "--"}</p></div><div><p className="text-xs text-muted-foreground">最小 / 最大</p><p className="mt-1 font-medium">{pd?.rangeText ?? "--"}</p></div></CardContent></Card>
        </div>
      </section>
    </>
  )
}, (previous, next) => (
  previous.active === next.active
  && previous.pending === next.pending
  && samePdSection(previous.snapshot?.pd, next.snapshot?.pd)
))
