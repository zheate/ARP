import { AutomaticCompletionSummary, PowerStabilityStatus } from "@/components/automatic-overview"
import { NativeSelect } from "@/components/native-select"
import { SaveScriptDialog } from "@/components/save-script-dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { AgingPowerChart, PowerEfficiencyChart, PowerRealtimeChart, SpectrumRealtimeChart } from "@/features/charts/realtime"
import { UpdateConfig, configurationsEqual } from "@/features/configuration/model"
import { AutomaticSpecificationsDialog, DeviceCard, DeviceSettingsDialog, DeviceSettingsKind } from "@/features/devices/settings"
import { AutomaticMetricCard, AutomaticTestControlCard, RealtimeMetricCards, RunCommand, formatFixedNumber, formatTrimmedNumber, powerMeterResourceLabel } from "@/features/shared/controls"
import { sameAutomaticTestState, sameDeviceSnapshot, sameMeasurements } from "@/features/shared/snapshot-equality"
import { type CurveLineStyle, type LatestPointMarkerStyle } from "@/lib/appearance"
import { automaticStartPresentation } from "@/lib/automatic-presentation"
import { type AppConfiguration, type BackendSnapshot, type TestScript } from "@/lib/backend"
import { powerMeterResourceValue } from "@/lib/power-meter-resource"
import { scriptPresetCompatible } from "@/lib/test-script-presets"
import { open as openDialog } from "@tauri-apps/plugin-dialog"
import { FileText, FolderOpen, Gauge, Milestone, Pencil, Plus, Power, QrCode, Radio, Save, Thermometer } from "lucide-react"
import { memo, useCallback, useEffect, useState } from "react"

type AutomaticSetupProps = {
  config: AppConfiguration
  update: UpdateConfig
  active: boolean
  dirty: boolean
  controlsLocked: boolean
  pending: boolean
  saveConfiguration: () => Promise<void>
  testScripts: TestScript[]
  onScriptSelect: (scriptId: string) => void
  onNewScript: () => void
  onEditScript: () => void
  onNotify?: (notice: { level?: "error" | "info" | "warning"; title: string; message: string; durationMs?: number }) => void
}

const AutomaticSetup = memo(function AutomaticSetup({ config, update, active, dirty, controlsLocked, pending, saveConfiguration, testScripts, onScriptSelect, onNewScript, onEditScript, onNotify }: AutomaticSetupProps) {
  const availableTestScripts = testScripts.filter((script) => (
    !script.powerSupplyKinds || script.powerSupplyKinds.includes(config.powerSupplyKind)
  ))
  const chooseOutputDir = async () => {
    if (controlsLocked) return
    try {
      const selected = await openDialog({
        multiple: false,
        directory: true,
        title: "选择测试数据输出目录",
        defaultPath: config.outputDir.trim() || undefined,
      })
      if (typeof selected === "string" && selected) {
        update("outputDir", selected)
      }
    } catch {
      // dialog cancelled or failed
    }
  }

  return (
    <div className="automatic-setup-panel sample-nameplate" data-locked={controlsLocked}>

        <div className="sample-nameplate__identity space-y-1.5 min-w-0">
          <Label className="text-[13px] font-medium text-muted-foreground" htmlFor="automatic-sn">
            <QrCode className="mr-1 inline size-3.5" />当前样品
          </Label>
          <Input
            className="h-10 text-base font-mono font-semibold rounded-lg"
            disabled={controlsLocked}
            aria-label="产品 SN"
            placeholder="输入产品 SN"
            title={config.sn}
            id="automatic-sn"
            onChange={(event) => update("sn", event.target.value)}
            value={config.sn}
          />
        </div>
        <div className="space-y-1.5 min-w-0">
          <Label className="text-[13px] font-medium text-muted-foreground" htmlFor="automatic-test-temperature">
            <Thermometer className="mr-1 inline size-3.5" />测试温度
          </Label>
          <Input
            className="h-10 text-[15px] rounded-lg"
            disabled={controlsLocked}
            id="automatic-test-temperature"
            onChange={(event) => update("testTemperatureC", event.target.value)}
            placeholder="例如：水冷25°C"
            value={config.testTemperatureC}
          />
        </div>
        <div className="space-y-1.5 min-w-0">
          <Label className="text-[13px] font-medium text-muted-foreground" htmlFor="automatic-station">
            <Milestone className="mr-1 inline size-3.5" />测试站别
          </Label>
          <Input
            className="h-10 text-[15px] rounded-lg"
            disabled={controlsLocked}
            id="automatic-station"
            onChange={(event) => update("station", event.target.value)}
            placeholder="例如：老化 / FT"
            value={config.station}
          />
        </div>
        <div className="sample-nameplate__script automatic-setup-field--large-type space-y-1.5 min-w-0">
          <Label className="text-[13px] font-medium text-muted-foreground">
            <FileText className="mr-1 inline size-3.5" />脚本选择
          </Label>
          <div className="sample-nameplate__script-controls">
            <NativeSelect aria-label="脚本选择" disabled={controlsLocked || pending} value={config.selectedScriptPresetId || config.selectedScriptId} onChange={onScriptSelect}>
              {availableTestScripts.map((script) => <option key={script.id} value={script.id}>{script.name}</option>)}
              {(config.testScriptPresets ?? []).map((preset) => <option disabled={!scriptPresetCompatible(config, preset)} key={preset.id} value={preset.id}>{preset.name}</option>)}
            </NativeSelect>
            <Button aria-haspopup="dialog" disabled={!active || pending || controlsLocked} onClick={onNewScript} title="另存为新脚本" variant="outline"><Plus className="size-4" />添加</Button>
            <Button disabled={!dirty || !active || pending || controlsLocked} onClick={() => void saveConfiguration().catch((reason) => onNotify?.({ level: "error", title: "保存失败", message: reason instanceof Error ? reason.message : String(reason) }))} variant="outline"><Save className="size-4" />保存</Button>
            <Button aria-haspopup="dialog" disabled={!active || pending || controlsLocked} onClick={onEditScript} variant="outline"><Pencil className="size-4" />编辑</Button>
          </div>
        </div>
        <div className="sample-nameplate__output space-y-1.5 min-w-0">
          <Label className="text-[13px] font-medium text-muted-foreground" htmlFor="automatic-output-directory">
            <FolderOpen className="mr-1 inline size-3.5" />输出目录
          </Label>
          <div className="flex items-center gap-1.5">
            <Input
              className="h-10 min-w-0 flex-1 text-[15px] rounded-lg"
              disabled={controlsLocked}
              id="automatic-output-directory"
              onChange={(event) => update("outputDir", event.target.value)}
              placeholder="测试数据保存目录"
              value={config.outputDir}
            />
            <Button
              className="size-10 shrink-0 px-0"
              disabled={controlsLocked}
              onClick={() => void chooseOutputDir()}
              size="sm"
              type="button"
              variant="outline"
              title="选择输出目录"
            >
              <FolderOpen className="size-4" />
            </Button>
          </div>
        </div>
      </div>
  )
})

export const AutomaticPage = memo(function AutomaticPage({
  snapshot,
  config,
  update,
  active,
  dirty,
  controlsLocked,
  curveLineStyle,
  latestPointMarkerStyle,
  pending,
  run,
  saveConfiguration,
  testScripts,
  onScriptSelect,
  onCreateScript,
  onNextDevice,
  onViewData,
  onRetryExport,
  onChooseExportDirectory,
  onShowCompletion,
  onNotify,
}: {
  snapshot: BackendSnapshot | null
  config: AppConfiguration
  update: UpdateConfig
  active: boolean
  dirty: boolean
  controlsLocked: boolean
  curveLineStyle: CurveLineStyle
  latestPointMarkerStyle: LatestPointMarkerStyle
  pending: boolean
  run: RunCommand
  saveConfiguration: () => Promise<void>
  testScripts: TestScript[]
  onScriptSelect: (scriptId: string) => void
  onCreateScript: (name: string) => Promise<void>
  onNextDevice: () => void
  onViewData: () => void
  onRetryExport: () => void
  onChooseExportDirectory: () => void
  onShowCompletion: () => void
  onNotify?: (notice: { level?: "error" | "info" | "warning"; title: string; message: string; durationMs?: number }) => void
}) {
  const auto = snapshot?.automaticTest
  const completed = auto?.state === "completed" && Boolean(auto.terminalOutcome)
  const [openSettings, setOpenSettings] = useState<DeviceSettingsKind | null>(null)
  const [specificationsOpen, setSpecificationsOpen] = useState(false)
  const [testSettingsOpen, setTestSettingsOpen] = useState(false)
  const [newScriptOpen, setNewScriptOpen] = useState(false)
  const closeSettings = () => setOpenSettings(null)
  const openNewScript = useCallback(() => setNewScriptOpen(true), [])
  const openTestSettings = useCallback(() => setTestSettingsOpen(true), [])
  const openPowerSupply = useCallback(() => setOpenSettings("powerSupply"), [])
  const openPowerMeter = useCallback(() => setOpenSettings("powerMeter"), [])
  const openSpectrometer = useCallback(() => setOpenSettings("spectrometer"), [])
  useEffect(() => {
    if (controlsLocked) {
      setOpenSettings(null)
      setTestSettingsOpen(false)
      setNewScriptOpen(false)
    }
  }, [controlsLocked])
  const { canStart, messages: readinessMessages } = automaticStartPresentation(auto, config)
  const startAutomaticTest = () => {
    if (!canStart) {
      onNotify?.({
        title: "未就绪",
        message: readinessMessages.length > 0 ? readinessMessages.join("；") : "当前状态无法开始测试",
        level: "error",
      })
      return
    }
    void run("automatic.start", { ...config })
  }
  const powerSupplySummary = config.powerSupplyKind === "ch341"
    ? `CH341 · ${config.initialCurrentA}A → ${config.targetCurrentA}A (${config.currentStepA}A)`
    : `TDK · ${config.initialCurrentA}A → ${config.targetCurrentA}A (${config.tdkVoltageV}V)`
  const currentPlanSummary = config.currentPlanMode === "custom"
    ? (config.customCurrentPoints.length
      ? `${config.customCurrentPoints.map((point) => formatTrimmedNumber(point.currentA, 3)).join(" → ")} A`
      : "未设置电流点")
    : `${formatTrimmedNumber(config.initialCurrentA, 3)} → ${formatTrimmedNumber(config.targetCurrentA, 3)} A · 步进 ${formatTrimmedNumber(config.currentStepA, 3)} A`
  const powerSupplyVisibleSummary = `${config.powerSupplyKind === "tdk" ? `电压 ${formatTrimmedNumber(config.tdkVoltageV, 3)} V · ` : ""}电流 ${currentPlanSummary}`
  const appliedPowerMeterConfig = snapshot?.configuration ?? config
  const powerMeterProfileLabel = appliedPowerMeterConfig.powerMeterProfileName
    ? `${appliedPowerMeterConfig.powerMeterProfileName}${appliedPowerMeterConfig.powerMeterProfileVersion ? ` · v${appliedPowerMeterConfig.powerMeterProfileVersion}` : ""}`
    : "未绑定方案"
  const powerMeterSummary = `${appliedPowerMeterConfig.powerMeterResource ? powerMeterResourceLabel(appliedPowerMeterConfig.powerMeterResource, snapshot?.devices.powerMeter?.resources) : "未选串口"} · ${powerMeterProfileLabel} · ${appliedPowerMeterConfig.powerMeterWavelengthNm} nm (×${appliedPowerMeterConfig.softwareGain})`
  const powerMeterVisibleSummary = `${powerMeterProfileLabel} / 系数 ×${formatTrimmedNumber(appliedPowerMeterConfig.softwareGain, 6)} · ${formatTrimmedNumber(appliedPowerMeterConfig.powerMeterWavelengthNm, 3)} nm`
  const powerMeter = snapshot?.devices.powerMeter
  const livePowerW = powerMeter?.quality === "live" ? powerMeter.powerW : null
  const powerSupply = snapshot?.devices.powerSupply
  // The planned test point remains at the last point after shutdown.
  // TDK reports measured current; CH341 follows successful current commands.
  const liveCurrentA = powerSupply?.connected
    ? (config.powerSupplyKind === "tdk" ? powerSupply.outputCurrentA : powerSupply.activeCurrentA)
    : null
  const spectrometer = snapshot?.devices.spectrometer
  const useSpectrometer = Boolean(spectrometer?.running || (completed && snapshot?.measurements?.spectrum.wavelengthNm.length))
  const spectrometerSummary = useSpectrometer
    ? `${config.integrationTimeUs} μs · PIB ${config.pibCenterWavelengthNm}±${config.pibHalfRangeNm}nm`
    : "未开启光谱采集"
  const spectrometerVisibleSummary = `中心波长 ${formatTrimmedNumber(config.centerWavelengthSpecNm, 3)} nm · FWHM ≤ ${formatTrimmedNumber(config.fwhmMaximumNm, 3)} nm`


  return (
    <>
      {completed && snapshot ? <AutomaticCompletionSummary snapshot={snapshot} config={config} active={active} pending={pending} onNextDevice={onNextDevice} onViewData={onViewData} onRetryExport={onRetryExport} onChooseExportDirectory={onChooseExportDirectory} onShowDetails={onShowCompletion} /> :
      <AutomaticSetup config={config} update={update} active={active} dirty={dirty} controlsLocked={controlsLocked} pending={pending} saveConfiguration={saveConfiguration} testScripts={testScripts} onScriptSelect={onScriptSelect} onNewScript={openNewScript} onEditScript={openTestSettings} onNotify={onNotify} />}

      <section className="automatic-device-rail" aria-label="测试设备与控制">
        <DeviceCard title="电源" icon={Power} device={snapshot?.devices.powerSupply} model={config.powerSupplyKind === "tdk" ? "TDK" : "CH341"} details={[["接口", config.powerSupplyKind === "tdk" ? config.tdkResource : config.ch341SelectedLocationPath], ["电流计划", currentPlanSummary], ["目标电压", config.powerSupplyKind === "tdk" ? `${config.tdkVoltageV} V` : "不适用"]]} disabled={controlsLocked} onOpenSettings={openPowerSupply} summary={powerSupplySummary} visibleSummary={powerSupplyVisibleSummary} />
        <DeviceCard title="功率计" icon={Gauge} device={snapshot?.devices.powerMeter} model={appliedPowerMeterConfig.powerMeterResource ? powerMeterResourceLabel(appliedPowerMeterConfig.powerMeterResource, powerMeter?.resources).split("|")[0].trim().replace(/功率计$/, "") : "未选择"} details={[["接口", powerMeterResourceValue(appliedPowerMeterConfig.powerMeterResource)], ["设备", powerMeter?.detail || "未连接"], ["系数方案", powerMeterProfileLabel], ["校准波长", `${appliedPowerMeterConfig.powerMeterWavelengthNm} nm`], ["功率系数", `× ${appliedPowerMeterConfig.softwareGain}`]]} disabled={controlsLocked} onOpenSettings={openPowerMeter} summary={powerMeterSummary} visibleSummary={powerMeterVisibleSummary} />
        <DeviceCard title="光谱仪" icon={Radio} device={snapshot?.devices.spectrometer} model="Ocean Insight" details={[["设备", config.spectrometerResource || spectrometer?.detail || "未选择"], ["积分时间", `${config.integrationTimeUs} μs`], ["PIB 范围", `${config.pibCenterWavelengthNm} ± ${config.pibHalfRangeNm} nm`], ["FWHM 上限", `${config.fwhmMaximumNm} nm`]]} disabled={controlsLocked} onOpenSettings={openSpectrometer} summary={spectrometerSummary} visibleSummary={spectrometerVisibleSummary} />
        <AutomaticTestControlCard
          active={active}
          auto={auto}
          config={config}
          controlsLocked={controlsLocked}
          onStart={startAutomaticTest}
          onStop={() => void run("automatic.end")}
          pending={pending}
        />
      </section>

      <section className={`automatic-chart-grid ${useSpectrometer ? "automatic-chart-grid--compact-metrics" : "without-spectrum"}`}>
        <div className="automatic-chart-grid__power-row">
          <div className="automatic-power-chart">
            <PowerRealtimeChart automatic curveLineStyle={curveLineStyle} latestPointMarkerStyle={latestPointMarkerStyle} snapshot={snapshot} smoothing={config.powerSmoothing ?? true} stretch />
            {!useSpectrometer && (
              <AutomaticMetricCard
                className="automatic-power-chart__reading automatic-metric-card--power automatic-metric-card--title-below"
                title={<PowerStabilityStatus automatic={snapshot?.automaticTest} meter={powerMeter} />}
                value={formatFixedNumber(livePowerW, 3)}
                unit={<>W @ <span className="automatic-metric-card__current">{formatTrimmedNumber(liveCurrentA, 2)} A</span></>}
                valueTone={snapshot?.devices.powerMeter?.quality === "live" ? "primary" : "muted"}
                recording={Boolean(powerMeter?.running || (snapshot?.automaticTest && !["idle", "paused", "completed"].includes(snapshot.automaticTest.state)))}
              />
            )}
          </div>
          {config.selectedScriptId === "aging"
            ? <AgingPowerChart snapshot={snapshot} smoothing={config.powerSmoothing ?? true} stretch />
            : <PowerEfficiencyChart snapshot={snapshot} smoothing={config.powerSmoothing ?? true} stretch />}
        </div>
        {useSpectrometer && (
          <RealtimeMetricCards
            compact
            automatic={snapshot?.automaticTest}
            config={config}
            currentA={liveCurrentA}
            onOpenSpecifications={() => setSpecificationsOpen(true)}
            powerMeter={snapshot?.devices.powerMeter}
            spectrometer={spectrometer}
          />
        )}
        {useSpectrometer && <SpectrumRealtimeChart snapshot={snapshot} smoothing={config.spectrumSmoothing ?? true} stretch />}
      </section>
      {openSettings && <DeviceSettingsDialog active={active} config={config} kind={openSettings} onClose={closeSettings} onSave={saveConfiguration} onScriptSelect={onScriptSelect} open pending={pending} run={run} snapshot={snapshot} update={update} />}
      {specificationsOpen && <AutomaticSpecificationsDialog active={active} config={config} onClose={() => setSpecificationsOpen(false)} onSave={saveConfiguration} pending={pending} update={update} />}
      {testSettingsOpen && <AutomaticSpecificationsDialog active={active && !controlsLocked} config={config} editScript onClose={() => setTestSettingsOpen(false)} onSave={saveConfiguration} pending={pending} snapshot={snapshot} update={update} />}
      {newScriptOpen && <SaveScriptDialog onClose={() => setNewScriptOpen(false)} onSave={onCreateScript} />}
    </>
  )
}, (previous, next) => (
  previous.active === next.active
  && previous.dirty === next.dirty
  && previous.controlsLocked === next.controlsLocked
  && previous.curveLineStyle === next.curveLineStyle
  && previous.latestPointMarkerStyle === next.latestPointMarkerStyle
  && previous.pending === next.pending
  && previous.testScripts === next.testScripts
  && configurationsEqual(previous.config, next.config)
  && sameAutomaticTestState(previous.snapshot?.automaticTest, next.snapshot?.automaticTest)
  && sameDeviceSnapshot(previous.snapshot?.devices.powerSupply, next.snapshot?.devices.powerSupply)
  && sameDeviceSnapshot(previous.snapshot?.devices.powerMeter, next.snapshot?.devices.powerMeter)
  && sameDeviceSnapshot(previous.snapshot?.devices.spectrometer, next.snapshot?.devices.spectrometer)
  && previous.snapshot?.safety.shutdown.confirmed === next.snapshot?.safety.shutdown.confirmed
  && previous.snapshot?.safety.shutdown.measuredCurrentA === next.snapshot?.safety.shutdown.measuredCurrentA
  && sameMeasurements(previous.snapshot?.measurements, next.snapshot?.measurements)
))
