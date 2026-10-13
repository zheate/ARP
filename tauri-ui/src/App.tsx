import { DisplayMenu } from "@/components/appearance-controls"
import { AutomaticPhaseTrack } from "@/components/automatic-overview"
import { deferredPage } from "@/components/deferred-page"
import { isModalOpen } from "@/components/dialog-portal"
import { ActivationView } from "@/components/license/ActivationView"
import { AnimatedToastStack, type ToastStatus } from "@/components/motion/animated-toast-stack"
import { Button } from "@/components/ui/button"
import { AutomaticCompletionDialog, AutomaticPauseDialog, PowerProtectionDialog, ShutdownFailureDialog } from "@/features/automatic/lifecycle-dialogs"
import { AutomaticPage } from "@/features/automatic/page"
import { BUILTIN_TEST_SCRIPT_OPTIONS, configurationPayload } from "@/features/configuration/model"
import { ManualPage } from "@/features/manual/page"
import { PdPage } from "@/features/pd/page"
import { SystemSettingsPage } from "@/features/settings/page"
import { RunCommand } from "@/features/shared/controls"
import { AppearanceProvider } from "@/hooks/use-appearance"
import { useBackendSnapshot } from "@/hooks/use-backend-snapshot"
import { useConfigurationEditor } from "@/hooks/use-configuration-editor"
import { ACCENT_THEMES, type AccentTheme, type CurveLineStyle, type LatestPointMarkerStyle, type Theme } from "@/lib/appearance"
import { automaticPauseCommand } from "@/lib/automatic-pause"
import { sendBackendRequest, type AutomaticPauseAction, type BackendSnapshot, type SnapshotView } from "@/lib/backend"
import { getLicenseStatus, installLicense, licenseReasonLabels, type LicenseStatus } from "@/lib/license"
import { automaticTerminalTitle } from "@/lib/safety-state"
import { invoke } from "@tauri-apps/api/core"
import { getCurrentWindow } from "@tauri-apps/api/window"
import { open as openDialog } from "@tauri-apps/plugin-dialog"
import {
  BarChart3,
  CircleGauge,
  FileText,
  Hand,
  LoaderCircle,
  OctagonAlert,
  Pause,
  Save,
  Settings,
} from "lucide-react"
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState, type ComponentProps } from "react"

const loadDataBrowser = () => import("@/components/data-browser-page").then(({ DataBrowserPage }) => ({ default: DataBrowserPage }))
const loadShippingReport = () => import("@/features/shipping/report").then(({ ShippingReportPage }) => ({ default: ShippingReportPage }))
const DataBrowserPage = deferredPage(loadDataBrowser, { source: "src/components/data-browser-page.tsx", exportName: "DataBrowserPage" })
const ShippingReportPage = deferredPage(loadShippingReport, { source: "src/features/shipping/report.tsx", exportName: "ShippingReportPage" })

function preloadPage(page: Page) {
  const load = page === "data" ? loadDataBrowser : page === "shipping" ? loadShippingReport : null
  if (load) void load().catch(() => undefined)
}

type Page = "automatic" | "manual" | "pd" | "data" | "shipping" | "settings"

const THEME_STORAGE_KEY = "power-test.theme"

const ACCENT_THEME_STORAGE_KEY = "power-test.accent-theme"

const CURVE_LINE_STYLE_STORAGE_KEY = "power-test.automatic-curve-line-style"

const LATEST_POINT_MARKER_STORAGE_KEY = "power-test.latest-point-marker"

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
    if (storedStyle === "point" || storedStyle === "paper-plane" || storedStyle === "paw" || storedStyle === "comet" || storedStyle === "ecg" || storedStyle === "flappy-bird") return storedStyle
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

function applyTheme(theme: Theme, accentTheme: AccentTheme): void {
  const root = document.documentElement
  root.classList.toggle("dark", theme === "dark")
  root.dataset.theme = theme
  root.dataset.accent = accentTheme
  root.style.background = theme === "dark" ? "#0f1112" : "#f4f6f8"
  root.style.colorScheme = theme
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0f1112" : "#f4f6f8")
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

const TitlebarActions = memo(function TitlebarActions({ showSave, onOpenAppearance, active, canPause, commandPending, controlsLocked, dirty, run, saveConfiguration }: Omit<ComponentProps<typeof AppTitlebar>, "snapshot" | "showPhase">) {
  return (
    <div className="app-titlebar__actions">
        <DisplayMenu onOpenSettings={onOpenAppearance} />
        {canPause && (
          <Button disabled={!active || commandPending} onClick={() => void run("automatic.pause")} size="sm" variant="outline">
            <Pause className="size-4" />暂停测试
          </Button>
        )}
        {showSave && <Button
          disabled={!dirty || !active || commandPending || controlsLocked}
          onClick={() => void saveConfiguration()}
          size="sm"
          variant="outline"
        >
          <Save className="size-4" />保存设置
        </Button>}
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
  )
})

function AppTitlebar({
  snapshot,
  showPhase,
  showSave,
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
  showSave: boolean
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
      <TitlebarActions showSave={showSave} onOpenAppearance={onOpenAppearance} active={active} canPause={canPause} commandPending={commandPending} controlsLocked={controlsLocked} dirty={dirty} pendingCommand={pendingCommand} run={run} saveConfiguration={saveConfiguration} />
    </header>
  )
}

const NavigationSidebar = memo(function NavigationSidebar({ page, pageIsBlocked, pageBlockedReason, changePage }: {
  page: Page
  pageIsBlocked: (page: Page) => boolean
  pageBlockedReason: (page: Page) => string
  changePage: (page: Page) => void
}) {
  return (
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
                onPointerEnter={() => { if (!pageIsBlocked(item.id)) preloadPage(item.id) }}
                onFocus={() => { if (!pageIsBlocked(item.id)) preloadPage(item.id) }}
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
  )
})

function LicensedApp({
  licenseStatus,
  onImportLicense,
}: {
  licenseStatus: LicenseStatus
  onImportLicense: () => Promise<void>
}) {
  const [page, setPage] = useState<Page>(() => (typeof window !== "undefined" && (new URLSearchParams(window.location.search).get("page") as Page)) || "automatic")
  const [dataBrowserVisited, setDataBrowserVisited] = useState(page === "data")
  const [theme, setTheme] = useState<Theme>(getStoredTheme)
  const [accentTheme, setAccentTheme] = useState<AccentTheme>(getStoredAccentTheme)
  const [curveLineStyle, setCurveLineStyle] = useState<CurveLineStyle>(getStoredCurveLineStyle)
  const [latestPointMarkerStyle, setLatestPointMarkerStyle] = useState<LatestPointMarkerStyle>(getStoredLatestPointMarkerStyle)
  const currentView: SnapshotView = page === "settings" ? "automatic" : page
  const { snapshot, error, commandPending, pendingCommand, command } = useBackendSnapshot(currentView)
  const [closeFailure, setCloseFailure] = useState<{ message: string; shutdownConfirmed: boolean } | null>(null)
  const [automaticCompletion, setAutomaticCompletion] = useState<{ title: string; message: string } | null>(null)
  const [closeRequestPending, setCloseRequestPending] = useState(false)
  const closeRequestInFlightRef = useRef(false)
  const automaticRunSeenRef = useRef(false)
  const pageScrollPositionsRef = useRef<Record<Page, number>>({ automatic: 0, manual: 0, pd: 0, data: 0, shipping: 0, settings: 0 })
  const active = snapshot?.backend.mode === "active"
  const operational = active && licenseStatus.valid
  const { config, dirty, current: currentConfiguration, waitForSave, accept: acceptConfiguration, acceptCommand, saveConfiguration, createTestScript, update, selectTestScript } = useConfigurationEditor(snapshot, operational, command)
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
    if (DEMO_PREVIEW_ENABLED) {
      await getCurrentWindow().destroy()
      return
    }
    closeRequestInFlightRef.current = true
    setCloseRequestPending(true)
    setCloseFailure(null)
    let shutdownConfirmed = false
    try {
      const result = await sendBackendRequest("app.shutdown", {})
      shutdownConfirmed = result.shutdownConfirmed
      if (!shutdownConfirmed) {
        throw new Error(result.shutdown.reason || "后端未确认安全下电")
      }
      if (!result.exportsDrained) {
        throw new Error("安全下电已确认，但后台 Excel 导出尚未排空；请稍后重试退出")
      }
      await waitForSave().catch(() => undefined)
      if (licenseStatus.valid && currentConfiguration().hydrated) {
        try {
          await sendBackendRequest(
            "app.configure",
            configurationPayload(currentConfiguration().value),
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
  }, [licenseStatus.valid, currentConfiguration, waitForSave])

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

  const run: RunCommand = useCallback(async (...args) => {
    const [method, params, sync] = args
    if (!active) return
    if (!licenseStatus.valid && !SAFE_UNLICENSED_METHODS.has(method)) {
      throw new Error(`软件授权无效：${licenseReasonLabels[licenseStatus.reason]}`)
    }
    if (sync && dirty) await saveConfiguration()
    const result = await command(method, params)
    acceptCommand(method, result)
    return result
  }, [active, command, dirty, licenseStatus.reason, licenseStatus.valid, saveConfiguration, acceptCommand])

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
    if (nextPage === "data") setDataBrowserVisited(true)
    setPage(nextPage)
  }, [page, pageIsBlocked])

  const openAppearance = useCallback(() => changePage("settings"), [changePage])

  useLayoutEffect(() => {
    const targetY = pageScrollPositionsRef.current[page] ?? 0
    window.scrollTo({ top: targetY, behavior: "instant" })
  }, [page])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (isModalOpen() || !(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return
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
    acceptConfiguration({ sn: "" })
    setAutomaticCompletion(null)
    window.requestAnimationFrame(() => document.getElementById("automatic-sn")?.focus())
  }, [run, acceptConfiguration])

  const retryAutomaticExport = useCallback(async () => {
    await run("automatic.retryExport")
  }, [run])

  const chooseAutomaticExportDirectory = useCallback(async () => {
    const selected = await openDialog({
      multiple: false,
      directory: true,
      title: "选择 Excel 输出目录",
      defaultPath: currentConfiguration().value.outputDir.trim() || undefined,
    })
    if (typeof selected !== "string" || !selected) return
    await run("automatic.retryExport", { outputDir: selected })
    acceptConfiguration({ outputDir: selected })
  }, [run, currentConfiguration, acceptConfiguration])

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
        showSave={page !== "automatic"}
        onOpenAppearance={openAppearance}
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
      {DEMO_PREVIEW_ENABLED && (
        <div className="border-b border-border bg-primary/5 px-5 py-2 text-sm text-muted-foreground" role="status">
          虚拟数据演示 · 不连接仪器，不写入正式数据库
        </div>
      )}
      <div className="app-shell__body grid min-h-0 flex-1 overflow-hidden">
        <NavigationSidebar page={page} pageIsBlocked={pageIsBlocked} pageBlockedReason={pageBlockedReason} changePage={changePage} />

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
            {page === "automatic" && <AutomaticPage snapshot={snapshot} config={config} update={update} active={operational} dirty={dirty} controlsLocked={automaticInteractionLocked || !licenseStatus.valid} curveLineStyle={curveLineStyle} latestPointMarkerStyle={latestPointMarkerStyle} pending={commandPending} run={run} saveConfiguration={saveConfiguration} testScripts={snapshot?.testScripts ?? BUILTIN_TEST_SCRIPT_OPTIONS} onScriptSelect={selectTestScript} onCreateScript={createTestScript} onNextDevice={() => void prepareNextDevice()} onViewData={() => changePage("data")} onRetryExport={() => void retryAutomaticExport()} onChooseExportDirectory={() => void chooseAutomaticExportDirectory()} onShowCompletion={() => { const auto = snapshot?.automaticTest; if (auto?.terminalOutcome) setAutomaticCompletion({ title: automaticTerminalTitle(auto.terminalOutcome), message: auto.detail }) }} onNotify={notify} />}
            {page === "manual" && <ManualPage snapshot={snapshot} config={config} update={update} active={operational} pending={commandPending} run={run} saveConfiguration={saveConfiguration} onNotify={notify} />}
            {page === "pd" && <PdPage snapshot={snapshot} active={operational} pending={commandPending} run={run} />}
            {page === "shipping" && <ShippingReportPage active={operational} controlsLocked={automaticInteractionLocked || !licenseStatus.valid} pending={commandPending} />}
            {page === "settings" && <SystemSettingsPage curveLineStyle={curveLineStyle} latestPointMarkerStyle={latestPointMarkerStyle} licenseStatus={licenseStatus} onCurveLineStyleChange={setCurveLineStyle} onImportLicense={onImportLicense} onLatestPointMarkerStyleChange={setLatestPointMarkerStyle} theme={theme} onThemeChange={setTheme} accentTheme={accentTheme} onAccentThemeChange={setAccentTheme} />}
          </div>
          <div className="db-page-host min-h-0 flex-1" style={{ display: page === "data" ? "flex" : "none", flexDirection: "column" }}>
            {dataBrowserVisited && <DataBrowserPage
              visible={page === "data"}
              defaultDirectory={config.outputDir}
              enabled={operational && !automaticInteractionLocked}
              disabledReason={
                !licenseStatus.valid ? "软件尚未激活，激活后可浏览测试数据。"
                  : !active ? "正在连接后端服务，连接完成后即可浏览测试数据。"
                    : automaticInteractionLocked ? "自动测试执行中，数据浏览已锁定；执行“结束并安全下电”后可继续。" : undefined
              }
            />}
          </div>
        </main>
      </div>
    </div>
  )
}

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
