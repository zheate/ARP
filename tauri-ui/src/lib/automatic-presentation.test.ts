import assert from "node:assert/strict"
import test from "node:test"
import { automaticPhase, automaticStartPresentation, automaticStoragePresentation, formatTestDuration, powerStabilityPresentation } from "./automatic-presentation.ts"
import type { BackendSnapshot } from "./backend"

type Automatic = BackendSnapshot["automaticTest"]
const auto = (patch: Partial<Automatic>) => ({ state: "idle", currents: [2, 4, 6], currentIndex: 0, ...patch }) as Automatic
const shutdown = (confirmed: boolean) => ({ requested: true, confirmed, measuredCurrentA: null, outputOffConfirmed: confirmed, reason: "" })

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
    assert.equal(powerStabilityPresentation({ ...meter, stableElapsedS }).time, "稳定时间待更新")
  }
  assert.equal(powerStabilityPresentation({ ...meter, stable: false }).label, "未稳定")
  assert.equal(powerStabilityPresentation({ ...meter, stable: false }).time, "稳定时间 --")
  for (const patch of [{ quality: "stale" as const }, { quality: "invalid" as const }, { running: false }, { powerW: NaN }]) {
    const status = powerStabilityPresentation({ ...meter, ...patch })
    assert.equal(status.tone, "muted")
    assert.equal(status.time, "稳定时间 --")
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
