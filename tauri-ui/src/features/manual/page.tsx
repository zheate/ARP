import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { NumericReadout } from "@/components/numeric-readout"
import { PowerEfficiencyChart, PowerRealtimeChart, SpectrumRealtimeChart } from "@/features/charts/realtime"
import { configurationsEqual, UpdateConfig } from "@/features/configuration/model"
import { currentInputMaximum, DeviceSettingsDialog, DeviceSettingsKind, selectedPowerMeterRevision } from "@/features/devices/settings"
import { formatFixedNumber, formatNumber, formatTrimmedNumber, NumberInput, RealtimeMetricCards, RealtimePowerReadout, RunCommand, StatusDot } from "@/features/shared/controls"
import { sameDeviceSnapshot, sameMeasurements } from "@/features/shared/snapshot-equality"
import { type AppConfiguration, type BackendSnapshot } from "@/lib/backend"
import { type CurveLineStyle, type LatestPointMarkerStyle } from "@/lib/appearance"
import { powerMeterCoefficientLibraryForResource, powerMeterResourceValue } from "@/lib/power-meter-resource"
import { ChevronRight, Gauge, Play, Power, Radio, Square } from "lucide-react"
import { memo, useEffect, useRef, useState } from "react"
import "./current-controls.css"

export const ManualPage = memo(function ManualPage({
  snapshot,
  config,
  update,
  active,
  pending,
  run,
  saveConfiguration,
  onNotify,
  curveLineStyle = "solid",
  latestPointMarkerStyle = "point",
}: {
  snapshot: BackendSnapshot | null
  config: AppConfiguration
  update: UpdateConfig
  active: boolean
  pending: boolean
  run: RunCommand
  saveConfiguration?: () => Promise<void>
  onNotify?: (notice: { level?: "error" | "info" | "warning"; title: string; message: string; durationMs?: number }) => void
  curveLineStyle?: CurveLineStyle
  latestPointMarkerStyle?: LatestPointMarkerStyle
}) {
  const psu = snapshot?.devices.powerSupply
  const measuredCurrentA = psu?.connected ? psu.outputCurrentA : null
  const measuredVoltageV = psu?.connected ? psu.outputVoltageV : null
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
  const safeStopAllowed = snapshot?.backend.mode === "active" && manualControlsAllowed
  const commandLockRef = useRef(false)
  const stopLockRef = useRef(false)
  const manualCommandGenerationRef = useRef(0)
  const [stopping, setStopping] = useState(false)
  const [applyingCurrent, setApplyingCurrent] = useState(false)
  const [applyingVoltage, setApplyingVoltage] = useState(false)
  const [closingOutput, setClosingOutput] = useState(false)
  // Readback updates do not replace the operator's draft. Explicit output
  // toggles reset it to zero along with the hardware setpoint.
  const targetKey = JSON.stringify([snapshot?.backend.runtimeId, config.powerSupplyKind,
    config.powerSupplyKind !== "ch341" ? (config.powerSupplyKind === "ru" ? config.ruResource : config.tdkResource) : config.ch341SelectedLocationPath])
  const [targetDraft, setTargetDraft] = useState<{ key: string; value: number } | null>(null)
  const targetCurrentA = targetDraft?.key === targetKey ? targetDraft.value : config.setCurrentA
  const setTargetCurrentA = (value: number) => setTargetDraft({ key: targetKey, value })
  const currentCommand = snapshot?.manualTest?.currentCommand
  const powerStopping = stopping || currentCommand?.stopping === true
  const shutdownUnconfirmed = config.powerSupplyKind === "ru"
    && snapshot?.safety.shutdown.requested === true && !snapshot.safety.shutdown.confirmed
  const outputNeedsClosing = Boolean(psu?.outputEnabled || shutdownUnconfirmed)
  const canApplyCurrent = Boolean(active && manualControlsAllowed && psu?.connected && !psu.detecting &&
    !pending && !applyingCurrent && !closingOutput && !powerStopping && !shutdownUnconfirmed && (config.powerSupplyKind === "ch341" || psu.outputEnabled))
  const voltageKey = config.powerSupplyKind === "ru" ? "ruVoltageV" : "tdkVoltageV"
  const canApplyVoltage = Boolean(config.powerSupplyKind !== "ch341" && active && manualControlsAllowed &&
    psu?.connected && !psu.detecting && !pending && !applyingVoltage && !applyingCurrent && !closingOutput && !powerStopping && !shutdownUnconfirmed)
  const capture = snapshot?.manualTest?.capture
  const captureMessage = capture?.state === "recorded" && snapshot?.manualTest?.pendingCount === 0 && snapshot.manualTest.exportStatus === "completed"
    ? "测试点已保存"
    : capture?.state === "waiting_voltage" && capture.remainingS != null
    ? `${capture.detail} · ${Math.ceil(capture.remainingS)} 秒`
    : capture?.detail ?? "施加电流后等待功率稳定"
  const currentStatus = (() => {
    if (closingOutput) return "输出已请求关闭，正在确认电流归零"
    if (powerStopping) return "正在安全下电，请等待确认"
    if (currentCommand?.pending && currentCommand.remainingS > 0) {
      return `等待 ${currentCommand.remainingS.toFixed(1)} 秒后施加 ${formatTrimmedNumber(currentCommand.requestedCurrentA ?? 0, 2)} A`
    }
    if (applyingCurrent || currentCommand?.pending) return "正在施加电流"
    if (snapshot?.safety.shutdown.requested) {
      return snapshot.safety.shutdown.confirmed ? "已确认安全下电" : `下电未确认：${snapshot.safety.shutdown.reason}`
    }
    if (!psu?.connected) return "请先连接电源"
    if (config.powerSupplyKind !== "ch341" && !psu.outputEnabled) return "请先开启输出，再设置电流"
    const maxA = currentInputMaximum(config, snapshot)
    return `${maxA == null ? "" : `上限 ${maxA} A · `}Ctrl+Enter ${config.powerSupplyKind !== "ch341" ? "设置电流" : "施加电流"}`
  })()

  useEffect(() => {
    if (!manualControlsAllowed) {
      setOpenSettings(null)
    }
  }, [manualControlsAllowed])

  const runManualCommand: RunCommand = async (...args) => {
    if (!manualControlsAllowed || powerStopping || commandLockRef.current || pending) return
    commandLockRef.current = true
    if (args[0] === "powerSupply.setOutput") setTargetCurrentA(0)
    try {
      return await run(...args)
    } finally {
      commandLockRef.current = false
    }
  }

  const applyCurrent = async (currentA = targetCurrentA) => {
    if (!canApplyCurrent || commandLockRef.current) return
    const generation = manualCommandGenerationRef.current
    setApplyingCurrent(true)
    try {
      await runManualCommand("powerSupply.setCurrent", { currentA }, true)
    } catch (reason) {
      if (generation === manualCommandGenerationRef.current) {
        onNotify?.({ level: "error", title: "电流未施加", message: reason instanceof Error ? reason.message : String(reason) })
      }
    } finally {
      setApplyingCurrent(false)
    }
  }

  const applyVoltage = async () => {
    if (!canApplyVoltage || commandLockRef.current) return
    const generation = manualCommandGenerationRef.current
    setApplyingVoltage(true)
    try {
      await runManualCommand("powerSupply.setVoltage", { voltageV: config[voltageKey] }, true)
    } catch (reason) {
      if (generation === manualCommandGenerationRef.current) {
        onNotify?.({ level: "error", title: "电压未设置", message: reason instanceof Error ? reason.message : String(reason) })
      }
    } finally {
      setApplyingVoltage(false)
    }
  }

  const closeOutput = async () => {
    if (!active || !manualControlsAllowed || !psu?.connected || pending || powerStopping || closingOutput || commandLockRef.current) return
    const generation = manualCommandGenerationRef.current
    setClosingOutput(true)
    try {
      await runManualCommand("powerSupply.setOutput", { enabled: false })
    } catch (reason) {
      if (generation === manualCommandGenerationRef.current) {
        onNotify?.({ level: "error", title: "关闭输出未确认", message: reason instanceof Error ? reason.message : String(reason) })
      }
    } finally {
      setClosingOutput(false)
    }
  }

  const safePowerDown = async () => {
    if (stopLockRef.current || !manualControlsAllowed || !psu?.connected) return
    stopLockRef.current = true
    manualCommandGenerationRef.current += 1
    setTargetCurrentA(targetCurrentA)
    setStopping(true)
    setApplyingCurrent(false)
    setApplyingVoltage(false)
    try {
      // Bypass ordinary command/configuration locks so shutdown can cancel a
      // current request that is still waiting for its CH341 command slot.
      await run("powerSupply.safeRampDown")
    } catch (reason) {
      onNotify?.({ level: "error", title: "安全下电未确认", message: reason instanceof Error ? reason.message : String(reason) })
    } finally {
      stopLockRef.current = false
      setStopping(false)
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
  const serialLabel = config.powerSupplyKind === "ru" ? "VARIED RU RS232" : "TDK RS232"
  const tdkResourceLabel = (config.powerSupplyKind === "ru" ? config.ruResource : config.tdkResource).trim() || "未选串口"
  const psuIdentitySummary = config.powerSupplyKind !== "ch341"
    ? (tdkResourceLabel === serialLabel ? tdkResourceLabel : `${serialLabel} · ${tdkResourceLabel}`)
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
        <Card size="sm" className="manual-device-card manual-device-card--power shadow-none">
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
                <StatusDot state={shutdownUnconfirmed ? "error" : psu?.detecting ? "connecting" : psu?.connected ? "connected" : "disconnected"} />
                {shutdownUnconfirmed ? "输出未确认" : psu?.detecting ? "识别中" : psu?.outputEnabled ? "输出中" : psu?.connected ? "已连接" : "未连接"}
              </span>
            </div>

            <div className="manual-device-card__body">
              {config.powerSupplyKind !== "ch341" && (
                <div className="manual-tdk-prepare" role="group" aria-label="连接并开启电源">
                  <Button
                    className="h-9 min-w-0 px-2 text-sm font-medium"
                    disabled={!active || psu?.detecting || pending || psu?.outputEnabled || powerStopping}
                    onClick={() => void runManualCommand(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)}
                    variant={psu?.connected ? "outline" : "default"}
                  >
                    <span className="manual-device-button__label">{psu?.connected ? "断开连接" : `连接 ${config.powerSupplyKind === "ru" ? "RU" : "TDK"}`}</span>
                  </Button>
                  <Button
                    className="h-9 min-w-0 px-2 text-sm font-medium"
                    disabled={!active || psu?.detecting || !psu?.connected || pending || powerStopping || closingOutput}
                    onClick={() => outputNeedsClosing ? void closeOutput() : void runManualCommand("powerSupply.setOutput", { enabled: true }, true)}
                    variant={outputNeedsClosing ? "destructive" : psu?.connected ? "default" : "outline"}
                  >
                    <span className="manual-device-button__label">{closingOutput ? "关闭中…" : outputNeedsClosing ? "关闭输出" : "开启输出"}</span>
                  </Button>
                </div>
              )}
              <div className={config.powerSupplyKind !== "ch341" ? "manual-power-setpoints" : undefined}>
              {config.powerSupplyKind !== "ch341" && (
                <div className="space-y-1 min-w-0">
                  <div className="manual-current-heading">
                    <Label className="text-xs font-normal text-muted-foreground cursor-pointer" htmlFor="manual-psu-voltage">设置电压 (V)</Label>
                  </div>
                  <div className="manual-current-controls">
                    <NumberInput
                      className="manual-power-digit manual-current-controls__input h-9 pl-1.5 pr-6"
                      disabled={!active || powerStopping || applyingVoltage || shutdownUnconfirmed}
                      displayDecimalPlaces="auto"
                      id="manual-psu-voltage"
                      min={0}
                      onChange={(value) => update(voltageKey, value)}
                      step="0.1"
                      value={config[voltageKey]}
                    />
                    <Button className="h-9 w-full min-w-0 px-2 text-sm font-medium" disabled={!canApplyVoltage} onClick={() => void applyVoltage()} variant="outline">
                      <span className="manual-device-button__label">设置电压</span>
                    </Button>
                  </div>
                </div>
              )}
              <div className="space-y-1 min-w-0">
                <div className="manual-current-heading">
                  <Label className="text-xs font-normal text-muted-foreground cursor-pointer" htmlFor="manual-psu-current">
                    {config.powerSupplyKind !== "ch341" ? "设置电流 (A)" : "目标电流 (A)"}
                  </Label>
                  <p className="manual-current-heading__status truncate text-[11px] text-muted-foreground" role="status" title={currentStatus}>{currentStatus}</p>
                </div>
                <div className="manual-current-controls">
                  <div className="min-w-0">
                    <NumberInput
                      className="manual-power-digit manual-current-controls__input h-9 pl-1.5 pr-6"
                      disabled={!active || powerStopping || applyingCurrent || shutdownUnconfirmed || (config.powerSupplyKind !== "ch341" && (!psu?.connected || !psu.outputEnabled))}
                      displayDecimalPlaces={1}
                      id="manual-psu-current"
                      max={currentInputMaximum(config, snapshot)}
                      min={0}
                      onChange={setTargetCurrentA}
                      onConfirm={(value) => void applyCurrent(value)}
                      step="1"
                      horizontalStep="0.1"
                      value={targetCurrentA}
                    />
                  </div>
                  <Button
                    className="h-9 w-full min-w-0 px-2 text-sm font-medium"
                    disabled={!canApplyCurrent}
                    onClick={() => void applyCurrent()}
                    variant={config.powerSupplyKind !== "ch341" && psu?.outputEnabled ? "default" : "outline"}
                  >
                    <span className="manual-device-button__label">{config.powerSupplyKind !== "ch341" ? "设置电流" : "施加电流"}</span>
                  </Button>
                </div>
              </div>
              </div>

              {config.powerSupplyKind !== "ch341" ? (
                <div className="manual-power-readouts text-muted-foreground" aria-label="电源反馈读数">
                  <div className="manual-power-readout">
                    <span className="whitespace-nowrap text-[11px]">输出电压：</span>
                    <span className="manual-power-measurement manual-power-measurement--voltage">
                      <span className="manual-power-digit" data-measurement="outputVoltageV" data-available={measuredVoltageV != null && Number.isFinite(measuredVoltageV)}>
                        <NumericReadout value={formatNumber(measuredVoltageV, "", 2)} />
                      </span>
                      <span className="manual-power-unit text-xs">V</span>
                    </span>
                  </div>
                  <div className="manual-power-readout">
                    <span className="whitespace-nowrap text-[11px]">输出电流：</span>
                    <span className={`manual-power-measurement${shutdownUnconfirmed ? " manual-power-measurement--unconfirmed" : ""}`}>
                      <span className="manual-power-digit" data-measurement="outputCurrentA" data-available={measuredCurrentA != null && Number.isFinite(measuredCurrentA)}>
                        <NumericReadout value={formatFixedNumber(measuredCurrentA, shutdownUnconfirmed ? 3 : 1)} />
                      </span>
                      <span className="manual-power-unit text-xs">A</span>
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
              {config.powerSupplyKind !== "ch341" ? (
                <Button
                  className="h-9 w-full text-sm px-2 font-medium"
                  disabled={!safeStopAllowed || !psu?.connected || powerStopping}
                  onClick={() => void safePowerDown()}
                  variant={psu?.outputEnabled ? "destructive" : "outline"}
                >
                  <span className="manual-device-button__label">{powerStopping ? "下电中…" : "归零下电"}</span>
                </Button>
              ) : (
                <div className="grid grid-cols-2 gap-2 w-full">
                  {(() => {
                    const selectedLoc = config.ch341SelectedLocationPath
                    const port = snapshot?.devices?.powerSupply?.ch341?.ports?.find((p) => p.locationPath === selectedLoc)
                    const isCh341ReadyToConnect = Boolean(
                      port?.online &&
                      snapshot?.devices?.powerSupply?.ch341?.discoveryState === "ready"
                    )
                    const btnText = psu?.connected
                      ? "断开连接"
                      : port?.online
                      ? `连接 Device ${port.deviceIndex}`
                      : "连接电源"
                    return (
                      <Button
                        className="h-9 w-full text-sm font-medium"
                        disabled={!active || psu?.detecting || (!psu?.connected && !isCh341ReadyToConnect) || pending || powerStopping}
                        onClick={() => void runManualCommand(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)}
                        variant={psu?.connected ? "outline" : "default"}
                      >
                        <span className="manual-device-button__label">{btnText}</span>
                      </Button>
                    )
                  })()}
                  <Button className="h-9 w-full text-sm font-medium" disabled={!safeStopAllowed || !psu?.connected || powerStopping} onClick={() => void safePowerDown()} variant="outline">
                    <span className="manual-device-button__label">{powerStopping ? "关闭中…" : "归零&关闭"}</span>
                  </Button>
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card size="sm" className="manual-device-card manual-device-card--acquisition shadow-none">
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
                    className="h-8 text-sm font-medium"
                    disabled={!active || meter?.detecting || meter?.running || !selectedPowerMeterResourceAvailable || pending}
                    onClick={() => void runManualCommand("powerMeter.setRelativeZero", { enabled: true }, true)}
                    variant="outline"
                  >
                    <span className="manual-device-button__label">相对调零</span>
                  </Button>
                  <Button
                    className="h-8 text-sm font-medium"
                    disabled={!active || meter?.detecting || meter?.running || !selectedPowerMeterResourceAvailable || pending}
                    onClick={() => void runManualCommand("powerMeter.setRelativeZero", { enabled: false }, true)}
                    variant="outline"
                  >
                    <span className="manual-device-button__label">取消调零</span>
                  </Button>
                </div>
              </div>

              <p className={`h-5 truncate text-[11px] leading-5 ${capture?.state === "failed" || capture?.state === "cancelled" ? "text-[var(--app-warning)]" : "text-muted-foreground"}`} role="status" title={captureMessage}>{captureMessage}</p>

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
                className={`w-full h-9 transition-colors text-sm font-medium ${meter?.running ? "btn-running-pulse" : ""}`}
                disabled={!active || meter?.detecting || (!meter?.running && !selectedPowerMeterResourceAvailable) || pending}
                onClick={() => void runManualCommand(meter?.running ? "powerMeter.stop" : "powerMeter.start", {}, true)}
                variant={meter?.running ? "destructive" : "default"}
              >
                {meter?.running ? <Square className="size-3.5 mr-1.5" /> : <Play className="size-3.5 mr-1.5" />}
                <span className="manual-device-button__label">{meter?.running ? "停止采集" : "开始采集"}</span>
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card size="sm" className="manual-device-card manual-device-card--acquisition shadow-none">
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
                className={`w-full h-9 transition-colors text-sm font-medium ${spectrum?.running ? "btn-running-pulse" : ""}`}
                disabled={!active || spectrum?.detecting || (!spectrum?.running && !selectedSpectrometerResourceAvailable) || pending}
                onClick={() => void runManualCommand(spectrum?.running ? "spectrometer.stop" : "spectrometer.start", {}, true)}
                variant={spectrum?.running ? "destructive" : "default"}
              >
                {spectrum?.running ? <Square className="size-3.5 mr-1.5" /> : <Play className="size-3.5 mr-1.5" />}
                <span className="manual-device-button__label">{spectrum?.running ? "停止采集" : "开始采集"}</span>
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>
      <section className="automatic-chart-grid manual-chart-grid">
        <div className="automatic-chart-grid__power-row">
          <PowerRealtimeChart curveLineStyle={curveLineStyle} latestPointMarkerStyle={latestPointMarkerStyle} snapshot={snapshot} smoothing={config.powerSmoothing ?? true} stretch readout={
            <RealtimePowerReadout currentA={psu?.connected ? (config.powerSupplyKind === "ch341" ? psu.activeCurrentA : psu.outputCurrentA) : null} meter={meter} />
          } />
          <PowerEfficiencyChart snapshot={snapshot} smoothing={config.powerSmoothing ?? true} stretch />
        </div>
        <SpectrumRealtimeChart snapshot={snapshot} smoothing={config.spectrumSmoothing ?? true} stretch metrics={
          <RealtimeMetricCards spectrumOnly config={config} spectrometer={spectrum} />
        } />
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
  && previous.curveLineStyle === next.curveLineStyle
  && previous.latestPointMarkerStyle === next.latestPointMarkerStyle
  && previous.pending === next.pending
  && previous.snapshot?.safety.manualControlsAllowed === next.snapshot?.safety.manualControlsAllowed
  && previous.snapshot?.backend.runtimeId === next.snapshot?.backend.runtimeId
  && JSON.stringify(previous.snapshot?.manualTest) === JSON.stringify(next.snapshot?.manualTest)
  && JSON.stringify(previous.snapshot?.safety.shutdown) === JSON.stringify(next.snapshot?.safety.shutdown)
  && configurationsEqual(previous.config, next.config)
  && sameDeviceSnapshot(previous.snapshot?.devices.powerSupply, next.snapshot?.devices.powerSupply)
  && sameDeviceSnapshot(previous.snapshot?.devices.powerMeter, next.snapshot?.devices.powerMeter)
  && sameDeviceSnapshot(previous.snapshot?.devices.spectrometer, next.snapshot?.devices.spectrometer)
  && sameMeasurements(previous.snapshot?.measurements, next.snapshot?.measurements)
))
