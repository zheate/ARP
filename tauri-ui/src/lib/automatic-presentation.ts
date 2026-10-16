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
  const labels = ["准备", "升流", "稳定", "记录", "完成"]
  const state = auto?.state ?? "idle"
  let activeIndex = 0
  let tone: "normal" | "success" | "warning" | "danger" = "normal"
  let complete = false
  if (state === "setting_current") activeIndex = 1
  if (state === "waiting_stable") activeIndex = 2
  if (["waiting_voltage", "saving_point"].includes(state)) activeIndex = 3
  if (state === "waiting_voltage" && (auto?.stability?.holdRequiredS ?? 0) > 0) labels[3] = "保持"
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

export function automaticRunProgress(auto: Automatic | undefined, shutdown?: SafetyShutdownResult) {
  const phase = automaticPhase(auto, shutdown)
  const state = auto?.state ?? "idle"
  const stages: Record<string, string> = {
    idle: "未开始", starting: "准备测试", setting_current: "施加电流",
    waiting_stable: "等待稳定", waiting_voltage: "保持中", saving_point: "记录测试点",
    paused: "已暂停", ramping_down: "安全下电中",
  }
  let stage = stages[state] ?? "等待状态"
  if (state === "waiting_stable" && auto?.stability?.powerStable && auto.stability.wavelengthRequired && !auto.stability.wavelengthStable) stage = "等待波长稳定"
  if (state === "waiting_voltage" && phase.dwell !== null) stage = "停留中"
  if (state === "waiting_voltage" && auto?.runContext?.selectedScriptId === "aging") stage = "老化中"
  if (state === "completed") stage = phase.labels[phase.activeIndex]
  const recorded = Math.max(0, auto?.savedPointCount ?? 0)
  return {
    point: phase.terminal ? "本轮已结束"
      : state === "idle" ? (phase.total ? `待测 ${phase.total} 点` : "待机")
      : phase.total ? `第 ${phase.point} / ${phase.total} 点` : "测试进行中",
    stage,
    recorded: `已记录 ${recorded} 点`,
    elapsed: formatTestDuration(auto?.elapsedS),
    detail: auto?.detail || stage,
    tone: phase.tone,
  }
}

export function powerStabilityPresentation(meter: DeviceSnapshot | undefined) {
  const available = meter?.running !== false && meter?.connected !== false
    && meter?.quality === "live" && typeof meter.powerW === "number" && Number.isFinite(meter.powerW)
  if (!available) {
    const label = meter?.running === false ? "采集已停止"
      : meter?.quality === "stale" ? "采样中断"
      : meter?.quality === "saturated" ? "读数饱和" : "等待采样"
    return { label, time: "—", tone: "muted" }
  }
  if (meter?.stable !== true) return { label: "未稳定", time: "—", tone: "warning" }
  const elapsed = meter.stableElapsedS
  const time = typeof elapsed === "number" && Number.isFinite(elapsed) && elapsed >= 0
    ? `持续 ${Math.floor(elapsed)} s` : "待更新"
  return { label: "已稳定", time, tone: "success" }
}

export function automaticStabilityPresentation(auto: Automatic | undefined, meter?: DeviceSnapshot) {
  const stability = auto?.stability
  const state = auto?.state ?? "idle"
  const result = (label: string, tone = "normal", time = "—", detail = "功率连续满足波动条件的时间") => ({ label, tone, time, detail })
  if (state === "idle") return result("未开始")
  if (auto?.runContext?.selectedScriptId === "aging") return result("定时老化", "normal", "—", "老化测试按设定时长运行")
  if (!stability) {
    const live = meter?.quality === "live" && meter.running !== false
    return result(live ? meter.stable ? "已稳定" : "未稳定" : "等待采样", live ? meter.stable ? "success" : "warning" : "normal", "—", "当前后端未提供判稳累计时间。")
  }

  const required = stability.holdRequiredS
  const holdElapsed = stability.holdElapsedS
  if (typeof required !== "number" || !Number.isFinite(required) || required < 0 || typeof holdElapsed !== "number" || !Number.isFinite(holdElapsed)) {
    return result(stability.powerStable ? "已稳定" : "判稳中", stability.powerStable ? "success" : "warning", "—", "当前后端未提供稳定后的保持计时。")
  }
  const preparing = ["starting", "setting_current"].includes(state)
  const sampling = ["waiting_stable", "waiting_voltage"].includes(state)
  const usable = stability.hasReading && (!sampling || stability.quality === "live") && !preparing
  const elapsed = usable && stability.powerStable ? Math.min(required, Math.max(0, holdElapsed)) : 0
  const time = required === 0 ? "—" : `${elapsed.toFixed(1)} / ${required.toFixed(1)} 秒`
  const criterion = stability.spanLimitW === null
    ? `相对波动 ≤ ${stability.limitPercent}%`
    : `峰峰值 ≤ ${stability.spanLimitW} W`
  const detail = required === 0
    ? `功率${criterion}，满足稳定条件后直接读取最终电压。`
    : `功率${criterion}，判稳后连续保持 ${required} 秒才继续；超限或采样中断后重新判稳、重新计时。`
  if (preparing) return result("等待判稳", "normal", time, detail)
  if (state === "paused") return result("已暂停", "warning", usable ? time : "—", detail)
  if (["completed", "ramping_down"].includes(state)) {
    return result(usable && stability.powerStable ? "末点已稳定" : "判稳已结束", usable && stability.powerStable ? "success" : "normal", usable ? time : "—", detail)
  }
  if (!usable) return result("等待采样", "warning", time, detail)
  if (stability.powerStable) {
    return stability.wavelengthRequired && !stability.wavelengthStable
      ? result("等待波长稳定", "warning", time, `${detail}功率已稳定，等待波长满足条件。`)
      : result(required === 0 ? "已稳定" : elapsed < required ? "保持中" : "保持完成", "success", time, detail)
  }
  const exceeds = stability.spanLimitW === null
    ? stability.percent !== null && stability.percent > stability.limitPercent
    : stability.spanW !== null && stability.spanW > stability.spanLimitW + 1e-12
  return result(elapsed === 0 && exceeds ? "波动超限" : "判稳中", "warning", time, detail)
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
