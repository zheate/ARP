import type { AutomaticPauseAction, AutomaticPauseKind } from "@/lib/backend"

const PAUSE_COPY: Record<AutomaticPauseKind, readonly [string, string]> = {
  operator: ["测试已暂停", "操作员主动暂停了当前测试。"],
  spectrum_saturated: ["光谱饱和", "请降低积分时间或调整光路，然后重测当前点。"],
  power_meter_disconnected: ["功率计连接中断", "恢复功率计后，重新测量当前测试点。"],
  spectrometer_disconnected: ["光谱仪连接中断", "恢复光谱仪后，重新测量当前测试点。"],
  record_persistence_failed: ["本地测试记录写入失败", "当前测量结果尚未安全落盘，程序不会进入下一点。"],
  measurement_timeout: ["测试点等待超时", "检查器件和采集状态后，重新测试当前点。"],
  device_error: ["设备异常", "检查设备状态后，重新测试当前点。"],
}

const PAUSE_COMMANDS: Record<AutomaticPauseAction, string> = {
  resume: "automatic.resume",
  adjust_spectrum: "automatic.adjustSpectrum",
  recover_power_meter: "automatic.recoverPowerMeter",
  recover_spectrometer: "automatic.recoverSpectrometer",
  retry_persist: "automatic.retryPersist",
  retry_point: "automatic.retryPoint",
  safe_end: "automatic.end",
}

export function automaticPauseCopy(kind: AutomaticPauseKind | null | undefined): readonly [string, string] {
  return PAUSE_COPY[kind ?? "device_error"]
}

export function automaticPauseCommand(action: AutomaticPauseAction): string {
  return PAUSE_COMMANDS[action]
}

export function pauseRemainingSeconds(deadlineAtUtc: string | null | undefined, nowMs: number): number | null {
  if (!deadlineAtUtc) return null
  const deadlineMs = new Date(deadlineAtUtc).getTime()
  if (!Number.isFinite(deadlineMs)) return null
  return Math.max(0, Math.ceil((deadlineMs - nowMs) / 1_000))
}
