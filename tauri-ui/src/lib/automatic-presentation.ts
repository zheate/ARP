import type { AppConfiguration, AutomaticReadinessBlocker, BackendSnapshot, DeviceSnapshot, SafetyShutdownResult } from "./backend"

type Automatic = BackendSnapshot["automaticTest"]

export function automaticStartPresentation(auto: Automatic | undefined, config: Pick<AppConfiguration, "sn" | "outputDir">) {
  if (!auto) return { canStart: false, messages: [] as string[] }

  // automatic.start submits the current form before the backend checks readiness.
  // Reconcile only these required text fields; retain every other backend blocker.
  const isFormBlocker = (blocker: AutomaticReadinessBlocker) => blocker.code === "missing_sn" || blocker.code === "missing_output_dir"
  const backendBlockers = auto.readiness?.blockers
  const messages = [
    ...(!config.sn.trim() ? ["请填写产品 SN"] : []),
    ...(!config.outputDir.trim() ? ["请选择测试数据输出目录"] : []),
    ...(backendBlockers
      ? backendBlockers.filter((blocker) => !isFormBlocker(blocker)).map((blocker) => blocker.message)
      : auto.settingsError ? [auto.settingsError] : []),
  ]
  const canSubmit = auto.canStart === true || Boolean(backendBlockers?.some(isFormBlocker))
  return {
    canStart: canSubmit && ["idle", "completed"].includes(auto.state) && !auto.canEnd && messages.length === 0,
    messages,
  }
}

export function automaticPhase(auto: Automatic | undefined, shutdown: SafetyShutdownResult | undefined) {
  const labels = ["准备", "升流", "等稳", "记录", "完成"]
  const state = auto?.state ?? "idle"
  let activeIndex = 0
  let tone: "normal" | "success" | "warning" | "danger" = "normal"
  let complete = false
  if (state === "setting_current") activeIndex = 1
  if (state === "waiting_stable") activeIndex = 2
  if (["waiting_voltage", "saving_point"].includes(state)) activeIndex = 3
  if (state === "waiting_voltage" && (auto?.currentDwellS ?? 0) > 0) labels[3] = "停留"
  if (state === "waiting_voltage" && auto?.runContext?.selectedScriptId === "aging") labels[3] = "老化"
  if (state === "paused") {
    activeIndex = 2
    labels[2] = "已暂停"
    tone = "warning"
  }
  if (state === "ramping_down") {
    activeIndex = 4
    labels[4] = "下电中"
    tone = "warning"
  }
  if (state === "completed") {
    activeIndex = 4
    complete = auto?.terminalOutcome === "succeeded" && shutdown?.confirmed === true
    const uncertain = !shutdown?.confirmed || auto?.terminalOutcome === "shutdown_unconfirmed"
    tone = complete ? "success" : uncertain || auto?.terminalOutcome === "aborted_shutdown_confirmed" ? "danger" : "warning"
    labels[4] = complete ? "完成" : uncertain ? "下电待确认" : auto?.terminalOutcome === "stopped_shutdown_confirmed" ? "提前结束" : "异常结束"
  }
  const total = auto?.currents?.length ?? 0
  const recorded = Math.max(0, Math.min(total, auto?.savedPointCount ?? 0))
  const point = state === "completed" ? recorded : state === "idle" ? 0 : Math.max(0, Math.min(total, (auto?.currentIndex ?? -1) + 1))
  // Only a phase transition that is actually underway may animate. An idle or finished run
  // must stay still, and a pause is waiting on the operator rather than progressing.
  const live = !["idle", "paused", "completed"].includes(state)
  // A dwell is the only phase with a real denominator, so its fraction is actual progress rather
  // than a guess. The backend reports this number only while a hold is genuinely running, and
  // quantizes it to whole seconds, so it steps once per second instead of streaming a clock.
  const dwellElapsedS = auto?.currentDwellElapsedS
  const dwellS = auto?.currentDwellS ?? 0
  const dwell = live && typeof dwellElapsedS === "number" && Number.isFinite(dwellElapsedS) && Number.isFinite(dwellS) && dwellS > 0
    ? Math.max(0, Math.min(1, dwellElapsedS / dwellS))
    : null
  return { labels, activeIndex, complete, dwell, live, tone, terminal: state === "completed", point, total }
}

export function powerStabilityPresentation(meter: DeviceSnapshot | undefined) {
  const available = meter?.running !== false && meter?.connected !== false
    && meter?.quality === "live" && typeof meter.powerW === "number" && Number.isFinite(meter.powerW)
  if (!available) {
    const label = meter?.running === false ? "采集已停止"
      : meter?.quality === "stale" ? "采样中断"
      : meter?.quality === "saturated" ? "读数饱和" : "等待采样"
    return { label, time: "稳定时间 --", tone: "muted" }
  }
  if (meter?.stable !== true) return { label: "未稳定", time: "稳定时间 --", tone: "warning" }
  const elapsed = meter.stableElapsedS
  const time = typeof elapsed === "number" && Number.isFinite(elapsed) && elapsed >= 0
    ? `持续 ${Math.floor(elapsed)} s` : "稳定时间待更新"
  return { label: "已稳定", time, tone: "success" }
}

export function formatTestDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "—"
  const whole = Math.floor(seconds)
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor(whole % 3600 / 60)
  const remainder = whole % 60
  return `${hours ? `${hours}:` : ""}${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
}

export function automaticStoragePresentation(auto: Automatic) {
  // A finished workflow and an Excel path alone do not prove journal persistence.
  const recorded = Math.max(0, auto.savedPointCount ?? 0)
  const journalSaved = Boolean(auto.journalPath && recorded > 0)
  const exported = auto.exportStatus === "completed" && Boolean(auto.exportPath)
  const label = journalSaved ? "本地已保存" : exported ? "Excel 已保存" : "未保存测试点"
  const exportLabel = auto.exportStatus === "failed" ? "Excel 导出失败"
    : auto.exportStatus === "completed" && exported ? "Excel 已生成"
    : ["queued", "exporting", "pending"].includes(auto.exportStatus ?? "") ? "Excel 生成中"
    : ""
  return { label, exportLabel, saved: journalSaved || exported, exportFailed: auto.exportStatus === "failed" }
}
