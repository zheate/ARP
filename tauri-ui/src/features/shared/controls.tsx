import { LivePowerRecordingDot, PowerStabilityStatus } from "@/components/automatic-overview"
import { Field } from "@/components/field"
import { MeasurementState } from "@/components/measurement-state"
import { NumericReadout } from "@/components/numeric-readout"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { automaticRunProgress, automaticStartPresentation } from "@/lib/automatic-presentation"
import { type AppConfiguration, type BackendSnapshot, type DeviceSnapshot } from "@/lib/backend"
import { measurementVerdict } from "@/lib/measurement-verdict"
import { normalizeNumericInput, stepNumericValue } from "@/lib/number-input"
import { powerMeterResourceValue } from "@/lib/power-meter-resource"
import { formatSpectrumNumber } from "@/lib/spectrum-annotations"
import { cn } from "@/lib/utils"
import { LoaderCircle, Play, Square } from "lucide-react"
import { useEffect, useState, type ReactNode } from "react"


export function asNumber(value: string): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

export function formatTrimmedNumber(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "--"
  const formatted = value.toFixed(digits)
  return formatted.includes(".") ? formatted.replace(/\.?0+$/, "") : formatted
}

export function formatFixedNumber(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "--"
  return value.toFixed(digits)
}

export function formatNumber(value: number | null | undefined, suffix = "", digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "--"
  return `${formatTrimmedNumber(value, digits)}${suffix}`
}

export function powerMeterResourceLabel(resourceValue: string, resources: string[] = []): string {
  if (!resourceValue) return "请选择串口资源"
  const matched = resources.find((item) => powerMeterResourceValue(item) === resourceValue)
  if (matched) return matched
  if (resourceValue.includes("|") || resourceValue.includes("彩煌") || resourceValue.includes("Coherent") || resourceValue.includes("LaserPoint") || resourceValue.includes("Ophir")) {
    return resourceValue
  }
  const asrlMatch = resourceValue.match(/ASRL(\d+)::INSTR/i)
  if (asrlMatch) {
    return `彩煌功率计 (COM${asrlMatch[1]} · ${resourceValue})`
  }
  if (resourceValue.startsWith("OPHIR::USB::")) {
    return `Ophir 激光功率计 (${resourceValue.replace("OPHIR::USB::", "USB ")})`
  }
  return resourceValue
}

export type FieldLayout = "stacked" | "inline"

export function ManualSectionHeading({ children }: { children: ReactNode }) {
  return <h3 className="manual-device-section-heading">{children}</h3>
}

export function StatusDot({ state }: { state?: string }) {
  const color = state === "connected" || state === "running"
    ? "bg-[var(--app-success)]"
    : state === "error"
      ? "bg-[var(--app-danger)]"
      : state === "connecting"
        ? "bg-[var(--app-warning)]"
        : "bg-muted-foreground/45"
  return <span className={`status-dot size-2 shrink-0 rounded-full ${color}`} key={state ?? "idle"} />
}

export function AutomaticTestControlCard({
  active,
  auto,
  config,
  controlsLocked,
  pending,
  actionPending = null,
  shutdown,
  onStart,
  onStop,
}: {
  active: boolean
  auto?: BackendSnapshot["automaticTest"]
  config: Pick<AppConfiguration, "sn" | "outputDir">
  controlsLocked: boolean
  pending: boolean
  actionPending?: "start" | "stop" | null
  shutdown?: BackendSnapshot["safety"]["shutdown"]
  onStart: () => void
  onStop: () => void
}) {
  const running = Boolean(auto?.canEnd)
  const { canStart, messages: readinessMessages } = automaticStartPresentation(auto, config)
  const progress = automaticRunProgress(auto, shutdown)
  const terminal = auto?.state === "completed"
  const progressTone = terminal && auto.terminalOutcome === "stopped_shutdown_confirmed" && shutdown?.confirmed
    ? "normal"
    : progress.tone
  return (
    <Card className="automatic-test-control-card gap-2.5 py-3 shadow-none">
      <CardContent className="automatic-test-control-card__content px-4 py-0">
        <div aria-label="自动测试进度" className="automatic-test-progress" data-tone={progressTone}>
          <div className="automatic-test-progress__current">
            <strong title={progress.detail}>{terminal ? progress.stage : progress.point}</strong>
            {!terminal && <span title={progress.detail}>{progress.stage}</span>}
          </div>
          <div className="automatic-test-progress__recorded">
            <span>{progress.recorded}</span>
            <span aria-hidden="true" className="automatic-test-progress__separator">·</span>
            <span>用时 {progress.elapsed}</span>
          </div>
        </div>
        <div className="automatic-test-control-card__actions">
          <Button
            className="automatic-test-start-btn"
            aria-busy={actionPending === "start"}
            aria-disabled={pending || actionPending !== null}
            disabled={!active || controlsLocked || running}
            onClick={() => { if (!pending && !actionPending) onStart() }}
            size="sm"
            title={!canStart && readinessMessages.length > 0 ? readinessMessages.join("；") : undefined}
          >
            {actionPending === "start" ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}开始
          </Button>
          <Button
            aria-busy={actionPending === "stop"}
            aria-disabled={pending || actionPending !== null}
            disabled={!active || !running}
            onClick={() => { if (!pending && !actionPending) onStop() }}
            size="sm"
            variant="destructive"
          >
            {actionPending === "stop" ? <LoaderCircle className="size-4 animate-spin" /> : <Square className="size-4" />}停止
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0 s"
  if (seconds % 60 === 0) return `${seconds / 60} min`
  return `${seconds} s`
}

export function formatMeasurementAge(ageMs?: number | null): string {
  if (typeof ageMs !== "number" || !Number.isFinite(ageMs)) return "时间未知"
  return `${Math.max(1, Math.ceil(ageMs / 1000))} s 前`
}

export function RealtimePowerReadout({ automatic, currentA, meter }: {
  automatic?: BackendSnapshot["automaticTest"]
  currentA?: number | null
  meter?: DeviceSnapshot
}) {
  return (
    <AutomaticMetricCard
      className="automatic-power-chart__reading automatic-metric-card--power automatic-metric-card--title-below power-chart-readout"
      title={<PowerStabilityStatus automatic={automatic} meter={meter} />}
      value={formatFixedNumber(meter?.quality === "live" ? meter.powerW : null, 3)}
      unit={<>
        <span className="power-chart-readout__power-unit">W @</span>
        <span className="automatic-metric-card__current">
          <NumericReadout value={formatFixedNumber(currentA, 2)} minimumIntegerDigits={2} />
          <span className="power-chart-readout__current-unit">A</span>
        </span>
      </>}
      valueTone={meter?.quality === "live" ? "primary" : "muted"}
    />
  )
}

export function AutomaticMetricCard({ title, value, unit, valueTone = "muted", recording = false, onClick, className = "", headerTrailing, footer, verdictLabel }: {
  title: ReactNode
  value: string
  unit: ReactNode
  valueTone?: "success" | "danger" | "warning" | "muted" | "primary"
  recording?: boolean
  onClick?: () => void
  className?: string
  headerTrailing?: ReactNode
  footer?: ReactNode
  verdictLabel?: string
}) {
  const clickable = Boolean(onClick)
  const isStringTitle = typeof title === "string"
  return (
    <div
      aria-label={clickable ? `${isStringTitle ? title : "实时功率"} ${value} ${typeof unit === "string" ? unit : ""}，${verdictLabel ?? ""}，点击设置规格` : undefined}
      className={`automatic-metric-card ${className} ${clickable ? "automatic-metric-card--clickable" : ""}`}
      onClick={onClick}
      onKeyDown={(event) => {
        if (!onClick || (event.key !== "Enter" && event.key !== " ")) return
        event.preventDefault()
        onClick()
      }}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
    >
      <div aria-label={recording ? `${isStringTitle ? title : "实时功率"}，实时记录中` : undefined} className={`automatic-metric-card__title${headerTrailing ? " automatic-metric-card__title--with-status" : ""}`}>
        {isStringTitle && recording ? <LivePowerRecordingDot active /> : null}
        {isStringTitle ? <span>{title}</span> : title}
        {headerTrailing}
      </div>
      <div className="automatic-metric-card__value">
        <span className={`automatic-metric-card__number is-${valueTone}`}><NumericReadout value={value} /></span>
        <span className="automatic-metric-card__unit">{unit}</span>
      </div>
      {footer}
    </div>
  )
}

export function RealtimeMetricCards({
  automatic,
  compact = false,
  config,
  currentA,
  onOpenSpecifications,
  powerMeter,
  spectrometer,
  spectrumOnly = false,
}: {
  automatic?: BackendSnapshot["automaticTest"]
  compact?: boolean
  config: AppConfiguration
  currentA?: number | null
  onOpenSpecifications?: () => void
  powerMeter?: DeviceSnapshot
  spectrometer?: DeviceSnapshot
  spectrumOnly?: boolean
}) {
  const livePowerW = powerMeter?.quality === "live" ? powerMeter.powerW : null
  const liveSpectrometer = spectrometer?.quality === "live" ? spectrometer : undefined
  const pibValue = liveSpectrometer?.pib ?? liveSpectrometer?.pibPercent
  const pibPercent = typeof pibValue === "number" && Number.isFinite(pibValue) ? (pibValue <= 1 ? pibValue * 100 : pibValue) : null
  const centerWavelengthValue = liveSpectrometer?.peakWavelengthNm
  const hasSpectrumMetrics = typeof centerWavelengthValue === "number" && Number.isFinite(centerWavelengthValue)
  const centerWavelengthPass = hasSpectrumMetrics && Math.abs((centerWavelengthValue ?? 0) - config.centerWavelengthSpecNm) <= config.centerWavelengthToleranceNm
  const fwhmPass = typeof liveSpectrometer?.fwhmNm === "number" && liveSpectrometer.fwhmNm <= config.fwhmMaximumNm
  const pibPass = pibPercent !== null && pibPercent >= config.pibMinimumPercent
  const wavelengthVerdict = measurementVerdict(centerWavelengthValue, spectrometer?.quality, centerWavelengthPass)
  const fwhmVerdict = measurementVerdict(liveSpectrometer?.fwhmNm, spectrometer?.quality, fwhmPass)
  const pibVerdict = measurementVerdict(pibValue, spectrometer?.quality, pibPass)

  return (
    <section className={spectrumOnly ? "spectrum-metric-list" : `automatic-metric-grid${compact ? " automatic-metric-grid--compact" : ""}`} aria-label={spectrumOnly ? "光谱关键指标" : "实时关键指标"}>
      {!spectrumOnly && <AutomaticMetricCard
        className="automatic-metric-card--power automatic-metric-card--title-below"
        title={<PowerStabilityStatus automatic={automatic} meter={powerMeter} />}
        value={formatFixedNumber(livePowerW, 3)}
        unit={<>W @ <span className="automatic-metric-card__current">{formatTrimmedNumber(currentA, 2)} A</span></>}
        valueTone={powerMeter?.quality === "live" ? "primary" : "muted"}
        recording={Boolean(powerMeter?.running || (automatic && !["idle", "paused", "completed"].includes(automatic.state)))}
      />}
      <AutomaticMetricCard
        className="automatic-metric-card--title-below"
        title="中心波长"
        value={formatSpectrumNumber(centerWavelengthValue, 3)}
        unit="nm"
        valueTone={wavelengthVerdict.tone === "success" ? "primary" : wavelengthVerdict.tone}
        verdictLabel={wavelengthVerdict.label}
        headerTrailing={spectrumOnly ? undefined : <MeasurementState verdict={wavelengthVerdict} limit={`规格 ${config.centerWavelengthSpecNm} ± ${config.centerWavelengthToleranceNm} nm`} />}
        onClick={onOpenSpecifications}
      />
      <AutomaticMetricCard
        className="automatic-metric-card--title-below"
        title="FWHM"
        value={formatSpectrumNumber(liveSpectrometer?.fwhmNm, 3)}
        unit="nm"
        valueTone={fwhmVerdict.tone === "success" ? "primary" : fwhmVerdict.tone}
        verdictLabel={fwhmVerdict.label}
        headerTrailing={spectrumOnly ? undefined : <MeasurementState verdict={fwhmVerdict} limit={`上限 ${config.fwhmMaximumNm} nm`} />}
        onClick={onOpenSpecifications}
      />
      <AutomaticMetricCard
        className="automatic-metric-card--title-below"
        title="PIB"
        value={formatSpectrumNumber(pibPercent, 2)}
        unit="%"
        valueTone={pibVerdict.tone === "success" ? "primary" : pibVerdict.tone}
        verdictLabel={pibVerdict.label}
        headerTrailing={spectrumOnly ? undefined : <MeasurementState verdict={pibVerdict} limit={`下限 ${config.pibMinimumPercent}%`} />}
        onClick={onOpenSpecifications}
      />
    </section>
  )
}

export type { RunCommand } from "@/lib/command-contract"

export function NumberInput({
  value,
  onChange,
  disabled = false,
  min,
  max,
  step = "1",
  horizontalStep,
  onConfirm,
  displayDecimalPlaces,
  className,
  "aria-label": ariaLabel,
  id,
}: {
  value: number
  onChange: (value: number) => void
  disabled?: boolean
  min?: number | string
  max?: number | string
  step?: string
  horizontalStep?: string
  onConfirm?: (value: number) => void
  displayDecimalPlaces?: number | "auto"
  className?: string
  "aria-label"?: string
  id?: string
}) {
  const [text, setText] = useState(() => (Number.isFinite(value) ? String(value) : ""))
  const [isFocused, setIsFocused] = useState(false)
  const [hasEdited, setHasEdited] = useState(false)

  useEffect(() => {
    if (!isFocused) {
      setText(Number.isFinite(value) ? String(value) : "")
    } else if (Number.isFinite(value) && Number(text) !== value && text !== "" && text !== "-" && !text.endsWith(".")) {
      setText(String(value))
    }
  }, [value, isFocused])

  const numMin = min !== undefined ? Number(min) : undefined
  const numMax = max !== undefined ? Number(max) : undefined
  const displayText = displayDecimalPlaces === undefined || displayDecimalPlaces === "auto" ? text
    : isFocused && hasEdited ? text : formatFixedNumber(Number(text), displayDecimalPlaces)
  const decimalPosition = displayText.indexOf(".")

  const handleChange = (rawValue: string) => {
    const normalized = normalizeNumericInput(rawValue)
    setHasEdited(true)
    setText(normalized)
    if (normalized === "" || normalized === "-" || normalized === "-0" || normalized.endsWith(".")) {
      return
    }
    const parsed = Number(normalized)
    if (Number.isFinite(parsed)) {
      let nextValue = parsed
      if (numMax !== undefined && Number.isFinite(numMax) && nextValue > numMax) {
        nextValue = numMax
      }
      onChange(nextValue)
    }
  }

  const handleBlur = () => {
    setIsFocused(false)
    setHasEdited(false)
    if (text === "" || text === "-" || text === "-0" || !Number.isFinite(Number(text))) {
      const fallback = numMin !== undefined && Number.isFinite(numMin) ? numMin : 0
      setText(String(fallback))
      onChange(fallback)
      return
    }
    let num = Number(text)
    if (numMin !== undefined && Number.isFinite(numMin)) num = Math.max(numMin, num)
    if (numMax !== undefined && Number.isFinite(numMax)) num = Math.min(numMax, num)
    setText(String(num))
    onChange(num)
  }

  const handleFocus = (event: React.FocusEvent<HTMLInputElement>) => {
    setIsFocused(true)
    setHasEdited(false)
    event.currentTarget.select()
  }

  const doStep = (direction: 1 | -1, stepSize = step) => {
    if (disabled) return
    const nextNum = stepNumericValue(text, stepSize, direction, numMin, numMax)
    setText(String(nextNum))
    onChange(nextNum)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (onConfirm && event.key === "Enter" && event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey) {
      if (event.nativeEvent.isComposing) return
      event.preventDefault()
      event.stopPropagation()
      if (disabled || event.repeat || !text.trim() || !Number.isFinite(Number(text))) return
      let next = Number(text)
      if (numMin !== undefined && Number.isFinite(numMin)) next = Math.max(numMin, next)
      if (numMax !== undefined && Number.isFinite(numMax)) next = Math.min(numMax, next)
      setText(String(next))
      onChange(next)
      onConfirm(next)
    } else if (event.key === "ArrowUp") {
      event.preventDefault()
      doStep(1)
    } else if (event.key === "ArrowDown") {
      event.preventDefault()
      doStep(-1)
    } else if (horizontalStep && (event.key === "ArrowLeft" || event.key === "ArrowRight") &&
      !event.nativeEvent.isComposing && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
      event.preventDefault()
      doStep(event.key === "ArrowRight" ? 1 : -1, horizontalStep)
    }
  }

  return (
    <div className="relative flex w-full min-w-0 items-center" data-fixed-display={displayDecimalPlaces !== undefined || undefined}>
      <Input
        aria-label={ariaLabel}
        aria-keyshortcuts={onConfirm ? "Control+Enter" : undefined}
        className={cn("pr-6", className)}
        disabled={disabled}
        id={id}
        inputMode="decimal"
        type="text"
        value={text}
        onBlur={handleBlur}
        onFocus={handleFocus}
        onKeyDown={handleKeyDown}
        onChange={(e) => handleChange(e.target.value)}
      />
      {displayDecimalPlaces !== undefined && (
        <span aria-hidden="true" className="number-input__formatted-value">
          {decimalPosition === -1 ? displayText : <>
            {displayText.slice(0, decimalPosition)}
            <span className="number-input__decimal-point">.</span>
            {displayText.slice(decimalPosition + 1)}
          </>}
        </span>
      )}
      <div className="pointer-events-auto absolute right-1 flex flex-col justify-center gap-0.5">
        <button
          aria-label="增加数值"
          className="flex h-3 w-3.5 cursor-pointer items-center justify-center rounded text-muted-foreground/70 transition-colors hover:bg-muted/50 hover:text-foreground active:bg-muted/80 disabled:pointer-events-none disabled:opacity-30"
          disabled={disabled}
          tabIndex={-1}
          type="button"
          onClick={() => doStep(1)}
        >
          <svg className="size-2.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" viewBox="0 0 24 24">
            <path d="m18 15-6-6-6 6" />
          </svg>
        </button>
        <button
          aria-label="减少数值"
          className="flex h-3 w-3.5 cursor-pointer items-center justify-center rounded text-muted-foreground/70 transition-colors hover:bg-muted/50 hover:text-foreground active:bg-muted/80 disabled:pointer-events-none disabled:opacity-30"
          disabled={disabled}
          tabIndex={-1}
          type="button"
          onClick={() => doStep(-1)}
        >
          <svg className="size-2.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" viewBox="0 0 24 24">
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
      </div>
    </div>
  )
}

export function NumberField({
  label,
  value,
  onChange,
  disabled = false,
  min,
  max,
  step = "1",
  layout = "stacked",
}: {
  label: string
  value: number
  onChange: (value: number) => void
  disabled?: boolean
  min?: number
  max?: number
  step?: string
  layout?: FieldLayout
}) {
  return (
    <Field label={label} layout={layout}>
      <NumberInput
        disabled={disabled}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={onChange}
      />
    </Field>
  )
}
