import assert from "node:assert/strict"
import test from "node:test"
import { automaticPhase, automaticStabilityPresentation, automaticStartPresentation, automaticStoragePresentation, formatTestDuration, powerStabilityPresentation } from "./automatic-presentation.ts"
import type { BackendSnapshot } from "./backend"

type Automatic = BackendSnapshot["automaticTest"]
const auto = (patch: Partial<Automatic>) => ({ state: "idle", currents: [2, 4, 6], currentIndex: 0, ...patch }) as Automatic
const shutdown = (confirmed: boolean) => ({ requested: true, confirmed, measuredCurrentA: null, outputOffConfirmed: confirmed, reason: "" })

const stability: NonNullable<Automatic["stability"]> = {
  powerStable: false, elapsedS: 6.3, requiredS: 10, spanW: 0.05, spanLimitW: 0.1,
  holdElapsedS: 0, holdRequiredS: 5,
  percent: 2, limitPercent: 0.2, quality: "live", hasReading: true,
  wavelengthRequired: false, wavelengthStable: false,
}

test("confirmation starts only after stability and completes after five seconds", () => {
  const pending = automaticStabilityPresentation(auto({ state: "waiting_stable", stability }))
  assert.equal(pending.label, "判稳中")
  assert.equal(pending.time, "0.0 / 5.0 秒")
  assert.match(pending.detail, /判稳后连续保持 5 秒/)
  const holding = automaticStabilityPresentation(auto({ state: "waiting_voltage", stability: { ...stability, powerStable: true, holdElapsedS: 2.3 } }))
  assert.equal(holding.label, "保持中")
  assert.equal(holding.time, "2.3 / 5.0 秒")
  const complete = automaticStabilityPresentation(auto({ state: "waiting_voltage", stability: { ...stability, powerStable: true, holdElapsedS: 5 } }))
  assert.equal(complete.label, "保持完成")
  assert.equal(complete.time, "5.0 / 5.0 秒")
})

test("new points, lost stability and stale readings clear the confirmation clock", () => {
  const held = { ...stability, powerStable: true, holdElapsedS: 3.2 }
  assert.equal(automaticStabilityPresentation(auto({ state: "setting_current", stability: held })).time, "0.0 / 5.0 秒")
  const stale = automaticStabilityPresentation(auto({ state: "waiting_voltage", stability: { ...held, quality: "stale" } }))
  assert.equal(stale.time, "0.0 / 5.0 秒")
  assert.equal(stale.label, "等待采样")
  const excursion = automaticStabilityPresentation(auto({ state: "waiting_stable", stability: { ...held, powerStable: false, spanW: 0.11 } }))
  assert.equal(excursion.time, "0.0 / 5.0 秒")
  assert.equal(excursion.label, "波动超限")
  assert.equal(automaticStabilityPresentation(auto({ state: "idle", stability })).time, "—")
})

test("final-point display uses the backend ten-second target", () => {
  const final = { ...stability, powerStable: true, holdRequiredS: 10, holdElapsedS: 5 }
  const holding = automaticStabilityPresentation(auto({ state: "waiting_voltage", stability: final }))
  assert.equal(holding.label, "保持中")
  assert.equal(holding.time, "5.0 / 10.0 秒")
  const complete = automaticStabilityPresentation(auto({ state: "waiting_voltage", stability: { ...final, holdElapsedS: 10 } }))
  assert.equal(complete.label, "保持完成")
  assert.equal(complete.time, "10.0 / 10.0 秒")
})

test("spectral testing also waits for wavelength stability before confirmation", () => {
  const wave = automaticStabilityPresentation(auto({ state: "waiting_stable", stability: { ...stability, powerStable: true, wavelengthRequired: true } }))
  assert.equal(wave.label, "等待波长稳定")
  assert.equal(wave.time, "0.0 / 5.0 秒")
})

test("paused and finished runs retain their recorded confirmation interval", () => {
  const held = { ...stability, powerStable: true, holdElapsedS: 2.3, quality: "stale" as const }
  assert.equal(automaticStabilityPresentation(auto({ state: "paused", stability: held })).time, "2.3 / 5.0 秒")
  const complete = automaticStabilityPresentation(auto({ state: "completed", stability: { ...held, holdElapsedS: 5 } }))
  assert.equal(complete.label, "末点已稳定")
  assert.equal(complete.time, "5.0 / 5.0 秒")
  assert.equal(automaticStabilityPresentation(auto({ state: "completed", stability: { ...held, hasReading: false } })).time, "—")
  assert.equal(automaticStabilityPresentation(auto({ state: "waiting_voltage", stability, runContext: { selectedScriptId: "aging", sn: "", station: "", testTemperatureC: "" } })).label, "定时老化")
})

test("older timing fields cannot be mistaken for the post-stability hold", () => {
  const meter = { state: "connected", label: "功率计", detail: "", running: true, quality: "live", powerW: 0.8, stable: true, stableElapsedS: 97 } as const
  const oldStability = { ...stability, powerStable: true, holdElapsedS: undefined, holdRequiredS: undefined }
  assert.equal(automaticStabilityPresentation(auto({ state: "waiting_voltage", stability: oldStability }), meter).time, "—")
  assert.equal(automaticStabilityPresentation(auto({ state: "waiting_voltage" }), meter).time, "—")
  const accepted = automaticStabilityPresentation(auto({ state: "waiting_voltage", stability: { ...stability, powerStable: true, holdElapsedS: 15 } }), meter)
  assert.equal(accepted.time, "5.0 / 5.0 秒")
})

const form = { sn: "555", outputDir: "C:\\test\\ES" }
const missingSn = { code: "missing_sn", message: "请填写产品 SN" }
const missingDirectory = { code: "missing_output_dir", message: "请选择测试数据输出目录" }
const blocked = (blockers = [missingSn]) => auto({ canStart: false, readiness: { ready: false, blockers } })

test("entering SN unlocks start even while the saved snapshot still has an empty SN", () => {
  const snapshot = blocked()
  assert.equal(automaticStartPresentation(snapshot, { ...form, sn: "" }).canStart, false)
  assert.deepEqual(automaticStartPresentation(snapshot, form), { canStart: true, messages: [] })
  assert.deepEqual(snapshot.readiness?.blockers, [missingSn])
})

test("SN and output directory are checked from the current form without a separate save", () => {
  const snapshot = blocked([missingSn, missingDirectory])
  assert.deepEqual(automaticStartPresentation(snapshot, { ...form, outputDir: " " }), {
    canStart: false, messages: [missingDirectory.message],
  })
  assert.deepEqual(automaticStartPresentation(snapshot, form), { canStart: true, messages: [] })
})

test("clearing either required field disables start despite a previously ready snapshot", () => {
  const snapshot = auto({ canStart: true, readiness: { ready: true, blockers: [] } })
  for (const key of ["sn", "outputDir"] as const) {
    const result = automaticStartPresentation(snapshot, { ...form, [key]: " \t " })
    assert.equal(result.canStart, false)
    assert.equal(result.messages.length, 1)
  }
})

test("editing SN preserves device, current, settings and unknown backend blockers", () => {
  for (const code of ["power_supply_not_connected", "output_current_not_zero", "tdk_output_disabled", "power_supply_identity_error", "invalid_test_settings", "script_power_supply_mismatch", "future_blocker"]) {
    const blocker = { code, message: `blocked: ${code}` }
    assert.deepEqual(automaticStartPresentation(blocked([missingSn, blocker]), form), {
      canStart: false, messages: [blocker.message],
    })
  }
})

test("editing SN cannot enable start while a test is active or readiness is unavailable", () => {
  for (const state of ["starting", "waiting_stable", "paused", "ramping_down"]) {
    assert.equal(automaticStartPresentation({ ...blocked(), state }, form).canStart, false)
  }
  assert.equal(automaticStartPresentation({ ...blocked(), canEnd: true }, form).canStart, false)
  assert.equal(automaticStartPresentation(undefined, form).canStart, false)
  assert.equal(automaticStartPresentation(auto({ canStart: false }), form).canStart, false)
  assert.equal(automaticStartPresentation(blocked([]), form).canStart, false)
})

test("legacy settings errors remain visible when structured readiness is unavailable", () => {
  assert.deepEqual(automaticStartPresentation(auto({ canStart: false, settingsError: "配置无效" }), form), {
    canStart: false, messages: ["配置无效"],
  })
})

test("phase track distinguishes recording, ramp down, pause and confirmed completion", () => {
  assert.equal(automaticPhase(auto({ state: "saving_point" }), undefined).activeIndex, 3)
  assert.equal(automaticPhase(auto({ state: "ramping_down" }), undefined).labels[4], "下电中")
  assert.equal(automaticPhase(auto({ state: "waiting_stable" }), undefined).labels[2], "稳定")
  assert.equal(automaticPhase(auto({ state: "paused" }), undefined).labels[2], "已暂停")
  assert.equal(automaticPhase(auto({ state: "completed", terminalOutcome: "succeeded" }), shutdown(false)).complete, false)
  const completed = automaticPhase(auto({ state: "completed", terminalOutcome: "succeeded", savedPointCount: 3 }), shutdown(true))
  assert.equal(completed.complete, true)
  assert.equal(completed.point, 3)
  assert.equal(automaticPhase(auto({ state: "completed", terminalOutcome: "stopped_shutdown_confirmed", savedPointCount: 1 }), shutdown(true)).labels[4], "提前结束")
})

test("only an in-flight phase animates, so the track cannot imply progress while stopped", () => {
  for (const state of ["starting", "setting_current", "waiting_stable", "waiting_voltage", "saving_point", "ramping_down"]) {
    assert.equal(automaticPhase(auto({ state }), undefined).live, true)
  }
  for (const state of ["idle", "paused", "completed"]) {
    assert.equal(automaticPhase(auto({ state }), undefined).live, false)
  }
  assert.equal(automaticPhase(undefined, undefined).live, false)
})

test("a reported hold drives the light as real progress instead of a looping sweep", () => {
  const phase = (patch: Partial<Automatic>) => automaticPhase(auto(patch), undefined)
  assert.equal(phase({ state: "waiting_voltage", currentDwellS: 60, currentDwellElapsedS: 15 }).dwell, 0.25)
  // The backend reports the number only while a hold runs, so its absence means "no denominator".
  assert.equal(phase({ state: "waiting_voltage", currentDwellS: 60 }).dwell, null)
  assert.equal(phase({ state: "waiting_voltage", currentDwellS: 60, currentDwellElapsedS: null }).dwell, null)
  assert.equal(phase({ state: "waiting_stable", currentDwellS: 60, currentDwellElapsedS: 15 }).dwell, 0.25)
  // A zero target cannot be divided, and the light must not run past the end of the segment.
  assert.equal(phase({ state: "waiting_voltage", currentDwellS: 0, currentDwellElapsedS: 5 }).dwell, null)
  assert.equal(phase({ state: "waiting_voltage", currentDwellS: 60, currentDwellElapsedS: 60 }).dwell, 1)
  assert.equal(phase({ state: "waiting_voltage", currentDwellS: 60, currentDwellElapsedS: 75 }).dwell, 1)
  // A stopped run never advances, however stale the reported hold is.
  for (const state of ["idle", "paused", "completed"]) {
    assert.equal(phase({ state, currentDwellS: 60, currentDwellElapsedS: 15 }).dwell, null)
  }
})

test("completion uses recorded count instead of the attempted point index", () => {
  const phase = automaticPhase(auto({ state: "completed", currentIndex: 2, savedPointCount: 1 }), shutdown(true))
  assert.equal(phase.point, 1)
  assert.equal(phase.total, 3)
})

test("power stability requires a live reading and a backend duration instead of a local timer", () => {
  const meter = { state: "connected", label: "功率计", detail: "", running: true, quality: "live", powerW: 0.8, stable: true, stableElapsedS: 6 } as const
  assert.deepEqual(powerStabilityPresentation(meter), { label: "已稳定", time: "持续 6 s", tone: "success" })
  assert.equal(powerStabilityPresentation({ ...meter, stableElapsedS: 0 }).time, "持续 0 s")
  for (const stableElapsedS of [undefined, null, NaN, Infinity, -1]) {
    assert.equal(powerStabilityPresentation({ ...meter, stableElapsedS }).time, "待更新")
  }
  assert.equal(powerStabilityPresentation({ ...meter, stable: false }).label, "未稳定")
  assert.equal(powerStabilityPresentation({ ...meter, stable: false }).time, "—")
  for (const patch of [{ quality: "stale" as const }, { quality: "invalid" as const }, { running: false }, { powerW: NaN }]) {
    const status = powerStabilityPresentation({ ...meter, ...patch })
    assert.equal(status.tone, "muted")
    assert.equal(status.time, "—")
  }
})

test("summary keeps journal persistence separate from export failures", () => {
  const saved = automaticStoragePresentation(auto({ savedPointCount: 2, journalPath: "/local/run.jsonl", exportStatus: "failed", exportPath: "" }))
  assert.equal(saved.label, "本地已保存")
  assert.equal(saved.exportLabel, "Excel 导出失败")
  assert.equal(saved.saved, true)
  assert.equal(automaticStoragePresentation(auto({ savedPointCount: 0, exportPath: "/old/file.xlsx", exportStatus: "failed" })).saved, false)
})

test("summary duration handles unavailable, minute and hour ranges", () => {
  assert.equal(formatTestDuration(null), "—")
  assert.equal(formatTestDuration(NaN), "—")
  assert.equal(formatTestDuration(-1), "—")
  assert.equal(formatTestDuration(0), "00:00")
  assert.equal(formatTestDuration(156.8), "02:36")
  assert.equal(formatTestDuration(3661), "1:01:01")
})
