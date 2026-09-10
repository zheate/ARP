import type { BackendSnapshot, SafetyShutdownResult } from "./backend"

type Automatic = BackendSnapshot["automaticTest"]

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
  return { labels, activeIndex, complete, tone, terminal: state === "completed", point, total }
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
