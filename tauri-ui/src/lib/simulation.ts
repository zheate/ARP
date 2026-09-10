import type { AutomaticTerminalOutcome, BackendSnapshot } from "./backend"
import { DEMO_SNAPSHOT } from "./demo-snapshot.ts"

export const SIMULATION_SCENARIOS = [
  { id: "normal", label: "正常测试", detail: "逐点升流、等稳、记录，完成后下电。" },
  { id: "unstable", label: "功率不稳", detail: "第 2 点持续波动，等稳 8 秒后结束。" },
  { id: "saturated", label: "光谱饱和", detail: "第 2 点光谱削顶，阻止记录并结束。" },
  { id: "export-failed", label: "导出失败", detail: "完成采样后模拟导出失败，可重试。" },
  { id: "shutdown-unconfirmed", label: "下电未确认", detail: "流程结束后保留残余电流，可重试下电。" },
] as const
export type SimulationScenario = typeof SIMULATION_SCENARIOS[number]["id"]
export type SimulationConfig = { sn: string; initialCurrentA: number; targetCurrentA: number; stepA: number; scenario: SimulationScenario }
export type SimulationDevice = "powerSupply" | "powerMeter" | "spectrometer"
export type SimulationPoint = { elapsedS: number; currentA: number; powerW: number; efficiencyPercent: number; wavelengthNm: number }
export type SimulationEvent = { time: number; message: string; tone: "info" | "success" | "warning" }
type Phase = "idle" | "preparing" | "setting_current" | "waiting_stable" | "saving_point" | "ramping_down" | "completed"
export type SimulationState = {
  config: SimulationConfig; connected: Record<SimulationDevice, boolean>; currents: number[]
  phase: Phase; paused: boolean; elapsedS: number; phaseElapsedS: number; index: number
  currentA: number; rampFromA: number; powerW: number; wavelengthNm: number; saturated: boolean
  points: SimulationPoint[]; power: Array<{ elapsedS: number; powerW: number }>; events: SimulationEvent[]
  outcome: AutomaticTerminalOutcome | null; reason: string; shutdownConfirmed: boolean
  exportStatus: "none" | "ready" | "failed"; retryingShutdown: boolean
}
export const DEFAULT_SIMULATION_CONFIG: SimulationConfig = { sn: "SIM-001", initialCurrentA: 2, targetCurrentA: 12, stepA: 2, scenario: "normal" }

export function simulationPlan(config: SimulationConfig): { currents: number[]; error: string } {
  const { initialCurrentA: start, targetCurrentA: end, stepA: step } = config
  if (!config.sn.trim()) return { currents: [], error: "请填写样品 SN。" }
  if (![start, end, step].every(Number.isFinite) || start <= 0 || end > 100 || end < start || step <= 0) {
    return { currents: [], error: "仿真电流需满足 0 < 起始 ≤ 目标 ≤ 100 A，步进需大于 0。" }
  }
  const count = Math.floor((end - start) / step + 1e-8) + 1
  if (count > 32) return { currents: [], error: "请增大步进，每次仿真最多 32 个测试点。" }
  const currents = Array.from({ length: count }, (_, index) => Number((start + index * step).toFixed(6)))
  if (Math.abs(currents.at(-1)! - end) > 1e-6) currents.push(end)
  if (currents.length > 32) return { currents: [], error: "请增大步进，每次仿真最多 32 个测试点。" }
  return { currents, error: "" }
}

export function createSimulation(config = DEFAULT_SIMULATION_CONFIG): SimulationState {
  return { config: { ...config }, connected: { powerSupply: true, powerMeter: true, spectrometer: true },
    currents: simulationPlan(config).currents, phase: "idle", paused: false, elapsedS: 0, phaseElapsedS: 0, index: 0,
    currentA: 0, rampFromA: 0, powerW: 0, wavelengthNm: 976, saturated: false,
    points: [], power: [], events: [{ time: 0, message: "虚拟仪器就绪，等待开始。", tone: "info" }],
    outcome: null, reason: "", shutdownConfirmed: true, exportStatus: "none", retryingShutdown: false }
}
export const simulationActive = (state: SimulationState) => state.phase !== "idle" && state.phase !== "completed"
const log = (state: SimulationState, message: string, tone: SimulationEvent["tone"] = "info") => {
  state.events = [...state.events, { time: state.elapsedS, message, tone }].slice(-160)
}
function enter(state: SimulationState, phase: Phase, message: string) {
  state.phase = phase
  state.phaseElapsedS = 0
  log(state, message)
}
export function startSimulation(state: SimulationState, config: SimulationConfig): SimulationState {
  if (simulationActive(state) || (state.phase === "completed" && !state.shutdownConfirmed) || simulationPlan(config).error || Object.values(state.connected).some((connected) => !connected)) return state
  const next = createSimulation({ ...config, sn: config.sn.trim() })
  next.connected = { ...state.connected }
  next.shutdownConfirmed = false
  enter(next, "preparing", `开始仿真 ${next.config.sn} · ${next.currents.length} 个测试点。`)
  return next
}
function finish(state: SimulationState) {
  const uncertain = state.config.scenario === "shutdown-unconfirmed" && !state.retryingShutdown
  state.shutdownConfirmed = !uncertain
  state.currentA = uncertain ? 0.2 : 0
  state.powerW = 0
  state.saturated = false
  state.outcome = uncertain ? "shutdown_unconfirmed" : state.outcome ?? "succeeded"
  state.exportStatus = state.points.length ? state.config.scenario === "export-failed" ? "failed" : "ready" : "none"
  state.phase = "completed"
  state.paused = false
  state.phaseElapsedS = 0
  log(state, uncertain ? "模拟下电未确认：残余电流 0.200 A。" : "模拟输出已关闭，电流归零。", uncertain ? "warning" : "success")
  if (state.exportStatus === "failed") log(state, "模拟导出失败，采样记录仍保留在本页内存。", "warning")
}
export function stopSimulation(state: SimulationState, emergency = false): SimulationState {
  if (!simulationActive(state)) return state
  const next = { ...state, paused: false, outcome: emergency ? "aborted_shutdown_confirmed" as const : "stopped_shutdown_confirmed" as const,
    reason: emergency ? "操作员触发模拟急停。" : "操作员提前结束仿真。", rampFromA: state.currentA }
  if (emergency) { log(next, next.reason, "warning"); finish(next) }
  else enter(next, "ramping_down", "提前结束，开始模拟下电。")
  return next
}
export function toggleSimulationPause(state: SimulationState): SimulationState {
  if (!simulationActive(state) || state.phase === "ramping_down") return state
  const next = { ...state, paused: !state.paused }
  log(next, next.paused ? "虚拟时钟已暂停，采样与阶段冻结。" : "继续仿真。")
  return next
}
export function retrySimulationShutdown(state: SimulationState): SimulationState {
  if (state.phase !== "completed" || state.shutdownConfirmed) return state
  const next = { ...state, retryingShutdown: true, outcome: state.points.length === state.currents.length ? null : "stopped_shutdown_confirmed" as const, rampFromA: state.currentA }
  enter(next, "ramping_down", "重新模拟下电确认。")
  return next
}
export function retrySimulationExport(state: SimulationState): SimulationState {
  if (state.exportStatus !== "failed") return state
  const next = { ...state, exportStatus: "ready" as const }
  log(next, "模拟导出重试成功，可下载 CSV。", "success")
  return next
}

function step(state: SimulationState, dt: number): SimulationState {
  const next = { ...state, elapsedS: state.elapsedS + dt, phaseElapsedS: state.phaseElapsedS + dt }
  const target = next.currents[next.index]
  const faultPoint = next.index === Math.min(1, next.currents.length - 1)
  const unstable = next.config.scenario === "unstable" && faultPoint && next.phase === "waiting_stable"
  next.saturated = next.config.scenario === "saturated" && faultPoint && next.phase === "waiting_stable"
  if (next.phase === "setting_current") next.currentA = next.rampFromA + (target - next.rampFromA) * Math.min(1, next.phaseElapsedS / 2)
  if (next.phase === "ramping_down") next.currentA = next.rampFromA * Math.max(0, 1 - next.phaseElapsedS / 3)
  const base = Math.max(0, next.currentA - 0.4) * 0.113
  next.powerW = Math.max(0, base * (1 + Math.sin(next.elapsedS * 4.7) * (unstable ? 0.085 : 0.0007) + Math.sin(next.elapsedS * 1.3) * 0.0003))
  next.wavelengthNm = 975.9 + next.currentA * 0.025 + Math.sin(next.elapsedS * 0.8) * 0.008
  if (next.phase !== "preparing") next.power = [...next.power, { elapsedS: next.elapsedS, powerW: next.powerW }].slice(-3000)
  if (next.phase === "preparing" && next.phaseElapsedS >= 1 - 1e-8) enter(next, "setting_current", `第 1 点：升流至 ${target} A。`)
  else if (next.phase === "setting_current" && next.phaseElapsedS >= 2 - 1e-8) enter(next, "waiting_stable", `第 ${next.index + 1} 点：等待功率和波长稳定。`)
  else if (next.phase === "waiting_stable") {
    if ((unstable && next.phaseElapsedS >= 8 - 1e-8) || (next.saturated && next.phaseElapsedS >= 3 - 1e-8)) {
      next.outcome = "aborted_shutdown_confirmed"
      next.reason = unstable ? "模拟功率等稳超时，此点未记录。" : "模拟光谱饱和，此点未记录。"
      next.rampFromA = next.currentA
      enter(next, "ramping_down", next.reason)
    } else if (!unstable && !next.saturated && next.phaseElapsedS >= 4 - 1e-8) enter(next, "saving_point", `第 ${next.index + 1} 点：读数稳定，记录中。`)
  } else if (next.phase === "saving_point" && next.phaseElapsedS >= 1 - 1e-8) {
    next.points = [...next.points, { elapsedS: next.elapsedS, currentA: target, powerW: next.powerW, efficiencyPercent: next.powerW / (target * 2.1) * 100, wavelengthNm: next.wavelengthNm }]
    log(next, `已记录 ${target} A · ${next.powerW.toFixed(3)} W。`, "success")
    next.rampFromA = next.currentA
    if (next.index + 1 >= next.currents.length) enter(next, "ramping_down", "全部测试点已记录，开始模拟下电。")
    else { next.index += 1; enter(next, "setting_current", `第 ${next.index + 1} 点：升流至 ${next.currents[next.index]} A。`) }
  } else if (next.phase === "ramping_down" && next.phaseElapsedS >= 3 - 1e-8) finish(next)
  return next
}
export function advanceSimulation(state: SimulationState, deltaS: number): SimulationState {
  if (!simulationActive(state) || state.paused || !Number.isFinite(deltaS) || deltaS <= 0) return state
  let next = state
  let remaining = Math.min(deltaS, 10)
  while (remaining > 1e-8 && simulationActive(next)) {
    const dt = Math.min(0.2, remaining)
    next = step(next, dt)
    remaining -= dt
  }
  return next
}
export function simulationSpectrum(state: SimulationState) {
  const wavelengthNm = Array.from({ length: 401 }, (_, index) => 972 + index * 0.02)
  const intensity = wavelengthNm.map((wavelength, index) => {
    if (!state.currentA || !state.connected.spectrometer) return 0
    const peak = (state.saturated ? 98000 : 18000 * state.powerW) * Math.exp(-0.5 * ((wavelength - state.wavelengthNm) / 0.22) ** 2)
    return Math.min(65535, Math.max(0, peak + 35 + Math.sin(index * 3.1 + state.elapsedS) * 18))
  })
  return { wavelengthNm, intensity }
}

/** Adapt only for the shared visual phase track. No runtime bridge is imported. */
export function simulationSnapshot(state: SimulationState): BackendSnapshot {
  return { ...DEMO_SNAPSHOT, automaticTest: {
    state: state.paused ? "paused" : state.phase, detail: state.events.at(-1)?.message ?? "",
    controlsEnabled: !simulationActive(state), currents: state.currents, currentIndex: state.index,
    currentA: state.currentA, savedPointCount: state.points.length, elapsedS: state.elapsedS,
    runContext: { sn: state.config.sn, station: "仿真工位", testTemperatureC: "25°C", selectedScriptId: "standard" },
    terminalOutcome: state.outcome,
  }, safety: { ...DEMO_SNAPSHOT.safety, hardwareAccess: false, shutdown: { requested: state.phase === "completed" || state.phase === "ramping_down", confirmed: state.shutdownConfirmed, measuredCurrentA: state.currentA, outputOffConfirmed: state.shutdownConfirmed, reason: state.reason } } }
}
export function simulationCsv(state: SimulationState): string {
  const escape = (value: string) => `"${value.replace(/"/g, '""')}"`
  // Prefix formula-like sample names before spreadsheet software opens the CSV.
  const sn = /^[=+\-@\t\r\n]/.test(state.config.sn) ? `'${state.config.sn}` : state.config.sn
  const header = "数据类型,SN,时间(s),电流(A),功率(W),效率(%),中心波长(nm)"
  return "\uFEFF" + [header, ...state.points.map((point) => ["仿真数据", escape(sn), point.elapsedS.toFixed(1), point.currentA.toFixed(3), point.powerW.toFixed(6), point.efficiencyPercent.toFixed(3), point.wavelengthNm.toFixed(4)].join(","))].join("\r\n")
}
