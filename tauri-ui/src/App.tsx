import { Children, isValidElement, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react"
import { createPortal } from "react-dom"
import { Tooltip } from "radix-ui"
import {
  BarChart3,
  Check,
  ChevronDown,
  ChevronRight,
  CircleGauge,
  FileText,
  FolderOpen,
  Gauge,
  Hand,
  KeyRound,
  LoaderCircle,
  Milestone,
  OctagonAlert,
  Pause,
  Play,
  Power,
  Plus,
  QrCode,
  Radio,
  RefreshCw,
  RotateCcw,
  Save,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Square,
  Thermometer,
  Upload,
  X,
} from "lucide-react"
import { open as openDialog } from "@tauri-apps/plugin-dialog"
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener"
import { invoke } from "@tauri-apps/api/core"
import { getCurrentWindow } from "@tauri-apps/api/window"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { CanvasLineChart, type CanvasChartAnnotation, type CanvasChartAnnotationLegendItem, type CanvasChartLine, type CanvasColumnarData } from "@/components/canvas-line-chart"
import { AnimatedToastStack, type ToastStatus } from "@/components/motion/animated-toast-stack"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { useBackendSnapshot } from "@/hooks/use-backend-snapshot"
import { sendBackendRequest, type AppConfiguration, type AppShutdownResult, type AutomaticPauseAction, type AutomaticTerminalOutcome, type BackendSnapshot, type DeviceSnapshot, type PowerMeterCoefficientLibrary, type PowerMeterCoefficientRevision, type SafetyShutdownResult, type ShippingReportBatchGenerationResult, type ShippingReportBatchInspection, type ShippingReportBatchItem, type ShippingReportFieldDefinition, type ShippingReportGenerationResult, type ShippingReportLanguage, type ShippingReportPointSuggestion, type ShippingReportPreviewResult, type ShippingReportType, type ShippingWorkbookInspection, type SnapshotView, type SpectrumAxisMode, type TestScript } from "@/lib/backend"
import { powerMeterCoefficientLibraryForResource, powerMeterIdentityForResource, powerMeterResourceValue } from "@/lib/power-meter-resource"
import { mergeAuthoritativePowerMeterConfiguration, powerMeterDerivedConfigurationKeys } from "@/lib/power-meter-configuration"
import { automaticPauseCommand, automaticPauseCopy, pauseRemainingSeconds } from "@/lib/automatic-pause"
import { openResultFile } from "@/lib/result-file"
import { automaticTerminalTitle, shutdownPresentation } from "@/lib/safety-state"
import { automaticStartPresentation } from "@/lib/automatic-presentation"
import { spectrumPeakAnnotations, spectrumPeakLegend } from "@/lib/spectrum-annotations"
import { ActivationView } from "@/components/license/ActivationView"
import { AppearanceProvider, useAppearance } from "@/hooks/use-appearance"
import { AppearanceSettings, DisplayMenu } from "@/components/appearance-controls"
import { AutomaticCompletionSummary, AutomaticPhaseTrack, LivePowerRecordingDot, PowerStabilityStatus } from "@/components/automatic-overview"
import { NumericReadout } from "@/components/numeric-readout"
import { ACCENT_THEMES, type Theme, type AccentTheme, type CurveLineStyle, type LatestPointMarkerStyle } from "@/lib/appearance"
import { DataBrowserPage } from "@/components/data-browser-page"
import { formatLicenseDate, getLicenseStatus, installLicense, licenseReasonLabels, type LicenseStatus } from "@/lib/license"
import { cn } from "@/lib/utils"
import { normalizeNumericInput, stepNumericValue } from "@/lib/number-input"

type Page = "automatic" | "manual" | "pd" | "data" | "shipping" | "settings"

function DialogPortal({ children }: { children: ReactNode }) {
  return createPortal(children, document.body)
}

const I2C_CURRENT_LIMIT_A = 20
const POWER_PLOT_HISTORY_S = 60
const POWER_TIME_TICK_INTERVAL_S = 10
const PD_PLOT_HISTORY_S = 60
const PD_TIME_TICK_INTERVAL_S = 10
const SPECTRUM_Y_AXIS_PADDING_FRACTION = 0.08
const SPECTRUM_Y_AXIS_SHRINK_TRIGGER_FRACTION = 0.72
const SPECTRUM_Y_AXIS_SHRINK_REQUIRED_FRAMES = 6
const SPECTRUM_Y_AXIS_MINIMUM_LIMIT = -1000
const SHIPPING_PRODUCT_NAME_STORAGE_KEY = "power-test.shipping-report.product-name"
const SHIPPING_REPORT_SPLIT_LAYOUT = "grid min-w-0 gap-6 xl:grid-cols-[minmax(380px,0.72fr)_minmax(560px,1.28fr)]"
const DEFAULT_ENCIRCLED_ENERGY_NA = "0.17"
const THEME_STORAGE_KEY = "power-test.theme"
const ACCENT_THEME_STORAGE_KEY = "power-test.accent-theme"
const CURVE_LINE_STYLE_STORAGE_KEY = "power-test.automatic-curve-line-style"
const LATEST_POINT_MARKER_STORAGE_KEY = "power-test.latest-point-marker"
const PLM_CHART_SERIES = {
  accent: "var(--chart-1)",
  efficiency: "var(--chart-efficiency)",
  tan: "#d5a98b",
  olive: "#9a907c",
  green: "#70a58a",
  blue: "#4ca4ff",
  yellow: "#ffcc00",
} as const
const HISTORY_CHART_SERIES = {
  power: "rgba(168, 171, 176, 0.46)",
} as const
const navigation = [
  { id: "automatic" as const, label: "自动测试", icon: CircleGauge, shortcut: 1 },
  { id: "manual" as const, label: "手动配置", icon: Hand, shortcut: 2 },
  { id: "pd" as const, label: "PD 采集", icon: BarChart3, shortcut: 3 },
  { id: "data" as const, label: "数据浏览", icon: FileText, shortcut: 4 },
  { id: "shipping" as const, label: "出货报告", icon: FileText, shortcut: 5 },
  { id: "settings" as const, label: "系统设置", icon: Settings },
]

const pageShortcuts: Page[] = ["automatic", "manual", "pd", "data", "shipping"]
const INFO_TOAST_DURATION_MS = 3000
const MAX_CUSTOM_CURRENT_POINTS = 50
const TAURI_RUNTIME_AVAILABLE = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window
const DEMO_PREVIEW_ENABLED = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("demo") === "1"
const SAFE_UNLICENSED_METHODS = new Set([
  "app.stopAll",
  "app.shutdown",
  "automatic.end",
  "automatic.retryPersist",
  "automatic.retryExport",
  "pd.stop",
  "powerMeter.stop",
  "spectrometer.stop",
])
const DEMO_LICENSE_STATUS: LicenseStatus = {
  valid: true,
  reason: "valid",
  machineHash: "demo-preview",
  machineCode: "ARP-DEMO-PREVIEW",
  licensePath: "",
  payload: {
    product: "arp-power-test",
    customer: "演示模式",
    machineHash: "demo-preview",
    issuedAt: "2026-08-24T00:00:00.000Z",
    expiresAt: "2099-12-31T23:59:59.999Z",
    features: ["standard"],
  },
}
const BROWSER_LICENSE_STATUS: LicenseStatus = {
  valid: false,
  reason: "machine_id_unavailable",
  machineHash: "",
  machineCode: "请打开 Power Test 桌面应用",
  licensePath: "",
}

function getStoredTheme(): Theme {
  if (typeof window === "undefined") return "dark"
  try {
    const storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY)
    if (storedTheme === "light" || storedTheme === "dark") return storedTheme
  } catch {
    // Local storage may be unavailable in restricted WebView environments.
  }
  return document.documentElement.dataset.theme === "light" ? "light" : "dark"
}

function getStoredAccentTheme(): AccentTheme {
  if (typeof window === "undefined") return "orange"
  try {
    const stored = window.localStorage.getItem(ACCENT_THEME_STORAGE_KEY)
    const preset = ACCENT_THEMES.find((item) => item.id === stored)
    if (preset) return preset.id
  } catch {
    // Use the startup preference if local storage is unavailable.
  }
  return ACCENT_THEMES.find((item) => item.id === document.documentElement.dataset.accent)?.id ?? "orange"
}

function getStoredLatestPointMarkerStyle(): LatestPointMarkerStyle {
  if (typeof window === "undefined") return "point"
  try {
    const storedStyle = window.localStorage.getItem(LATEST_POINT_MARKER_STORAGE_KEY)
    if (storedStyle === "point" || storedStyle === "paper-plane" || storedStyle === "paw" || storedStyle === "comet" || storedStyle === "ecg") return storedStyle
  } catch {
    // Use the original point marker if storage is unavailable.
  }
  return "point"
}

function getStoredCurveLineStyle(): CurveLineStyle {
  if (typeof window === "undefined") return "solid"
  try {
    const storedStyle = window.localStorage.getItem(CURVE_LINE_STYLE_STORAGE_KEY)
    if (storedStyle === "solid" || storedStyle === "dashed" || storedStyle === "dotted") return storedStyle
  } catch {
    // Use the original solid line if storage is unavailable.
  }
  return "solid"
}

function curveLineDash(style: CurveLineStyle): number[] {
  if (style === "dashed") return [10, 6]
  if (style === "dotted") return [1, 5]
  return []
}

function applyTheme(theme: Theme, accentTheme: AccentTheme): void {
  const root = document.documentElement
  root.classList.toggle("dark", theme === "dark")
  root.dataset.theme = theme
  root.dataset.accent = accentTheme
  root.style.background = theme === "dark" ? "#0f1112" : "#f4f6f8"
  root.style.colorScheme = theme
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0f1112" : "#f4f6f8")
}

type SpectrumYAxisState = { domain: [number, number]; compactFrameCount: number }

function powerYAxisDomain(values: Array<number | null | undefined>): [number, number] | undefined {
  const finiteValues = values.filter((value): value is number => typeof value === "number" && Number.isFinite(value))
  if (finiteValues.length === 0) return undefined
  const minimum = Math.min(...finiteValues)
  const maximum = Math.max(...finiteValues)
  const padding = minimum === maximum ? Math.max(Math.abs(minimum) * 0.1, 0.001) : (maximum - minimum) * 0.12
  return [minimum >= 0 ? Math.max(0, minimum - padding) : minimum - padding, maximum <= 0 ? 0 : maximum + padding]
}

function spectrumYAxisDomain(values: number[]): [number, number] {
  if (values.length === 0) return [0, 1]
  const minimum = Math.min(...values)
  const maximum = Math.max(...values)
  const measuredSpan = maximum - minimum
  const fallbackSpan = Math.max(Math.abs(minimum), Math.abs(maximum), 1) * 0.1
  const span = Math.max(measuredSpan, fallbackSpan, 1)
  const padding = span * SPECTRUM_Y_AXIS_PADDING_FRACTION
  const lowerPadding = minimum > 0 ? Math.min(padding, minimum * 0.25) : padding
  return [Math.max(SPECTRUM_Y_AXIS_MINIMUM_LIMIT, minimum - lowerPadding), maximum + padding]
}

function stabilizeSpectrumYAxis(
  desiredDomain: [number, number],
  current: SpectrumYAxisState | null,
): SpectrumYAxisState {
  if (!current) return { domain: desiredDomain, compactFrameCount: 0 }
  const [desiredMinimum, desiredMaximum] = desiredDomain
  const [currentMinimum, currentMaximum] = current.domain
  if (desiredMinimum < currentMinimum || desiredMaximum > currentMaximum) {
    return {
      domain: [Math.min(desiredMinimum, currentMinimum), Math.max(desiredMaximum, currentMaximum)],
      compactFrameCount: 0,
    }
  }
  const currentSpan = currentMaximum - currentMinimum
  const desiredSpan = desiredMaximum - desiredMinimum
  const shouldCompact = desiredSpan <= currentSpan * SPECTRUM_Y_AXIS_SHRINK_TRIGGER_FRACTION
    || desiredMinimum - currentMinimum >= currentSpan * 0.15
    || currentMaximum - desiredMaximum >= currentSpan * 0.15
  if (!shouldCompact) {
    return current.compactFrameCount === 0 ? current : { ...current, compactFrameCount: 0 }
  }
  const compactFrameCount = current.compactFrameCount + 1
  return compactFrameCount >= SPECTRUM_Y_AXIS_SHRINK_REQUIRED_FRAMES
    ? { domain: desiredDomain, compactFrameCount: 0 }
    : { ...current, compactFrameCount }
}

const emptyConfig: AppConfiguration = {
  sn: "",
  productModel: "",
  batch: "",
  station: "",
  testTemperatureC: "",
  outputDir: "",
  powerSupplyKind: "ch341",
  ch341SelectedLocationPath: "",
  ch341PortBindings: [],
  tdkResource: "",
  setCurrentA: 0,
  tdkVoltageV: 0,
  powerMeterResource: "",
  powerMeterSerialNumber: "",
  powerMeterDriverKind: "",
  powerMeterWavelengthNm: 976,
  softwareGain: 1,
  powerMeterProfileId: "",
  powerMeterProfileName: "未绑定方案",
  powerMeterProfileVersion: 0,
  powerMeterProfileNotes: "",
  powerMeterProfileWavelengthPending: false,
  powerMeterIntervalMs: 100,
  powerSmoothing: true,
  spectrometerResource: "",
  spectrometerSerialNumber: "",
  integrationTimeUs: 1000,
  autoIntegration: false,
  spectrometerIntervalMs: 100,
  spectrumAverageFrames: 5,
  spectrumSmoothing: true,
  pibCenterWavelengthNm: 976,
  pibHalfRangeNm: 1.5,
  centerWavelengthSpecNm: 976,
  centerWavelengthToleranceNm: 1.5,
  fwhmMaximumNm: 0.6,
  pibMinimumPercent: 95,
  stableWindowS: 3,
  stabilityLimitPercent: 0.2,
  initialCurrentA: 1,
  targetCurrentA: 20,
  currentStepA: 3,
  selectedScriptId: "standard",
  currentPlanMode: "range",
  customCurrentPoints: [
    { currentA: 3, dwellS: 300 },
    { currentA: 5, dwellS: 300 },
  ],
  pointTimeoutS: 120,
  rampDownStepA: 5,
  rampDownIntervalS: 1.1,
  pauseRampDownTimeoutS: 30,
}

const configurationKeys = Object.keys(emptyConfig) as Array<keyof AppConfiguration>

function configurationPayload(config: AppConfiguration): Record<string, unknown> {
  const payload = { ...config } as Record<string, unknown>
  for (const key of powerMeterDerivedConfigurationKeys) delete payload[key]
  return payload
}

function configurationsEqual(left: AppConfiguration, right: AppConfiguration): boolean {
  if (!sameRows(left.customCurrentPoints, right.customCurrentPoints, ["currentA", "dwellS"])) return false
  if (!sameRows(left.ch341PortBindings, right.ch341PortBindings, ["locationPath", "name"])) return false
  return configurationKeys.every((key) => (
    key === "customCurrentPoints" || key === "ch341PortBindings" || left[key] === right[key]
  ))
}

const BUILTIN_TEST_SCRIPT_OPTIONS: TestScript[] = [
  { id: "standard", name: "标准测试", builtin: true, currentPlanMode: "range" },
  { id: "hold-points", name: "增加停留点", builtin: true, currentPlanMode: "custom" },
  { id: "aging", name: "AU 老化测试", builtin: true, currentPlanMode: "custom", powerSupplyKinds: ["ch341"] },
]

function applyTestScript(current: AppConfiguration, script: TestScript): AppConfiguration {
  if (script.id === "aging") {
    return {
      ...current,
      selectedScriptId: script.id,
      initialCurrentA: 2,
      targetCurrentA: 20,
      currentStepA: 2,
      currentPlanMode: "custom",
      customCurrentPoints: [{ currentA: 20, dwellS: 2 * 60 * 60 }],
    }
  }
  if (script.currentPlanMode === "custom") {
    return {
      ...current,
      selectedScriptId: script.id,
      currentPlanMode: "custom",
      customCurrentPoints: current.customCurrentPoints.length
        ? current.customCurrentPoints
        : [{ currentA: current.initialCurrentA, dwellS: 300 }],
    }
  }
  return { ...current, selectedScriptId: script.id, currentPlanMode: "range" }
}

function asNumber(value: string): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function formatTrimmedNumber(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "--"
  const formatted = value.toFixed(digits)
  return formatted.includes(".") ? formatted.replace(/\.?0+$/, "") : formatted
}

function formatFixedNumber(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "--"
  return value.toFixed(digits)
}

function formatNumber(value: number | null | undefined, suffix = "", digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "--"
  return `${formatTrimmedNumber(value, digits)}${suffix}`
}

const defaultSpectrometerResourceLabel = "请选择光谱仪"

function powerMeterResourceLabel(resourceValue: string, resources: string[] = []): string {
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

function pendingCommandLabel(method: string | null): string {
  if (!method) return "正在处理操作"
  if (method === "app.configure") return "正在保存设置"
  if (method === "app.stopAll") return "正在安全停止"
  if (method === "automatic.start") return "正在启动自动测试"
  if (method === "automatic.pause") return "正在暂停自动测试"
  if (method === "automatic.resume") return "正在继续自动测试"
  if (method === "automatic.retryPoint") return "正在重试当前点"
  if (method === "automatic.retryPersist") return "正在重试本地记录"
  if (method === "automatic.adjustSpectrum") return "正在应用光谱参数"
  if (method === "automatic.recoverPowerMeter") return "正在恢复功率计"
  if (method === "automatic.recoverSpectrometer") return "正在恢复光谱仪"
  if (method === "automatic.nextDevice") return "正在准备下一台"
  if (method === "automatic.retryExport") return "正在重新生成 Excel"
  if (method === "automatic.end") return "正在结束并安全下电"
  if (method === "device.refresh") return "正在识别设备"
  if (method.startsWith("powerSupply.")) return "正在执行电源操作"
  if (method.startsWith("powerMeter.")) return "正在执行功率计操作"
  if (method.startsWith("spectrometer.")) return "正在执行光谱仪操作"
  if (method.startsWith("pd.")) return "正在执行 PD 采集操作"
  return "正在处理操作"
}

type FieldLayout = "stacked" | "inline"

function Field({ label, children, className = "", layout = "stacked" }: { label: string; children: ReactNode; className?: string; layout?: FieldLayout }) {
  if (layout === "inline") {
    return (
      <div className={`manual-inline-field ${className}`}>
        <Label className="manual-inline-field-label">{label}：</Label>
        <div className="manual-inline-field-control">{children}</div>
      </div>
    )
  }
  return (
    <div className={`space-y-1.5 ${className}`}>
      <Label>{label}</Label>
      {children}
    </div>
  )
}

function ManualSectionHeading({ children }: { children: ReactNode }) {
  return <h3 className="manual-device-section-heading">{children}</h3>
}

function NativeSelect({ value, onChange, children, disabled, ariaLabel }: {
  value: string
  onChange: (value: string) => void
  children: ReactNode
  disabled?: boolean
  ariaLabel?: string
}) {
  const emptyValue = "__empty__"
  const options = Children.toArray(children).filter((child): child is ReactElement<{ value?: string; children?: ReactNode; disabled?: boolean }> => isValidElement(child))
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const selectedValue = value || emptyValue
  const selectedOption = options.find((option) => (String(option.props.value ?? "") || emptyValue) === selectedValue)
  const selectedIndex = options.findIndex((option) => (String(option.props.value ?? "") || emptyValue) === selectedValue)
  const displayValue = selectedOption?.props.children ?? (value || "请选择")

  useEffect(() => {
    if (!open) return
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener("pointerdown", handlePointerDown)
    return () => document.removeEventListener("pointerdown", handlePointerDown)
  }, [open])

  useEffect(() => {
    if (!open || options.length === 0) return
    const initialIndex = selectedIndex >= 0 && !options[selectedIndex]?.props.disabled
      ? selectedIndex
      : options.findIndex((option) => !option.props.disabled)
    const frame = window.requestAnimationFrame(() => optionRefs.current[Math.max(0, initialIndex)]?.focus())
    return () => window.cancelAnimationFrame(frame)
  }, [open, options.length, selectedIndex])

  const closeAndRestoreFocus = () => {
    setOpen(false)
    window.requestAnimationFrame(() => triggerRef.current?.focus())
  }

  const moveOptionFocus = (currentIndex: number, offset: number) => {
    for (let distance = 1; distance <= options.length; distance += 1) {
      const nextIndex = (currentIndex + offset * distance + options.length) % options.length
      if (!options[nextIndex]?.props.disabled) {
        optionRefs.current[nextIndex]?.focus()
        return
      }
    }
  }

  const focusBoundaryOption = (fromEnd: boolean) => {
    const indices = options.map((_option, index) => index)
    if (fromEnd) indices.reverse()
    const index = indices.find((candidate) => !options[candidate]?.props.disabled)
    if (index !== undefined) optionRefs.current[index]?.focus()
  }

  return (
    <div className="relative w-full" ref={rootRef}>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={ariaLabel}
        className="flex h-9 w-full items-center justify-between gap-2 rounded-lg border border-input bg-background px-3 text-left text-sm outline-none transition-[color,background-color,border-color,box-shadow] duration-150 hover:border-[var(--app-border-strong)] hover:bg-muted/25 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20 disabled:cursor-not-allowed disabled:border-border disabled:bg-muted/30 disabled:text-muted-foreground/55"
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
        ref={triggerRef}
        type="button"
      >
        <span className="min-w-0 truncate">{displayValue}</span>
        <ChevronDown className={`size-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && <div className="menu-enter absolute left-0 top-full z-[80] mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-[var(--plm-flat-border-strong)] bg-popover p-1 shadow-lg shadow-black/30" role="listbox">
        {options.map((option, index) => {
          const optionValue = String(option.props.value ?? "") || emptyValue
          const selected = optionValue === selectedValue
          const optionDisabled = Boolean(option.props.disabled)
          return <button
            aria-disabled={optionDisabled}
            aria-selected={selected}
            className={`relative flex w-full items-center rounded-md px-3 py-2 text-left text-sm outline-none transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-45 ${optionDisabled ? "" : "hover:bg-[var(--plm-flat-hover)] hover:text-foreground focus-visible:bg-[var(--plm-flat-hover)] focus-visible:text-foreground"} ${selected ? "bg-[var(--app-selected-soft)] pl-4 text-foreground before:absolute before:inset-y-2 before:left-1.5 before:w-0.5 before:rounded-full before:bg-primary" : "text-muted-foreground"}`}
            disabled={optionDisabled}
            key={`${optionValue}-${index}`}
            onClick={() => {
              onChange(optionValue === emptyValue ? "" : optionValue)
              closeAndRestoreFocus()
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault()
                moveOptionFocus(index, 1)
              } else if (event.key === "ArrowUp") {
                event.preventDefault()
                moveOptionFocus(index, -1)
              } else if (event.key === "Home") {
                event.preventDefault()
                focusBoundaryOption(false)
              } else if (event.key === "End") {
                event.preventDefault()
                focusBoundaryOption(true)
              } else if (event.key === "Escape") {
                event.stopPropagation()
                event.preventDefault()
                closeAndRestoreFocus()
              }
            }}
            ref={(element) => { optionRefs.current[index] = element }}
            role="option"
            type="button"
          >{option.props.children}</button>
        })}
      </div>}
    </div>
  )
}

function Ch341PortSelector({
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
  onRefresh: () => void
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
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
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
          <Button
            className={`btn-refresh-spin ${detecting ? "cursor-wait" : ""}`}
            disabled={!active || pending || detecting || isConnected}
            onClick={onRefresh}
            variant="outline"
          >
            <RefreshCw className={`size-4 ${detecting ? "animate-spin" : ""}`} />
            {detecting ? "识别中" : "识别"}
          </Button>
        </div>
      </Field>
    </div>
  )
}

function StatusDot({ state }: { state?: string }) {
  const color = state === "connected" || state === "running"
    ? "bg-[var(--app-success)]"
    : state === "error"
      ? "bg-[var(--app-danger)]"
      : state === "connecting"
        ? "bg-[var(--app-warning)]"
        : "bg-muted-foreground/45"
  return <span className={`status-dot size-2 shrink-0 rounded-full ${color}`} key={state ?? "idle"} />
}

type DeviceSettingsKind = "powerSupply" | "powerMeter" | "spectrometer"

function DeviceCard({ title, icon, device, model, details, summary, visibleSummary, disabled = false, onOpenSettings }: { title: string; icon: ReactNode; device?: DeviceSnapshot; model: string; details: Array<[string, string]>; summary?: string; visibleSummary?: string; disabled?: boolean; onOpenSettings: () => void }) {
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
          <span className="device-rail-item__icon" data-active={device?.outputEnabled}>{icon}</span>
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
}

function AutomaticTestControlCard({
  active,
  auto,
  config,
  controlsLocked,
  pending,
  onStart,
  onStop,
}: {
  active: boolean
  auto?: BackendSnapshot["automaticTest"]
  config: Pick<AppConfiguration, "sn" | "outputDir">
  controlsLocked: boolean
  pending: boolean
  onStart: () => void
  onStop: () => void
}) {
  const running = Boolean(auto?.canEnd)
  const { canStart, messages: readinessMessages } = automaticStartPresentation(auto, config)
  return (
    <Card className="automatic-test-control-card gap-2.5 py-3 shadow-none">
      <CardContent className="automatic-test-control-card__content px-4 py-0">
        <div className="automatic-test-control-card__actions">
          <Button
            disabled={!active || pending || controlsLocked || running}
            onClick={onStart}
            size="sm"
            title={!canStart && readinessMessages.length > 0 ? readinessMessages.join("；") : undefined}
          >
            <Play className="size-4" />开始
          </Button>
          <Button
            disabled={!active || pending || !running}
            onClick={onStop}
            size="sm"
            variant="destructive"
          >
            <Square className="size-4" />停止
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0 s"
  if (seconds % 60 === 0) return `${seconds / 60} min`
  return `${seconds} s`
}

function formatMeasurementAge(ageMs?: number | null): string {
  if (typeof ageMs !== "number" || !Number.isFinite(ageMs)) return "时间未知"
  return `${Math.max(1, Math.ceil(ageMs / 1000))} s 前`
}

function CustomCurrentPlanEditor({ config, update, disabled, maximumCurrentA }: {
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
        <Button disabled={disabled || config.customCurrentPoints.length >= MAX_CUSTOM_CURRENT_POINTS} onClick={addPoint} size="sm" type="button" variant="outline"><Plus className="size-3.5" />增加停留点</Button>
      </div>
      <p className="custom-current-plan__hint">自动测试仍按起始、目标和步长逐点判稳；指定电流点会插入序列，判稳后按停留时间每 1 s 记录完整数据。</p>
    </div>
  )
}

function PowerSupplySettingsForm({ snapshot, config, update, active, pending, run }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
}) {
  const psu = snapshot?.devices.powerSupply
  const resources = psu?.resources ?? []
  const selectedResource = config.tdkResource.trim()
  const agingMode = config.selectedScriptId === "aging"
  return (
    <div className="power-settings-layout">
      <section className="power-settings-section">
        <div className="power-settings-section-heading">
          <p className="power-settings-section-title">手动控制</p>
          <p className="power-settings-section-description">连接设备并执行输出控制与状态读取</p>
        </div>
        <div className="power-settings-fields">
          <Field label="控制器"><NativeSelect disabled={!active || pending || psu?.detecting || psu?.connected} value={config.powerSupplyKind} onChange={(v) => updatePowerSupplyKind(config, update, v as AppConfiguration["powerSupplyKind"])}><option value="ch341">CH341 I²C</option><option disabled={agingMode} value="tdk">{agingMode ? "TDK RS232（AU 老化测试不可用）" : "TDK RS232"}</option></NativeSelect></Field>
          {config.powerSupplyKind === "tdk" ? <Field label="TDK 串口">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
              <NativeSelect disabled={!active || pending || psu?.detecting || psu?.connected} value={selectedResource} onChange={(value) => update("tdkResource", value)}>
                <option value="">请点击识别电源</option>
                {selectedResource && !resources.includes(selectedResource) && <option value={selectedResource}>{selectedResource}</option>}
                {resources.map((resource) => <option key={resource} value={resource}>{resource}</option>)}
              </NativeSelect>
              <Button className={`btn-refresh-spin ${psu?.detecting ? "cursor-wait" : ""}`} disabled={!active || pending || psu?.detecting || psu?.connected} onClick={() => void run("device.refresh", { device: "powerSupply" }, true)} variant="outline"><RefreshCw className={`size-4 ${psu?.detecting ? "animate-spin" : ""}`} />{psu?.detecting ? "识别中" : "识别"}</Button>
            </div>
          </Field> : (
            <Ch341PortSelector
              config={config}
              snapshot={snapshot}
              active={active}
              pending={pending}
              layout="stacked"
              update={update}
              onRefresh={() => void run("device.refresh", { device: "powerSupply" }, true)}
            />
          )}
          <div className="power-settings-value-grid">
            {config.powerSupplyKind === "tdk" && <NumberField label="输出电压 (V)" value={config.tdkVoltageV} onChange={(v) => update("tdkVoltageV", v)} step="0.1" />}
            <NumberField label="设定电流 (A)" value={config.setCurrentA} onChange={(v) => update("setCurrentA", v)} max={currentInputMaximum(config, snapshot)} step="0.1" />
          </div>
          {config.powerSupplyKind === "tdk" ? <div className="power-settings-action-grid">
            <Button disabled={!active || pending || psu?.detecting} onClick={() => void run(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)} variant={psu?.connected ? "outline" : "default"}>{psu?.connected ? "安全断开" : "连接 TDK"}</Button>
            <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setVoltage", { voltageV: config.tdkVoltageV }, true)} variant="outline">设置电压</Button>
            <Button className={psu?.outputEnabled ? "btn-power-active" : ""} disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setOutput", { enabled: !psu?.outputEnabled })} variant={psu?.outputEnabled ? "destructive" : "outline"}>{psu?.outputEnabled ? <><span className="size-2 rounded-full bg-white animate-pulse shrink-0 mr-1.5 inline-block" />关闭输出</> : "开启输出"}</Button>
            <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)}>设置电流</Button>
          </div> : <div className="power-settings-action-grid">
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
                  disabled={!active || pending || psu?.detecting || (!psu?.connected && !isCh341ReadyToConnect)}
                  onClick={() => void run(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)}
                  variant={psu?.connected ? "outline" : "default"}
                >
                  {btnText}
                </Button>
              )
            })()}
            <Button disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)}>设置电流</Button>
            <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputVoltage" })} size="sm" variant="outline">读取输出电压</Button>
            <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "outputCurrent" })} size="sm" variant="outline">读取输出电流</Button>
            <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "inputVoltage" })} size="sm" variant="outline">读取输入电压</Button>
            <Button className="active:bg-primary/20 transition-colors" disabled={!active || pending || !psu?.connected} onClick={() => void run("powerSupply.read", { value: "temperature" })} size="sm" variant="outline">读取模块温度</Button>
            <p className="col-span-full text-xs text-muted-foreground">
              回读：输入 {formatNumber(psu?.inputVoltageV, " V", 2)} · 输出 {formatNumber(psu?.outputVoltageV, " V", 2)} · 电流 {formatNumber(psu?.outputCurrentA, " A", 2)} · 温度 {formatNumber(psu?.temperatureC, " °C", 2)}
            </p>
          </div>}
        </div>
      </section>
      <section className="power-settings-section power-settings-section--automatic">
        <div className="power-settings-section-heading">
          <p className="power-settings-section-title">测试脚本参数</p>
          <p className="power-settings-section-description">顶部选择脚本；这里查看升流节拍和设置安全下电参数</p>
        </div>
        <div className="power-settings-parameter-grid">
          <NumberField disabled={agingMode} label="起始电流 (A)" value={config.initialCurrentA} onChange={(v) => update("initialCurrentA", v)} max={currentInputMaximum(config, snapshot)} />
          <NumberField disabled={agingMode} label="目标电流 (A)" value={config.targetCurrentA} onChange={(v) => update("targetCurrentA", v)} max={currentInputMaximum(config, snapshot)} />
          <NumberField disabled={agingMode} label="电流间隔 (A)" value={config.currentStepA} onChange={(v) => update("currentStepA", v)} max={currentInputMaximum(config, snapshot)} />
        </div>
        {agingMode ? <div className="custom-current-plan">
          <p className="custom-current-plan__hint">CH341 从 2 A 开始，每步增加 2 A；每步等待 1 s，硬件命令保护下实际下发间隔不短于 1.1 s。到达 20 A 后不判稳，立即开始 2 小时老化计时。</p>
        </div> : config.currentPlanMode === "custom" && <CustomCurrentPlanEditor config={config} disabled={pending} maximumCurrentA={currentInputMaximum(config, snapshot)} update={update} />}
        <div className="power-settings-parameter-grid power-settings-parameter-grid--safety">
          <NumberField label="单点超时 (s)" value={config.pointTimeoutS} onChange={(v) => update("pointTimeoutS", v)} />
          <NumberField label="下电步长 (A)" value={config.rampDownStepA} onChange={(v) => update("rampDownStepA", v)} max={currentInputMaximum(config, snapshot)} />
          <NumberField label="下电间隔 (s)" value={config.rampDownIntervalS} onChange={(v) => update("rampDownIntervalS", v)} step="0.1" />
        </div>
      </section>
    </div>
  )
}

type PowerMeterProfileDraftIntent = "new" | "copy" | "save"

interface PowerMeterProfileDraft {
  name: string
  softwareGain: number
  wavelengthNm: number
  notes: string
  operator: string
  reason: string
}

function selectedPowerMeterRevision(library: PowerMeterCoefficientLibrary | null): PowerMeterCoefficientRevision | null {
  if (!library) return null
  const profile = library.profiles.find((item) => item.id === library.selectedProfileId)
  return profile?.versions.find((revision) => revision.version === library.selectedVersion)
    ?? profile?.versions[profile.versions.length - 1]
    ?? null
}

function formatProfileTimestamp(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value || "时间未知" : date.toLocaleString("zh-CN", { hour12: false })
}

function PowerMeterProfileEditor({ snapshot, config, active, pending, run, compact = false }: {
  snapshot: BackendSnapshot | null
  config: AppConfiguration
  active: boolean
  pending: boolean
  run: RunCommand
  compact?: boolean
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
  const mutationLocked = !active || pending || busy || Boolean(meter?.detecting || meter?.running) || automaticActive || pendingManualPoints > 0
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

  const perform = async (method: string, params: Record<string, unknown>) => {
    if (mutationLocked || !driverKind || !serialNumber) return
    setBusy(true)
    setError("")
    try {
      const result = await run(method, { driverKind, serialNumber, ...params }, true)
      if (!result) throw new Error("操作未执行，请等待当前设备命令完成后重试")
      setDraftIntent(null)
      setDraftTarget(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      throw reason
    } finally {
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

  return (
    <section className={`power-meter-profile ${compact ? "power-meter-profile--compact" : ""}`}>
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
          disabled={mutationLocked || draftIntent !== null}
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
        <Field label="方案名称"><Input disabled={mutationLocked} maxLength={60} value={draft.name} onChange={(event) => updateDraft("name", event.target.value)} /></Field>
        <div className="device-settings-field-grid">
          <NumberField disabled={mutationLocked} label="功率系数" min={0.000001} step="0.001" value={draft.softwareGain} onChange={(value) => updateDraft("softwareGain", value)} />
          <NumberField disabled={mutationLocked} label="校准波长 (nm)" min={0.000001} step="0.1" value={draft.wavelengthNm} onChange={(value) => updateDraft("wavelengthNm", value)} />
        </div>
        <Field label="条件备注"><textarea className="power-meter-profile__textarea" disabled={mutationLocked} maxLength={300} rows={2} value={draft.notes} onChange={(event) => updateDraft("notes", event.target.value)} /></Field>
        <div className="device-settings-field-grid">
          <Field label="修改人（可选）"><Input disabled={mutationLocked} maxLength={60} value={draft.operator} onChange={(event) => updateDraft("operator", event.target.value)} /></Field>
          <Field label="修改原因（可选）"><Input disabled={mutationLocked} maxLength={120} value={draft.reason} onChange={(event) => updateDraft("reason", event.target.value)} /></Field>
        </div>
        <div className="power-meter-profile__draft-actions">
          <Button disabled={busy} onClick={() => { setDraftIntent(null); setDraftTarget(null); setError("") }} size="sm" variant="outline">取消</Button>
          <Button disabled={mutationLocked} onClick={() => void saveDraft()} size="sm"><Save className="size-3.5" />{busy ? "保存中…" : "保存方案"}</Button>
        </div>
        {draftStale && <p className="power-meter-profile__message is-warning">此方案在草稿打开后已有新版本。草稿仍保留，保存时会由后端阻止覆盖；建议取消后查看最新版本。</p>}
      </div>}

      {!draftIntent && <div className="power-meter-profile__actions">
        <Button disabled={mutationLocked || !serialNumber} onClick={() => beginDraft("new")} size="sm" variant="outline"><Plus className="size-3.5" />新建</Button>
        <Button disabled={mutationLocked || !selectedRevision} onClick={() => beginDraft("copy")} size="sm" variant="outline">另存为</Button>
        <Button disabled={mutationLocked || !selectedRevision} onClick={() => beginDraft("save")} size="sm" variant="outline">保存修改</Button>
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
            <Button disabled={mutationLocked || index === 0} onClick={() => void restoreRevision(revision)} size="sm" variant="ghost"><RotateCcw className="size-3.5" />{index === 0 ? "当前版本" : "恢复此版本"}</Button>
          </article>
        ))}
      </div>}

      {(lockReason || error) && <p aria-live="polite" className={`power-meter-profile__message ${error ? "is-error" : ""}`}>{error || lockReason}</p>}
    </section>
  )
}

function PowerMeterSettingsForm({ snapshot, config, update, active, pending, run }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
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
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
          <NativeSelect disabled={!active || pending || meter?.detecting || meter?.running} value={displayedResource} onChange={selectResource}>
            <option value="">请选择串口资源</option>
            {resources.map((item) => <option key={item} value={powerMeterResourceValue(item)}>{item}</option>)}
          </NativeSelect>
          <Button className={`btn-refresh-spin ${meter?.detecting ? "cursor-wait" : ""}`} disabled={!active || pending || meter?.detecting || meter?.running} onClick={() => void run("device.refresh", { device: "powerMeter" }, true)} variant="outline"><RefreshCw className={`size-4 ${meter?.detecting ? "animate-spin" : ""}`} />{meter?.detecting ? "识别中" : "识别"}</Button>
        </div>
      </Field>
      <PowerMeterProfileEditor active={active} config={config} pending={pending} run={run} snapshot={snapshot} />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <label className="device-settings-checkbox"><input checked={config.powerSmoothing ?? true} onChange={(e) => update("powerSmoothing", e.target.checked)} type="checkbox" />功率曲线平滑</label>
        <span className="text-xs text-muted-foreground">仅影响显示，不改变测量数据</span>
      </div>
      <div className="device-settings-action-grid"><Button disabled={!active || pending || meter?.detecting || meter?.running || !selectedResourceAvailable} onClick={() => void run("powerMeter.setRelativeZero", { enabled: true }, true)} variant="outline">相对调零</Button><Button disabled={!active || pending || meter?.detecting || meter?.running || !selectedResourceAvailable} onClick={() => void run("powerMeter.setRelativeZero", { enabled: false }, true)} variant="outline">取消调零</Button></div>
      <Button className={`w-full transition-all ${meter?.running ? "btn-running-pulse" : ""}`} disabled={!active || pending || meter?.detecting || (!meter?.running && !selectedResourceAvailable)} onClick={() => void run(meter?.running ? "powerMeter.stop" : "powerMeter.start", {}, true)} variant={meter?.running ? "destructive" : "default"}>{meter?.running ? <><Square className="size-4" />停止采集</> : <><Play className="size-4" />开始采集</>}</Button>
      <p className="text-xs text-muted-foreground">{measurementSummary}</p>
    </div>
  )
}

function SpectrometerSettingsForm({ snapshot, config, update, active, pending, run }: {
  snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
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
      <Field label="设备资源">
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
          <NativeSelect disabled={!active || pending || spectrum?.detecting || spectrum?.running} value={displayedResource} onChange={(v) => update("spectrometerResource", v)}>
            <option value="">{defaultSpectrometerResourceLabel}</option>
            {resources.map((item) => <option key={item} value={item}>{item}</option>)}
          </NativeSelect>
          <Button className={`btn-refresh-spin ${spectrum?.detecting ? "cursor-wait" : ""}`} disabled={!active || pending || spectrum?.detecting || spectrum?.running} onClick={() => void run("device.refresh", { device: "spectrometer" }, true)} variant="outline"><RefreshCw className={`size-4 ${spectrum?.detecting ? "animate-spin" : ""}`} />{spectrum?.detecting ? "识别中" : "识别"}</Button>
        </div>
      </Field>
      <div className="device-settings-field-grid">
        <NumberField label="积分时间 (μs)" value={config.integrationTimeUs} onChange={(v) => update("integrationTimeUs", v)} />
        <NumberField label="刷新间隔 (ms)" value={config.spectrometerIntervalMs} onChange={(v) => update("spectrometerIntervalMs", v)} />
        <NumberField label="平均帧数" min={1} max={20} value={config.spectrumAverageFrames} onChange={(v) => update("spectrumAverageFrames", Math.max(1, Math.min(20, Math.round(v))))} />
        <Field label="采集模式"><label className="device-settings-checkbox"><input checked={config.autoIntegration} onChange={(e) => update("autoIntegration", e.target.checked)} type="checkbox" />自动积分</label></Field>
        <NumberField label="PIB 中心波长 (nm)" step="0.1" value={config.pibCenterWavelengthNm} onChange={(v) => update("pibCenterWavelengthNm", v)} />
        <NumberField label="PIB 半带宽 (nm)" step="0.1" value={config.pibHalfRangeNm} onChange={(v) => update("pibHalfRangeNm", v)} />
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <label className="device-settings-checkbox"><input checked={config.spectrumSmoothing ?? true} onChange={(e) => update("spectrumSmoothing", e.target.checked)} type="checkbox" />光谱曲线平滑</label>
        <span className="text-xs text-muted-foreground">仅影响显示，不改变测量数据</span>
      </div>
      <Button className={`w-full transition-all ${spectrum?.running ? "btn-running-pulse" : ""}`} disabled={!active || pending || spectrum?.detecting || (!spectrum?.running && !selectedResourceAvailable)} onClick={() => void run(spectrum?.running ? "spectrometer.stop" : "spectrometer.start", {}, true)} variant={spectrum?.running ? "destructive" : "default"}>{spectrum?.running ? <><Square className="size-4" />停止采集</> : <><Play className="size-4" />开始采集</>}</Button>
      <p className="text-xs text-muted-foreground">{measurementSummary}</p>
    </div>
  )
}

function DeviceSettingsDialog({ kind, open, onClose, onSave, snapshot, config, update, active, pending, run }: {
  kind: DeviceSettingsKind; open: boolean; onClose: () => void; onSave: () => Promise<void>; snapshot: BackendSnapshot | null; config: AppConfiguration; update: UpdateConfig; active: boolean; pending: boolean; run: RunCommand
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
    powerSupply: { title: "电源设置", description: "配置电源控制器、输出参数并执行安全控制", icon: <Power className="size-5" /> },
    powerMeter: { title: "功率计设置", description: "选择功率计资源并管理探头系数方案", icon: <Gauge className="size-5" /> },
    spectrometer: { title: "光谱仪设置", description: "配置光谱仪硬件资源与光谱采集参数", icon: <Radio className="size-5" /> },
  }[kind]
  const handleSave = async () => {
    if (!active || saving || pending || commandLockRef.current) return
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

  const runDialogCommand: RunCommand = async (method, params, sync) => {
    if (commandLockRef.current || pending) return
    commandLockRef.current = true
    try {
      return await run(method, params, sync)
    } finally {
      commandLockRef.current = false
    }
  }

  return (
    <DialogPortal>
      <div className="dialog-overlay motion-overlay-enter" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
        <div aria-labelledby={`${kind}-settings-title`} aria-modal="true" className="device-settings-dialog motion-dialog-enter" data-kind={kind} role="dialog">
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
          <div className="device-settings-dialog__body">
            {kind === "powerSupply" && <PowerSupplySettingsForm active={active} config={config} pending={false} run={runDialogCommand} snapshot={snapshot} update={update} />}
            {kind === "powerMeter" && <PowerMeterSettingsForm active={active} config={config} pending={false} run={runDialogCommand} snapshot={snapshot} update={update} />}
            {kind === "spectrometer" && <SpectrometerSettingsForm active={active} config={config} pending={false} run={runDialogCommand} snapshot={snapshot} update={update} />}
          </div>
          <div className="device-settings-dialog__footer">
            <p className="device-settings-dialog__hint">{kind === "powerMeter" ? "普通设置不会覆盖已保存的系数方案" : "修改内容将保存到当前测试配置"}</p>
            <div className="device-settings-dialog__actions">
              <Button className="device-settings-dialog__cancel" onClick={onClose} variant="outline">关闭</Button>
              <Button aria-busy={saving} aria-disabled={!active || pending || saving} className="device-settings-dialog__save" disabled={!active || saving} onClick={() => void handleSave()}>{saving ? "保存中…" : "保存设置"}</Button>
            </div>
          </div>
        </div>
      </div>
    </DialogPortal>
  )
}

function PowerMeterProfileDialog({
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

  const runDialogCommand: RunCommand = async (method, params, sync) => {
    if (commandLockRef.current || pending) return
    commandLockRef.current = true
    try {
      return await run(method, params, sync)
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

function AutomaticSpecificationsDialog({ config, update, active, pending, onClose, onSave }: {
  config: AppConfiguration
  update: UpdateConfig
  active: boolean
  pending: boolean
  onClose: () => void
  onSave: () => Promise<void>
}) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const onCloseRef = useRef(onClose)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const [draft, setDraft] = useState({
    centerWavelengthSpecNm: config.centerWavelengthSpecNm,
    centerWavelengthToleranceNm: config.centerWavelengthToleranceNm,
    fwhmMaximumNm: config.fwhmMaximumNm,
    pibMinimumPercent: config.pibMinimumPercent,
  })
  onCloseRef.current = onClose

  useEffect(() => {
    const body = document.body
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = body.style.overflow
    const previousPaddingRight = body.style.paddingRight
    const focusFrame = window.requestAnimationFrame(() => closeButtonRef.current?.focus())
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || saving) return
      event.preventDefault()
      onCloseRef.current()
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
  }, [saving])

  const updateDraft = <K extends keyof typeof draft>(key: K, value: (typeof draft)[K]) => {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  const handleSave = async () => {
    if (saving || pending || !active) return
    setError("")
    setSaving(true)
    try {
      update("centerWavelengthSpecNm", Math.max(0, draft.centerWavelengthSpecNm))
      update("centerWavelengthToleranceNm", Math.max(0, draft.centerWavelengthToleranceNm))
      update("fwhmMaximumNm", Math.max(0, draft.fwhmMaximumNm))
      update("pibMinimumPercent", Math.max(0, Math.min(100, draft.pibMinimumPercent)))
      await onSave()
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }

  return (
    <DialogPortal>
      <div className="dialog-overlay motion-overlay-enter" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onClose() }}>
        <div aria-labelledby="automatic-specifications-title" aria-modal="true" className="device-settings-dialog automatic-specification-dialog motion-dialog-enter" role="dialog">
          <div className="device-settings-dialog__header">
            <div className="device-settings-dialog__identity">
              <div className="device-settings-dialog__icon"><Settings className="size-5" /></div>
              <div className="min-w-0">
                <h2 className="device-settings-dialog__title" id="automatic-specifications-title">设置测试规格</h2>
                <p className="device-settings-dialog__description">配置自动测试指标的判定范围，保存后应用于当前工位</p>
              </div>
            </div>
            <button aria-label="关闭规格设置弹窗" className="device-settings-dialog__close" onClick={onClose} ref={closeButtonRef} type="button"><X className="size-[18px]" /></button>
          </div>
          <div className="device-settings-dialog__body">
            <div className="automatic-specification-form">
              <div className="automatic-specification-form__intro">
                <div>
                  <p className="text-sm font-semibold">光谱指标</p>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">点击指标卡即可再次打开此设置。</p>
                </div>
                <Badge variant="outline">当前工位</Badge>
              </div>
              <div className="device-settings-field-grid">
                <NumberField label="中心波长规格 (nm)" step="0.1" value={draft.centerWavelengthSpecNm} onChange={(value) => updateDraft("centerWavelengthSpecNm", value)} />
                <NumberField label="中心波长允许偏差 (± nm)" min={0} step="0.1" value={draft.centerWavelengthToleranceNm} onChange={(value) => updateDraft("centerWavelengthToleranceNm", value)} />
                <NumberField label="FWHM 上限 (nm)" min={0} step="0.01" value={draft.fwhmMaximumNm} onChange={(value) => updateDraft("fwhmMaximumNm", value)} />
                <NumberField label="PIB 下限 (%)" min={0} max={100} step="0.1" value={draft.pibMinimumPercent} onChange={(value) => updateDraft("pibMinimumPercent", value)} />
              </div>
              {error && <p className="text-xs text-destructive">保存失败：{error}</p>}
            </div>
          </div>
          <div className="device-settings-dialog__footer">
            <p className="device-settings-dialog__hint">规格保存到当前工位配置</p>
            <div className="device-settings-dialog__actions">
              <Button className="device-settings-dialog__cancel" disabled={saving} onClick={onClose} variant="outline">取消</Button>
              <Button aria-busy={saving} className="device-settings-dialog__save" disabled={!active || pending || saving} onClick={() => void handleSave()}>{saving ? "保存中…" : "保存规格"}</Button>
            </div>
          </div>
        </div>
      </div>
    </DialogPortal>
  )
}

function PowerProtectionDialog({ message, onClose }: { message: string; onClose: () => void }) {
  const acknowledgeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const body = document.body
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = body.style.overflow
    const previousPaddingRight = body.style.paddingRight
    const focusFrame = window.requestAnimationFrame(() => acknowledgeButtonRef.current?.focus())
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    const hasStableScrollbarGutter = getComputedStyle(document.documentElement).scrollbarGutter.includes("stable")
    body.style.overflow = "hidden"
    if (scrollbarWidth > 0 && !hasStableScrollbarGutter) body.style.paddingRight = `${scrollbarWidth}px`
    return () => {
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPaddingRight
      window.cancelAnimationFrame(focusFrame)
      previousFocus?.focus()
    }
  }, [])

  return (
    <DialogPortal>
      <div className="motion-overlay-enter fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4 backdrop-blur-[2px]">
        <div aria-labelledby="power-protection-title" aria-modal="true" className="status-dialog motion-dialog-enter w-[min(520px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-[color-mix(in_srgb,var(--app-danger)_55%,transparent)] bg-popover shadow-2xl shadow-black/45" role="alertdialog">
        <div className="flex items-start gap-4 border-b border-border px-5 py-5">
          <div className="grid size-11 shrink-0 place-items-center rounded-full bg-[color-mix(in_srgb,var(--app-danger)_16%,transparent)] text-[var(--app-danger)]"><OctagonAlert className="size-6" /></div>
          <div><h2 className="text-lg font-semibold" id="power-protection-title">功率保护已触发</h2><p className="mt-1 text-sm text-muted-foreground">设备已进入断电保护状态</p></div>
        </div>
        <div className="px-5 py-5"><p className="whitespace-pre-line text-sm leading-6 text-foreground">{message}</p></div>
          <div className="flex justify-end border-t border-border bg-background/35 px-5 py-3"><Button onClick={onClose} ref={acknowledgeButtonRef} variant="destructive">确认并关闭</Button></div>
        </div>
      </div>
    </DialogPortal>
  )
}

function AutomaticPauseDialog({ auto, integrationTimeUs, pending, onAction }: {
  auto: BackendSnapshot["automaticTest"]
  integrationTimeUs: number
  pending: boolean
  onAction: (action: AutomaticPauseAction, params?: Record<string, unknown>) => void
}) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const [now, setNow] = useState(() => Date.now())
  const [draftIntegrationTimeUs, setDraftIntegrationTimeUs] = useState(integrationTimeUs)
  const actions = new Set(auto.pauseActions ?? [])
  const deadlineMs = auto.pauseDeadlineAtUtc ? new Date(auto.pauseDeadlineAtUtc).getTime() : null
  const remainingSeconds = pauseRemainingSeconds(auto.pauseDeadlineAtUtc, now)
  const copy = automaticPauseCopy(auto.pauseKind)

  useEffect(() => {
    if (deadlineMs === null) return
    const timer = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(timer)
  }, [deadlineMs])

  useEffect(() => {
    const body = document.body
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = body.style.overflow
    const previousPaddingRight = body.style.paddingRight
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    const hasStableScrollbarGutter = getComputedStyle(document.documentElement).scrollbarGutter.includes("stable")
    const focusFrame = window.requestAnimationFrame(() => dialogRef.current?.focus())
    body.style.overflow = "hidden"
    if (scrollbarWidth > 0 && !hasStableScrollbarGutter) body.style.paddingRight = `${scrollbarWidth}px`
    return () => {
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPaddingRight
      window.cancelAnimationFrame(focusFrame)
      previousFocus?.focus()
    }
  }, [])

  return (
    <DialogPortal>
      <div className="motion-overlay-enter fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4 backdrop-blur-[2px]">
        <div aria-labelledby="automatic-pause-title" aria-modal="true" className="status-dialog motion-dialog-enter w-[min(580px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-[color-mix(in_srgb,var(--app-warning)_55%,transparent)] bg-popover shadow-2xl shadow-black/45" ref={dialogRef} role="alertdialog" tabIndex={-1}>
          <div className="flex items-start gap-4 border-b border-border px-5 py-5">
            <div className="grid size-11 shrink-0 place-items-center rounded-full bg-[color-mix(in_srgb,var(--app-warning)_16%,transparent)] text-[var(--app-warning)]"><Pause className="size-6" /></div>
            <div>
              <h2 className="text-lg font-semibold" id="automatic-pause-title">{copy[0]}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{copy[1]}</p>
            </div>
          </div>
          <div className="space-y-4 px-5 py-5">
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg border border-border bg-muted/25 px-3.5 py-3"><p className="text-[11px] text-muted-foreground">当前测试点</p><p className="mt-1 font-mono text-sm font-semibold">{typeof auto.currentA === "number" ? `${auto.currentA} A` : "--"}</p></div>
              <div className="rounded-lg border border-border bg-muted/25 px-3.5 py-3"><p className="text-[11px] text-muted-foreground">安全处理</p><p className={`mt-1 text-sm font-semibold ${remainingSeconds === null ? "text-amber-600" : remainingSeconds <= 5 ? "text-destructive" : "text-foreground"}`}>{remainingSeconds === null ? "等待明确操作" : `${remainingSeconds} s 后自动安全降流`}</p></div>
            </div>
            <div className="rounded-lg border border-border/70 bg-muted/20 px-3.5 py-3"><p className="text-[11px] text-muted-foreground">原因</p><p className="mt-1 whitespace-pre-line text-sm leading-6">{auto.pauseReason || auto.detail}</p></div>
            {actions.has("adjust_spectrum") && (
              <div className="rounded-lg border border-border px-3.5 py-3">
                <Label className="text-xs" htmlFor="pause-integration-time">积分时间 (μs)</Label>
                <div className="mt-2 flex gap-2">
                  <Input id="pause-integration-time" min={1} onChange={(event) => setDraftIntegrationTimeUs(Number(event.target.value))} type="number" value={draftIntegrationTimeUs} />
                  <Button disabled={pending || !Number.isFinite(draftIntegrationTimeUs) || draftIntegrationTimeUs <= 0} onClick={() => onAction("adjust_spectrum", { integrationTimeUs: draftIntegrationTimeUs })} variant="outline">应用并重测</Button>
                </div>
              </div>
            )}
          </div>
          <div className="flex flex-wrap justify-end gap-2 border-t border-border bg-background/35 px-5 py-3">
            {actions.has("resume") && <Button disabled={pending} onClick={() => onAction("resume")} variant="outline">继续测试</Button>}
            {actions.has("recover_power_meter") && <Button disabled={pending} onClick={() => onAction("recover_power_meter")} variant="outline">重新识别功率计并重测</Button>}
            {actions.has("recover_spectrometer") && <Button disabled={pending} onClick={() => onAction("recover_spectrometer")} variant="outline">重新识别光谱仪并重测</Button>}
            {actions.has("retry_persist") && <Button disabled={pending} onClick={() => onAction("retry_persist")} variant="outline">重试本地保存</Button>}
            {actions.has("retry_point") && <Button disabled={pending} onClick={() => onAction("retry_point")} variant="outline"><RotateCcw className="size-4" />重测当前点</Button>}
            {actions.has("safe_end") && <Button disabled={pending} onClick={() => onAction("safe_end")} variant="destructive">安全结束</Button>}
          </div>
        </div>
      </div>
    </DialogPortal>
  )
}

function AutomaticCompletionDialog({
  title,
  message,
  onClose,
  outcome,
  shutdown,
  totalPoints,
  completedPoints,
  sn,
  exportStatus,
  exportPath,
  exportError,
  journalPath,
  canNextDevice,
  canRetryExport,
  onNextDevice,
  onRetryExport,
  onChooseExportDirectory,
}: {
  title: string
  message: string
  onClose: () => void
  outcome?: AutomaticTerminalOutcome | null
  shutdown?: SafetyShutdownResult
  totalPoints?: number
  completedPoints?: number
  sn: string
  exportStatus?: string
  exportPath?: string
  exportError?: string
  journalPath?: string
  canNextDevice: boolean
  canRetryExport: boolean
  onNextDevice: () => void
  onRetryExport: () => void
  onChooseExportDirectory: () => void
}) {
  const acknowledgeButtonRef = useRef<HTMLButtonElement>(null)
  const onCloseRef = useRef(onClose)
  const [openingResult, setOpeningResult] = useState(false)
  const [resultOpenError, setResultOpenError] = useState("")
  onCloseRef.current = onClose

  useEffect(() => {
    const body = document.body
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = body.style.overflow
    const previousPaddingRight = body.style.paddingRight
    const focusFrame = window.requestAnimationFrame(() => acknowledgeButtonRef.current?.focus())
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    const hasStableScrollbarGutter = getComputedStyle(document.documentElement).scrollbarGutter.includes("stable")
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return
      event.preventDefault()
      onCloseRef.current()
    }
    body.style.overflow = "hidden"
    if (scrollbarWidth > 0 && !hasStableScrollbarGutter) body.style.paddingRight = `${scrollbarWidth}px`
    document.addEventListener("keydown", handleKeyDown)
    return () => {
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPaddingRight
      document.removeEventListener("keydown", handleKeyDown)
      window.cancelAnimationFrame(focusFrame)
      previousFocus?.focus()
    }
  }, [])

  const shutdownState = shutdownPresentation(outcome, shutdown)
  const shutdownConfirmed = shutdownState.confirmed
  const isSuccess = outcome === "succeeded" && shutdownConfirmed
  const isStopped = outcome === "stopped_shutdown_confirmed" && shutdownConfirmed

  const iconContainerClass = isSuccess
    ? "bg-emerald-500/15 text-emerald-500 ring-1 ring-emerald-500/25"
    : isStopped
    ? "bg-amber-500/15 text-amber-500 ring-1 ring-amber-500/25"
    : "bg-destructive/15 text-destructive ring-1 ring-destructive/25"

  const subtitle = isSuccess
    ? `测试流程已完成，电源状态已确认`
    : isStopped
    ? `测试由操作员终止，电源状态已确认`
    : shutdownConfirmed
    ? `测试异常中断，电源状态已确认`
    : `测试已停止，但下电状态未确认`

  const shutdownStatus = shutdownState.label
  const exportLabel = exportStatus === "completed"
    ? "Excel 已生成"
    : exportStatus === "failed"
    ? "Excel 生成失败"
    : exportStatus === "exporting"
    ? "Excel 后台生成中"
    : "尚未生成 Excel"

  const showDetailMessage = Boolean(message && message !== title && message !== "测试完成" && message !== "测试已完成")

  const handleOpenResult = async () => {
    if (!exportPath || openingResult) return
    setOpeningResult(true)
    setResultOpenError("")
    try {
      const outcome = await openResultFile(exportPath, openPath, revealItemInDir)
      if (outcome.status === "revealed") {
        setResultOpenError(`无法直接打开 Excel，已在文件夹中定位。${outcome.openError}`)
      }
    } catch (reason) {
      setResultOpenError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setOpeningResult(false)
    }
  }

  return (
    <DialogPortal>
      <div className="motion-overlay-enter fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4 backdrop-blur-xs">
        <div
          aria-labelledby="automatic-completion-title"
          aria-modal="true"
          className="status-dialog motion-dialog-enter w-[min(560px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-border/80 bg-popover shadow-2xl shadow-black/50"
          role={shutdownConfirmed ? "dialog" : "alertdialog"}
        >
          <div className="relative flex items-start gap-3.5 border-b border-border/70 px-6 py-5">
            <div className={`grid size-10 shrink-0 place-items-center rounded-xl transition-all ${iconContainerClass}`}>
              {isSuccess ? <Check className="size-5 stroke-[2.5]" /> : isStopped ? <Hand className="size-5" /> : <OctagonAlert className="size-5" />}
            </div>
            <div className="min-w-0 flex-1 pr-6">
              <h2 className="text-base font-semibold tracking-tight text-foreground" id="automatic-completion-title">
                {title}
              </h2>
              <p className="mt-0.5 text-xs text-muted-foreground leading-normal">
                {subtitle}
              </p>
            </div>
            <button
              aria-label="关闭"
              className="absolute right-4 top-4 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              onClick={onClose}
              type="button"
            >
              <X className="size-4" />
            </button>
          </div>

          <div className="space-y-3.5 px-6 py-5">
            <div className="rounded-lg border border-border/60 bg-muted/20 px-3.5 py-2.5">
              <span className="text-[11px] font-medium text-muted-foreground">产品 SN</span>
              <p className="mt-1 font-mono text-sm font-semibold text-foreground">{sn || "--"}</p>
            </div>
            <div className="grid grid-cols-2 gap-2.5">
              <div className="rounded-lg border border-border/60 bg-muted/30 px-3.5 py-2.5">
                <span className="text-[11px] font-medium text-muted-foreground">已保存测试点</span>
                <p className="mt-1 font-mono text-sm font-semibold text-foreground">
                  {typeof completedPoints === "number"
                    ? typeof totalPoints === "number" && totalPoints > 0
                      ? `${completedPoints} / ${totalPoints} 点`
                      : `${completedPoints} 点`
                    : "保存点数未确认"}
                </p>
              </div>
              <div className="rounded-lg border border-border/60 bg-muted/30 px-3.5 py-2.5">
                <span className="text-[11px] font-medium text-muted-foreground">下电保护状态</span>
                <p className={`mt-1 flex items-center gap-1.5 text-xs font-semibold ${shutdownConfirmed ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}>
                  <span className={`size-1.5 rounded-full ${shutdownConfirmed ? "bg-emerald-500" : "bg-destructive"}`} />
                  {shutdownStatus}
                </p>
              </div>
            </div>

            {showDetailMessage && (
              <div className="rounded-lg border border-border/50 bg-muted/20 px-3.5 py-2.5">
                <span className="text-[11px] font-medium text-muted-foreground">状态详情</span>
                <p className="mt-0.5 whitespace-pre-line text-xs leading-relaxed text-foreground/90 font-mono">
                  {message}
                </p>
              </div>
            )}
            {!shutdownConfirmed && shutdown?.reason && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3.5 py-2.5">
                <span className="text-[11px] font-medium text-destructive">必须检查电源实体面板</span>
                <p className="mt-0.5 whitespace-pre-line text-xs leading-relaxed text-foreground">{shutdown.reason}</p>
              </div>
            )}
            <div className={`rounded-lg border px-3.5 py-2.5 ${exportStatus === "failed" ? "border-destructive/40 bg-destructive/10" : "border-border/60 bg-muted/20"}`}>
              <span className="text-[11px] font-medium text-muted-foreground">数据与结果文件</span>
              <p className={`mt-1 text-xs font-semibold ${exportStatus === "failed" ? "text-destructive" : "text-foreground"}`}>本地 JSONL 已保存 · {exportLabel}</p>
              {exportPath && <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground" title={exportPath}>{exportPath}</p>}
              {exportError && <p className="mt-1 text-xs leading-5 text-destructive">{exportError}</p>}
              {resultOpenError && <p aria-live="polite" className="mt-1 text-xs leading-5 text-destructive">{resultOpenError}</p>}
              {journalPath && exportStatus === "failed" && <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground" title={journalPath}>原始记录：{journalPath}</p>}
            </div>
          </div>

          <div className="flex flex-wrap justify-end gap-2 border-t border-border/70 bg-muted/20 px-6 py-3.5">
            {exportPath && exportStatus === "completed" && <Button disabled={openingResult} onClick={() => void handleOpenResult()} size="sm" variant="outline">{openingResult ? <LoaderCircle className="size-4 animate-spin" /> : <FileText className="size-4" />}{openingResult ? "正在打开…" : "打开结果"}</Button>}
            {canRetryExport && <Button onClick={onRetryExport} size="sm" variant="outline"><RefreshCw className="size-4" />重新生成 Excel</Button>}
            {canRetryExport && <Button onClick={onChooseExportDirectory} size="sm" variant="outline"><FolderOpen className="size-4" />更换目录</Button>}
            {canNextDevice && <Button onClick={onNextDevice} size="sm"><QrCode className="size-4" />测试下一台</Button>}
            <Button
              className="px-5 font-medium shadow-xs"
              onClick={onClose}
              ref={acknowledgeButtonRef}
              size="sm"
              variant={canNextDevice ? "outline" : "default"}
            >
              关闭
            </Button>
          </div>
        </div>
      </div>
    </DialogPortal>
  )
}

function ShutdownFailureDialog({
  message,
  shutdownConfirmed,
  pending,
  onCancel,
  onRetry,
  onForceExit,
}: {
  message: string
  shutdownConfirmed: boolean
  pending: boolean
  onCancel: () => void
  onRetry: () => void
  onForceExit: () => void
}) {
  const [physicalShutdownConfirmed, setPhysicalShutdownConfirmed] = useState(false)
  const retryButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const body = document.body
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = body.style.overflow
    const previousPaddingRight = body.style.paddingRight
    const focusFrame = window.requestAnimationFrame(() => retryButtonRef.current?.focus())
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    const hasStableScrollbarGutter = getComputedStyle(document.documentElement).scrollbarGutter.includes("stable")
    body.style.overflow = "hidden"
    if (scrollbarWidth > 0 && !hasStableScrollbarGutter) body.style.paddingRight = `${scrollbarWidth}px`
    return () => {
      body.style.overflow = previousOverflow
      body.style.paddingRight = previousPaddingRight
      window.cancelAnimationFrame(focusFrame)
      previousFocus?.focus()
    }
  }, [])

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || pending) return
      event.preventDefault()
      onCancel()
    }
    document.addEventListener("keydown", handleEscape)
    return () => document.removeEventListener("keydown", handleEscape)
  }, [onCancel, pending])

  return (
    <DialogPortal>
      <div className="motion-overlay-enter fixed inset-0 z-[90] flex items-center justify-center bg-black/75 p-4 backdrop-blur-[2px]">
        <div aria-describedby="shutdown-failure-description" aria-labelledby="shutdown-failure-title" aria-modal="true" className="status-dialog motion-dialog-enter w-[min(600px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-[color-mix(in_srgb,var(--app-danger)_55%,transparent)] bg-popover shadow-2xl shadow-black/50" role="alertdialog">
        <div className="flex items-start gap-4 border-b border-border px-5 py-5">
          <div className="grid size-11 shrink-0 place-items-center rounded-full bg-[color-mix(in_srgb,var(--app-danger)_16%,transparent)] text-[var(--app-danger)]"><OctagonAlert className="size-6" /></div>
          <div><h2 className="text-lg font-semibold" id="shutdown-failure-title">{shutdownConfirmed ? "设备已安全下电，但退出收尾未完成" : "无法确认安全下电"}</h2><p className="mt-1 text-sm text-muted-foreground">{shutdownConfirmed ? "可以重试完成导出与配置保存，或直接退出程序" : "电源通信异常，程序无法自动确认输出已经关闭"}</p></div>
        </div>
        <div className="space-y-4 px-5 py-5">
          <p className="whitespace-pre-line text-sm leading-6 text-foreground" id="shutdown-failure-description">{message}</p>
          {shutdownConfirmed ? (
            <div className="rounded-lg border border-emerald-500/35 bg-emerald-500/10 px-4 py-3 text-sm leading-6 text-foreground">后端已经确认安全下电。导出或配置保存失败不会撤销该安全结果。</div>
          ) : (
            <>
              <div className="rounded-lg border border-[color-mix(in_srgb,var(--app-warning)_35%,transparent)] bg-[var(--app-warning-soft)] px-4 py-3 text-sm leading-6 text-foreground">
                请检查电源实体面板。若能够恢复通信，请重试安全下电；若已人工确认输出关闭，可强制退出程序。
              </div>
              <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-border bg-background/35 px-4 py-3 text-sm leading-5">
                <input checked={physicalShutdownConfirmed} className="mt-0.5 size-4 accent-[var(--app-danger)]" disabled={pending} onChange={(event) => setPhysicalShutdownConfirmed(event.target.checked)} type="checkbox" />
                <span>我已检查电源实体面板，并确认输出已关闭或电流已归零。</span>
              </label>
            </>
          )}
        </div>
          <div className="flex flex-wrap items-center justify-end gap-6 border-t border-border bg-background/35 px-5 py-3">
            <Button disabled={pending} onClick={onCancel} variant="outline">取消退出</Button>
            <Button disabled={pending} onClick={onRetry} ref={retryButtonRef} variant="outline">{pending ? "正在重试…" : shutdownConfirmed ? "重试收尾并退出" : "重试安全下电"}</Button>
            <Button disabled={pending || (!shutdownConfirmed && !physicalShutdownConfirmed)} onClick={onForceExit} variant="destructive">{pending ? "正在退出…" : shutdownConfirmed ? "直接退出" : "确认并强制退出"}</Button>
          </div>
        </div>
      </div>
    </DialogPortal>
  )
}

function sameRows<T>(
  left: readonly T[] | undefined,
  right: readonly T[] | undefined,
  keys: readonly (keyof T)[],
): boolean {
  if (left === right) return true
  if (!left || !right || left.length !== right.length) return false
  return left.every((row, index) => {
    const other = right[index]
    return keys.every((key) => row[key] === other[key])
  })
}

function sameNumberArray(left: readonly number[] | undefined, right: readonly number[] | undefined): boolean {
  return left === right || Boolean(
    left
    && right
    && left.length === right.length
    && left.every((value, index) => value === right[index]),
  )
}

function sameStringArray(left: readonly string[] | undefined, right: readonly string[] | undefined): boolean {
  return left === right || Boolean(
    left
    && right
    && left.length === right.length
    && left.every((value, index) => value === right[index]),
  )
}

function sameCh341State(left: DeviceSnapshot["ch341"], right: DeviceSnapshot["ch341"]): boolean {
  if (left === right) return true
  if (!left || !right) return false
  const leftConnected = left.connectedPort
  const rightConnected = right.connectedPort
  const connectedPortEqual = leftConnected === rightConnected || Boolean(
    leftConnected
    && rightConnected
    && leftConnected.locationPath === rightConnected.locationPath
    && leftConnected.name === rightConnected.name
    && leftConnected.deviceIndex === rightConnected.deviceIndex,
  )
  return left.discoveryState === right.discoveryState
    && left.error === right.error
    && connectedPortEqual
    && sameRows(left.ports, right.ports, ["locationPath", "name", "online", "deviceIndex"])
}

function sameSpectrumWindow(
  left: DeviceSnapshot["spectrumWindow"],
  right: DeviceSnapshot["spectrumWindow"],
): boolean {
  return left === right || Boolean(
    left
    && right
    && left.centerNm === right.centerNm
    && left.halfRangeNm === right.halfRangeNm,
  )
}

// Page-level memo comparators. Only skip a page re-render when every slice
// the page renders is unchanged. Small transport arrays compare by value
// because JSON IPC recreates their object references on every snapshot.

function samePowerMeterResourceProfiles(
  left: DeviceSnapshot["resourceProfiles"],
  right: DeviceSnapshot["resourceProfiles"],
): boolean {
  if (left === right) return true
  if (!left || !right || left.length !== right.length) return false
  return left.every((profile, index) => {
    const other = right[index]
    return profile.resource === other?.resource
      && profile.driverKind === other.driverKind
      && profile.serialNumber === other.serialNumber
      && profile.softwareGain === other.softwareGain
      && JSON.stringify(profile.coefficientLibrary ?? null) === JSON.stringify(other.coefficientLibrary ?? null)
  })
}

function sameDeviceSnapshot(left: DeviceSnapshot | undefined, right: DeviceSnapshot | undefined): boolean {
  if (left === right) return true
  if (!left || !right) return false
  return left.state === right.state
    && left.label === right.label
    && left.detail === right.detail
    && left.connected === right.connected
    && left.running === right.running
    && left.detecting === right.detecting
    && left.ready === right.ready
    && sameStringArray(left.resources, right.resources)
    && samePowerMeterResourceProfiles(left.resourceProfiles, right.resourceProfiles)
    && JSON.stringify(left.coefficientLibrary ?? null) === JSON.stringify(right.coefficientLibrary ?? null)
    && sameCh341State(left.ch341, right.ch341)
    && left.outputEnabled === right.outputEnabled
    && left.activeCurrentA === right.activeCurrentA
    && left.inputVoltageV === right.inputVoltageV
    && left.outputVoltageV === right.outputVoltageV
    && left.outputCurrentA === right.outputCurrentA
    && left.temperatureC === right.temperatureC
    && left.powerW === right.powerW
    && left.stable === right.stable
    && left.stableElapsedS === right.stableElapsedS
    && left.sampledAt === right.sampledAt
    && left.ageMs === right.ageMs
    && left.quality === right.quality
    && left.peakWavelengthNm === right.peakWavelengthNm
    && left.centroidNm === right.centroidNm
    && left.fwhmNm === right.fwhmNm
    && left.smsrDb === right.smsrDb
    && left.pib === right.pib
    && left.pibPercent === right.pibPercent
    && left.saturated === right.saturated
    && sameSpectrumWindow(left.spectrumWindow, right.spectrumWindow)
}

function sameAutomaticReadiness(
  left: BackendSnapshot["automaticTest"]["readiness"],
  right: BackendSnapshot["automaticTest"]["readiness"],
): boolean {
  if (left === right) return true
  if (!left || !right || left.ready !== right.ready || left.blockers.length !== right.blockers.length) return false
  return left.blockers.every((blocker, index) => (
    blocker.code === right.blockers[index]?.code
    && blocker.message === right.blockers[index]?.message
  ))
}

function sameMeasurements(left: BackendSnapshot["measurements"], right: BackendSnapshot["measurements"]): boolean {
  if (left === right) return true
  if (!left || !right) return false
  return left.power === right.power
    && left.stable === right.stable
    && left.agingPower === right.agingPower
    && left.spectrum === right.spectrum
    && left.spectrumPeaks === right.spectrumPeaks
}

function sameAutomaticStability(
  left: BackendSnapshot["automaticTest"]["stability"],
  right: BackendSnapshot["automaticTest"]["stability"],
): boolean {
  if (left === right) return true
  if (!left || !right) return false
  return left.elapsedS === right.elapsedS
    && left.requiredS === right.requiredS
    && left.holdElapsedS === right.holdElapsedS
    && left.holdRequiredS === right.holdRequiredS
    && left.powerStable === right.powerStable
    && left.spanW === right.spanW
    && left.spanLimitW === right.spanLimitW
    && left.percent === right.percent
    && left.limitPercent === right.limitPercent
    && left.quality === right.quality
    && left.hasReading === right.hasReading
    && left.wavelengthRequired === right.wavelengthRequired
    && left.wavelengthStable === right.wavelengthStable
}

function sameAutomaticTestState(
  left: BackendSnapshot["automaticTest"] | undefined,
  right: BackendSnapshot["automaticTest"] | undefined,
): boolean {
  if (left === right) return true
  if (!left || !right) return false
  return left.state === right.state
    && left.detail === right.detail
    && left.elapsedS === right.elapsedS
    && sameAutomaticStability(left.stability, right.stability)
    && left.runContext?.sn === right.runContext?.sn
    && left.runContext?.station === right.runContext?.station
    && left.runContext?.testTemperatureC === right.runContext?.testTemperatureC
    && left.runContext?.selectedScriptId === right.runContext?.selectedScriptId
    && left.currentA === right.currentA
    && left.currentIndex === right.currentIndex
    && left.currentDwellS === right.currentDwellS
    && left.currentDwellElapsedS === right.currentDwellElapsedS
    && left.progress === right.progress
    && left.pauseReason === right.pauseReason
    && left.terminalOutcome === right.terminalOutcome
    && left.terminalReason === right.terminalReason
    && left.savedPointCount === right.savedPointCount
    && left.exportStatus === right.exportStatus
    && left.exportPath === right.exportPath
    && left.exportError === right.exportError
    && left.settingsError === right.settingsError
    && sameAutomaticReadiness(left.readiness, right.readiness)
    && left.canStart === right.canStart
    && left.exportedPointCount === right.exportedPointCount
    && left.journalPath === right.journalPath
    && left.canNextDevice === right.canNextDevice
    && left.canRetryExport === right.canRetryExport
    && left.pauseKind === right.pauseKind
    && left.pauseStartedAtUtc === right.pauseStartedAtUtc
    && left.pauseDeadlineAtUtc === right.pauseDeadlineAtUtc
    && sameStringArray(left.pauseActions, right.pauseActions)
    && left.canEnd === right.canEnd
    && sameNumberArray(left.currents, right.currents)
}

function samePdSection(
  left: BackendSnapshot["pd"] | undefined,
  right: BackendSnapshot["pd"] | undefined,
): boolean {
  if (left === right) return true
  if (!left || !right) return false
  return left.state === right.state
    && left.status === right.status
    && left.devices === right.devices
    && left.channels === right.channels
    && left.ranges === right.ranges
    && left.settings === right.settings
    && left.currentValue === right.currentValue
    && left.voltage === right.voltage
    && left.mean === right.mean
    && left.standardDeviation === right.standardDeviation
    && left.rangeText === right.rangeText
    && left.sampleCount === right.sampleCount
    && left.points === right.points
}

function ChartLegend({ lines, stacked = false }: { lines: CanvasChartLine[]; stacked?: boolean }) {
  return (
    <div aria-label="图例" className={`flex min-w-0 gap-x-4 gap-y-1 text-sm text-muted-foreground ${stacked ? "flex-col items-start" : "flex-wrap items-center"}`}>
      {lines.filter((line) => !line.hideLegend).map((line) => (
        <span className="flex items-center gap-1.5 whitespace-nowrap" key={line.key}>
          <svg aria-hidden="true" className="h-3.5 w-7 overflow-visible" viewBox="0 0 28 14">
            <line stroke={line.color} strokeDasharray={line.dash?.join(" ")} strokeLinecap="round" strokeWidth="2" x1="1" x2="27" y1="7" y2="7" />
            {line.showPoints && (line.pointShape === "triangle" ? (
              <path
                d="M14 2.5 18.2 10.5 9.8 10.5Z"
                fill={line.pointStyle === "hollow" ? "var(--card)" : line.color}
                stroke={line.color}
                strokeLinejoin="round"
                strokeWidth="2"
              />
            ) : (
              <circle
                cx="14"
                cy="7"
                fill={line.pointStyle === "hollow" ? "var(--card)" : line.color}
                r="4"
                stroke={line.color}
                strokeWidth="2"
              />
            ))}
          </svg>
          <span>{line.label}</span>
        </span>
      ))}
    </div>
  )
}

const ChartPanel = memo(function ChartPanel({ title, subtitle, data = [], columns, xKey, lines, empty = "暂无实时数据", heightClassName, annotations, annotationLegend, stretch = false, compactHeader = false, hideHeader = false, insetLegend = false, xDomain, xTicks, yDomain, yMinimumLimit, rightYDomain, preserveAllPoints = false, xLabel, xTickSuffix, yLabel }: {
  title: string
  subtitle?: string
  data?: Array<Record<string, number | null>>
  columns?: CanvasColumnarData
  xKey: string
  lines: CanvasChartLine[]
  empty?: string
  heightClassName?: string
  annotations?: CanvasChartAnnotation[]
  annotationLegend?: CanvasChartAnnotationLegendItem[]
  stretch?: boolean
  compactHeader?: boolean
  hideHeader?: boolean
  insetLegend?: boolean
  xDomain?: [number, number]
  xTicks?: number[]
  yDomain?: [number, number]
  yMinimumLimit?: number
  rightYDomain?: [number, number]
  preserveAllPoints?: boolean
  xLabel?: string
  xTickSuffix?: string
  yLabel?: string
}) {
  const dataLength = columns
    ? Math.min(...Object.values(columns).map((values) => values.length))
    : data.length
  return (
    <Card aria-label={title} size="sm" className={`${compactHeader ? "gap-0" : ""} shadow-none ${stretch ? "h-full min-h-0 flex flex-col" : ""}`}>
      {!hideHeader && (
        <CardHeader className="flex min-h-8 shrink-0 flex-row items-center px-3 py-1.5">
          <strong className="chart-panel-heading">{title}</strong>
          {subtitle ? <small className="chart-panel-subtitle">{subtitle}</small> : <ChartLegend lines={lines} />}
        </CardHeader>
      )}
      <CardContent className={`relative ${stretch ? "min-h-0 flex-1" : heightClassName || (dataLength === 0 ? "h-40" : "h-56")} ${hideHeader ? "px-1.5 py-1.5" : "px-3 pb-1.5 pt-0"}`}>
        {dataLength === 0 ? (
          <div className="grid h-full min-h-[120px] place-items-center rounded-lg bg-background/30 text-xs text-muted-foreground/70">{empty}</div>
        ) : (
          <CanvasLineChart annotationLegend={annotationLegend} annotations={annotations} ariaLabel={`${title}曲线`} columns={columns} data={data} lines={lines} preserveAllPoints={preserveAllPoints} rightYDomain={rightYDomain} tightInsets={hideHeader} xDomain={xDomain} xKey={xKey} xLabel={xLabel} xTickSuffix={xTickSuffix} xTicks={xTicks} yDomain={yDomain} yLabel={yLabel} yMinimumLimit={yMinimumLimit} />
        )}
        {insetLegend && dataLength > 0 && (
          <div className={`chart-inset-legend pointer-events-none absolute isolate top-5 z-[1] px-3 py-2 ${hideHeader ? "left-[70px]" : "left-[88px]"}`}>
            <ChartLegend lines={lines} stacked />
          </div>
        )}
      </CardContent>
    </Card>
  )
}, (previous, next) => {
  const sameLines = previous.lines.length === next.lines.length
    && previous.lines.every((line, index) => {
      const other = next.lines[index]
      return line.key === other.key
        && line.label === other.label
        && line.color === other.color
        && line.axisColor === other.axisColor
        && line.yAxisId === other.yAxisId
        && line.showPoints === other.showPoints
        && line.showLatestPoint === other.showLatestPoint
        && line.latestPointStyle === other.latestPointStyle
        && line.pointShape === other.pointShape
        && line.pointStyle === other.pointStyle
        && line.pointSize === other.pointSize
        && line.lineWidth === other.lineWidth
        && line.interpolation === other.interpolation
        && line.hideLegend === other.hideLegend
        && line.trail === other.trail
        && sameNumberArray(line.dash, other.dash)
    })
  const sameNumbers = (left?: number[], right?: number[]) => (
    left === right || (
      left !== undefined
      && right !== undefined
      && left.length === right.length
      && left.every((value, index) => value === right[index])
    )
  )
  const dataKeys = [previous.xKey, ...previous.lines.map((line) => line.key)]
  const sameColumns = previous.columns === next.columns || Boolean(
    previous.columns
    && next.columns
    && Object.keys(previous.columns).length === Object.keys(next.columns).length
    && Object.keys(previous.columns).every((key) => sameNumberArray(previous.columns?.[key], next.columns?.[key])),
  )
  return sameRows(previous.data, next.data, dataKeys)
    && sameColumns
    && previous.title === next.title
    && previous.subtitle === next.subtitle
    && previous.xKey === next.xKey
    && previous.empty === next.empty
    && previous.heightClassName === next.heightClassName
    && previous.stretch === next.stretch
    && previous.compactHeader === next.compactHeader
    && previous.hideHeader === next.hideHeader
    && previous.insetLegend === next.insetLegend
    && previous.preserveAllPoints === next.preserveAllPoints
    && previous.xLabel === next.xLabel
    && previous.xTickSuffix === next.xTickSuffix
    && previous.yLabel === next.yLabel
    && sameLines
    && sameNumbers(previous.xDomain, next.xDomain)
    && sameNumbers(previous.xTicks, next.xTicks)
    && sameNumbers(previous.yDomain, next.yDomain)
    && previous.yMinimumLimit === next.yMinimumLimit
    && sameNumbers(previous.rightYDomain, next.rightYDomain)
    && sameRows(previous.annotations, next.annotations, ["label", "x", "y", "color"])
    && sameRows(previous.annotationLegend, next.annotationLegend, ["label", "color", "value"])
})

const PowerRealtimeChart = memo(function PowerRealtimeChart({ snapshot, smoothing = true, curveLineStyle = "solid", latestPointMarkerStyle = "point", stretch = false, heightClassName, automatic = false }: { snapshot: BackendSnapshot | null; automatic?: boolean; smoothing?: boolean; curveLineStyle?: CurveLineStyle; latestPointMarkerStyle?: LatestPointMarkerStyle; stretch?: boolean; heightClassName?: string }) {
  const { appearance } = useAppearance()
  const powerData = (snapshot?.measurements?.power ?? []) as Array<Record<string, number | null>>
  const latestElapsedS = powerData[powerData.length - 1]?.elapsedS
  const powerTimeDomain: [number, number] = typeof latestElapsedS === "number"
    ? [Math.max(0, latestElapsedS - POWER_PLOT_HISTORY_S), Math.max(10, latestElapsedS)]
    : [0, 10]
  const firstPowerTickS = Math.ceil(powerTimeDomain[0] / POWER_TIME_TICK_INTERVAL_S) * POWER_TIME_TICK_INTERVAL_S
  const powerTimeTicks = Array.from(
    { length: Math.floor((powerTimeDomain[1] - firstPowerTickS) / POWER_TIME_TICK_INTERVAL_S) + 1 },
    (_, index) => firstPowerTickS + index * POWER_TIME_TICK_INTERVAL_S,
  )
  const powerStabilityColor = PLM_CHART_SERIES.accent
  const powerYDomain = powerYAxisDomain(powerData.flatMap((row) => (
    typeof row.elapsedS === "number" && row.elapsedS >= powerTimeDomain[0]
      ? [row.powerW]
      : []
  )))
  const lines: CanvasChartLine[] = [{ key: "powerW", label: "实时功率", color: powerStabilityColor, interpolation: smoothing ? "monotone" : "linear", dash: curveLineDash(curveLineStyle), showLatestPoint: true, latestPointStyle: latestPointMarkerStyle, trail: automatic }]

  return (
    <ChartPanel
      title="功率实时"
      subtitle={automatic ? `最近 60 s${appearance.trailEnabled ? ` · 尾迹 ${appearance.trailSeconds} s` : ""}` : undefined}
      compactHeader
      data={powerData}
      hideHeader
      xKey="elapsedS"
      lines={lines}
      stretch={stretch}
      heightClassName={heightClassName}
      xDomain={powerTimeDomain}
      xTickSuffix=" s"
      xTicks={powerTimeTicks}
      yDomain={powerYDomain}
    />
  )
}, (previous, next) => {
  const previousPower = previous.snapshot?.measurements?.power
  const nextPower = next.snapshot?.measurements?.power
  return previous.stretch === next.stretch
    && previous.automatic === next.automatic
    && previous.smoothing === next.smoothing
    && previous.curveLineStyle === next.curveLineStyle
    && previous.heightClassName === next.heightClassName
    && previous.latestPointMarkerStyle === next.latestPointMarkerStyle
    && previous.snapshot?.automaticTest.currentA === next.snapshot?.automaticTest.currentA
    && previous.snapshot?.devices.powerMeter.powerW === next.snapshot?.devices.powerMeter.powerW
    && sameRows(previousPower, nextPower, ["elapsedS", "powerW"])
})

const agingPowerLines: CanvasChartLine[] = [
  { key: "powerW", label: "功率 (W)", color: PLM_CHART_SERIES.accent, axisColor: PLM_CHART_SERIES.accent, showPoints: true, pointShape: "circle", pointStyle: "hollow", pointSize: 7, lineWidth: 2.4 },
]

const agingPowerTimeDomain: [number, number] = [0, 120]
const agingPowerTimeTicks = [0, 20, 40, 60, 80, 100, 120]

const AgingPowerChart = memo(function AgingPowerChart({ snapshot, smoothing = true, stretch = false, heightClassName }: { snapshot: BackendSnapshot | null; smoothing?: boolean; stretch?: boolean; heightClassName?: string }) {
  const chartData = snapshot?.measurements?.agingPower ?? []
  const powerDomain = useMemo(
    () => powerYAxisDomain(chartData.map((row) => row.powerW)),
    [chartData],
  )

  return (
    <ChartPanel
      compactHeader
      data={chartData}
      empty="AU 老化测试每 5 分钟记录一次功率"
      hideHeader
      lines={agingPowerLines.map((line) => ({ ...line, interpolation: smoothing ? "monotone" : "linear" }))}
      stretch={stretch}
      heightClassName={heightClassName}
      title="AU 老化功率"
      xDomain={agingPowerTimeDomain}
      xKey="elapsedMinutes"
      xTickSuffix=" min"
      xTicks={agingPowerTimeTicks}
      yDomain={powerDomain}
    />
  )
}, (previous, next) => previous.stretch === next.stretch
  && previous.smoothing === next.smoothing
  && previous.heightClassName === next.heightClassName
  && sameRows(previous.snapshot?.measurements?.agingPower, next.snapshot?.measurements?.agingPower, ["elapsedMinutes", "powerW"]))

const powerEfficiencyLines: CanvasChartLine[] = [
  { key: "powerW", label: "功率 (W)", color: PLM_CHART_SERIES.accent, axisColor: PLM_CHART_SERIES.accent, showPoints: true, pointShape: "circle", pointStyle: "hollow", pointSize: 8, lineWidth: 2.4, connectNulls: true },
  { key: "efficiencyPercent", label: "效率 (%)", color: PLM_CHART_SERIES.efficiency, axisColor: PLM_CHART_SERIES.efficiency, yAxisId: "right", showPoints: true, pointShape: "circle", pointStyle: "hollow", pointSize: 8, lineWidth: 2.4, connectNulls: true },
]

const PowerEfficiencyChart = memo(function PowerEfficiencyChart({ snapshot, smoothing = true, stretch = false, heightClassName }: { snapshot: BackendSnapshot | null; smoothing?: boolean; stretch?: boolean; heightClassName?: string }) {
  const stableData = (snapshot?.measurements?.stable ?? []) as Array<Record<string, number | null>>
  const referencePoints = snapshot?.automaticTest.reference?.points ?? []
  const chartData = useMemo<Array<Record<string, number | null>>>(() => {
    const rows = new Map<number, Record<string, number | null>>()
    stableData.forEach((row) => {
      if (typeof row.currentA === "number") rows.set(row.currentA, { ...row })
    })
    referencePoints.forEach((point) => {
      const row = rows.get(point.currentA) ?? { currentA: point.currentA, powerW: null, efficiencyPercent: null }
      rows.set(point.currentA, { ...row, referencePowerW: point.powerW })
    })
    return [...rows.values()].sort((left, right) => (left.currentA ?? 0) - (right.currentA ?? 0))
  }, [referencePoints, stableData])
  const lines: CanvasChartLine[] = [
    ...powerEfficiencyLines.map((line): CanvasChartLine => ({
      ...line,
      interpolation: line.key === "powerW" && smoothing ? "monotone" : "linear",
    })),
    ...(referencePoints.length ? [{ key: "referencePowerW", label: "历史参考", color: HISTORY_CHART_SERIES.power, interpolation: smoothing ? "monotone" as const : "linear" as const, lineWidth: 1.2, dash: [4, 4] }] : []),
  ]
  const currentDomain = useMemo<[number, number] | undefined>(() => {
    const currents = chartData
      .map((row) => row.currentA)
      .filter((value): value is number => typeof value === "number" && Number.isFinite(value))
    if (!currents.length) return undefined
    const minimumCurrent = Math.min(...currents)
    const maximumCurrent = Math.max(...currents)
    const padding = minimumCurrent === maximumCurrent
      ? Math.max(Math.abs(minimumCurrent) * 0.1, 1)
      : (maximumCurrent - minimumCurrent) * 0.12
    return [minimumCurrent - padding, maximumCurrent + padding]
  }, [chartData])
  const powerDomain = useMemo(
    () => powerYAxisDomain(chartData.flatMap((row) => [row.powerW, row.referencePowerW])),
    [chartData],
  )
  const currentTicks = useMemo(() => {
    const currents = chartData
      .map((row) => row.currentA)
      .filter((value): value is number => typeof value === "number" && Number.isFinite(value))
    if (currents.length <= 7) return currents
    return Array.from(
      new Set(Array.from({ length: 7 }, (_, index) => currents[Math.round(index * (currents.length - 1) / 6)])),
    )
  }, [chartData])

  return (
    <ChartPanel
      compactHeader
      data={chartData}
      empty="完成稳定测试点后显示功率 / 效率"
      hideHeader
      insetLegend
      lines={lines}
      stretch={stretch}
      heightClassName={heightClassName}
      title="功率 / 效率"
      xDomain={currentDomain}
      xKey="currentA"
      xTickSuffix=" A"
      xTicks={currentTicks}
      yDomain={powerDomain}
    />
  )
}, (previous, next) => previous.stretch === next.stretch
  && previous.smoothing === next.smoothing
  && previous.heightClassName === next.heightClassName
  && sameRows(previous.snapshot?.measurements?.stable, next.snapshot?.measurements?.stable, ["currentA", "powerW", "efficiencyPercent"])
  && previous.snapshot?.automaticTest.reference?.sourcePath === next.snapshot?.automaticTest.reference?.sourcePath
  && sameRows(previous.snapshot?.automaticTest.reference?.points, next.snapshot?.automaticTest.reference?.points, ["currentA", "powerW"]))

const SpectrumRealtimeChart = memo(function SpectrumRealtimeChart({ snapshot, smoothing = true, stretch = false, heightClassName }: { snapshot: BackendSnapshot | null; smoothing?: boolean; stretch?: boolean; heightClassName?: string }) {
  const spectrumSource = snapshot?.measurements?.spectrum
  const spectrumData = spectrumSource
    ? { wavelengthNm: spectrumSource.wavelengthNm, intensity: spectrumSource.intensity }
    : { wavelengthNm: [], intensity: [] }
  const spectrum = snapshot?.devices.spectrometer
  const spectrumPeaks = snapshot?.measurements?.spectrumPeaks ?? []
  const peakAnnotations = spectrumPeakAnnotations(spectrumPeaks)
  const spectrumYAxisStateRef = useRef<SpectrumYAxisState | null>(null)
  const spectrumXDomain = useMemo<[number, number] | undefined>(() => {
    const spectrumWindow = spectrum?.spectrumWindow
    if (
      spectrumWindow
      && Number.isFinite(spectrumWindow.centerNm)
      && Number.isFinite(spectrumWindow.halfRangeNm)
      && spectrumWindow.halfRangeNm > 0
    ) {
      return [
        spectrumWindow.centerNm - spectrumWindow.halfRangeNm,
        spectrumWindow.centerNm + spectrumWindow.halfRangeNm,
      ]
    }

    let minimum = Number.POSITIVE_INFINITY
    let maximum = Number.NEGATIVE_INFINITY
    for (const wavelength of spectrumData.wavelengthNm) {
      if (typeof wavelength !== "number" || !Number.isFinite(wavelength)) continue
      minimum = Math.min(minimum, wavelength)
      maximum = Math.max(maximum, wavelength)
    }
    return minimum < maximum ? [minimum, maximum] : undefined
  }, [spectrum?.spectrumWindow, spectrumData])
  const spectrumYDomain = useMemo<[number, number]>(() => {
    if (spectrumData.intensity.length === 0) {
      spectrumYAxisStateRef.current = null
      return [0, 1]
    }
    const intensities: number[] = []
    for (const intensity of spectrumData.intensity) {
      if (typeof intensity === "number" && Number.isFinite(intensity)) {
        intensities.push(intensity)
      }
    }
    spectrumYAxisStateRef.current = stabilizeSpectrumYAxis(
      spectrumYAxisDomain(intensities),
      spectrumYAxisStateRef.current,
    )
    return spectrumYAxisStateRef.current.domain
  }, [spectrumData])

  return (
    <ChartPanel
      title="光谱"
      compactHeader
      hideHeader
      columns={spectrumData}
      xKey="wavelengthNm"
      xDomain={spectrumXDomain}
      xTickSuffix=" nm"
      yDomain={spectrumYDomain}
      yMinimumLimit={SPECTRUM_Y_AXIS_MINIMUM_LIMIT}
      preserveAllPoints
      lines={[{ key: "intensity", label: "光谱（实时）", color: PLM_CHART_SERIES.accent, interpolation: smoothing ? "monotone" : "linear" }]}
      stretch={stretch}
      heightClassName={heightClassName}
      annotations={peakAnnotations}
      annotationLegend={spectrumPeakLegend(spectrumPeaks)}
    />
  )
}, (previous, next) => {
  const previousMeasurements = previous.snapshot?.measurements
  const nextMeasurements = next.snapshot?.measurements
  const previousSpectrum = previous.snapshot?.devices.spectrometer
  const nextSpectrum = next.snapshot?.devices.spectrometer
  return previous.stretch === next.stretch
    && previous.smoothing === next.smoothing
    && previous.heightClassName === next.heightClassName
    && sameNumberArray(previousMeasurements?.spectrum.wavelengthNm, nextMeasurements?.spectrum.wavelengthNm)
    && sameNumberArray(previousMeasurements?.spectrum.intensity, nextMeasurements?.spectrum.intensity)
    && sameRows(previousMeasurements?.spectrumPeaks, nextMeasurements?.spectrumPeaks, ["label", "peakWavelengthNm", "peakIntensity"])
    && previousSpectrum?.spectrumWindow?.centerNm === nextSpectrum?.spectrumWindow?.centerNm
    && previousSpectrum?.spectrumWindow?.halfRangeNm === nextSpectrum?.spectrumWindow?.halfRangeNm
})

function AutomaticMetricCard({ title, value, unit, valueTone = "muted", recording = false, onClick, className = "", headerLeading, footer }: {
  title: ReactNode
  value: string
  unit: ReactNode
  valueTone?: "success" | "danger" | "warning" | "muted"
  recording?: boolean
  onClick?: () => void
  className?: string
  headerLeading?: ReactNode
  footer?: ReactNode
}) {
  const clickable = Boolean(onClick)
  const isStringTitle = typeof title === "string"
  return (
    <div
      aria-label={clickable ? `${isStringTitle ? title : "实时功率"}，点击设置规格` : undefined}
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
      <div aria-label={recording ? `${isStringTitle ? title : "实时功率"}，实时记录中` : undefined} className={`automatic-metric-card__title${headerLeading ? " automatic-metric-card__title--with-status" : ""}`}>
        {headerLeading}
        {isStringTitle && recording ? <LivePowerRecordingDot active /> : null}
        {isStringTitle ? <span>{title}</span> : title}
      </div>
      <div className="automatic-metric-card__value">
        <span className={`automatic-metric-card__number is-${valueTone}`}><NumericReadout value={value} /></span>
        <span className="automatic-metric-card__unit">{unit}</span>
      </div>
      {footer}
    </div>
  )
}

function RealtimeMetricCards({
  automatic,
  compact = false,
  config,
  currentA,
  onOpenSpecifications,
  powerMeter,
  spectrometer,
}: {
  automatic?: BackendSnapshot["automaticTest"]
  compact?: boolean
  config: AppConfiguration
  currentA?: number | null
  onOpenSpecifications?: () => void
  powerMeter?: DeviceSnapshot
  spectrometer?: DeviceSnapshot
}) {
  const livePowerW = powerMeter?.quality === "live" ? powerMeter.powerW : null
  const liveSpectrometer = spectrometer?.quality === "live" ? spectrometer : undefined
  const pibValue = liveSpectrometer?.pib ?? liveSpectrometer?.pibPercent
  const pibPercent = typeof pibValue === "number" && Number.isFinite(pibValue) ? (pibValue <= 1 ? pibValue * 100 : pibValue) : null
  const centerWavelengthValue = liveSpectrometer?.peakWavelengthNm
  const hasSpectrumMetrics = typeof centerWavelengthValue === "number"
  const centerWavelengthPass = hasSpectrumMetrics && Math.abs((centerWavelengthValue ?? 0) - config.centerWavelengthSpecNm) <= config.centerWavelengthToleranceNm
  const fwhmPass = typeof liveSpectrometer?.fwhmNm === "number" && liveSpectrometer.fwhmNm <= config.fwhmMaximumNm
  const pibPass = pibPercent !== null && pibPercent >= config.pibMinimumPercent

  return (
    <section className={`automatic-metric-grid${compact ? " automatic-metric-grid--compact" : ""}`} aria-label="实时关键指标">
      <AutomaticMetricCard
        className="automatic-metric-card--power automatic-metric-card--title-below"
        title={<PowerStabilityStatus automatic={automatic} meter={powerMeter} />}
        value={formatFixedNumber(livePowerW, 3)}
        unit={<>W @ <span className="automatic-metric-card__current">{formatTrimmedNumber(currentA, 2)} A</span></>}
        valueTone="warning"
        recording={Boolean(powerMeter?.running || (automatic && !["idle", "paused", "completed"].includes(automatic.state)))}
      />
      <AutomaticMetricCard
        className="automatic-metric-card--title-below"
        title="中心波长"
        value={formatFixedNumber(centerWavelengthValue, 3)}
        unit="nm"
        valueTone={hasSpectrumMetrics ? (centerWavelengthPass ? "success" : "danger") : "muted"}
        onClick={onOpenSpecifications}
      />
      <AutomaticMetricCard
        className="automatic-metric-card--title-below"
        title="FWHM"
        value={formatFixedNumber(liveSpectrometer?.fwhmNm, 3)}
        unit="nm"
        valueTone={typeof liveSpectrometer?.fwhmNm === "number" ? (fwhmPass ? "success" : "danger") : "muted"}
        onClick={onOpenSpecifications}
      />
      <AutomaticMetricCard
        className="automatic-metric-card--title-below"
        title="PIB"
        value={formatFixedNumber(pibPercent, 2)}
        unit="%"
        valueTone={pibPercent !== null ? (pibPass ? "success" : "danger") : "muted"}
        onClick={onOpenSpecifications}
      />
    </section>
  )
}

function AppTitlebar({
  snapshot,
  showPhase,
  onOpenAppearance,
  active,
  canPause,
  commandPending,
  controlsLocked,
  dirty,
  pendingCommand,
  run,
  saveConfiguration,
}: {
  snapshot: BackendSnapshot | null
  showPhase: boolean
  onOpenAppearance: () => void
  active: boolean
  canPause: boolean
  commandPending: boolean
  controlsLocked: boolean
  dirty: boolean
  pendingCommand: string | null
  run: RunCommand
  saveConfiguration: () => Promise<void>
}) {
  return (
    <header className="app-titlebar" data-tauri-drag-region>
      <div className="app-titlebar__brand" data-tauri-drag-region>
        <span data-tauri-drag-region>Power Test</span>
        {commandPending && (
          <span className="app-titlebar__pending">
            <LoaderCircle className="size-3.5 animate-spin text-primary" />
            {pendingCommandLabel(pendingCommand)}
          </span>
        )}
      </div>
      {showPhase && <AutomaticPhaseTrack snapshot={snapshot} />}
      <div className="app-titlebar__actions">
        <DisplayMenu onOpenSettings={onOpenAppearance} />
        {canPause && (
          <Button disabled={!active || commandPending} onClick={() => void run("automatic.pause")} size="sm" variant="outline">
            <Pause className="size-4" />暂停测试
          </Button>
        )}
        <Button
          disabled={!dirty || !active || commandPending || controlsLocked}
          onClick={() => void saveConfiguration()}
          size="sm"
          variant="outline"
        >
          <Save className="size-4" />保存设置
        </Button>
        <Button
          className="app-emergency-stop"
          disabled={!active}
          onClick={() => void run("app.stopAll")}
          size="sm"
          variant="outline"
        >
          <OctagonAlert className="size-4" />急停
        </Button>
      </div>
    </header>
  )
}

function LicensedApp({
  licenseStatus,
  onImportLicense,
}: {
  licenseStatus: LicenseStatus
  onImportLicense: () => Promise<void>
}) {
  const [page, setPage] = useState<Page>(() => (typeof window !== "undefined" && (new URLSearchParams(window.location.search).get("page") as Page)) || "automatic")
  const [theme, setTheme] = useState<Theme>(getStoredTheme)
  const [accentTheme, setAccentTheme] = useState<AccentTheme>(getStoredAccentTheme)
  const [curveLineStyle, setCurveLineStyle] = useState<CurveLineStyle>(getStoredCurveLineStyle)
  const [latestPointMarkerStyle, setLatestPointMarkerStyle] = useState<LatestPointMarkerStyle>(getStoredLatestPointMarkerStyle)
  const currentView: SnapshotView = page === "settings" ? "automatic" : page
  const { snapshot, error, commandPending, pendingCommand, command } = useBackendSnapshot(currentView)
  const [config, setConfig] = useState<AppConfiguration>(emptyConfig)
  const [dirty, setDirty] = useState(false)
  const configRef = useRef<AppConfiguration>(emptyConfig)
  const configHydratedRef = useRef(false)
  const configRevisionRef = useRef(0)
  const automaticSaveFlightRef = useRef<Promise<void>>(Promise.resolve())
  const pendingPersistedConfigRef = useRef<AppConfiguration | null>(null)
  const [closeFailure, setCloseFailure] = useState<{ message: string; shutdownConfirmed: boolean } | null>(null)
  const [automaticCompletion, setAutomaticCompletion] = useState<{ title: string; message: string } | null>(null)
  const [closeRequestPending, setCloseRequestPending] = useState(false)
  const closeRequestInFlightRef = useRef(false)
  const automaticRunSeenRef = useRef(false)
  const pageScrollPositionsRef = useRef<Record<Page, number>>({ automatic: 0, manual: 0, pd: 0, data: 0, shipping: 0, settings: 0 })
  const active = snapshot?.backend.mode === "active"
  const operational = active && licenseStatus.valid
  const automaticWorkflowActive = Boolean(snapshot && !["idle", "completed"].includes(snapshot.automaticTest.state))
  const automaticPaused = snapshot?.automaticTest.state === "paused"
  const automaticInteractionLocked = automaticWorkflowActive || pendingCommand === "automatic.start"
  const activeOutputCurrentA = snapshot?.devices.powerSupply.activeCurrentA
  const manualPowerOutputActive = Boolean(
    snapshot
    && !automaticWorkflowActive
    && (
      (typeof activeOutputCurrentA === "number" && Math.abs(activeOutputCurrentA) > 0.005)
      || (config.powerSupplyKind === "tdk" && snapshot.devices.powerSupply.outputEnabled)
    )
  )
  const attemptGracefulClose = useCallback(async () => {
    if (!("__TAURI_INTERNALS__" in window) || closeRequestInFlightRef.current) return
    closeRequestInFlightRef.current = true
    setCloseRequestPending(true)
    setCloseFailure(null)
    let shutdownConfirmed = false
    try {
      const result = await sendBackendRequest<AppShutdownResult>("app.shutdown")
      shutdownConfirmed = result.shutdownConfirmed
      if (!shutdownConfirmed) {
        throw new Error(result.shutdown.reason || "后端未确认安全下电")
      }
      if (!result.exportsDrained) {
        throw new Error("安全下电已确认，但后台 Excel 导出尚未排空；请稍后重试退出")
      }
      await automaticSaveFlightRef.current.catch(() => undefined)
      if (licenseStatus.valid && configHydratedRef.current) {
        try {
          await sendBackendRequest(
            "app.configure",
            configurationPayload(configRef.current),
          )
        } catch (reason) {
          setCloseFailure({
            message: reason instanceof Error ? reason.message : String(reason),
            shutdownConfirmed: true,
          })
          return
        }
      }
      await invoke("bridge_disconnect")
      await getCurrentWindow().destroy()
    } catch (reason) {
      setCloseFailure({
        message: reason instanceof Error ? reason.message : String(reason),
        shutdownConfirmed,
      })
    } finally {
      closeRequestInFlightRef.current = false
      setCloseRequestPending(false)
    }
  }, [licenseStatus.valid])

  const forceClose = useCallback(async () => {
    try {
      await invoke("bridge_force_disconnect")
    } finally {
      await getCurrentWindow().destroy().catch(() => undefined)
    }
  }, [])

  const cancelClose = useCallback(() => {
    setCloseFailure(null)
  }, [])

  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return
    const unlistenPromise = getCurrentWindow().onCloseRequested((event) => {
      event.preventDefault()
      void attemptGracefulClose()
    })
    return () => {
      void unlistenPromise.then((unlisten) => unlisten())
    }
  }, [attemptGracefulClose])

  useEffect(() => {
    const currentConfig = snapshot?.configuration
    if (!currentConfig || dirty) return
    const pendingPersisted = pendingPersistedConfigRef.current
    if (pendingPersisted && !configurationsEqual(currentConfig, pendingPersisted)) return
    pendingPersistedConfigRef.current = null
    setConfig((current) => {
      const next = configurationsEqual(current, currentConfig) ? current : currentConfig
      configRef.current = next
      configHydratedRef.current = true
      return next
    })
  }, [snapshot?.configuration, dirty])

  const saveConfiguration = useCallback(async () => {
    if (!operational) return
    const revision = configRevisionRef.current
    await automaticSaveFlightRef.current.catch(() => undefined)
    const savedSnapshot = await command(
      "app.configure",
      configurationPayload(configRef.current),
    )
    if (configRevisionRef.current === revision) {
      const persisted = savedSnapshot.configuration ?? configRef.current
      pendingPersistedConfigRef.current = persisted
      configRef.current = persisted
      setConfig(persisted)
      setDirty(false)
    }
  }, [command, operational])

  const run: RunCommand = useCallback(async (method, params, sync) => {
    if (!active) return
    if (!licenseStatus.valid && !SAFE_UNLICENSED_METHODS.has(method)) {
      throw new Error(`软件授权无效：${licenseReasonLabels[licenseStatus.reason]}`)
    }
    if (sync && dirty) await saveConfiguration()
    const result = await command(method, params)
    if (method.startsWith("powerMeter.profile.") && result.configuration) {
      pendingPersistedConfigRef.current = null
      const next = mergeAuthoritativePowerMeterConfiguration(configRef.current, result.configuration)
      configRef.current = next
      setConfig(next)
    }
    if (method === "automatic.start") setDirty(false)
    return result
  }, [active, command, dirty, licenseStatus.reason, licenseStatus.valid, saveConfiguration])

  const update: UpdateConfig = useCallback((key, value) => {
    configRevisionRef.current += 1
    const next = { ...configRef.current, [key]: value }
    configRef.current = next
    setConfig(next)
    setDirty(true)
  }, [])

  const selectTestScript = useCallback((scriptId: string) => {
    const script = (snapshot?.testScripts ?? BUILTIN_TEST_SCRIPT_OPTIONS).find((item) => item.id === scriptId)
    if (!script) return
    configRevisionRef.current += 1
    const next = applyTestScript(configRef.current, script)
    configRef.current = next
    setConfig(next)
    setDirty(true)
  }, [snapshot?.testScripts])

  useEffect(() => {
    applyTheme(theme, accentTheme)
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme)
      window.localStorage.setItem(ACCENT_THEME_STORAGE_KEY, accentTheme)
    } catch {
      // The theme remains active for this session if storage is unavailable.
    }
  }, [theme, accentTheme])

  useEffect(() => {
    try {
      window.localStorage.setItem(CURVE_LINE_STYLE_STORAGE_KEY, curveLineStyle)
    } catch {
      // The curve preference remains active for this session if storage is unavailable.
    }
  }, [curveLineStyle])

  useEffect(() => {
    try {
      window.localStorage.setItem(LATEST_POINT_MARKER_STORAGE_KEY, latestPointMarkerStyle)
    } catch {
      // The marker preference remains active for this session if storage is unavailable.
    }
  }, [latestPointMarkerStyle])

  const pageIsBlocked = useCallback((targetPage: Page): boolean => {
    if (targetPage === "settings") return false
    if (!licenseStatus.valid) return true
    // The data browser stays reachable while a run is active: the page keeps
    // its own read-only lock and states the reason, instead of leaving a dead
    // navigation button that explains nothing.
    if (targetPage === "data") return false
    if (targetPage === "pd") return false
    if (targetPage === "automatic") return manualPowerOutputActive
    if (targetPage === "manual") return automaticWorkflowActive && !automaticPaused
    return automaticInteractionLocked
  }, [automaticInteractionLocked, automaticPaused, automaticWorkflowActive, licenseStatus.valid, manualPowerOutputActive])

  /** Blocked navigation explains itself in the tooltip of the disabled item. */
  const pageBlockedReason = useCallback((targetPage: Page): string => {
    if (!licenseStatus.valid) return "软件尚未激活，激活后可继续。"
    if (targetPage === "automatic") return manualPowerOutputActive ? "手动电源正在输出，请先在手动配置中将电流归零" : ""
    if (targetPage === "manual") return automaticWorkflowActive && !automaticPaused ? "自动测试执行中，手动配置已锁定；执行“结束并安全下电”后可继续。" : ""
    if (targetPage === "shipping") return automaticInteractionLocked ? "自动测试执行中，出货报告已锁定；执行“结束并安全下电”后可继续。" : ""
    return ""
  }, [automaticInteractionLocked, automaticPaused, automaticWorkflowActive, licenseStatus.valid, manualPowerOutputActive])

  const changePage = useCallback((nextPage: Page) => {
    if (pageIsBlocked(nextPage)) return
    pageScrollPositionsRef.current[page] = window.scrollY
    setPage(nextPage)
  }, [page, pageIsBlocked])

  useLayoutEffect(() => {
    const targetY = pageScrollPositionsRef.current[page] ?? 0
    window.scrollTo({ top: targetY, behavior: "instant" })
  }, [page])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return
      const numericKey = Number(event.key)
      if (!Number.isInteger(numericKey) || numericKey < 1 || numericKey > pageShortcuts.length) return
      const targetPage = pageShortcuts[numericKey - 1]
      if (!targetPage) return
      event.preventDefault()
      changePage(targetPage)
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [changePage])

  const notices = snapshot?.backend.notices ?? []
  const deviceDetectionErrors = [
    { key: "powerSupply", title: "电源识别失败", detail: snapshot?.devices.powerSupply?.detail },
    { key: "powerMeter", title: "功率计识别失败", detail: snapshot?.devices.powerMeter?.detail },
    { key: "spectrometer", title: "光谱仪识别失败", detail: snapshot?.devices.spectrometer?.detail },
    { key: "pd", title: "采集卡识别失败", detail: snapshot?.pd?.status },
  ]
    .filter((item) => item.detail && item.detail.startsWith("识别失败："))
    .map((item) => {
      const message = item.detail!.replace(/^识别失败[：:]\s*/, "")
      return {
        key: `device-failure:${item.key}:${message}`,
        level: "error" as const,
        title: item.title,
        message,
      }
    })
    .filter((item) => !notices.some((notice) => notice.message === item.message))

  const deviceDetectionCompletions = [
    { key: "powerSupply", title: "电源识别", detail: snapshot?.devices.powerSupply?.detail },
    { key: "powerMeter", title: "功率计识别", detail: snapshot?.devices.powerMeter?.detail },
    { key: "spectrometer", title: "光谱仪识别", detail: snapshot?.devices.spectrometer?.detail },
    { key: "pd", title: "采集卡识别", detail: snapshot?.pd?.status },
  ]
    .filter((item) => item.detail && item.detail.startsWith("识别完成："))
    .map((item) => {
      const message = item.detail!.replace(/^识别完成[：:]\s*/, "")
      return {
        key: `device-complete:${item.key}:${message}`,
        level: "info" as const,
        title: item.title,
        message,
      }
    })
    .filter((item) => !notices.some((notice) => notice.message === item.message))

  const [customNotices, setCustomNotices] = useState<Array<{ key: string; level: "error" | "info" | "warning"; title: string; message: string }>>([])
  const notify = useCallback((notice: { level?: "error" | "info" | "warning"; title: string; message: string; durationMs?: number }) => {
    const key = `custom-notice:${Date.now()}:${Math.random().toString(36).slice(2, 6)}`
    const level = notice.level ?? "error"
    setCustomNotices((current) => {
      const filtered = current.filter((item) => !(item.title === notice.title && item.message === notice.message))
      return [...filtered, { key, level, title: notice.title, message: notice.message }]
    })
    const duration = notice.durationMs ?? (level === "info" ? INFO_TOAST_DURATION_MS : 5000)
    if (duration > 0) {
      window.setTimeout(() => {
        setDismissedNotifications((current) => new Set(current).add(key))
        setCustomNotices((current) => current.filter((item) => item.key !== key))
      }, duration)
    }
  }, [])

  const notifications: Array<{ key: string; level: "error" | "info" | "warning"; title: string; message: string; code?: string; id?: string }> = [
    ...(error ? [{ key: `request-error:${error}`, level: "error" as const, title: "系统错误", message: error }] : []),
    ...notices.map((notice, index) => ({ ...notice, key: notice.id ? `backend-notice:${notice.id}` : `backend-notice:${notice.level}:${notice.title}:${notice.message}:${index}` })),
    ...deviceDetectionErrors,
    ...deviceDetectionCompletions,
    ...customNotices,
    ...(!active && snapshot ? [{ key: "runtime-warning", level: "warning" as const, title: "运行环境提示", message: "当前 Python 环境未加载所需设备驱动，因此控制功能暂不可用。请使用项目指定环境启动。" }] : []),
  ]
  const [dismissedNotifications, setDismissedNotifications] = useState<Set<string>>(() => new Set())
  const hasNotificationSnapshot = snapshot !== null
  const notificationKeys = notifications.map((notification) => notification.key)
  const notificationKey = notificationKeys.join("\n")
  useEffect(() => {
    // Navigation clears the snapshot while the next view subscribes. Only a
    // loaded snapshot can confirm that a dismissed notice has been removed.
    if (!hasNotificationSnapshot) return
    const activeKeys = new Set(notificationKeys)
    setDismissedNotifications((current) => {
      const retained = new Set([...current].filter((key) => activeKeys.has(key)))
      return retained.size === current.size ? current : retained
    })
  }, [notificationKey, hasNotificationSnapshot])

  const autoDismissNotificationKeys = notifications.filter((notification) => notification.level === "info").map((notification) => notification.key)
  const autoDismissNotificationKey = autoDismissNotificationKeys.join("\n")
  useEffect(() => {
    if (!autoDismissNotificationKey) return
    const timer = window.setTimeout(() => {
      setDismissedNotifications((current) => new Set([...current, ...autoDismissNotificationKeys]))
    }, INFO_TOAST_DURATION_MS)
    return () => window.clearTimeout(timer)
  }, [autoDismissNotificationKey])

  useEffect(() => {
    const auto = snapshot?.automaticTest
    if (!auto) return
    if (!["idle", "completed"].includes(auto.state)) {
      automaticRunSeenRef.current = true
      setAutomaticCompletion(null)
      return
    }
    if (
      auto.state !== "completed"
      || !automaticRunSeenRef.current
      || auto.detail === "正在写入最终测试状态"
      || !auto.terminalOutcome
    ) return

    automaticRunSeenRef.current = false
    const title = automaticTerminalTitle(auto.terminalOutcome)
    setAutomaticCompletion(auto.terminalOutcome === "succeeded" && snapshot?.safety.shutdown.confirmed
      ? null
      : { title, message: auto.detail || title })
  }, [snapshot?.automaticTest, snapshot?.safety.shutdown.confirmed])

  const prepareNextDevice = useCallback(async () => {
    await run("automatic.nextDevice")
    const nextConfig = { ...configRef.current, sn: "" }
    configRef.current = nextConfig
    setConfig(nextConfig)
    setDirty(false)
    setAutomaticCompletion(null)
    window.requestAnimationFrame(() => document.getElementById("automatic-sn")?.focus())
  }, [run])

  const retryAutomaticExport = useCallback(async () => {
    await run("automatic.retryExport")
  }, [run])

  const chooseAutomaticExportDirectory = useCallback(async () => {
    const selected = await openDialog({
      multiple: false,
      directory: true,
      title: "选择 Excel 输出目录",
      defaultPath: configRef.current.outputDir.trim() || undefined,
    })
    if (typeof selected !== "string" || !selected) return
    await run("automatic.retryExport", { outputDir: selected })
    const nextConfig = { ...configRef.current, outputDir: selected }
    configRef.current = nextConfig
    setConfig(nextConfig)
    setDirty(false)
  }, [run])

  const runAutomaticPauseAction = useCallback((action: AutomaticPauseAction, params: Record<string, unknown> = {}) => {
    void run(automaticPauseCommand(action), params)
  }, [run])

  const visibleNotifications = notifications.filter((notification) => !dismissedNotifications.has(notification.key))
  const powerProtectionNotification = visibleNotifications.find(
    (notification) => notification.code === "power_protection" || notification.title.endsWith("功率保护"),
  )
  const spectrumSaturationNotification = visibleNotifications.find(
    (notification) => notification.code === "spectrum_saturation" || notification.title.endsWith("光谱饱和"),
  )
  const activeAlertNotification = powerProtectionNotification
  const toastNotifications = visibleNotifications.filter((notification) => notification !== powerProtectionNotification && notification !== spectrumSaturationNotification)
  return (
    <div className="app-shell flex h-screen flex-col overflow-hidden bg-background text-foreground" data-native={TAURI_RUNTIME_AVAILABLE}>
      <AppTitlebar
        snapshot={snapshot}
        showPhase={page === "automatic" || automaticWorkflowActive}
        onOpenAppearance={() => changePage("settings")}
        active={active}
        canPause={Boolean(page === "automatic" && snapshot?.automaticTest.canPause)}
        commandPending={commandPending}
        controlsLocked={automaticInteractionLocked}
        dirty={dirty}
        pendingCommand={pendingCommand}
        run={run}
        saveConfiguration={saveConfiguration}
      />
      {!licenseStatus.valid && (
        <div className="license-runtime-warning" role="alert">
          <div><strong>{licenseReasonLabels[licenseStatus.reason]}</strong><span>已禁止开始新测试和修改硬件输出；仍可安全结束当前任务或执行急停。</span></div>
          <div>
            {automaticWorkflowActive && <Button onClick={() => void run("automatic.end")} size="sm" variant="outline">安全结束测试</Button>}
            <Button onClick={() => void run("app.stopAll")} size="sm" variant="destructive">急停</Button>
          </div>
        </div>
      )}
      <div className="app-shell__body grid min-h-0 flex-1 overflow-hidden">
        <aside className="app-shell__sidebar shell-sidebar-enter flex h-full min-h-0 flex-col border-r border-border bg-[var(--app-sidebar)] px-3 py-4 text-[var(--app-text)]">
          <nav className="space-y-1.5">
            {navigation.map((item) => (
              <button
                aria-current={page === item.id ? "page" : undefined}
                aria-keyshortcuts={"shortcut" in item ? `Meta+${item.shortcut} Control+${item.shortcut}` : undefined}
                className={`app-nav-item relative flex w-full items-center gap-3 rounded-lg px-3 py-3 text-[15px] outline-none transition-[color,background-color,box-shadow] duration-200 before:absolute before:inset-y-1.5 before:left-0 before:w-0.5 before:origin-center before:rounded-full before:bg-primary before:transition-transform before:duration-200 focus-visible:ring-2 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:text-[var(--app-text-tertiary)] ${page === item.id ? "bg-[var(--app-selected-soft)] font-medium text-[var(--app-text)] before:scale-y-100" : "text-[var(--app-text-tertiary)] before:scale-y-0 hover:bg-[var(--app-hover-soft)] hover:text-[var(--app-text-secondary)] disabled:hover:bg-transparent"}`}
                disabled={"unavailable" in item || pageIsBlocked(item.id)}
                key={item.id}
                onClick={() => { if (!("unavailable" in item)) changePage(item.id) }}
                title={
                  "unavailable" in item
                    ? `${item.label}功能尚未接入`
                    : pageBlockedReason(item.id) || ("shortcut" in item ? `${item.label}（⌘/Ctrl + ${item.shortcut}）` : item.label)
                }
                type="button"
              >
                <item.icon className="app-nav-icon size-5 shrink-0" /><span className="whitespace-nowrap font-medium">{item.label}</span>
              </button>
            ))}
          </nav>
          <div className="mt-auto grid justify-items-center gap-2 px-3 pb-1 pt-3" aria-label="侧栏格言 Stay True">
            <div className="h-px w-full bg-gradient-to-r from-transparent via-border/80 to-transparent" />
            <p
              className="whitespace-nowrap text-base font-normal leading-snug tracking-[0.01em] text-[var(--app-text-tertiary)] opacity-80"
              style={{ fontFamily: "'Snell Roundhand', 'Segoe Print', 'Bradley Hand', cursive" }}
            >
              Stay True
            </p>
          </div>
        </aside>

        <main className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          {toastNotifications.length > 0 && (
            <AnimatedToastStack
              disabled={automaticInteractionLocked}
              onDismiss={(id) => {
                setDismissedNotifications((current) => new Set(current).add(id))
                setCustomNotices((current) => current.filter((item) => item.key !== id))
              }}
              toasts={toastNotifications.map((notification) => ({
                id: notification.key,
                title: notification.title,
                description: notification.message,
                status: notification.level as ToastStatus,
              }))}
            />
          )}

          {activeAlertNotification === powerProtectionNotification && powerProtectionNotification && <PowerProtectionDialog message={powerProtectionNotification.message} onClose={() => setDismissedNotifications((current) => new Set(current).add(powerProtectionNotification.key))} />}
          {snapshot?.automaticTest.state === "paused" && !activeAlertNotification && !closeFailure && <AutomaticPauseDialog auto={snapshot.automaticTest} integrationTimeUs={config.integrationTimeUs} onAction={runAutomaticPauseAction} pending={commandPending} />}
          {automaticCompletion && !activeAlertNotification && !closeFailure && (
            <AutomaticCompletionDialog
              canNextDevice={snapshot?.automaticTest.canNextDevice === true}
              canRetryExport={snapshot?.automaticTest.canRetryExport === true}
              completedPoints={snapshot?.automaticTest.savedPointCount}
              exportError={snapshot?.automaticTest.exportError}
              exportPath={snapshot?.automaticTest.exportPath}
              exportStatus={snapshot?.automaticTest.exportStatus}
              journalPath={snapshot?.automaticTest.journalPath}
              message={automaticCompletion.message}
              onChooseExportDirectory={() => void chooseAutomaticExportDirectory()}
              onClose={() => setAutomaticCompletion(null)}
              onNextDevice={() => void prepareNextDevice()}
              onRetryExport={() => void retryAutomaticExport()}
              outcome={snapshot?.automaticTest.terminalOutcome}
              sn={config.sn}
              shutdown={snapshot?.safety.shutdown}
              title={automaticCompletion.title}
              totalPoints={snapshot?.automaticTest.currents?.length}
            />
          )}
          {closeFailure && <ShutdownFailureDialog message={closeFailure.message} shutdownConfirmed={closeFailure.shutdownConfirmed} onCancel={cancelClose} onForceExit={() => void forceClose()} onRetry={() => void attemptGracefulClose()} pending={closeRequestPending} />}

          <div
            className={`page-enter flex flex-1 min-h-0 flex-col overflow-y-auto ${page === "automatic" ? "gap-2 px-3 py-0" : page === "manual" ? "gap-3 p-4" : "space-y-4 p-4"}`}
            style={page === "data" ? { display: "none" } : undefined}
            data-page={page}
            key={page}
          >
            {page === "automatic" && <AutomaticPage snapshot={snapshot} config={config} update={update} active={operational} controlsLocked={automaticInteractionLocked || !licenseStatus.valid} curveLineStyle={curveLineStyle} latestPointMarkerStyle={latestPointMarkerStyle} pending={commandPending} run={run} saveConfiguration={saveConfiguration} testScripts={snapshot?.testScripts ?? BUILTIN_TEST_SCRIPT_OPTIONS} onScriptSelect={selectTestScript} onNextDevice={() => void prepareNextDevice()} onViewData={() => changePage("data")} onRetryExport={() => void retryAutomaticExport()} onChooseExportDirectory={() => void chooseAutomaticExportDirectory()} onShowCompletion={() => { const auto = snapshot?.automaticTest; if (auto?.terminalOutcome) setAutomaticCompletion({ title: automaticTerminalTitle(auto.terminalOutcome), message: auto.detail }) }} onNotify={notify} />}
            {page === "manual" && <ManualPage snapshot={snapshot} config={config} update={update} active={operational} pending={commandPending} run={run} />}
            {page === "pd" && <PdPage snapshot={snapshot} active={operational} pending={commandPending} run={run} />}
            {page === "shipping" && <ShippingReportPage active={operational} controlsLocked={automaticInteractionLocked || !licenseStatus.valid} pending={commandPending} />}
            {page === "settings" && <SystemSettingsPage curveLineStyle={curveLineStyle} latestPointMarkerStyle={latestPointMarkerStyle} licenseStatus={licenseStatus} onCurveLineStyleChange={setCurveLineStyle} onImportLicense={onImportLicense} onLatestPointMarkerStyleChange={setLatestPointMarkerStyle} theme={theme} onThemeChange={setTheme} accentTheme={accentTheme} onAccentThemeChange={setAccentTheme} />}
          </div>
          <div className="db-page-host min-h-0 flex-1" style={{ display: page === "data" ? "flex" : "none", flexDirection: "column" }}>
            <DataBrowserPage
              visible={page === "data"}
              defaultDirectory={config.outputDir}
              enabled={operational && !automaticInteractionLocked}
              disabledReason={
                !licenseStatus.valid ? "软件尚未激活，激活后可浏览测试数据。"
                  : !active ? "正在连接后端服务，连接完成后即可浏览测试数据。"
                    : automaticInteractionLocked ? "自动测试执行中，数据浏览已锁定；执行“结束并安全下电”后可继续。" : undefined
              }
            />
          </div>
        </main>
      </div>
    </div>
  )
}

type UpdateConfig = <K extends keyof AppConfiguration>(key: K, value: AppConfiguration[K]) => void
type RunCommand = (method: string, params?: Record<string, unknown>, sync?: boolean) => Promise<BackendSnapshot | void>

function SystemSettingsPage({ theme, accentTheme, curveLineStyle, latestPointMarkerStyle, licenseStatus, onThemeChange, onAccentThemeChange, onCurveLineStyleChange, onLatestPointMarkerStyleChange, onImportLicense }: { theme: Theme; accentTheme: AccentTheme; curveLineStyle: CurveLineStyle; latestPointMarkerStyle: LatestPointMarkerStyle; licenseStatus: LicenseStatus; onThemeChange: (theme: Theme) => void; onAccentThemeChange: (theme: AccentTheme) => void; onCurveLineStyleChange: (style: CurveLineStyle) => void; onLatestPointMarkerStyleChange: (style: LatestPointMarkerStyle) => void; onImportLicense: () => Promise<void> }) {
  const [licenseBusy, setLicenseBusy] = useState(false)
  const [licenseError, setLicenseError] = useState("")
  const importLicenseFile = async () => {
    setLicenseBusy(true)
    setLicenseError("")
    try {
      await onImportLicense()
    } catch (reason) {
      setLicenseError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLicenseBusy(false)
    }
  }
  return (
    <section className="system-settings-page">
      <div className="system-settings-page__heading">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">系统设置 / 外观</h1>
          <p className="mt-1 text-sm text-muted-foreground">选择后立即生效，自动保存。</p>
        </div>
      </div>
      <AppearanceSettings theme={theme} accentTheme={accentTheme} curveLineStyle={curveLineStyle} latestPointMarkerStyle={latestPointMarkerStyle} onThemeChange={onThemeChange} onAccentThemeChange={onAccentThemeChange} onCurveLineStyleChange={onCurveLineStyleChange} onLatestPointMarkerStyleChange={onLatestPointMarkerStyleChange} />
      <Card className="system-settings-card mt-4 overflow-visible shadow-none">
        <CardHeader className="system-settings-card__header">
          <div className="system-settings-card__icon"><KeyRound className="size-5" /></div>
          <div>
            <CardTitle className="text-base">软件授权</CardTitle>
            <CardDescription>查看当前设备许可证及有效期。</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <div className="license-settings-summary">
            <div className="license-settings-status" data-valid={licenseStatus.valid ? "true" : "false"}><ShieldCheck /><span><strong>{licenseReasonLabels[licenseStatus.reason]}</strong><small>{licenseStatus.payload?.customer ?? "当前设备没有可用许可证"}</small></span></div>
            <dl>
              <div><dt>到期日期</dt><dd>{licenseStatus.payload ? formatLicenseDate(licenseStatus.payload.expiresAt) : "--"}</dd></div>
              <div><dt>授权功能</dt><dd>{licenseStatus.payload?.features.join("、") ?? "--"}</dd></div>
              <div><dt>机器码</dt><dd title={licenseStatus.machineCode}>{licenseStatus.machineCode}</dd></div>
            </dl>
            {licenseError && <p className="license-activation-error" role="alert">{licenseError}</p>}
            <Button disabled={licenseBusy} onClick={() => void importLicenseFile()} size="sm" variant="outline"><Upload className="size-4" />{licenseBusy ? "正在验证..." : "重新导入许可证"}</Button>
          </div>
        </CardContent>
      </Card>
    </section>
  )
}

function currentInputMaximum(config: AppConfiguration, snapshot: BackendSnapshot | null): number | undefined {
  const connectedKind = snapshot?.devices.powerSupply.connected
    ? snapshot.configuration?.powerSupplyKind ?? config.powerSupplyKind
    : config.powerSupplyKind
  return connectedKind !== "tdk" ? I2C_CURRENT_LIMIT_A : undefined
}

function updatePowerSupplyKind(config: AppConfiguration, update: UpdateConfig, kind: AppConfiguration["powerSupplyKind"]) {
  if (config.selectedScriptId === "aging" && kind === "tdk") return
  update("powerSupplyKind", kind)
  if (kind === "tdk") return
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

type ShippingFieldState = Record<string, { include: boolean; value: string }>

function ShippingReportFieldSelector({
  checked,
  definition,
  disabled = false,
  encircledEnergyNa,
  onCheckedChange,
  onEncircledEnergyNaChange,
}: {
  checked: boolean
  definition: ShippingReportFieldDefinition
  disabled?: boolean
  encircledEnergyNa: string
  onCheckedChange: (checked: boolean) => void
  onEncircledEnergyNaChange: (value: string) => void
}) {
  if (definition.key !== "na017Energy") {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input checked={checked} disabled={disabled} onChange={(event) => onCheckedChange(event.target.checked)} type="checkbox" />
        <span>{definition.label}</span>
      </label>
    )
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm">
      <label className="flex items-center gap-2 whitespace-nowrap">
        <input checked={checked} disabled={disabled} onChange={(event) => onCheckedChange(event.target.checked)} type="checkbox" />
        <span>NA</span>
      </label>
      <Input
        aria-label="Encircled Energy NA"
        className="h-7 w-16 px-2 text-sm"
        disabled={disabled}
        inputMode="decimal"
        max="1"
        min="0"
        step="0.01"
        type="number"
        value={encircledEnergyNa}
        onChange={(event) => onEncircledEnergyNaChange(event.target.value)}
      />
      <span>Encircled Energy</span>
    </div>
  )
}

function shippingSuggestedValue(point: ShippingReportPointSuggestion | undefined, measuredKey?: string | null): string {
  if (!point || !measuredKey) return ""
  const value = (point as unknown as Record<string, unknown>)[measuredKey]
  if (typeof value !== "number" || !Number.isFinite(value)) return "N/A"
  if (measuredKey === "currentA") return String(value)
  if (measuredKey === "powerW") return value.toFixed(1)
  if (measuredKey === "voltageV" || measuredKey === "efficiencyPercent") return value.toFixed(2)
  return value.toFixed(3).replace(/\.?0+$/, "")
}

function compactSpectrumAxisMaximum(peakCounts: number): number {
  if (peakCounts <= 0 || !Number.isFinite(peakCounts)) return 1
  const paddedPeak = peakCounts * 1.03
  const magnitude = 10 ** Math.floor(Math.log10(paddedPeak))
  const multiplier = [1, 2, 2.5, 5, 10].find((candidate) => candidate * magnitude >= paddedPeak) ?? 10
  return multiplier * magnitude
}

function suggestedSpectrumAxis(inspection: ShippingWorkbookInspection, currentA: number) {
  const point = inspection.points.find((item) => Math.abs(item.currentA - currentA) < 1e-9)
  const wavelengthRange = inspection.spectrumWavelengthRange
  const centerWavelength = point?.peakWavelengthNm
    ?? point?.centroidNm
    ?? (wavelengthRange ? (wavelengthRange.minimum + wavelengthRange.maximum) / 2 : 0)
  let maximumPeakCounts = 0
  for (const candidate of inspection.points) {
    if (typeof candidate.spectrumPeakCounts === "number" && Number.isFinite(candidate.spectrumPeakCounts)) {
      maximumPeakCounts = Math.max(maximumPeakCounts, candidate.spectrumPeakCounts)
    }
  }
  return {
    mode: "counts" as const,
    minimum: 0,
    maximum: compactSpectrumAxisMaximum(maximumPeakCounts),
    wavelengthMinimum: centerWavelength - 15,
    wavelengthMaximum: centerWavelength + 15,
  }
}

function preferredSpectrumAxis(inspection: ShippingWorkbookInspection, currentA: number) {
  const suggested = suggestedSpectrumAxis(inspection, currentA)
  const saved = inspection.preferences.spectrum?.spectrumAxis
  if (!saved) return suggested
  if (saved.mode === "counts") {
    return {
      ...suggested,
      wavelengthMinimum: saved.wavelengthMinimum ?? suggested.wavelengthMinimum,
      wavelengthMaximum: saved.wavelengthMaximum ?? suggested.wavelengthMaximum,
    }
  }
  return {
    mode: saved.mode,
    minimum: saved.minimum,
    maximum: saved.maximum,
    wavelengthMinimum: saved.wavelengthMinimum ?? suggested.wavelengthMinimum,
    wavelengthMaximum: saved.wavelengthMaximum ?? suggested.wavelengthMaximum,
  }
}

function shippingDefaultOutputDirectory(sourcePath: string): string {
  const separatorIndex = Math.max(sourcePath.lastIndexOf("/"), sourcePath.lastIndexOf("\\"))
  return separatorIndex >= 0 ? sourcePath.slice(0, separatorIndex) : ""
}

function shippingOutputFilePath(outputDirectory: string, sn: string): string {
  const trimmedDirectory = outputDirectory.trim()
  if (!trimmedDirectory) return ""
  const directory = trimmedDirectory.replace(/[\\/]+$/, "") || trimmedDirectory
  const separator = directory.includes("\\") ? "\\" : "/"
  const safeSn = (sn.trim() || "出货报告").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
  return `${directory}${directory.endsWith("/") || directory.endsWith("\\") ? "" : separator}${safeSn}_出货报告.pdf`
}

function getStoredShippingProductName(): string {
  try {
    return window.localStorage.getItem(SHIPPING_PRODUCT_NAME_STORAGE_KEY) ?? ""
  } catch {
    return ""
  }
}

function storeShippingProductName(value: string): void {
  try {
    if (value.trim()) window.localStorage.setItem(SHIPPING_PRODUCT_NAME_STORAGE_KEY, value)
    else window.localStorage.removeItem(SHIPPING_PRODUCT_NAME_STORAGE_KEY)
  } catch {
    // Local storage may be unavailable in restricted WebView environments.
  }
}

function ShippingReportWorkflow({ disabled }: { disabled: boolean }) {
  const [inspection, setInspection] = useState<ShippingWorkbookInspection | null>(null)
  const [reportType, setReportType] = useState<ShippingReportType>("pole")
  const [reportLanguage, setReportLanguage] = useState<ShippingReportLanguage>("en")
  const [productName, setProductName] = useState(getStoredShippingProductName)
  const [sn, setSn] = useState("")
  const [operatingCurrentA, setOperatingCurrentA] = useState(0)
  const [fields, setFields] = useState<ShippingFieldState>({})
  const [customFields, setCustomFields] = useState<ShippingReportFieldDefinition[]>([])
  const [encircledEnergyNa, setEncircledEnergyNa] = useState(DEFAULT_ENCIRCLED_ENERGY_NA)
  const [axisMode, setAxisMode] = useState<SpectrumAxisMode>("relative_db")
  const [axisMinimum, setAxisMinimum] = useState(-80)
  const [axisMaximum, setAxisMaximum] = useState(0)
  const [wavelengthMinimum, setWavelengthMinimum] = useState(0)
  const [wavelengthMaximum, setWavelengthMaximum] = useState(2000)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [lastOutputPath, setLastOutputPath] = useState("")
  const [openingResult, setOpeningResult] = useState(false)
  const [resultOpenNotice, setResultOpenNotice] = useState("")
  const [previewPages, setPreviewPages] = useState<string[]>([])
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState("")
  const [sourcePath, setSourcePath] = useState("")
  const [outputDirectory, setOutputDirectory] = useState("")
  const previewRequestRef = useRef(0)

  const initializeFields = useCallback((nextInspection: ShippingWorkbookInspection, nextType: ShippingReportType, currentA: number) => {
    const point = nextInspection.points.find((item) => Math.abs(item.currentA - currentA) < 1e-9)
    const preference = nextInspection.preferences[nextType] ?? { selectedFields: [], manualValues: {}, customFields: [] }
    const selected = new Set(preference.selectedFields)
    const nextCustomFields = preference.customFields ?? []
    const nextDefinitions = [...nextInspection.fieldDefinitions[nextType], ...nextCustomFields]
    const nextFields = Object.fromEntries(nextDefinitions.map((definition) => [
      definition.key,
      {
        include: selected.has(definition.key),
        value: definition.measuredKey
          ? shippingSuggestedValue(point, definition.measuredKey)
          : preference.manualValues[definition.key] ?? "",
      },
    ]))
    setFields(nextFields)
    setCustomFields(nextCustomFields)
    setReportLanguage(preference.reportLanguage ?? "en")
    if (nextType === "spectrum") {
      setEncircledEnergyNa(preference.encircledEnergyNa ?? DEFAULT_ENCIRCLED_ENERGY_NA)
      const axis = preferredSpectrumAxis(nextInspection, currentA)
      setAxisMode(axis.mode)
      setAxisMinimum(axis.minimum)
      setAxisMaximum(axis.maximum)
      setWavelengthMinimum(axis.wavelengthMinimum)
      setWavelengthMaximum(axis.wavelengthMaximum)
    }
  }, [])

  const chooseSourcePath = async () => {
    if (disabled || busy) return
    try {
      const selected = await openDialog({
        multiple: false,
        directory: false,
        title: "选择出货报告测试数据",
        filters: [{ name: "Excel 测试文件", extensions: ["xlsx"] }],
      })
      if (typeof selected !== "string") return
      setSourcePath(selected)
      setOutputDirectory(shippingDefaultOutputDirectory(selected))
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const chooseOutputDirectory = async () => {
    if (disabled || busy) return
    try {
      const selected = await openDialog({
        multiple: false,
        directory: true,
        title: "选择出货报告保存文件夹",
      })
      if (typeof selected === "string") {
        setOutputDirectory(selected)
        setError("")
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const configureReport = async () => {
    if (disabled || busy) return
    const nextSourcePath = sourcePath.trim()
    const nextOutputDirectory = outputDirectory.trim()
    if (!nextSourcePath) {
      setError("请选择 Excel 测试数据路径")
      return
    }
    if (!nextOutputDirectory) {
      setError("请选择本地 PDF 保存文件夹")
      return
    }
    setError("")
    setPreviewPages([])
    setPreviewError("")
    try {
      setBusy(true)
      const result = await sendBackendRequest<ShippingWorkbookInspection>("shippingReport.inspect", { path: nextSourcePath })
      if (result.compatibility.kind === "rejected") throw new Error(result.compatibility.message)
      const nextType: ShippingReportType = result.allowedReportTypes.includes("spectrum") ? "spectrum" : "pole"
      const currentA = result.currents[result.currents.length - 1] ?? 0
      setInspection(result)
      setReportType(nextType)
      setProductName((current) => current.trim() || result.productName)
      setSn(result.sn)
      setOperatingCurrentA(currentA)
      initializeFields(result, nextType, currentA)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const changeReportType = (nextType: ShippingReportType) => {
    if (!inspection) return
    setReportType(nextType)
    initializeFields(inspection, nextType, operatingCurrentA)
    setError("")
  }

  const changeProductName = (value: string) => {
    setProductName(value)
    storeShippingProductName(value)
  }

  const changeOperatingCurrent = (currentA: number) => {
    if (!inspection) return
    setOperatingCurrentA(currentA)
    const point = inspection.points.find((item) => Math.abs(item.currentA - currentA) < 1e-9)
    const measuredKeys = new Map(inspection.fieldDefinitions[reportType].filter((item) => item.measuredKey).map((item) => [item.key, item.measuredKey]))
    setFields((previous) => Object.fromEntries(Object.entries(previous).map(([key, state]) => [
      key,
      measuredKeys.has(key) ? { ...state, value: shippingSuggestedValue(point, measuredKeys.get(key)) } : state,
    ])))
  }

  const addCustomField = () => {
    const key = `custom-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const definition: ShippingReportFieldDefinition = {
      key,
      label: "新参数",
      unit: "",
      side: reportType === "spectrum" ? "left" : "single",
    }
    setCustomFields((previous) => [...previous, definition])
    setFields((previous) => ({ ...previous, [key]: { include: true, value: "" } }))
  }

  const updateCustomField = (key: string, patch: Partial<Pick<ShippingReportFieldDefinition, "label" | "unit">>) => {
    setCustomFields((previous) => previous.map((field) => field.key === key ? { ...field, ...patch } : field))
  }

  const removeCustomField = (key: string) => {
    setCustomFields((previous) => previous.filter((field) => field.key !== key))
    setFields((previous) => {
      const next = { ...previous }
      delete next[key]
      return next
    })
  }

  const generate = async () => {
    if (!inspection || busy) return
    setError("")
    try {
      const targetPath = shippingOutputFilePath(outputDirectory, sn)
      if (!targetPath) throw new Error("请选择本地 PDF 保存文件夹")
      setBusy(true)
      const result = await sendBackendRequest<ShippingReportGenerationResult>("shippingReport.generate", {
        sourcePath: inspection.sourcePath,
        outputPath: targetPath,
        reportType,
        reportLanguage,
        productName,
        sn,
        operatingCurrentA,
        fields,
        customFields,
        encircledEnergyNa,
        legacyCompletionConfirmed: true,
        ...(reportType === "spectrum" ? { spectrumAxis: { mode: axisMode, minimum: axisMinimum, maximum: axisMaximum, wavelengthMinimum, wavelengthMaximum } } : {}),
      })
      setLastOutputPath(result.outputPath)
      setResultOpenNotice("")
      setInspection(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const openGeneratedReport = async (revealOnly = false) => {
    if (!lastOutputPath || openingResult) return
    setOpeningResult(true)
    setError("")
    setResultOpenNotice("")
    try {
      if (revealOnly) {
        await revealItemInDir(lastOutputPath)
      } else {
        const outcome = await openResultFile(lastOutputPath, openPath, revealItemInDir)
        if (outcome.status === "revealed") {
          setResultOpenNotice(`无法直接打开 PDF，已在文件夹中定位。${outcome.openError}`)
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setOpeningResult(false)
    }
  }

  useEffect(() => {
    if (!inspection) return
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) setInspection(null)
    }
    window.addEventListener("keydown", handleKey)
    return () => window.removeEventListener("keydown", handleKey)
  }, [inspection, busy])

  useEffect(() => {
    if (!inspection || busy) {
      setPreviewBusy(false)
      return
    }
    let cancelled = false
    const requestId = ++previewRequestRef.current
    setPreviewBusy(true)
    const timer = window.setTimeout(() => {
      if (cancelled) return
      void sendBackendRequest<ShippingReportPreviewResult>("shippingReport.preview", {
        sourcePath: inspection.sourcePath,
        reportType,
        reportLanguage,
        productName,
        sn,
        operatingCurrentA,
        fields,
        customFields,
        encircledEnergyNa,
        legacyCompletionConfirmed: true,
        ...(reportType === "spectrum" ? { spectrumAxis: { mode: axisMode, minimum: axisMinimum, maximum: axisMaximum, wavelengthMinimum, wavelengthMaximum } } : {}),
      })
        .then((result) => {
          if (cancelled || requestId !== previewRequestRef.current) return
          setPreviewPages(result.pages)
          setPreviewError("")
        })
        .catch((reason: unknown) => {
          if (cancelled || requestId !== previewRequestRef.current) return
          setPreviewError(reason instanceof Error ? reason.message : String(reason))
        })
        .finally(() => {
          if (!cancelled && requestId === previewRequestRef.current) setPreviewBusy(false)
        })
    }, 500)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [axisMaximum, axisMinimum, axisMode, busy, customFields, encircledEnergyNa, fields, inspection, operatingCurrentA, productName, reportLanguage, reportType, sn, wavelengthMaximum, wavelengthMinimum])

  const definitions: ShippingReportFieldDefinition[] = inspection ? [...inspection.fieldDefinitions[reportType], ...customFields] : []
  const outputPath = shippingOutputFilePath(outputDirectory, sn)
  return (
    <>
      <div className="rounded-lg border border-border/70 bg-card/40 p-4">
        <div className="mb-3 flex items-center gap-2">
          <Badge variant="outline" className="text-xs bg-primary/10 text-primary border-primary/30">
            步骤 1 · 选择数据源与输出目录
          </Badge>
        </div>
        <div className="grid gap-4">
          <Field label="Excel 测试数据路径">
            <div className="flex min-w-0 gap-2">
              <Input aria-label="Excel 测试数据路径" disabled={disabled || busy} onChange={(event) => { setSourcePath(event.target.value); setError("") }} placeholder="选择自动或手动测试生成的 .xlsx 文件" value={sourcePath} />
              <Button className="shrink-0" disabled={disabled || busy} onClick={() => void chooseSourcePath()} size="sm" type="button" variant="outline"><FolderOpen className="size-3.5" />浏览</Button>
            </div>
          </Field>
          <Field label="本地 PDF 保存文件夹">
            <div className="flex min-w-0 gap-2">
              <Input aria-label="本地 PDF 保存文件夹" disabled={disabled || busy} onChange={(event) => { setOutputDirectory(event.target.value); setError("") }} placeholder="选择 PDF 保存文件夹，文件名将自动生成" value={outputDirectory} />
              <Button className="shrink-0" disabled={disabled || busy} onClick={() => void chooseOutputDirectory()} size="sm" type="button" variant="outline"><FolderOpen className="size-3.5" />选择文件夹</Button>
            </div>
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
          <div className="min-w-0 text-xs text-muted-foreground">
            {error && <p className="font-medium text-destructive">{error}</p>}
            {resultOpenNotice && <p role="status">{resultOpenNotice}</p>}
            {lastOutputPath && (
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="max-w-[520px] truncate" title={lastOutputPath}>已生成：{lastOutputPath}</span>
                <Button disabled={openingResult} onClick={() => void openGeneratedReport()} size="sm" variant="outline">
                  <FileText className="size-3.5" />{openingResult ? "正在打开…" : "打开 PDF"}
                </Button>
                <Button disabled={openingResult} onClick={() => void openGeneratedReport(true)} size="sm" variant="outline">
                  <FolderOpen className="size-3.5" />打开文件夹
                </Button>
              </div>
            )}
            {!error && !lastOutputPath && <p>填写数据路径与输出目录后，点击“读取数据”开始配置报告。</p>}
          </div>
          <Button disabled={disabled || busy} onClick={() => void configureReport()}><FileText className="size-4" />{busy && !inspection ? "正在读取…" : "读取并配置报告"}</Button>
        </div>
      </div>

      {inspection && (
        <section aria-labelledby="shipping-report-title" className="mt-5 rounded-lg border border-border/70 bg-card/40 p-4">
          <div className="mb-4 flex items-start justify-between gap-4">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs bg-primary/10 text-primary border-primary/30">
                步骤 2 · 报告配置与参数勾选
              </Badge>
              {inspection.testMode === "manual" && inspection.singlePoint && (
                <Badge variant="outline" className="text-xs border-emerald-500/30 bg-emerald-500/10 text-emerald-600">
                  手动单点 Excel · {Number.isInteger(inspection.currents[0]) ? inspection.currents[0].toFixed(0) : String(inspection.currents[0])} A
                </Badge>
              )}
              <span className="max-w-md truncate text-xs text-muted-foreground">{inspection.sourcePath}</span>
            </div>
            <button aria-label="收起报告配置" className="rounded-md p-1 text-muted-foreground hover:bg-muted" disabled={busy} onClick={() => setInspection(null)} type="button">
              <X className="size-4" />
            </button>
          </div>

          <div className={SHIPPING_REPORT_SPLIT_LAYOUT}>
            <div className="min-w-0">
              <div className="grid grid-cols-1 gap-4 2xl:grid-cols-3">
                <Field label="报告类型"><NativeSelect value={reportType} onChange={(value) => changeReportType(value as ShippingReportType)}>{inspection.allowedReportTypes.includes("spectrum") && <option value="spectrum">含光谱测试报告</option>}<option value="pole">Pole 无光谱测试报告</option></NativeSelect></Field>
                <Field label="报告语言"><NativeSelect value={reportLanguage} onChange={(value) => setReportLanguage(value as ShippingReportLanguage)}><option value="en">English</option><option value="zh">中文</option></NativeSelect></Field>
                <Field label="工作电流点"><NativeSelect value={String(operatingCurrentA)} onChange={(value) => changeOperatingCurrent(Number(value))}>{inspection.currents.map((current) => <option key={current} value={String(current)}>{Number.isInteger(current) ? current.toFixed(0) : String(current)} A</option>)}</NativeSelect></Field>
                <Field className="2xl:col-span-2" label="产品名称"><Input value={productName} onChange={(event) => changeProductName(event.target.value)} /></Field>
                <Field label="SN"><Input value={sn} onChange={(event) => setSn(event.target.value)} /></Field>
              </div>

              <Separator className="my-5" />

              <div>
                <div className="mb-3 flex items-end justify-between gap-4">
                  <div>
                    <h3 className="text-sm font-semibold">报告判定参数</h3>
                    <p className="mt-0.5 text-xs text-muted-foreground">勾选需要呈现在出货 PDF 中的参数</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <span className="text-xs text-muted-foreground">已选 {Object.values(fields).filter((item) => item.include).length} 项</span>
                    <Button disabled={busy} onClick={addCustomField} size="sm" type="button" variant="outline"><Plus className="size-3.5" />增加参数</Button>
                  </div>
                </div>
                <div className="pr-1">
                  <div className="grid grid-cols-1 gap-x-6 gap-y-3 2xl:grid-cols-2">
                    {definitions.filter((definition) => !customFields.some((field) => field.key === definition.key)).map((definition) => {
                      const state = fields[definition.key] ?? { include: false, value: "" }
                      return (
                        <div className="grid grid-cols-[minmax(150px,0.9fr)_minmax(110px,1fr)_52px] items-center gap-2" key={definition.key}>
                          <ShippingReportFieldSelector
                            checked={state.include}
                            definition={definition}
                            encircledEnergyNa={encircledEnergyNa}
                            onCheckedChange={(include) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, include } }))}
                            onEncircledEnergyNaChange={setEncircledEnergyNa}
                          />
                          <Input aria-label={`${definition.label}数值`} className="h-8" value={state.value} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, value: event.target.value } }))} />
                          <span className="text-xs text-muted-foreground">{definition.unit || "-"}</span>
                        </div>
                      )
                    })}
                    {customFields.map((definition) => {
                      const state = fields[definition.key] ?? { include: true, value: "" }
                      return (
                        <div className="col-span-1 grid grid-cols-[auto_minmax(130px,0.8fr)_minmax(110px,1fr)_minmax(60px,0.35fr)_auto] items-center gap-2 rounded-md border border-dashed border-border bg-muted/20 p-2 2xl:col-span-2" key={definition.key}>
                          <input aria-label={`选择${definition.label}`} checked={state.include} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, include: event.target.checked } }))} type="checkbox" />
                          <Input aria-label="自定义参数名称" className="h-8" placeholder="参数名称" value={definition.label} onChange={(event) => updateCustomField(definition.key, { label: event.target.value })} />
                          <Input aria-label={`${definition.label}数值`} className="h-8" placeholder="参数值" value={state.value} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, value: event.target.value } }))} />
                          <Input aria-label={`${definition.label}单位`} className="h-8" placeholder="单位" value={definition.unit} onChange={(event) => updateCustomField(definition.key, { unit: event.target.value })} />
                          <button aria-label={`删除${definition.label}`} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" onClick={() => removeCustomField(definition.key)} type="button"><X className="size-4" /></button>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </div>

              {reportType === "spectrum" && (
                <>
                  <Separator className="my-5" />
                  <div>
                    <h3 className="text-sm font-semibold">光谱坐标轴配置</h3>
                    <div className="mt-3 grid grid-cols-3 gap-4">
                      <Field label="单位"><NativeSelect disabled value={axisMode} onChange={() => undefined}><option value="counts">原始强度 (counts)</option></NativeSelect></Field>
                      <NumberField label="下限" value={axisMinimum} onChange={setAxisMinimum} step="1" />
                      <NumberField label="上限" value={axisMaximum} onChange={setAxisMaximum} step="1" />
                    </div>
                  </div>
                  <Separator className="my-5" />
                  <div>
                    <h3 className="text-sm font-semibold">光谱横轴</h3>
                    <div className="mt-3 grid grid-cols-2 gap-4">
                      <NumberField label="下限 (nm)" value={wavelengthMinimum} onChange={setWavelengthMinimum} step="0.1" />
                      <NumberField label="上限 (nm)" value={wavelengthMaximum} onChange={setWavelengthMaximum} step="0.1" />
                    </div>
                  </div>
                </>
              )}
              {error && <p className="mt-4 rounded-md bg-destructive/10 px-3 py-2 text-sm font-medium text-destructive">{error}</p>}
            </div>

            <aside aria-labelledby="shipping-report-preview-title" className="min-w-0 border-t border-border pt-5 pr-1 pb-4 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <Badge variant="outline" className="text-xs bg-emerald-500/10 text-emerald-600 border-emerald-500/30">
                    步骤 3 · 实时预览与导出
                  </Badge>
                  <p aria-live="polite" className="mt-1 text-xs text-muted-foreground">{previewBusy ? "正在更新预览…" : previewPages.length ? "与导出的 PDF 使用同一渲染器" : "填写完整后将自动生成预览"}</p>
                </div>
                {previewBusy && <LoaderCircle aria-label="正在更新预览" className="mt-0.5 size-4 shrink-0 animate-spin text-muted-foreground" />}
              </div>
              {previewError && <div className="mt-4 rounded-md border border-amber-300/70 bg-amber-50 px-3 py-2 text-xs text-amber-950"><p className="font-medium">当前输入暂无法预览</p><p className="mt-1 leading-relaxed">{previewError}</p></div>}
              {previewPages.length > 0 ? (
                <div className="mt-4 space-y-5">
                  {previewPages.map((page, index) => (
                    <figure className="overflow-hidden rounded-sm border border-border bg-white shadow-sm" key={`${index}-${page.slice(-24)}`}>
                      <img alt={`出货报告预览，第 ${index + 1} 页`} className="block h-auto w-full" src={`data:image/png;base64,${page}`} />
                      <figcaption className="border-t border-border bg-muted/30 px-3 py-1.5 text-center text-[11px] text-muted-foreground">第 {index + 1} 页</figcaption>
                    </figure>
                  ))}
                </div>
              ) : !previewError && !previewBusy && (
                <div className="mt-4 grid min-h-56 place-items-center border border-dashed border-border bg-muted/20 px-6 text-center text-xs text-muted-foreground">
                  <div>
                    <FileText className="mx-auto mb-2 size-5 opacity-60" />
                    <p>正在准备报告预览</p>
                  </div>
                </div>
              )}
            </aside>
          </div>

          <div className="mt-6 flex items-center justify-between gap-3 border-t border-border pt-4">
            <p className="max-w-[60%] truncate text-xs text-muted-foreground" title={outputPath}>将保存至：{outputPath}</p>
            <div className="flex gap-2">
              <Button disabled={busy} onClick={() => setInspection(null)} variant="outline">取消</Button>
              <Button disabled={busy} onClick={() => void generate()}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}{busy ? "正在生成…" : "生成 PDF"}</Button>
            </div>
          </div>
        </section>
      )}
    </>
  )
}

type BatchGenerationOutcome = {
  status: "generating" | "success" | "skipped" | "failed"
  message: string
  outputPath?: string
}

function BatchShippingReportWorkflow({ disabled }: { disabled: boolean }) {
  const [rootPath, setRootPath] = useState("")
  const [outputDirectory, setOutputDirectory] = useState("")
  const [batch, setBatch] = useState<ShippingReportBatchInspection | null>(null)
  const [representative, setRepresentative] = useState<ShippingWorkbookInspection | null>(null)
  const [reportType, setReportType] = useState<ShippingReportType>("pole")
  const [reportLanguage, setReportLanguage] = useState<ShippingReportLanguage>("en")
  const [productName, setProductName] = useState(getStoredShippingProductName)
  const [operatingCurrentA, setOperatingCurrentA] = useState(Number.NaN)
  const [fields, setFields] = useState<ShippingFieldState>({})
  const [customFields, setCustomFields] = useState<ShippingReportFieldDefinition[]>([])
  const [encircledEnergyNa, setEncircledEnergyNa] = useState(DEFAULT_ENCIRCLED_ENERGY_NA)
  const [axisMode, setAxisMode] = useState<SpectrumAxisMode>("relative_db")
  const [axisMinimum, setAxisMinimum] = useState(-80)
  const [axisMaximum, setAxisMaximum] = useState(0)
  const [wavelengthMinimum, setWavelengthMinimum] = useState(0)
  const [wavelengthMaximum, setWavelengthMaximum] = useState(2000)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [previewPages, setPreviewPages] = useState<string[]>([])
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState("")
  const [outcomes, setOutcomes] = useState<Record<string, BatchGenerationOutcome>>({})
  const [generationProgress, setGenerationProgress] = useState({ completed: 0, total: 0 })
  const [generating, setGenerating] = useState(false)
  const previewRequestRef = useRef(0)

  const initializeFields = useCallback((inspection: ShippingWorkbookInspection, nextType: ShippingReportType, currentA: number) => {
    const point = Number.isFinite(currentA)
      ? inspection.points.find((item) => Math.abs(item.currentA - currentA) < 1e-9)
      : undefined
    const preference = inspection.preferences[nextType] ?? { selectedFields: [], manualValues: {}, customFields: [] }
    const selected = new Set(preference.selectedFields)
    const nextCustomFields = preference.customFields ?? []
    const definitions = [...inspection.fieldDefinitions[nextType], ...nextCustomFields]
    setFields(Object.fromEntries(definitions.map((definition) => [
      definition.key,
      {
        include: selected.has(definition.key),
        value: definition.measuredKey
          ? shippingSuggestedValue(point, definition.measuredKey)
          : preference.manualValues[definition.key] ?? "",
      },
    ])))
    setCustomFields(nextCustomFields)
    setReportLanguage(preference.reportLanguage ?? "en")
    if (nextType === "spectrum") {
      setEncircledEnergyNa(preference.encircledEnergyNa ?? DEFAULT_ENCIRCLED_ENERGY_NA)
      const axis = preferredSpectrumAxis(inspection, currentA)
      setAxisMode(axis.mode)
      setAxisMinimum(axis.minimum)
      setAxisMaximum(axis.maximum)
      setWavelengthMinimum(axis.wavelengthMinimum)
      setWavelengthMaximum(axis.wavelengthMaximum)
    }
  }, [])

  const updateMeasuredFields = useCallback((inspection: ShippingWorkbookInspection, nextType: ShippingReportType, currentA: number) => {
    const point = inspection.points.find((item) => Math.abs(item.currentA - currentA) < 1e-9)
    const measuredKeys = new Map(inspection.fieldDefinitions[nextType].filter((item) => item.measuredKey).map((item) => [item.key, item.measuredKey]))
    setFields((previous) => Object.fromEntries(Object.entries(previous).map(([key, state]) => [
      key,
      measuredKeys.has(key) ? { ...state, value: shippingSuggestedValue(point, measuredKeys.get(key)) } : state,
    ])))
  }, [])

  const inspectRepresentative = useCallback(async (item: ShippingReportBatchItem, nextType: ShippingReportType, currentA: number, resetFields: boolean) => {
    const inspection = await sendBackendRequest<ShippingWorkbookInspection>("shippingReport.inspect", { path: item.sourcePath })
    setRepresentative(inspection)
    if (resetFields) initializeFields(inspection, nextType, currentA)
    else if (Number.isFinite(currentA)) updateMeasuredFields(inspection, nextType, currentA)
    return inspection
  }, [initializeFields, updateMeasuredFields])

  const chooseRootPath = async () => {
    if (disabled || busy) return
    try {
      const selected = await openDialog({ multiple: false, directory: true, title: "选择 Excel 批量根目录" })
      if (typeof selected !== "string") return
      setRootPath(selected)
      setOutputDirectory((current) => current.trim() || selected)
      setBatch(null)
      setRepresentative(null)
      setOutcomes({})
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const chooseOutputDirectory = async () => {
    if (disabled || busy) return
    try {
      const selected = await openDialog({ multiple: false, directory: true, title: "选择批量 PDF 保存文件夹" })
      if (typeof selected === "string") {
        setOutputDirectory(selected)
        setError("")
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const openOutputDirectory = async () => {
    const directory = outputDirectory.trim()
    if (!directory) {
      setError("请选择批量 PDF 保存文件夹")
      return
    }
    try {
      await invoke("open_directory", { path: directory })
      setError("")
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : String(reason)
      setError(`无法打开输出文件夹：${detail}`)
    }
  }

  const scan = async () => {
    const nextRoot = rootPath.trim()
    if (!nextRoot || busy) {
      if (!nextRoot) setError("请选择 Excel 批量根目录")
      return
    }
    setBusy(true)
    setError("")
    setOutcomes({})
    setGenerationProgress({ completed: 0, total: 0 })
    setPreviewPages([])
    setPreviewError("")
    try {
      const result = await sendBackendRequest<ShippingReportBatchInspection>("shippingReport.batchInspect", { rootPath: nextRoot })
      setBatch(result)
      const ready = result.items.filter((item) => item.status === "ready")
      if (!ready.length) {
        setRepresentative(null)
        setError(result.items.length ? "未找到可生成报告的 Excel，请查看预检结果。" : "所选目录中没有可处理的 .xlsx 文件。")
        return
      }
      const nextType: ShippingReportType = result.allReadySupportSpectrum ? "spectrum" : "pole"
      const currentA = result.commonCurrents.length ? result.commonCurrents[result.commonCurrents.length - 1] : Number.NaN
      const item = ready.find((candidate) => candidate.allowedReportTypes.includes(nextType) && (!Number.isFinite(currentA) || candidate.currents.includes(currentA))) ?? ready[0]
      const inspection = await inspectRepresentative(item, nextType, currentA, true)
      setReportType(nextType)
      setOperatingCurrentA(currentA)
      setProductName((current) => current.trim() || inspection.productName)
      if (!Number.isFinite(currentA)) setError("这些 Excel 没有共同电流点，请从全部候选电流中统一选择一个；缺少该测试点的文件会被跳过。")
    } catch (reason) {
      setBatch(null)
      setRepresentative(null)
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const changeProductName = (value: string) => {
    setProductName(value)
    storeShippingProductName(value)
    setOutcomes({})
  }

  const changeOperatingCurrent = async (currentA: number) => {
    if (!batch || busy) return
    setOperatingCurrentA(currentA)
    setOutcomes({})
    const item = batch.items.find((candidate) => candidate.status === "ready" && candidate.currents.includes(currentA) && candidate.allowedReportTypes.includes(reportType))
    if (!item) return
    try {
      setBusy(true)
      await inspectRepresentative(item, reportType, currentA, false)
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const changeReportType = async (nextType: ShippingReportType) => {
    if (!batch || busy) return
    setReportType(nextType)
    setOutcomes({})
    const item = batch.items.find((candidate) => candidate.status === "ready" && candidate.allowedReportTypes.includes(nextType) && (!Number.isFinite(operatingCurrentA) || candidate.currents.includes(operatingCurrentA)))
    if (!item) return
    try {
      setBusy(true)
      await inspectRepresentative(item, nextType, operatingCurrentA, true)
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const selectRepresentative = async (item: ShippingReportBatchItem) => {
    if (busy || item.status !== "ready" || !Number.isFinite(operatingCurrentA) || !item.currents.includes(operatingCurrentA) || !item.allowedReportTypes.includes(reportType)) return
    try {
      setBusy(true)
      await inspectRepresentative(item, reportType, operatingCurrentA, false)
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const addCustomField = () => {
    const key = `custom-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    setCustomFields((previous) => [...previous, { key, label: "新参数", unit: "", side: reportType === "spectrum" ? "left" : "single" }])
    setFields((previous) => ({ ...previous, [key]: { include: true, value: "" } }))
  }

  const updateCustomField = (key: string, patch: Partial<Pick<ShippingReportFieldDefinition, "label" | "unit">>) => {
    setCustomFields((previous) => previous.map((field) => field.key === key ? { ...field, ...patch } : field))
  }

  const removeCustomField = (key: string) => {
    setCustomFields((previous) => previous.filter((field) => field.key !== key))
    setFields((previous) => {
      const next = { ...previous }
      delete next[key]
      return next
    })
  }

  const itemSkipReason = useCallback((item: ShippingReportBatchItem): string => {
    if (item.status !== "ready") return item.message
    if (!Number.isFinite(operatingCurrentA)) return "尚未选择统一工作电流"
    if (!item.currents.includes(operatingCurrentA)) return `缺少 ${Number.isInteger(operatingCurrentA) ? operatingCurrentA.toFixed(0) : operatingCurrentA} A 测试点`
    if (!item.allowedReportTypes.includes(reportType)) return "光谱数据不完整，不支持当前报告类型"
    return ""
  }, [operatingCurrentA, reportType])

  const generateBatch = async () => {
    if (!batch || busy) return
    if (!outputDirectory.trim()) {
      setError("请选择批量 PDF 保存文件夹")
      return
    }
    if (!productName.trim()) {
      setError("产品名称不能为空")
      return
    }
    if (!Number.isFinite(operatingCurrentA)) {
      setError("请选择统一工作电流")
      return
    }
    const readyItems = batch.items.filter((item) => !itemSkipReason(item))
    if (!readyItems.length) {
      setError("当前设置下没有可生成的 Excel")
      return
    }
    const initialOutcomes = Object.fromEntries(batch.items.filter((item) => itemSkipReason(item)).map((item) => [item.sourcePath, { status: "skipped" as const, message: itemSkipReason(item) }]))
    setOutcomes(initialOutcomes)
    setGenerationProgress({ completed: 0, total: readyItems.length })
    setGenerating(true)
    setBusy(true)
    setError("")
    let completed = 0
    for (const item of readyItems) {
      setOutcomes((previous) => ({ ...previous, [item.sourcePath]: { status: "generating", message: "正在生成…" } }))
      try {
        const result = await sendBackendRequest<ShippingReportBatchGenerationResult>("shippingReport.batchGenerateItem", {
          sourcePath: item.sourcePath,
          outputDirectory,
          reportType,
          reportLanguage,
          productName,
          operatingCurrentA,
          fields,
          customFields,
          encircledEnergyNa,
          legacyCompletionConfirmed: true,
          ...(reportType === "spectrum" ? { spectrumAxis: { mode: axisMode, minimum: axisMinimum, maximum: axisMaximum, wavelengthMinimum, wavelengthMaximum } } : {}),
        })
        setOutcomes((previous) => ({ ...previous, [item.sourcePath]: { status: "success", message: "已生成", outputPath: result.outputPath } }))
      } catch (reason) {
        setOutcomes((previous) => ({ ...previous, [item.sourcePath]: { status: "failed", message: reason instanceof Error ? reason.message : String(reason) } }))
      }
      completed += 1
      setGenerationProgress({ completed, total: readyItems.length })
    }
    setGenerating(false)
    setBusy(false)
  }

  useEffect(() => {
    if (!representative || busy || !Number.isFinite(operatingCurrentA)) {
      setPreviewBusy(false)
      return
    }
    if (!representative.currents.some((current) => Math.abs(current - operatingCurrentA) < 1e-9) || !representative.allowedReportTypes.includes(reportType)) return
    let cancelled = false
    const requestId = ++previewRequestRef.current
    setPreviewBusy(true)
    const timer = window.setTimeout(() => {
      void sendBackendRequest<ShippingReportPreviewResult>("shippingReport.preview", {
        sourcePath: representative.sourcePath,
        reportType,
        reportLanguage,
        productName,
        sn: representative.sn,
        operatingCurrentA,
        fields,
        customFields,
        encircledEnergyNa,
        legacyCompletionConfirmed: true,
        ...(reportType === "spectrum" ? { spectrumAxis: { mode: axisMode, minimum: axisMinimum, maximum: axisMaximum, wavelengthMinimum, wavelengthMaximum } } : {}),
      }).then((result) => {
        if (cancelled || requestId !== previewRequestRef.current) return
        setPreviewPages(result.pages)
        setPreviewError("")
      }).catch((reason: unknown) => {
        if (cancelled || requestId !== previewRequestRef.current) return
        setPreviewError(reason instanceof Error ? reason.message : String(reason))
      }).finally(() => {
        if (!cancelled && requestId === previewRequestRef.current) setPreviewBusy(false)
      })
    }, 500)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [axisMaximum, axisMinimum, axisMode, busy, customFields, encircledEnergyNa, fields, operatingCurrentA, productName, reportLanguage, reportType, representative, wavelengthMaximum, wavelengthMinimum])

  const reportTypes: ShippingReportType[] = batch?.items.some((item) => item.status === "ready" && item.allowedReportTypes.includes("spectrum")) ? ["spectrum", "pole"] : ["pole"]
  const definitions = representative ? [...representative.fieldDefinitions[reportType], ...customFields] : []
  const resultValues = Object.values(outcomes)
  const successCount = resultValues.filter((item) => item.status === "success").length
  const skippedCount = resultValues.filter((item) => item.status === "skipped").length
  const failedCount = resultValues.filter((item) => item.status === "failed").length
  const productMismatch = (batch?.productNames.length ?? 0) > 1

  return <>
    <div className="grid gap-4">
      <Field label="Excel 批量根目录"><div className="flex min-w-0 gap-2"><Input aria-label="Excel 批量根目录" disabled={disabled || busy} onChange={(event) => { setRootPath(event.target.value); setBatch(null); setError("") }} placeholder="递归扫描目录中的 .xlsx 文件" value={rootPath} /><Button className="shrink-0" disabled={disabled || busy} onClick={() => void chooseRootPath()} size="sm" type="button" variant="outline"><FolderOpen className="size-3.5" />浏览</Button></div></Field>
      <Field label="本地 PDF 保存文件夹"><div className="flex min-w-0 gap-2"><Input aria-label="批量 PDF 保存文件夹" disabled={disabled || busy} onChange={(event) => { setOutputDirectory(event.target.value); setError("") }} placeholder="已有同名文件时自动添加序号" value={outputDirectory} /><Button className="shrink-0" disabled={disabled || busy} onClick={() => void chooseOutputDirectory()} size="sm" type="button" variant="outline"><FolderOpen className="size-3.5" />选择文件夹</Button></div></Field>
    </div>
    <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4"><div className="text-xs text-muted-foreground">{error || "扫描后将按 Excel 内 SN 去重，并选择修改时间最新的测试文件。"}</div><Button disabled={disabled || busy} onClick={() => void scan()}><RefreshCw className={`size-4 ${busy && !batch ? "animate-spin" : ""}`} />{busy && !batch ? "正在扫描…" : "扫描并预检"}</Button></div>
    {batch && <>
      <section className="mt-6 border-t border-border pt-6">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-base font-semibold">批量预检</h2><p className="mt-1 text-xs text-muted-foreground">共发现 {batch.items.length} 个 Excel；点击可生成的行可切换预览样本。</p></div><div className="flex gap-2 text-xs"><Badge variant="outline">可候选 {batch.items.filter((item) => item.status === "ready").length}</Badge><Badge variant="outline">已忽略 {batch.items.filter((item) => item.status === "ignored_duplicate").length}</Badge><Badge variant="outline">无效 {batch.items.filter((item) => item.status === "invalid").length}</Badge></div></div>
        <div className="max-h-72 overflow-auto rounded-md border border-border"><table className="w-full min-w-[820px] border-collapse text-left text-xs"><thead className="sticky top-0 bg-muted"><tr><th className="px-3 py-2 font-medium">SN</th><th className="px-3 py-2 font-medium">Excel</th><th className="px-3 py-2 font-medium">修改时间</th><th className="px-3 py-2 font-medium">状态</th><th className="px-3 py-2 font-medium">结果</th></tr></thead><tbody>{batch.items.map((item) => { const reason = itemSkipReason(item); const outcome = outcomes[item.sourcePath]; const canPreview = item.status === "ready" && !reason; const activeRow = representative?.sourcePath === item.sourcePath; return <tr className={`border-t border-border ${canPreview ? "cursor-pointer hover:bg-muted/50" : "text-muted-foreground"} ${activeRow ? "bg-muted/60" : ""}`} key={item.sourcePath} onClick={() => canPreview && void selectRepresentative(item)}><td className="whitespace-nowrap px-3 py-2 font-medium">{item.sn || "-"}</td><td className="max-w-[360px] truncate px-3 py-2" title={item.sourcePath}>{item.sourcePath}</td><td className="whitespace-nowrap px-3 py-2">{new Date(item.modifiedAtMs).toLocaleString()}</td><td className="px-3 py-2"><span className={reason ? "text-amber-600" : "text-emerald-600"}>{reason || "可生成"}</span></td><td className="max-w-[300px] px-3 py-2">{outcome?.outputPath ? <button className="max-w-[280px] truncate text-primary underline-offset-2 hover:underline" onClick={(event) => { event.stopPropagation(); void openPath(outcome.outputPath!) }} title={outcome.outputPath}>{outcome.message}：{outcome.outputPath}</button> : outcome?.message ?? "-"}</td></tr>})}</tbody></table></div>
      </section>
      {representative && <section className="mt-6 border-t border-border pt-6">
        <div className="mb-5"><h2 className="text-base font-semibold">统一报告配置</h2><p className="mt-1 max-w-3xl truncate text-xs text-muted-foreground">预览样本：{representative.sourcePath}</p></div>
        {productMismatch && <p className="mb-4 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-950">Excel 中存在多个产品名称，本次统一使用下方填写的产品名称。</p>}
        <div className={SHIPPING_REPORT_SPLIT_LAYOUT}><div className="min-w-0">
          <div className="grid grid-cols-1 gap-4 2xl:grid-cols-3"><Field label="报告类型"><NativeSelect disabled={busy} value={reportType} onChange={(value) => void changeReportType(value as ShippingReportType)}>{reportTypes.includes("spectrum") && <option value="spectrum">含光谱测试报告</option>}<option value="pole">Pole 无光谱测试报告</option></NativeSelect></Field><Field label="报告语言"><NativeSelect disabled={busy} value={reportLanguage} onChange={(value) => setReportLanguage(value as ShippingReportLanguage)}><option value="en">English</option><option value="zh">中文</option></NativeSelect></Field><Field label="统一工作电流"><NativeSelect disabled={busy} value={Number.isFinite(operatingCurrentA) ? String(operatingCurrentA) : ""} onChange={(value) => void changeOperatingCurrent(Number(value))}><option disabled value="">请选择工作电流</option>{batch.unionCurrents.map((current) => <option key={current} value={String(current)}>{Number.isInteger(current) ? current.toFixed(0) : String(current)} A{batch.commonCurrents.includes(current) ? " · 全部共有" : ""}</option>)}</NativeSelect></Field><Field className="2xl:col-span-3" label="统一产品名称"><Input disabled={busy} value={productName} onChange={(event) => changeProductName(event.target.value)} /></Field></div>
          <Separator className="my-5" />
          <div><div className="mb-3 flex items-end justify-between gap-4"><div><h3 className="text-sm font-semibold">报告参数</h3><p className="mt-1 text-xs text-muted-foreground">测量参数会按每份 Excel 重新取值；手工参数统一应用。</p></div><div className="flex shrink-0 items-center gap-3"><span className="text-xs text-muted-foreground">已选 {Object.values(fields).filter((item) => item.include).length} 项</span><Button disabled={busy} onClick={addCustomField} size="sm" type="button" variant="outline"><Plus className="size-3.5" />手动增加参数</Button></div></div><div className="pr-1"><div className="grid grid-cols-1 gap-x-6 gap-y-3 2xl:grid-cols-2">{definitions.filter((definition) => !customFields.some((field) => field.key === definition.key)).map((definition) => { const state = fields[definition.key] ?? { include: false, value: "" }; return <div className="grid grid-cols-[minmax(150px,0.9fr)_minmax(110px,1fr)_52px] items-center gap-2" key={definition.key}><ShippingReportFieldSelector checked={state.include} definition={definition} disabled={busy} encircledEnergyNa={encircledEnergyNa} onCheckedChange={(include) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, include } }))} onEncircledEnergyNaChange={setEncircledEnergyNa} /><Input aria-label={`${definition.label}数值`} className="h-8" disabled={busy || Boolean(definition.measuredKey)} value={state.value} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, value: event.target.value } }))} /><span className="text-xs text-muted-foreground">{definition.unit || "-"}</span></div>})}{customFields.map((definition) => { const state = fields[definition.key] ?? { include: true, value: "" }; return <div className="col-span-1 grid grid-cols-[auto_minmax(130px,0.8fr)_minmax(110px,1fr)_minmax(60px,0.35fr)_auto] items-center gap-2 rounded-md border border-dashed border-border bg-muted/20 p-2 2xl:col-span-2" key={definition.key}><input checked={state.include} disabled={busy} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, include: event.target.checked } }))} type="checkbox" /><Input className="h-8" disabled={busy} placeholder="参数名称" value={definition.label} onChange={(event) => updateCustomField(definition.key, { label: event.target.value })} /><Input className="h-8" disabled={busy} placeholder="参数值" value={state.value} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, value: event.target.value } }))} /><Input className="h-8" disabled={busy} placeholder="单位" value={definition.unit} onChange={(event) => updateCustomField(definition.key, { unit: event.target.value })} /><button aria-label={`删除${definition.label}`} disabled={busy} onClick={() => removeCustomField(definition.key)} type="button"><X className="size-4" /></button></div>})}</div></div></div>
          {reportType === "spectrum" && <><Separator className="my-5" /><div><h3 className="text-sm font-semibold">光谱纵轴</h3><div className="mt-3 grid grid-cols-3 gap-4"><Field label="单位"><NativeSelect disabled value={axisMode} onChange={() => undefined}><option value="counts">原始强度 (counts)</option></NativeSelect></Field><NumberField disabled={busy} label="下限" value={axisMinimum} onChange={setAxisMinimum} /><NumberField disabled={busy} label="上限" value={axisMaximum} onChange={setAxisMaximum} /></div></div><Separator className="my-5" /><div><h3 className="text-sm font-semibold">光谱横轴</h3><div className="mt-3 grid grid-cols-2 gap-4"><NumberField disabled={busy} label="下限 (nm)" value={wavelengthMinimum} onChange={setWavelengthMinimum} step="0.1" /><NumberField disabled={busy} label="上限 (nm)" value={wavelengthMaximum} onChange={setWavelengthMaximum} step="0.1" /></div></div></>}
        </div><aside className="min-w-0 border-t border-border pt-5 pr-1 pb-4 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0"><div className="flex items-center justify-between"><div><h3 className="text-sm font-semibold">代表文件预览</h3><p className="mt-1 text-xs text-muted-foreground">SN：{representative.sn}</p></div>{previewBusy && <LoaderCircle className="size-4 animate-spin text-muted-foreground" />}</div>{previewError && <p className="mt-4 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-950">{previewError}</p>}{previewPages.length ? <div className="mt-4 space-y-5">{previewPages.map((page, index) => <img alt={`批量出货报告代表预览，第 ${index + 1} 页`} className="w-full border border-border bg-white" key={`${index}-${page.slice(-24)}`} src={`data:image/png;base64,${page}`} />)}</div> : !previewBusy && !previewError && <div className="mt-4 grid min-h-56 place-items-center border border-dashed border-border text-xs text-muted-foreground">等待有效配置</div>}</aside></div>
        <div className="mt-6 border-t border-border pt-4">{generationProgress.total > 0 && <div className="mb-4"><div className="mb-2 flex justify-between text-xs text-muted-foreground"><span>批量生成进度</span><span>{generationProgress.completed} / {generationProgress.total}</span></div><Progress value={generationProgress.total ? generationProgress.completed / generationProgress.total * 100 : 0} /></div>}<div className="flex flex-wrap items-center justify-between gap-3"><div className="text-xs text-muted-foreground">{resultValues.length ? `成功 ${successCount}，跳过 ${skippedCount}，失败 ${failedCount}` : `输出目录：${outputDirectory || "未选择"}`}</div><div className="flex gap-2">{resultValues.length > 0 && <Button disabled={!outputDirectory.trim()} onClick={() => void openOutputDirectory()} variant="outline"><FolderOpen className="size-4" />打开输出文件夹</Button>}<Button disabled={disabled || busy} onClick={() => void generateBatch()}>{generating ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}{generating ? "正在批量生成…" : "批量生成 PDF"}</Button></div></div></div>
      </section>}
    </>}
  </>
}

const AutomaticPage = memo(function AutomaticPage({
  snapshot,
  config,
  update,
  active,
  controlsLocked,
  curveLineStyle,
  latestPointMarkerStyle,
  pending,
  run,
  saveConfiguration,
  testScripts,
  onScriptSelect,
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
  controlsLocked: boolean
  curveLineStyle: CurveLineStyle
  latestPointMarkerStyle: LatestPointMarkerStyle
  pending: boolean
  run: RunCommand
  saveConfiguration: () => Promise<void>
  testScripts: TestScript[]
  onScriptSelect: (scriptId: string) => void
  onNextDevice: () => void
  onViewData: () => void
  onRetryExport: () => void
  onChooseExportDirectory: () => void
  onShowCompletion: () => void
  onNotify?: (notice: { level?: "error" | "info" | "warning"; title: string; message: string; durationMs?: number }) => void
}) {
  const auto = snapshot?.automaticTest
  const completed = auto?.state === "completed" && Boolean(auto.terminalOutcome)
  const availableTestScripts = testScripts.filter((script) => (
    !script.powerSupplyKinds || script.powerSupplyKinds.includes(config.powerSupplyKind)
  ))
  const [openSettings, setOpenSettings] = useState<DeviceSettingsKind | null>(null)
  const [specificationsOpen, setSpecificationsOpen] = useState(false)
  const closeSettings = () => setOpenSettings(null)
  useEffect(() => {
    if (controlsLocked) setOpenSettings(null)
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
  const appliedPowerMeterConfig = snapshot?.configuration ?? config
  const powerMeterProfileLabel = appliedPowerMeterConfig.powerMeterProfileName
    ? `${appliedPowerMeterConfig.powerMeterProfileName}${appliedPowerMeterConfig.powerMeterProfileVersion ? ` · v${appliedPowerMeterConfig.powerMeterProfileVersion}` : ""}`
    : "未绑定方案"
  const powerMeterSummary = `${appliedPowerMeterConfig.powerMeterResource ? powerMeterResourceLabel(appliedPowerMeterConfig.powerMeterResource, snapshot?.devices.powerMeter?.resources) : "未选串口"} · ${powerMeterProfileLabel} · ${appliedPowerMeterConfig.powerMeterWavelengthNm} nm (×${appliedPowerMeterConfig.softwareGain})`
  const powerMeterVisibleSummary = `${powerMeterProfileLabel} / ×${formatTrimmedNumber(appliedPowerMeterConfig.softwareGain, 6)} · ${formatTrimmedNumber(appliedPowerMeterConfig.powerMeterWavelengthNm, 3)} nm`
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
    <>
      {completed && snapshot ? <AutomaticCompletionSummary snapshot={snapshot} config={config} active={active} pending={pending} onNextDevice={onNextDevice} onViewData={onViewData} onRetryExport={onRetryExport} onChooseExportDirectory={onChooseExportDirectory} onShowDetails={onShowCompletion} /> :
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
        <div className="automatic-setup-field--large-type space-y-1.5 min-w-0">
          <Label className="text-[13px] font-medium text-muted-foreground">
            <FileText className="mr-1 inline size-3.5" />脚本选择
          </Label>
          <NativeSelect disabled={controlsLocked || pending} value={config.selectedScriptId} onChange={onScriptSelect}>
            {availableTestScripts.map((script) => <option key={script.id} value={script.id}>{script.name}</option>)}
          </NativeSelect>
        </div>
        <div className="space-y-1.5 min-w-0">
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
      </div>}

      <section className="automatic-device-rail" aria-label="测试设备与控制">
        <DeviceCard title="电源" icon={<Power className="size-4" />} device={snapshot?.devices.powerSupply} model={config.powerSupplyKind === "tdk" ? "TDK" : "CH341"} details={[["接口", config.powerSupplyKind === "tdk" ? config.tdkResource : config.ch341SelectedLocationPath], ["电流计划", `${config.initialCurrentA} → ${config.targetCurrentA} A`], ["目标电压", `${config.tdkVoltageV} V`]]} disabled={controlsLocked} onOpenSettings={() => setOpenSettings("powerSupply")} summary={powerSupplySummary} />
        <DeviceCard title="功率计" icon={<Gauge className="size-4" />} device={snapshot?.devices.powerMeter} model={appliedPowerMeterConfig.powerMeterResource ? powerMeterResourceLabel(appliedPowerMeterConfig.powerMeterResource, powerMeter?.resources).split("|")[0].trim().replace(/功率计$/, "") : "未选择"} details={[["接口", powerMeterResourceValue(appliedPowerMeterConfig.powerMeterResource)], ["设备", powerMeter?.detail || "未连接"], ["系数方案", powerMeterProfileLabel], ["校准波长", `${appliedPowerMeterConfig.powerMeterWavelengthNm} nm`], ["功率系数", `× ${appliedPowerMeterConfig.softwareGain}`]]} disabled={controlsLocked} onOpenSettings={() => setOpenSettings("powerMeter")} summary={powerMeterSummary} visibleSummary={powerMeterVisibleSummary} />
        <DeviceCard title="光谱仪" icon={<Radio className="size-4" />} device={snapshot?.devices.spectrometer} model="Ocean Insight" details={[["设备", config.spectrometerResource || spectrometer?.detail || "未选择"], ["积分时间", `${config.integrationTimeUs} μs`], ["PIB 范围", `${config.pibCenterWavelengthNm} ± ${config.pibHalfRangeNm} nm`]]} disabled={controlsLocked} onOpenSettings={() => setOpenSettings("spectrometer")} summary={spectrometerSummary} />
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
                valueTone="warning"
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
      {openSettings && <DeviceSettingsDialog active={active} config={config} kind={openSettings} onClose={closeSettings} onSave={saveConfiguration} open pending={pending} run={run} snapshot={snapshot} update={update} />}
      {specificationsOpen && <AutomaticSpecificationsDialog active={active} config={config} onClose={() => setSpecificationsOpen(false)} onSave={saveConfiguration} pending={pending} update={update} />}
    </>
  )
}, (previous, next) => (
  previous.active === next.active
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

const ShippingReportPage = memo(function ShippingReportPage({ active, controlsLocked, pending }: { active: boolean; controlsLocked: boolean; pending: boolean }) {
  const [mode, setMode] = useState<"single" | "batch">("single")
  const disabled = !active || controlsLocked || pending
  return (
    <Card className="mb-6 shrink-0 shadow-none">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-4"><div><CardTitle className="text-base">出货报告</CardTitle><CardDescription>{mode === "single" ? "填写测试 Excel 和本地 PDF 保存文件夹，再配置报告参数并导出。" : "递归扫描同一批次 Excel，一次配置并逐份生成出货报告。"}</CardDescription></div><div className="inline-flex rounded-md border border-border bg-muted/30 p-1"><button className={`rounded px-3 py-1.5 text-xs font-medium ${mode === "single" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`} disabled={disabled} onClick={() => setMode("single")} type="button">单个生成</button><button className={`rounded px-3 py-1.5 text-xs font-medium ${mode === "batch" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`} disabled={disabled} onClick={() => setMode("batch")} type="button">批量生成</button></div></div>
      </CardHeader>
      <CardContent>
        {mode === "single" ? <ShippingReportWorkflow disabled={disabled} /> : <BatchShippingReportWorkflow disabled={disabled} />}
      </CardContent>
    </Card>
  )
}, (previous, next) => (
  previous.active === next.active
  && previous.controlsLocked === next.controlsLocked
  && previous.pending === next.pending
))

function NumberInput({
  value,
  onChange,
  disabled = false,
  min,
  max,
  step = "1",
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
  className?: string
  "aria-label"?: string
  id?: string
}) {
  const [text, setText] = useState(() => (Number.isFinite(value) ? String(value) : ""))
  const [isFocused, setIsFocused] = useState(false)

  useEffect(() => {
    if (!isFocused) {
      setText(Number.isFinite(value) ? String(value) : "")
    } else if (Number.isFinite(value) && Number(text) !== value && text !== "" && text !== "-" && !text.endsWith(".")) {
      setText(String(value))
    }
  }, [value, isFocused])

  const numMin = min !== undefined ? Number(min) : undefined
  const numMax = max !== undefined ? Number(max) : undefined

  const handleChange = (rawValue: string) => {
    const normalized = normalizeNumericInput(rawValue)
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
    event.currentTarget.select()
  }

  const doStep = (direction: 1 | -1) => {
    if (disabled) return
    const nextNum = stepNumericValue(text, step, direction, numMin, numMax)
    setText(String(nextNum))
    onChange(nextNum)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowUp") {
      event.preventDefault()
      doStep(1)
    } else if (event.key === "ArrowDown") {
      event.preventDefault()
      doStep(-1)
    }
  }

  return (
    <div className="relative flex w-full min-w-0 items-center">
      <Input
        aria-label={ariaLabel}
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

function NumberField({
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

const ManualPage = memo(function ManualPage({
  snapshot,
  config,
  update,
  active,
  pending,
  run,
}: {
  snapshot: BackendSnapshot | null
  config: AppConfiguration
  update: UpdateConfig
  active: boolean
  pending: boolean
  run: RunCommand
}) {
  const psu = snapshot?.devices.powerSupply
  const meter = snapshot?.devices.powerMeter
  const spectrum = snapshot?.devices.spectrometer
  const powerSupplyResources = psu?.resources ?? []
  const selectedPowerSupplyResource = config.tdkResource.trim()
  const powerMeterResources = meter?.resources ?? []
  const selectedPowerMeterResource = config.powerMeterResource.trim()
  const selectedPowerMeterResourceAvailable = powerMeterResources.some((item) => powerMeterResourceValue(item) === selectedPowerMeterResource)
  const displayedPowerMeterResource = selectedPowerMeterResourceAvailable ? selectedPowerMeterResource : ""
  const selectPowerMeterResource = (resource: string) => {
    update("powerMeterResource", resource)
  }
  const [profileDialogOpen, setProfileDialogOpen] = useState(false)
  const powerMeterLibrary = powerMeterCoefficientLibraryForResource(
    meter,
    config.powerMeterResource,
    snapshot?.configuration?.powerMeterResource,
  )
  const powerMeterIdentity = powerMeterIdentityForResource(
    meter,
    config.powerMeterResource,
    snapshot?.configuration?.powerMeterResource,
    { driverKind: config.powerMeterDriverKind, serialNumber: config.powerMeterSerialNumber },
  )
  const selectedRevision = selectedPowerMeterRevision(powerMeterLibrary)
  const softwareGain = selectedRevision?.softwareGain ?? config.softwareGain
  const wavelengthNm = selectedRevision?.wavelengthNm ?? config.powerMeterWavelengthNm
  const spectrometerResources = spectrum?.resources ?? []
  const selectedSpectrometerResourceAvailable = spectrometerResources.includes(config.spectrometerResource)
  const displayedSpectrometerResource = selectedSpectrometerResourceAvailable ? config.spectrometerResource : ""
  const manualControlsAllowed = snapshot?.safety.manualControlsAllowed === true
  const commandLockRef = useRef(false)
  const runManualCommand: RunCommand = async (method, params, sync) => {
    if (!manualControlsAllowed || commandLockRef.current || pending) return
    commandLockRef.current = true
    try {
      return await run(method, params, sync)
    } finally {
      commandLockRef.current = false
    }
  }
  const isPsuActive = Boolean(psu?.connected || psu?.outputEnabled)
  const isMeterActive = Boolean(meter?.running)
  const isSpectrumActive = Boolean(spectrum?.running)
  const manualTest = snapshot?.manualTest
  const pendingManualPointCount = manualTest?.pendingCount ?? 0
  const manualSaveReady = Boolean(
    active
    && manualControlsAllowed
    && !pending
    && pendingManualPointCount > 0
    && config.sn.trim()
    && config.station.trim()
    && config.outputDir.trim()
  )
  const chooseOutputDir = async () => {
    if (!active || !manualControlsAllowed || pending) return
    try {
      const selected = await openDialog({
        multiple: false,
        directory: true,
        title: "选择手动测试数据输出目录",
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
    <div className="manual-page flex min-h-0 flex-1 flex-col gap-3">
      <section className="grid shrink-0 grid-cols-1 items-end gap-3 rounded-lg border border-border/70 bg-card/40 p-3 md:grid-cols-2 xl:grid-cols-[minmax(170px,0.75fr)_minmax(170px,0.75fr)_minmax(300px,1.5fr)_minmax(180px,auto)]">
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground" htmlFor="manual-sn">
            <QrCode className="mr-1 inline size-3.5" />产品 SN
          </Label>
          <Input
            className="h-9 rounded-lg font-mono text-[18px] leading-none"
            disabled={!active || !manualControlsAllowed || pending}
            id="manual-sn"
            onChange={(event) => update("sn", event.target.value)}
            placeholder="例如 HAM540001"
            value={config.sn}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground" htmlFor="manual-station">
            <Milestone className="mr-1 inline size-3.5" />测试站别
          </Label>
          <Input
            className="h-9 rounded-lg text-[18px] leading-none"
            disabled={!active || !manualControlsAllowed || pending}
            id="manual-station"
            onChange={(event) => update("station", event.target.value)}
            placeholder="例如 老化"
            value={config.station}
          />
        </div>
        <div className="min-w-0 space-y-1">
          <Label className="text-xs text-muted-foreground" htmlFor="manual-output-directory">
            <FolderOpen className="mr-1 inline size-3.5" />输出目录
          </Label>
          <div className="flex items-center gap-1.5">
            <Input
              className="h-9 min-w-0 flex-1 rounded-lg text-[18px] leading-none"
              disabled={!active || !manualControlsAllowed || pending}
              id="manual-output-directory"
              onChange={(event) => update("outputDir", event.target.value)}
              placeholder="测试数据保存目录"
              value={config.outputDir}
            />
            <Button
              className="size-9 shrink-0 px-0"
              disabled={!active || !manualControlsAllowed || pending}
              onClick={() => void chooseOutputDir()}
              size="sm"
              title="选择输出目录"
              type="button"
              variant="outline"
            >
              <FolderOpen className="size-4" />
            </Button>
          </div>
        </div>
        <div className="flex items-end justify-center self-end">
          <Button
            className="h-9 w-full max-w-[220px]"
            disabled={!manualSaveReady}
            onClick={() => void runManualCommand("manual.saveExcel", {}, true)}
            type="button"
          >
            {pending ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
            保存当前测试点
          </Button>
        </div>
      </section>
      {!manualControlsAllowed && (
        <div className="rounded-lg border border-[color-mix(in_srgb,var(--app-warning)_40%,transparent)] bg-[var(--app-warning-soft)] px-4 py-3 text-sm text-foreground" role="status">
          <p className="font-semibold text-[var(--app-warning)]">手动控制已锁定</p>
          <p className="mt-1 text-xs text-muted-foreground">硬件控制权由自动测试持有。请返回自动测试，执行“结束并安全下电”后再操作设备。</p>
        </div>
      )}
      <fieldset aria-disabled={!manualControlsAllowed} className="contents" disabled={!manualControlsAllowed}>
      <section className="manual-device-grid shrink-0">
        <Card size="sm" className="manual-device-card shadow-none"><CardHeader className="manual-device-card__header flex flex-row items-center justify-between"><CardTitle className="text-base">电源控制</CardTitle><Power className={`size-4 transition-colors ${isPsuActive ? "text-primary" : "text-muted-foreground"}`} /></CardHeader><CardContent className="manual-control-content manual-device-card__content">
          <ManualSectionHeading>资源设置</ManualSectionHeading>
          {config.powerSupplyKind === "tdk" ? <>
            <div className="manual-power-source-grid">
              <Field label="控制器"><NativeSelect disabled={!active || pending || psu?.detecting || psu?.connected} value={config.powerSupplyKind} onChange={(v) => updatePowerSupplyKind(config, update, v as AppConfiguration["powerSupplyKind"])}><option value="ch341">CH341 I²C</option><option disabled={config.selectedScriptId === "aging"} value="tdk">{config.selectedScriptId === "aging" ? "TDK RS232（AU 老化测试不可用）" : "TDK RS232"}</option></NativeSelect></Field>
              <Field label="TDK 串口">
              <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
                <NativeSelect disabled={!active || pending || psu?.detecting || psu?.connected} value={selectedPowerSupplyResource} onChange={(value) => update("tdkResource", value)}>
                  <option value="">请点击识别电源</option>
                  {selectedPowerSupplyResource && !powerSupplyResources.includes(selectedPowerSupplyResource) && <option value={selectedPowerSupplyResource}>{selectedPowerSupplyResource}</option>}
                  {powerSupplyResources.map((resource) => <option key={resource} value={resource}>{resource}</option>)}
                </NativeSelect>
                <Button className={`btn-refresh-spin ${psu?.detecting ? "cursor-wait" : ""}`} disabled={!active || pending || psu?.detecting || psu?.connected} onClick={() => void runManualCommand("device.refresh", { device: "powerSupply" }, true)} variant="outline"><RefreshCw className={`size-4 ${psu?.detecting ? "animate-spin" : ""}`} />{psu?.detecting ? "识别中" : "识别"}</Button>
              </div>
              </Field>
            </div>
            <ManualSectionHeading>参数设置</ManualSectionHeading>
            <div className="device-settings-field-grid">
              <NumberField label="输出电压 (V)" value={config.tdkVoltageV} onChange={(v) => update("tdkVoltageV", v)} step="0.1" />
              <NumberField label="设定电流 (A)" value={config.setCurrentA} onChange={(v) => update("setCurrentA", v)} max={currentInputMaximum(config, snapshot)} step="0.1" />
            </div>
            <ManualSectionHeading>操作</ManualSectionHeading>
            <div className="manual-tdk-actions grid grid-cols-2 gap-2">
              <Button className="manual-action-button manual-action-button--disconnect" disabled={!active || psu?.detecting} onClick={() => void runManualCommand(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)} variant={psu?.connected ? "outline" : "default"}>{psu?.connected ? "安全断开" : "连接 TDK"}</Button>
              <Button className="manual-action-button manual-action-button--current" disabled={!active || psu?.detecting || !psu?.connected} onClick={() => void runManualCommand("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)} variant="outline">设置电流</Button>
              <Button className={`manual-action-button manual-action-button--output${psu?.outputEnabled ? " btn-power-active" : ""}`} disabled={!active || psu?.detecting || !psu?.connected} onClick={() => void runManualCommand("powerSupply.setOutput", { enabled: !psu?.outputEnabled })} variant={psu?.outputEnabled ? "destructive" : "outline"}>{psu?.outputEnabled ? <><span className="size-2 rounded-full bg-white animate-pulse shrink-0 mr-1.5 inline-block" />关闭输出</> : "开启输出"}</Button>
              <Button className="manual-action-button manual-action-button--voltage" disabled={!active || psu?.detecting || !psu?.connected} onClick={() => void runManualCommand("powerSupply.setVoltage", { voltageV: config.tdkVoltageV }, true)} variant="outline">设置电压</Button>
              <Button className="manual-action-button manual-action-button--read-voltage" disabled={!active || psu?.detecting || !psu?.connected} onClick={() => void runManualCommand("powerSupply.read", { value: "outputVoltage" })} size="sm" variant="outline">读取输出电压</Button>
              <Button className="manual-action-button manual-action-button--read-current" disabled={!active || psu?.detecting || !psu?.connected} onClick={() => void runManualCommand("powerSupply.read", { value: "outputCurrent" })} size="sm" variant="outline">读取输出电流</Button>
              <p className="col-span-2 text-xs text-muted-foreground">
                回读：输出 {formatNumber(psu?.outputVoltageV, " V", 2)} · 电流 {formatNumber(psu?.outputCurrentA, " A", 2)}
              </p>
            </div>
          </> : <>
            <div className="manual-power-source-grid">
              <Field label="控制器"><NativeSelect disabled={!active || pending || psu?.detecting || psu?.connected} value={config.powerSupplyKind} onChange={(v) => updatePowerSupplyKind(config, update, v as AppConfiguration["powerSupplyKind"])}><option value="ch341">CH341 I²C</option><option disabled={config.selectedScriptId === "aging"} value="tdk">{config.selectedScriptId === "aging" ? "TDK RS232（AU 老化测试不可用）" : "TDK RS232"}</option></NativeSelect></Field>
              <Ch341PortSelector
                config={config}
                snapshot={snapshot}
                active={active}
                pending={pending}
                layout="stacked"
                update={update}
                onRefresh={() => void runManualCommand("device.refresh", { device: "powerSupply" }, true)}
              />
            </div>
            <ManualSectionHeading>参数设置</ManualSectionHeading>
            <NumberField label="设定电流 (A)" layout="inline" value={config.setCurrentA} onChange={(v) => update("setCurrentA", v)} max={currentInputMaximum(config, snapshot)} step="0.1" />
            <ManualSectionHeading>操作</ManualSectionHeading>
            <div className="grid grid-cols-2 gap-2">
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
                    disabled={!active || psu?.detecting || (!psu?.connected && !isCh341ReadyToConnect)}
                    onClick={() => void runManualCommand(psu?.connected ? "powerSupply.disconnect" : "powerSupply.connect", {}, true)}
                    variant={psu?.connected ? "outline" : "default"}
                  >
                    {btnText}
                  </Button>
                )
              })()}
              <Button disabled={!active || psu?.detecting || !psu?.connected} onClick={() => void runManualCommand("powerSupply.setCurrent", { currentA: config.setCurrentA }, true)} variant="outline">设置电流</Button>
            </div>
            <ManualSectionHeading>快速读取</ManualSectionHeading>
            <div className="grid grid-cols-2 gap-2">
              <Button className="active:bg-primary/20 transition-colors" disabled={!active || !psu?.connected} onClick={() => void runManualCommand("powerSupply.read", { value: "outputVoltage" })} size="sm" variant="outline">读取输出电压</Button>
              <Button className="active:bg-primary/20 transition-colors" disabled={!active || !psu?.connected} onClick={() => void runManualCommand("powerSupply.read", { value: "outputCurrent" })} size="sm" variant="outline">读取输出电流</Button>
              <Button className="active:bg-primary/20 transition-colors" disabled={!active || !psu?.connected} onClick={() => void runManualCommand("powerSupply.read", { value: "inputVoltage" })} size="sm" variant="outline">读取输入电压</Button>
              <Button className="active:bg-primary/20 transition-colors" disabled={!active || !psu?.connected} onClick={() => void runManualCommand("powerSupply.read", { value: "temperature" })} size="sm" variant="outline">读取模块温度</Button>
            </div>
            <ManualSectionHeading>状态回读</ManualSectionHeading>
            <div>
              <p className="manual-device-readback">
                <span className="is-blue">输入 {formatNumber(psu?.inputVoltageV, " V", 2)}</span>
                <span className="is-green">输出 {formatNumber(psu?.outputVoltageV, " V", 2)}</span>
                <span className="is-sky">电流 {formatNumber(psu?.outputCurrentA, " A", 2)}</span>
                <span className="is-accent">温度 {formatNumber(psu?.temperatureC, " °C", 2)}</span>
              </p>
            </div>
          </>}
        </CardContent></Card>

        <Card size="sm" className="manual-device-card shadow-none"><CardHeader className="manual-device-card__header flex flex-row items-center justify-between"><CardTitle className="text-base">功率计</CardTitle><Gauge className={`size-4 transition-colors ${isMeterActive ? "text-primary" : "text-muted-foreground"}`} /></CardHeader><CardContent className="manual-control-content manual-device-card__content">
          <ManualSectionHeading>资源设置</ManualSectionHeading>
          <Field label="串口资源">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
              <NativeSelect disabled={!active || meter?.detecting || meter?.running} value={displayedPowerMeterResource} onChange={selectPowerMeterResource}>
                <option value="">请选择串口资源</option>
                {powerMeterResources.map((item) => <option key={item} value={powerMeterResourceValue(item)}>{item}</option>)}
              </NativeSelect>
              <Button className={`btn-refresh-spin ${meter?.detecting ? "cursor-wait" : ""}`} disabled={!active || meter?.detecting || meter?.running} onClick={() => void runManualCommand("device.refresh", { device: "powerMeter" }, true)} variant="outline"><RefreshCw className={`size-4 ${meter?.detecting ? "animate-spin" : ""}`} />{meter?.detecting ? "识别中" : "识别"}</Button>
            </div>
          </Field>
          <ManualSectionHeading>系数方案</ManualSectionHeading>
          <Field label="当前方案">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
              <NativeSelect
                ariaLabel="手动测试系数方案"
                disabled={!active || !manualControlsAllowed || pending || !powerMeterLibrary?.profiles.length}
                value={powerMeterLibrary?.selectedProfileId ?? ""}
                onChange={(value) => {
                  if (!powerMeterLibrary || value === powerMeterLibrary.selectedProfileId) return
                  const profile = powerMeterLibrary.profiles.find((item) => item.id === value)
                  const latest = profile?.versions[profile.versions.length - 1]
                  if (!latest) return
                  void runManualCommand("powerMeter.profile.select", {
                    driverKind: powerMeterIdentity.driverKind,
                    serialNumber: powerMeterIdentity.serialNumber,
                    profileId: value,
                    expectedVersion: latest.version,
                  }, true)
                }}
              >
                {powerMeterLibrary?.profiles.length ? (
                  powerMeterLibrary.profiles.map((profile) => {
                    const revision = profile.versions[profile.versions.length - 1]
                    return <option key={profile.id} value={profile.id}>{revision?.name ?? "未命名方案"} · v{revision?.version ?? 0}</option>
                  })
                ) : (
                  <option value="">{powerMeterIdentity.serialNumber ? "暂无可用方案" : "未识别探头"}</option>
                )}
              </NativeSelect>
              <Button
                disabled={!active || !manualControlsAllowed || pending}
                onClick={() => setProfileDialogOpen(true)}
                variant="outline"
              >
                <SlidersHorizontal className="size-3.5" />
                方案管理
              </Button>
            </div>
          </Field>
          <div>
            <p className="manual-device-readback">
              <span className="is-blue">系数 ×{formatTrimmedNumber(softwareGain, 4)}</span>
              <span className="is-green">波长 {wavelengthNm != null ? `${formatTrimmedNumber(wavelengthNm, 1)} nm` : "待补充"}</span>
            </p>
          </div>
          <ManualSectionHeading>功能操作</ManualSectionHeading>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <label className="device-settings-checkbox"><input checked={config.powerSmoothing ?? true} onChange={(e) => update("powerSmoothing", e.target.checked)} type="checkbox" />功率曲线平滑</label>
            <span className="text-xs text-muted-foreground">仅影响显示，不改变测量数据</span>
          </div>
          <div className="grid grid-cols-2 gap-2"><Button disabled={!active || meter?.detecting || meter?.running || !selectedPowerMeterResourceAvailable} onClick={() => void runManualCommand("powerMeter.setRelativeZero", { enabled: true }, true)} variant="outline">相对调零</Button><Button disabled={!active || meter?.detecting || meter?.running || !selectedPowerMeterResourceAvailable} onClick={() => void runManualCommand("powerMeter.setRelativeZero", { enabled: false }, true)} variant="outline">取消调零</Button></div>
          <ManualSectionHeading>状态</ManualSectionHeading>
          <p className="manual-device-status"><span className={`manual-device-status__dot ${meter?.running ? "is-active" : ""}`} />状态：{meter?.running ? "采集中" : "未调零"}</p>
          <Button className={`w-full transition-all ${meter?.running ? "btn-running-pulse" : ""}`} disabled={!active || meter?.detecting || (!meter?.running && !selectedPowerMeterResourceAvailable)} onClick={() => void runManualCommand(meter?.running ? "powerMeter.stop" : "powerMeter.start", {}, true)} variant={meter?.running ? "destructive" : "default"}>{meter?.running ? <><Square className="size-4" />停止采集</> : <><Play className="size-4" />开始采集</>}</Button>
        </CardContent></Card>

        <Card size="sm" className="manual-device-card shadow-none"><CardHeader className="manual-device-card__header flex flex-row items-center justify-between"><CardTitle className="text-base">光谱仪</CardTitle><Radio className={`size-4 transition-colors ${isSpectrumActive ? "text-primary" : "text-muted-foreground"}`} /></CardHeader><CardContent className="manual-control-content manual-device-card__content">
          <ManualSectionHeading>资源设置</ManualSectionHeading>
          <Field label="设备资源">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
              <NativeSelect disabled={!active || spectrum?.detecting || spectrum?.running} value={displayedSpectrometerResource} onChange={(v) => update("spectrometerResource", v)}>
                <option value="">{defaultSpectrometerResourceLabel}</option>
                {spectrometerResources.map((item) => <option key={item} value={item}>{item}</option>)}
              </NativeSelect>
              <Button className={`btn-refresh-spin ${spectrum?.detecting ? "cursor-wait" : ""}`} disabled={!active || spectrum?.detecting || spectrum?.running} onClick={() => void runManualCommand("device.refresh", { device: "spectrometer" }, true)} variant="outline"><RefreshCw className={`size-4 ${spectrum?.detecting ? "animate-spin" : ""}`} />{spectrum?.detecting ? "识别中" : "识别"}</Button>
            </div>
          </Field>
          <ManualSectionHeading>参数设置</ManualSectionHeading>
          <div className="device-settings-field-grid">
            <NumberField label="积分时间 (μs)" value={config.integrationTimeUs} onChange={(v) => update("integrationTimeUs", v)} />
            <NumberField label="刷新间隔 (ms)" value={config.spectrometerIntervalMs} onChange={(v) => update("spectrometerIntervalMs", v)} />
            <NumberField label="平均帧数" min={1} max={20} value={config.spectrumAverageFrames} onChange={(v) => update("spectrumAverageFrames", Math.max(1, Math.min(20, Math.round(v))))} />
            <Field label="采集模式"><label className="device-settings-checkbox"><input checked={config.autoIntegration} onChange={(e) => update("autoIntegration", e.target.checked)} type="checkbox" />自动积分</label></Field>
            <NumberField label="PIB 中心波长 (nm)" step="0.1" value={config.pibCenterWavelengthNm} onChange={(v) => update("pibCenterWavelengthNm", v)} />
            <NumberField label="PIB 半带宽 (nm)" step="0.1" value={config.pibHalfRangeNm} onChange={(v) => update("pibHalfRangeNm", v)} />
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <label className="device-settings-checkbox"><input checked={config.spectrumSmoothing ?? true} onChange={(e) => update("spectrumSmoothing", e.target.checked)} type="checkbox" />光谱曲线平滑</label>
            <span className="text-xs text-muted-foreground">仅影响显示，不改变测量数据</span>
          </div>
          <Button className={`w-full transition-all ${spectrum?.running ? "btn-running-pulse" : ""}`} disabled={!active || spectrum?.detecting || (!spectrum?.running && !selectedSpectrometerResourceAvailable)} onClick={() => void runManualCommand(spectrum?.running ? "spectrometer.stop" : "spectrometer.start", {}, true)} variant={spectrum?.running ? "destructive" : "default"}>{spectrum?.running ? <><Square className="size-4" />停止采集</> : <><Play className="size-4" />开始采集</>}</Button>
        </CardContent></Card>
      </section>
      <section className="automatic-chart-grid">
        <div className="automatic-chart-grid__power-row">
          <PowerRealtimeChart snapshot={snapshot} smoothing={config.powerSmoothing ?? true} stretch />
          <PowerEfficiencyChart snapshot={snapshot} smoothing={config.powerSmoothing ?? true} stretch />
        </div>
        <RealtimeMetricCards
          config={config}
          currentA={psu?.activeCurrentA ?? config.setCurrentA}
          powerMeter={meter}
          spectrometer={spectrum}
        />
        <SpectrumRealtimeChart snapshot={snapshot} smoothing={config.spectrumSmoothing ?? true} stretch />
      </section>
      </fieldset>
      {profileDialogOpen && (
        <PowerMeterProfileDialog
          active={active && manualControlsAllowed}
          config={config}
          onClose={() => setProfileDialogOpen(false)}
          open={profileDialogOpen}
          pending={pending}
          run={runManualCommand}
          snapshot={snapshot}
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

const PdPage = memo(function PdPage({ snapshot, active, pending, run }: { snapshot: BackendSnapshot | null; active: boolean; pending: boolean; run: RunCommand }) {
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
      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(360px,380px)_minmax(0,1fr)]">
        <Card className="pd-settings-card shadow-none"><CardHeader><CardTitle className="text-base">NI-DAQ 采集设置</CardTitle><CardDescription>PD 采集可在电源加电期间独立启动或停止</CardDescription></CardHeader><CardContent className="pd-settings-grid grid grid-cols-2 gap-3">
          <div className="col-span-2 pt-1"><p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">采集参数</p></div>
          <Field label="采集卡" className="pd-inline-field col-span-2" layout="inline">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
              <NativeSelect disabled={!active || pending || pd?.detecting || pd?.state === "running"} value={current.device} onChange={(v) => set("device", v)}><option value="">请选择</option>{pd?.devices.map((item) => <option key={item} value={item}>{item}</option>)}</NativeSelect>
              <Button className={`btn-refresh-spin ${pd?.detecting ? "cursor-wait" : ""}`} disabled={!active || pending || pd?.detecting || pd?.state === "running"} onClick={() => void run("pd.refresh")} variant="outline"><RefreshCw className={`size-4 ${pd?.detecting ? "animate-spin" : ""}`} />{pd?.detecting ? "识别中" : "识别"}</Button>
            </div>
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
          <div className="col-span-2 space-y-2">
            <div className="flex items-center gap-2 text-xs text-muted-foreground"><span className="grid size-5 place-items-center rounded-full bg-primary/15 font-semibold text-primary">1</span><span>先识别采集卡，再开始采集</span></div>
            <Button className="w-full" disabled={!active || pending || pd?.detecting || (pd?.state !== "running" && !current.device)} onClick={() => void run(pd?.state === "running" ? "pd.stop" : "pd.start", current as unknown as Record<string, unknown>)}>{pd?.state === "running" ? <><Square className="size-4" />停止并保存</> : <><Play className="size-4" />开始采集</>}</Button>
          </div>
          <p className="col-span-2 text-xs text-muted-foreground">{pd?.status || "等待识别采集卡"}</p>
        </CardContent></Card>
        <div className="space-y-4">
          <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">{[["当前值", pd?.currentValue ?? "--"], ["电压", pd?.voltage ?? "--"], ["采样数", pd?.sampleCount ?? "0"]].map(([label, value]) => <Card className="py-4 shadow-none" key={label}><CardContent><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold">{value}</p></CardContent></Card>)}</section>
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

function App() {
  const [licenseStatus, setLicenseStatus] = useState<LicenseStatus | null>(() => DEMO_PREVIEW_ENABLED ? DEMO_LICENSE_STATUS : TAURI_RUNTIME_AVAILABLE ? null : BROWSER_LICENSE_STATUS)
  const [sessionAuthorized, setSessionAuthorized] = useState(DEMO_PREVIEW_ENABLED)

  useEffect(() => {
    if (!TAURI_RUNTIME_AVAILABLE || DEMO_PREVIEW_ENABLED) return
    let active = true
    const refresh = async () => {
      try {
        const status = await getLicenseStatus()
        if (!active) return
        setLicenseStatus(status)
        if (status.valid) setSessionAuthorized(true)
      } catch {
        if (active) setLicenseStatus(BROWSER_LICENSE_STATUS)
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 60_000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [])

  const importLicenseFile = useCallback(async () => {
    const selected = await openDialog({
      multiple: false,
      directory: false,
      title: "选择 ARP 许可证",
      filters: [{ name: "ARP License", extensions: ["json"] }],
    })
    if (typeof selected !== "string" || !selected) return
    const status = await installLicense(selected)
    setLicenseStatus(status)
    if (status.valid) setSessionAuthorized(true)
    else throw new Error(licenseReasonLabels[status.reason])
  }, [])

  if (!licenseStatus) {
    return <main className="license-loading-screen"><LoaderCircle className="size-6 animate-spin" /><span>正在检查软件授权...</span></main>
  }
  if (!sessionAuthorized && !licenseStatus.valid) {
    return <ActivationView onStatusChange={(status) => { setLicenseStatus(status); if (status.valid) setSessionAuthorized(true) }} runtimeAvailable={TAURI_RUNTIME_AVAILABLE} status={licenseStatus} />
  }
  return <AppearanceProvider><LicensedApp licenseStatus={licenseStatus} onImportLicense={importLicenseFile} /></AppearanceProvider>
}

export default App
