import type { BackendSnapshot } from "./backend"

// Actual seconds, shared by the replay timeline and the displayed hold target.
export const DEMO_DWELL_S = 6
const DEMO_STAGE_S = 1.3
const POINTS = [
  { currentA: 2, powerW: 0.28, efficiencyPercent: 6.2 },
  { currentA: 6, powerW: 0.61, efficiencyPercent: 8 },
  { currentA: 10, powerW: 0.76, efficiencyPercent: 8.5 },
]
const POINT_DURATION_S = DEMO_STAGE_S * 3 + DEMO_DWELL_S
const FINAL_HOLD_S = 10
const POINTS_END_S = POINTS.length * POINT_DURATION_S + Math.max(0, FINAL_HOLD_S - DEMO_DWELL_S)
export const DEMO_RUN_DURATION_S = POINTS_END_S + DEMO_STAGE_S

function stageAt(elapsedS: number) {
  // Compare integer milliseconds so exact second boundaries do not round down.
  const elapsedMs = Math.round(elapsedS * 1000)
  const pointDurationMs = Math.round(POINT_DURATION_S * 1000)
  const index = Math.min(POINTS.length - 1, Math.floor(elapsedMs / pointDurationMs))
  const point = POINTS[index]
  const localMs = elapsedMs - index * pointDurationMs
  const holdStartedMs = DEMO_STAGE_S * 2000
  const holdRequiredS = index === POINTS.length - 1 ? FINAL_HOLD_S : 5
  const recordAtMs = holdStartedMs + Math.max(DEMO_DWELL_S, holdRequiredS) * 1000
  const state = elapsedMs >= Math.round(DEMO_RUN_DURATION_S * 1000) ? "completed"
    : elapsedMs >= Math.round(POINTS_END_S * 1000) ? "ramping_down"
    : localMs < DEMO_STAGE_S * 1000 ? "setting_current"
    : localMs < holdStartedMs ? "waiting_stable"
    : localMs < recordAtMs ? "waiting_voltage" : "saving_point"
  const stable = state === "waiting_voltage" || state === "saving_point"
  const currentA = state === "completed" ? 0 : state === "ramping_down" ? 2 : point.currentA
  const powerW = state === "completed" ? 0 : state === "ramping_down" ? 0.05
    : state === "setting_current" ? point.powerW * 0.6 : point.powerW
  const savedCount = POINTS.filter((_, i) => elapsedMs >= i * pointDurationMs + holdStartedMs + Math.max(DEMO_DWELL_S, i === POINTS.length - 1 ? FINAL_HOLD_S : 5) * 1000).length
  const stabilityElapsedS = state === "setting_current" ? 0
    : state === "waiting_stable" ? Math.floor((localMs - DEMO_STAGE_S * 1000) / 100) / 10 : DEMO_STAGE_S
  return { index, state, stable, currentA, powerW, savedCount, stabilityElapsedS, holdRequiredS, holdElapsedS: Math.max(0, localMs - holdStartedMs) / 1000 }
}

/** Deterministic replay: repeated refreshes cannot append duplicate records. */
export function demoRunSnapshot(base: BackendSnapshot, elapsedSeconds: number): BackendSnapshot {
  const elapsedS = Math.min(DEMO_RUN_DURATION_S, Math.max(0, Number.isFinite(elapsedSeconds) ? elapsedSeconds : 0))
  const stage = stageAt(elapsedS)
  const completed = stage.state === "completed"
  const dwell = stage.state === "waiting_voltage"
  const detail = stage.state === "setting_current" ? `第 ${stage.index + 1} 点：正在加载电流至 ${stage.currentA} A`
    : stage.state === "waiting_stable" ? `第 ${stage.index + 1} 点：正在等待功率稳定`
    : dwell ? `第 ${stage.index + 1} 点：功率已稳定，保持 ${Math.min(stage.holdRequiredS, Math.floor(stage.holdElapsedS))} / ${stage.holdRequiredS} s，停留 ${Math.min(DEMO_DWELL_S, Math.floor(stage.holdElapsedS))} / ${DEMO_DWELL_S} s`
    : stage.state === "saving_point" ? `第 ${stage.index + 1} 点：停留完成，已记录数据`
    : completed ? "测试完成，示例数据已归档并安全下电" : "全部点完成，正在安全降电归零"
  const config = base.configuration!
  return {
    ...base,
    seriesRevisions: { ...base.seriesRevisions!, power: 1 + Math.floor(elapsedS * 5), stable: 1 + stage.savedCount },
    devices: {
      ...base.devices,
      powerSupply: { ...base.devices.powerSupply, activeCurrentA: stage.currentA, outputCurrentA: stage.currentA, outputEnabled: !completed },
      powerMeter: { ...base.devices.powerMeter, powerW: stage.powerW, stable: stage.stable, stableElapsedS: stage.stable ? Math.floor(stage.holdElapsedS) : null },
    },
    automaticTest: {
      ...base.automaticTest,
      state: stage.state,
      detail,
      stability: {
        powerStable: stage.stabilityElapsedS >= DEMO_STAGE_S,
        elapsedS: stage.stabilityElapsedS, requiredS: DEMO_STAGE_S,
        holdElapsedS: stage.stabilityElapsedS >= DEMO_STAGE_S ? Math.min(stage.holdRequiredS, Math.floor(stage.holdElapsedS * 10 + 1e-9) / 10) : 0,
        holdRequiredS: stage.holdRequiredS,
        spanW: 0, spanLimitW: stage.currentA === 2 ? 0.2 : null,
        percent: 0, limitPercent: config.stabilityLimitPercent,
        quality: "live", hasReading: stage.state !== "setting_current",
        wavelengthRequired: false, wavelengthStable: false,
      },
      controlsEnabled: false,
      elapsedS: Math.floor(elapsedS),
      runContext: { sn: config.sn, station: config.station, testTemperatureC: config.testTemperatureC, selectedScriptId: config.selectedScriptId },
      currentA: stage.currentA,
      currentIndex: stage.index,
      currents: POINTS.map((point) => point.currentA),
      dwellTimesS: POINTS.map(() => DEMO_DWELL_S),
      currentDwellS: dwell ? DEMO_DWELL_S : 0,
      currentDwellElapsedS: dwell ? Math.min(DEMO_DWELL_S, Math.floor(stage.holdElapsedS)) : null,
      progress: stage.savedCount / POINTS.length,
      canStart: false,
      canEnd: !completed,
      terminalOutcome: completed ? "succeeded" : null,
      savedPointCount: stage.savedCount,
      exportedPointCount: completed ? stage.savedCount : 0,
      journalPath: "/preview/test-records/demo-session.jsonl",
      canNextDevice: completed,
      canRetryExport: false,
      exportStatus: completed ? "completed" : "recording",
      exportPath: completed ? "/preview/DEMO-001.xlsx" : "",
    },
    safety: {
      ...base.safety,
      controlOwner: completed ? "manual" : "automatic",
      manualControlsAllowed: completed,
      shutdown: { requested: completed, confirmed: completed, measuredCurrentA: completed ? 0 : null, outputOffConfirmed: completed, reason: completed ? "示例数据已确认安全下电" : "" },
    },
    measurements: {
      ...base.measurements!,
      power: Array.from({ length: Math.floor(elapsedS * 5) + 1 }, (_, tick) => ({ elapsedS: tick / 5, powerW: stageAt(tick / 5).powerW })),
      stable: POINTS.slice(0, stage.savedCount),
    },
  }
}
