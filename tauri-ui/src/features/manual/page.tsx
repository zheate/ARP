import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { PowerEfficiencyChart, PowerRealtimeChart, SpectrumRealtimeChart } from "@/features/charts/realtime"
import { configurationsEqual, UpdateConfig } from "@/features/configuration/model"
import { currentInputMaximum, DeviceSettingsDialog, DeviceSettingsKind, selectedPowerMeterRevision } from "@/features/devices/settings"
import { formatNumber, formatTrimmedNumber, NumberInput, RealtimeMetricCards, RunCommand, StatusDot } from "@/features/shared/controls"
import { sameDeviceSnapshot, sameMeasurements } from "@/features/shared/snapshot-equality"
import { type AppConfiguration, type BackendSnapshot } from "@/lib/backend"
import { powerMeterCoefficientLibraryForResource, powerMeterResourceValue } from "@/lib/power-meter-resource"
import { ChevronRight, Gauge, Play, Power, Radio, Square } from "lucide-react"
import { memo, useEffect, useRef, useState } from "react"

export const ManualPage = memo(function ManualPage({
  snapshot,
  config,
  update,
  active,
  pending,
  run,
  saveConfiguration,
  onNotify,
}: {
  snapshot: BackendSnapshot | null
  config: AppConfiguration
  update: UpdateConfig
  active: boolean
  pending: boolean
  run: RunCommand
  saveConfiguration?: () => Promise<void>
  onNotify?: (notice: { level?: "error" | "info" | "warning"; title: string; message: string; durationMs?: number }) => void
}) {
  const psu = snapshot?.devices.powerSupply
  const meter = snapshot?.devices.powerMeter
  const spectrum = snapshot?.devices.spectrometer
  const powerMeterResources = meter?.resources ?? []
  const selectedPowerMeterResource = config.powerMeterResource.trim()
  const selectedPowerMeterResourceAvailable = powerMeterResources.some((item) => powerMeterResourceValue(item) === selectedPowerMeterResource)
  const displayedPowerMeterResource = selectedPowerMeterResourceAvailable ? selectedPowerMeterResource : ""
  const [openSettings, setOpenSettings] = useState<DeviceSettingsKind | null>(null)
  const powerMeterLibrary = powerMeterCoefficientLibraryForResource(
    meter,
    config.powerMeterResource,
    snapshot?.configuration?.powerMeterResource,
  )
  const selectedRevision = selectedPowerMeterRevision(powerMeterLibrary)
  const softwareGain = selectedRevision?.softwareGain ?? config.softwareGain
  const wavelengthNm = selectedRevision?.wavelengthNm ?? config.powerMeterWavelengthNm
  const spectrometerResources = spectrum?.resources ?? []
  const selectedSpectrometerResourceAvailable = spectrometerResources.includes(config.spectrometerResource)
  const displayedSpectrometerResource = selectedSpectrometerResourceAvailable ? config.spectrometerResource : ""
  const manualControlsAllowed = snapshot?.safety.manualControlsAllowed === true
  const commandLockRef = useRef(false)

  useEffect(() => {
    if (!manualControlsAllowed) {
      setOpenSettings(null)
    }
  }, [manualControlsAllowed])

  const runManualCommand: RunCommand = async (...args) => {
    if (!manualControlsAllowed || commandLockRef.current || pending) return
    commandLockRef.current = true
    try {
      return await run(...args)
    } finally {
      commandLockRef.current = false
    }
  }

  const handleSaveSettings = async () => {
    if (!saveConfiguration) return
    try {
      await saveConfiguration()
    } catch (reason) {
      onNotify?.({
        level: "error",
        title: "保存失败",
        message: reason instanceof Error ? reason.message : String(reason),
      })
      throw reason
    }
  }

  const isPsuActive = Boolean(psu?.connected || psu?.outputEnabled)
  const isMeterActive = Boolean(meter?.running)
  const isSpectrumActive = Boolean(spectrum?.running)
  const psuIdentitySummary = config.powerSupplyKind === "tdk"
    ? (config.tdkResource ? `TDK RS232 · ${config.tdkResource}` : "TDK RS232 · 未选串口")
    : (config.ch341SelectedLocationPath ? `CH341 I²C · ${config.ch341SelectedLocationPath}` : "CH341 I²C · 自动分配")

  return (
    <div className="manual-page flex min-h-0 flex-1 flex-col gap-3">
      {!manualControlsAllowed && (
        <div className="rounded-lg border border-[color-mix(in_srgb,var(--app-warning)_40%,transparent)] bg-[var(--app-warning-soft)] px-4 py-3 text-sm text-foreground" role="status">
          <p className="font-semibold text-[var(--app-warning)]">手动控制已锁定</p>
          <p className="mt-1 text-xs text-muted-foreground">硬件控制权由自动测试持有。请返回自动测试，执行“结束并安全下电”后再操作设备。</p>
        </div>
      )}
      <fieldset aria-disabled={!manualControlsAllowed} className="contents" disabled={!manualControlsAllowed}>
      <section className="manual-device-grid shrink-0" aria-label="手动测试设备卡片">
        <Card size="sm" className="manual-device-card shadow-none">
          <CardHeader
            aria-disabled={!active || !manualControlsAllowed || pending}
            className="manual-device-card__header flex flex-row items-center justify-between"
            onClick={() => {
              if (active && manualControlsAllowed && !pending) {
                setOpenSettings("powerSupply")
              }
            }}
            onKeyDown={(e) => {
              if ((e.key === "Enter" || e.key === " ") && active && manualControlsAllowed && !pending) {
                e.preventDefault()
                setOpenSettings("powerSupply")
              }
            }}
            role="button"
            tabIndex={active && manualControlsAllowed && !pending ? 0 : -1}
            title="点击进入电源设置"
          >
            <div className="flex items-center gap-2 min-w-0">
              <CardTitle className="text-sm font-semibold shrink-0">电源控制</CardTitle>
              <Power className={`size-4 transition-colors ${isPsuActive ? "text-primary" : "text-muted-foreground"}`} />
            </div>
            <ChevronRight className="manual-device-card__chevron size-4" />
          </CardHeader>
          <CardContent className="manual-control-content manual-device-card__content">
            <div
              className="manual-device-card__summary cursor-pointer select-none hover:text-foreground transition-colors"
              onClick={() => {
                if (active && manualControlsAllowed && !pending) {
                  setOpenSettings("powerSupply")
                }
              }}
              title="点击进入电源设置"
            >
              <span className="manual-device-card__identity truncate font-medium text-xs" title={psuIdentitySummary}>
                {psuIdentitySummary}
              </span>
              <span className="manual-device-card__state shrink-0 flex items-center gap-1.5 text-xs text-muted-foreground">
                <StatusDot state={psu?.detecting ? "connecting" : psu?.connected ? "connected" : "disconnected"} />
                {psu?.detecting ? "识别中" : psu?.outputEnabled ? "输出中" : psu?.connected ? "已连接" : "未连接"}
              </span>
            </div>

            <div className="manual-device-card__body">
              <div className="space-y-1">
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <Label className="text-xs font-normal text-muted-foreground cursor-pointer" htmlFor="manual-psu-current">
                    设定电流 (A)
                  </Label>
                  {(() => {
                    const maxA = currentInputMaximum(config, snapshot)
                    return maxA != null ? (
                      <span className="font-mono text-[11px] text-muted-foreground">
                        上限 {maxA} A
                      </span>
                    ) : null
                  })()}
                </div>
                <div className="flex items-center gap-2">
                  <div className="relative flex-1 min-w-0">
                    <NumberInput
                      className="h-8 font-mono text-sm pr-6"
                      disabled={!active || psu?.detecting || !psu?.connected || pending}
                      id="manual-psu-current"
                      max={currentInputMaximum(config, snapshot)}
                      min={0}
                      onChange={(v) => update("setCurrentA", v)}
                      step="0.1"
                      value={config.setCurrentA}
                    />
                  </div>
                  <Button
                    className="h-8 shrink-0 px-3 text-xs font-medium"
                    disabled={!active || psu?.detecting || !psu?.connected || pending}
                    onClick={() => void runManualCommand("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)}
                    variant="outline"
                  >
                    设置电流
                  </Button>
                </div>
              </div>

              {config.powerSupplyKind === "tdk" ? (
                <div className="flex items-center justify-between rounded-md border border-border/50 bg-muted/20 px-3 py-1.5 text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="text-muted-foreground">输出电压</span>
                    <span className="font-mono font-medium text-foreground">
                      {formatNumber(psu?.outputVoltageV, " V", 2)}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-muted-foreground">输出电流</span>
                    <span className="font-mono font-medium text-foreground">
                      {formatNumber(psu?.outputCurrentA, " A", 2)}
                    </span>
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-4 gap-1 rounded-md border border-border/50 bg-muted/20 px-2 py-1.5 text-xs text-center">
                  <div><span className="text-muted-foreground block text-[10px]">输入</span><span className="font-mono font-medium">{formatNumber(psu?.inputVoltageV, "V", 1)}</span></div>
                  <div><span className="text-muted-foreground block text-[10px]">输出</span><span className="font-mono font-medium">{formatNumber(psu?.outputVoltageV, "V", 1)}</span></div>
                  <div><span className="text-muted-foreground block text-[10px]">电流</span><span className="font-mono font-medium">{formatNumber(psu?.outputCurrentA, "A", 2)}</span></div>
                  <div><span className="text-muted-foreground block text-[10px]">温度</span><span className="font-mono font-medium">{formatNumber(psu?.temperatureC, "°C", 1)}</span></div>
                </div>
              )}
            </div>

            <div className="manual-device-card__footer">
              {config.powerSupplyKind === "tdk" ? (
                <div className="grid grid-cols-3 gap-2 w-full">
                  <Button
                    className="h-9 text-xs px-2 truncate font-medium"
                    disabled={!active || psu?.detecting || pending || psu?.outputEnabled}
                    onClick={() => void runManualCommand(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)}
                    variant={psu?.connected ? "outline" : "default"}
                  >
                    {psu?.connected ? "安全断开" : "连接 TDK"}
                  </Button>
                  <Button
                    className="h-9 text-xs px-2 truncate font-medium"
                    disabled={!active || psu?.detecting || !psu?.connected || psu?.outputEnabled || pending}
                    onClick={() => void runManualCommand("powerSupply.setOutput", { enabled: true }, true)}
                    variant={psu?.outputEnabled ? "outline" : "default"}
                  >
                    开启输出
                  </Button>
                  <Button
                    className={`h-9 text-xs px-2 truncate font-medium transition-colors ${psu?.outputEnabled ? "bg-destructive text-destructive-foreground hover:bg-destructive/90 shadow-sm animate-pulse" : ""}`}
                    disabled={!active || psu?.detecting || !psu?.connected || !psu?.outputEnabled || pending}
                    onClick={() => void runManualCommand("powerSupply.setOutput", { enabled: false }, true)}
                    variant={psu?.outputEnabled ? "destructive" : "outline"}
                  >
                    {psu?.outputEnabled ? (
                      <>
                        <span className="size-2 rounded-full bg-white animate-ping shrink-0 mr-1 inline-block" />
                        关闭输出
                      </>
                    ) : (
                      "关闭输出"
                    )}
                  </Button>
                </div>
              ) : (
                <div className="w-full">
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
                        className="h-9 w-full text-xs font-medium"
                        disabled={!active || psu?.detecting || (!psu?.connected && !isCh341ReadyToConnect) || pending}
                        onClick={() => void runManualCommand(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)}
                        variant={psu?.connected ? "outline" : "default"}
                      >
                        {btnText}
                      </Button>
                    )
                  })()}
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card size="sm" className="manual-device-card shadow-none">
          <CardHeader
            aria-disabled={!active || !manualControlsAllowed || pending}
            className="manual-device-card__header flex flex-row items-center justify-between"
            onClick={() => {
              if (active && manualControlsAllowed && !pending) {
                setOpenSettings("powerMeter")
              }
            }}
            onKeyDown={(e) => {
              if ((e.key === "Enter" || e.key === " ") && active && manualControlsAllowed && !pending) {
                e.preventDefault()
                setOpenSettings("powerMeter")
              }
            }}
            role="button"
            tabIndex={active && manualControlsAllowed && !pending ? 0 : -1}
            title="点击进入功率计设置"
          >
            <div className="flex items-center gap-2 min-w-0">
              <CardTitle className="text-sm font-semibold shrink-0">功率计</CardTitle>
              <Gauge className={`size-4 transition-colors ${isMeterActive ? "text-primary" : "text-muted-foreground"}`} />
            </div>
            <ChevronRight className="manual-device-card__chevron size-4" />
          </CardHeader>
          <CardContent className="manual-control-content manual-device-card__content">
            <div
              className="manual-device-card__summary cursor-pointer select-none hover:text-foreground transition-colors"
              onClick={() => {
                if (active && manualControlsAllowed && !pending) {
                  setOpenSettings("powerMeter")
                }
              }}
              title="点击进入功率计设置"
            >
              <span className="manual-device-card__identity truncate font-medium text-xs" title={displayedPowerMeterResource || meter?.detail || "未选串口"}>
                {displayedPowerMeterResource || meter?.detail || "未选串口"}
              </span>
              <span className="manual-device-card__state shrink-0 flex items-center gap-1.5 text-xs text-muted-foreground">
                <StatusDot state={meter?.detecting ? "connecting" : meter?.running ? "connected" : selectedPowerMeterResourceAvailable ? "ready" : "disconnected"} />
                {meter?.detecting ? "识别中" : meter?.running ? "采集中" : selectedPowerMeterResourceAvailable ? "就绪" : "未连接"}
              </span>
            </div>

            <div className="manual-device-card__body">
              <div className="space-y-1">
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>调零控制</span>
                  <span className="text-[11px] text-muted-foreground flex items-center gap-1">
                    <span className={`size-1.5 rounded-full ${meter?.running ? "bg-emerald-500" : "bg-muted-foreground/50"}`} />
                    {meter?.running ? "采集中" : "未调零"}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Button
                    className="h-8 text-xs font-medium"
                    disabled={!active || meter?.detecting || meter?.running || !selectedPowerMeterResourceAvailable || pending}
                    onClick={() => void runManualCommand("powerMeter.setRelativeZero", { enabled: true }, true)}
                    variant="outline"
                  >
                    相对调零
                  </Button>
                  <Button
                    className="h-8 text-xs font-medium"
                    disabled={!active || meter?.detecting || meter?.running || !selectedPowerMeterResourceAvailable || pending}
                    onClick={() => void runManualCommand("powerMeter.setRelativeZero", { enabled: false }, true)}
                    variant="outline"
                  >
                    取消调零
                  </Button>
                </div>
              </div>

              <div className="flex items-center justify-between rounded-md border border-border/50 bg-muted/20 px-3 py-1.5 text-xs">
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground">功率系数</span>
                  <span className="font-mono font-medium text-foreground">
                    ×{formatTrimmedNumber(softwareGain, 4)}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground">校准波长</span>
                  <span className="font-mono font-medium text-foreground">
                    {wavelengthNm != null ? `${formatTrimmedNumber(wavelengthNm, 1)} nm` : "待补充"}
                  </span>
                </div>
              </div>
            </div>

            <div className="manual-device-card__footer">
              <Button
                className={`w-full h-9 transition-colors text-xs font-medium ${meter?.running ? "btn-running-pulse" : ""}`}
                disabled={!active || meter?.detecting || (!meter?.running && !selectedPowerMeterResourceAvailable) || pending}
                onClick={() => void runManualCommand(meter?.running ? "powerMeter.stop" : "powerMeter.start", {}, true)}
                variant={meter?.running ? "destructive" : "default"}
              >
                {meter?.running ? <><Square className="size-3.5 mr-1.5" />停止采集</> : <><Play className="size-3.5 mr-1.5" />开始采集</>}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card size="sm" className="manual-device-card shadow-none">
          <CardHeader
            aria-disabled={!active || !manualControlsAllowed || pending}
            className="manual-device-card__header flex flex-row items-center justify-between"
            onClick={() => {
              if (active && manualControlsAllowed && !pending) {
                setOpenSettings("spectrometer")
              }
            }}
            onKeyDown={(e) => {
              if ((e.key === "Enter" || e.key === " ") && active && manualControlsAllowed && !pending) {
                e.preventDefault()
                setOpenSettings("spectrometer")
              }
            }}
            role="button"
            tabIndex={active && manualControlsAllowed && !pending ? 0 : -1}
            title="点击进入光谱仪设置"
          >
            <div className="flex items-center gap-2 min-w-0">
              <CardTitle className="text-sm font-semibold shrink-0">光谱仪</CardTitle>
              <Radio className={`size-4 transition-colors ${isSpectrumActive ? "text-primary" : "text-muted-foreground"}`} />
            </div>
            <ChevronRight className="manual-device-card__chevron size-4" />
          </CardHeader>
          <CardContent className="manual-control-content manual-device-card__content">
            <div
              className="manual-device-card__summary cursor-pointer select-none hover:text-foreground transition-colors"
              onClick={() => {
                if (active && manualControlsAllowed && !pending) {
                  setOpenSettings("spectrometer")
                }
              }}
              title="点击进入光谱仪设置"
            >
              <span className="manual-device-card__identity truncate font-medium text-xs" title={displayedSpectrometerResource || spectrum?.detail || "未选设备"}>
                {displayedSpectrometerResource || spectrum?.detail || "未选设备"}
              </span>
              <span className="manual-device-card__state shrink-0 flex items-center gap-1.5 text-xs text-muted-foreground">
                <StatusDot state={spectrum?.detecting ? "connecting" : spectrum?.running ? "connected" : selectedSpectrometerResourceAvailable ? "ready" : "disconnected"} />
                {spectrum?.detecting ? "识别中" : spectrum?.running ? "采集中" : selectedSpectrometerResourceAvailable ? "就绪" : "未连接"}
              </span>
            </div>

            <div className="manual-device-card__body">
              <div className="space-y-1">
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>采集配置</span>
                  <span className="text-[11px] text-muted-foreground flex items-center gap-1">
                    <span className={`size-1.5 rounded-full ${spectrum?.running ? "bg-emerald-500" : "bg-muted-foreground/50"}`} />
                    {spectrum?.running ? "采集中" : "空闲"}
                  </span>
                </div>
                <div className="flex items-center justify-between rounded-md border border-border/50 bg-muted/20 px-3 py-1.5 text-xs h-8">
                  <div className="flex items-center gap-1.5">
                    <span className="text-muted-foreground">积分时间</span>
                    <span className="font-mono font-medium text-foreground">{config.integrationTimeUs} μs</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-muted-foreground">平均帧数</span>
                    <span className="font-mono font-medium text-foreground">{config.spectrumAverageFrames} 帧</span>
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between rounded-md border border-border/50 bg-muted/20 px-3 py-1.5 text-xs">
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground">PIB 范围</span>
                  <span className="font-mono font-medium text-foreground">
                    {config.pibCenterWavelengthNm} ± {config.pibHalfRangeNm} nm
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground">FWHM 上限</span>
                  <span className="font-mono font-medium text-foreground">{config.fwhmMaximumNm} nm</span>
                </div>
              </div>
            </div>

            <div className="manual-device-card__footer">
              <Button
                className={`w-full h-9 transition-colors text-xs font-medium ${spectrum?.running ? "btn-running-pulse" : ""}`}
                disabled={!active || spectrum?.detecting || (!spectrum?.running && !selectedSpectrometerResourceAvailable) || pending}
                onClick={() => void runManualCommand(spectrum?.running ? "spectrometer.stop" : "spectrometer.start", {}, true)}
                variant={spectrum?.running ? "destructive" : "default"}
              >
                {spectrum?.running ? <><Square className="size-3.5 mr-1.5" />停止采集</> : <><Play className="size-3.5 mr-1.5" />开始采集</>}
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>
      <section className="automatic-chart-grid">
        <div className="automatic-chart-grid__power-row">
          <PowerRealtimeChart snapshot={snapshot} smoothing={config.powerSmoothing ?? true} stretch />
          <PowerEfficiencyChart snapshot={snapshot} smoothing={config.powerSmoothing ?? true} stretch />
        </div>
        <RealtimeMetricCards
          config={config}
          currentA={psu?.connected ? (config.powerSupplyKind === "ch341" ? psu.activeCurrentA : psu.outputCurrentA) : null}
          powerMeter={meter}
          spectrometer={spectrum}
        />
        <SpectrumRealtimeChart snapshot={snapshot} smoothing={config.spectrumSmoothing ?? true} stretch />
      </section>
      </fieldset>
      {openSettings && (
        <DeviceSettingsDialog
          active={active && manualControlsAllowed}
          config={config}
          kind={openSettings}
          mode="manual"
          onClose={() => setOpenSettings(null)}
          onSave={handleSaveSettings}
          open={Boolean(openSettings)}
          pending={pending}
          run={runManualCommand}
          snapshot={snapshot}
          update={update}
        />
      )}
    </div>
  )
}, (previous, next) => (
  previous.active === next.active
  && previous.pending === next.pending
  && previous.snapshot?.safety.manualControlsAllowed === next.snapshot?.safety.manualControlsAllowed
  && previous.snapshot?.manualTest?.pendingCount === next.snapshot?.manualTest?.pendingCount
  && previous.snapshot?.manualTest?.exportStatus === next.snapshot?.manualTest?.exportStatus
  && previous.snapshot?.manualTest?.exportPath === next.snapshot?.manualTest?.exportPath
  && previous.snapshot?.manualTest?.exportError === next.snapshot?.manualTest?.exportError
  && configurationsEqual(previous.config, next.config)
  && sameDeviceSnapshot(previous.snapshot?.devices.powerSupply, next.snapshot?.devices.powerSupply)
  && sameDeviceSnapshot(previous.snapshot?.devices.powerMeter, next.snapshot?.devices.powerMeter)
  && sameDeviceSnapshot(previous.snapshot?.devices.spectrometer, next.snapshot?.devices.spectrometer)
  && sameMeasurements(previous.snapshot?.measurements, next.snapshot?.measurements)
))
