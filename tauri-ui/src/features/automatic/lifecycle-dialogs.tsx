import { busyInteraction } from "@/components/busy-interaction"
import { useActionFeedback } from "@/hooks/use-action-feedback"
import { DialogPortal } from "@/components/dialog-portal"
import { Button } from "@/components/ui/button"
import { DiagnosticExportButton } from "@/components/diagnostic-export-button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { automaticPauseCopy, pauseRemainingSeconds } from "@/lib/automatic-pause"
import { type AutomaticPauseAction, type AutomaticTerminalOutcome, type BackendSnapshot, type SafetyShutdownResult } from "@/lib/backend"
import { openResultFile } from "@/lib/result-file"
import { shutdownPresentation } from "@/lib/safety-state"
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener"
import { Check, FileText, FolderOpen, Hand, LoaderCircle, OctagonAlert, Pause, QrCode, RefreshCw, RotateCcw, X } from "lucide-react"
import { useEffect, useRef, useState } from "react"


export function PowerProtectionDialog({ message, onClose }: { message: string; onClose: () => void }) {
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
          <div><h2 className="text-lg font-semibold" id="power-protection-title">功率保护已触发</h2><p className="mt-1 text-sm text-muted-foreground">保护已触发；请核对下电结果与电源实体面板</p></div>
        </div>
        <div className="px-5 py-5"><p className="whitespace-pre-line text-sm leading-6 text-foreground">{message}</p></div>
          <div className="flex justify-end border-t border-border bg-background/35 px-5 py-3"><Button onClick={onClose} ref={acknowledgeButtonRef} variant="destructive">确认并关闭</Button></div>
        </div>
      </div>
    </DialogPortal>
  )
}

export function AutomaticPauseDialog({ auto, integrationTimeUs, pending, onAction }: {
  auto: BackendSnapshot["automaticTest"]
  integrationTimeUs: number
  pending: boolean
  onAction: (action: AutomaticPauseAction, params?: Record<string, unknown>) => void | Promise<unknown>
}) {
  const feedback = useActionFeedback(pending)
  const performAction = (action: AutomaticPauseAction, params?: Record<string, unknown>) => feedback.runAction(action, () => onAction(action, params))
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
        <div {...busyInteraction(pending || feedback.action !== null)} aria-busy={pending || feedback.action !== null} aria-labelledby="automatic-pause-title" aria-modal="true" className="status-dialog motion-dialog-enter w-[min(580px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-[color-mix(in_srgb,var(--app-warning)_55%,transparent)] bg-popover shadow-2xl shadow-black/45" ref={dialogRef} role="alertdialog" tabIndex={-1}>
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
                  <Button disabled={!Number.isFinite(draftIntegrationTimeUs) || draftIntegrationTimeUs <= 0} aria-disabled={pending || feedback.action !== null} onClick={() => void performAction("adjust_spectrum", { integrationTimeUs: draftIntegrationTimeUs })} variant="outline"><LoaderCircle className={`size-4 ${feedback.action === "adjust_spectrum" ? "animate-spin" : "invisible"}`} />应用并重测</Button>
                </div>
              </div>
            )}
          </div>
          {feedback.error && <p className="px-5 pb-3 text-sm text-destructive" role="alert">{feedback.error}</p>}
          <div className="flex flex-wrap justify-end gap-2 border-t border-border bg-background/35 px-5 py-3">
            {actions.has("resume") && <Button aria-disabled={pending || feedback.action !== null} onClick={() => void performAction("resume")} variant="outline"><LoaderCircle className={`size-4 ${feedback.action === "resume" ? "animate-spin" : "invisible"}`} />继续测试</Button>}
            {actions.has("recover_power_meter") && <Button aria-disabled={pending || feedback.action !== null} onClick={() => void performAction("recover_power_meter")} variant="outline"><LoaderCircle className={`size-4 ${feedback.action === "recover_power_meter" ? "animate-spin" : "invisible"}`} />重新识别功率计并重测</Button>}
            {actions.has("recover_spectrometer") && <Button aria-disabled={pending || feedback.action !== null} onClick={() => void performAction("recover_spectrometer")} variant="outline"><LoaderCircle className={`size-4 ${feedback.action === "recover_spectrometer" ? "animate-spin" : "invisible"}`} />重新识别光谱仪并重测</Button>}
            {actions.has("retry_persist") && <Button aria-disabled={pending || feedback.action !== null} onClick={() => void performAction("retry_persist")} variant="outline"><LoaderCircle className={`size-4 ${feedback.action === "retry_persist" ? "animate-spin" : "invisible"}`} />重试本地保存</Button>}
            {actions.has("retry_point") && <Button aria-disabled={pending || feedback.action !== null} onClick={() => void performAction("retry_point")} variant="outline">{feedback.action === "retry_point" ? <LoaderCircle className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}重测当前点</Button>}
            {actions.has("safe_end") && <Button aria-disabled={pending || feedback.action !== null} onClick={() => void performAction("safe_end")} variant="destructive"><LoaderCircle className={`size-4 ${feedback.action === "safe_end" ? "animate-spin" : "invisible"}`} />安全结束</Button>}
          </div>
        </div>
      </div>
    </DialogPortal>
  )
}

export function AutomaticCompletionDialog({
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
  sessionId,
  canExportDiagnostics = false,
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
  sessionId?: string | null
  canExportDiagnostics?: boolean
  canNextDevice: boolean
  canRetryExport: boolean
  onNextDevice: () => void | Promise<unknown>
  onRetryExport: () => void | Promise<unknown>
  onChooseExportDirectory: () => void | Promise<unknown>
}) {
  const feedback = useActionFeedback()
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
              <p className={`mt-1 text-xs font-semibold ${exportStatus === "failed" ? "text-destructive" : "text-foreground"}`}>{journalPath ? "本地记录路径已提供" : "本地记录路径未提供"} · {exportLabel}</p>
              {exportPath && <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground" title={exportPath}>{exportPath}</p>}
              {exportError && <p className="mt-1 text-xs leading-5 text-destructive">{exportError}</p>}
              {resultOpenError && <p aria-live="polite" className="mt-1 text-xs leading-5 text-destructive">{resultOpenError}</p>}
              {journalPath && exportStatus === "failed" && <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground" title={journalPath}>原始记录：{journalPath}</p>}
            </div>
          </div>

          <div className="flex flex-wrap justify-end gap-2 border-t border-border/70 bg-muted/20 px-6 py-3.5">
            <DiagnosticExportButton key={`${sessionId}:${exportStatus}:${exportPath}:${exportError}`} sessionId={sessionId} disabled={!canExportDiagnostics} />
            {feedback.error && <p role="alert">{feedback.error}</p>}
            {exportPath && exportStatus === "completed" && <Button className="w-32" aria-disabled={openingResult} onClick={() => void handleOpenResult()} size="sm" variant="outline">{openingResult ? <LoaderCircle className="size-4 animate-spin" /> : <FileText className="size-4" />}{openingResult ? "正在打开…" : "打开结果"}</Button>}
            {canRetryExport && <Button aria-busy={feedback.action === "retry"} aria-disabled={feedback.action !== null} onClick={() => void feedback.runAction("retry", onRetryExport)} size="sm" variant="outline"><RefreshCw className={`size-4 ${feedback.action === "retry" ? "animate-spin" : ""}`} />重新生成 Excel</Button>}
            {canRetryExport && <Button aria-disabled={feedback.action !== null} onClick={() => void feedback.runAction("directory", onChooseExportDirectory)} size="sm" variant="outline">{feedback.action === "directory" ? <LoaderCircle className="size-4 animate-spin" /> : <FolderOpen className="size-4" />}更换目录</Button>}
            {canNextDevice && <Button aria-disabled={feedback.action !== null} onClick={() => void feedback.runAction("next", onNextDevice)} size="sm">{feedback.action === "next" ? <LoaderCircle className="size-4 animate-spin" /> : <QrCode className="size-4" />}测试下一台</Button>}
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

export function ShutdownFailureDialog({
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
  onRetry: () => void | Promise<void>
  onForceExit: () => void | Promise<void>
}) {
  const [physicalShutdownConfirmed, setPhysicalShutdownConfirmed] = useState(false)
  const [action, setAction] = useState<"retry" | "force" | null>(null)
  const [actionError, setActionError] = useState("")
  const actionLock = useRef(false)
  const retryButtonRef = useRef<HTMLButtonElement>(null)
  const busy = pending || action !== null
  const performAction = async (next: "retry" | "force") => {
    if (pending || actionLock.current || (next === "force" && !shutdownConfirmed && !physicalShutdownConfirmed)) return
    actionLock.current = true
    setAction(next)
    setActionError("")
    try {
      await (next === "retry" ? onRetry() : onForceExit())
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      actionLock.current = false
      setAction(null)
    }
  }

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
      if (event.key !== "Escape" || busy || actionLock.current) return
      event.preventDefault()
      onCancel()
    }
    document.addEventListener("keydown", handleEscape)
    return () => document.removeEventListener("keydown", handleEscape)
  }, [onCancel, busy])

  return (
    <DialogPortal>
      <div className="motion-overlay-enter fixed inset-0 z-[90] flex items-center justify-center bg-black/75 p-4 backdrop-blur-[2px]">
        <div aria-busy={busy} aria-describedby="shutdown-failure-description" aria-labelledby="shutdown-failure-title" aria-modal="true" className="status-dialog motion-dialog-enter w-[min(600px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-[color-mix(in_srgb,var(--app-danger)_55%,transparent)] bg-popover shadow-2xl shadow-black/50" role="alertdialog">
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
                <input aria-disabled={busy} checked={physicalShutdownConfirmed} className="mt-0.5 size-4 accent-[var(--app-danger)]" onChange={(event) => { if (!busy && !actionLock.current) setPhysicalShutdownConfirmed(event.target.checked) }} type="checkbox" />
                <span>我已检查电源实体面板，并确认输出已关闭或电流已归零。</span>
              </label>
            </>
          )}
        </div>
          {actionError && <p className="px-5 pb-3 text-sm text-destructive" role="alert">{actionError}</p>}
          <div className="flex flex-wrap items-center justify-end gap-3 border-t border-border bg-background/35 px-5 py-3">
            <Button aria-disabled={busy} className="w-24" onClick={() => { if (!busy && !actionLock.current) onCancel() }} variant="outline">取消退出</Button>
            <Button aria-busy={action === "retry"} aria-disabled={busy} className="w-36" onClick={() => void performAction("retry")} ref={retryButtonRef} variant="outline">{action === "retry" && <LoaderCircle className="size-4 animate-spin" />}{action === "retry" ? "正在重试…" : shutdownConfirmed ? "重试收尾并退出" : "重试安全下电"}</Button>
            <Button aria-busy={action === "force"} aria-disabled={busy || (!shutdownConfirmed && !physicalShutdownConfirmed)} className="w-36" disabled={!shutdownConfirmed && !physicalShutdownConfirmed} onClick={() => void performAction("force")} variant="destructive">{action === "force" && <LoaderCircle className="size-4 animate-spin" />}{action === "force" ? "正在退出…" : shutdownConfirmed ? "直接退出" : "确认并强制退出"}</Button>
          </div>
        </div>
      </div>
    </DialogPortal>
  )
}
