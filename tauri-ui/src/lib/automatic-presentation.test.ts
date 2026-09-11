import assert from "node:assert/strict"
import test from "node:test"
import { automaticPhase, automaticStartPresentation, automaticStoragePresentation, formatTestDuration } from "./automatic-presentation.ts"
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

test("completion uses recorded count instead of the attempted point index", () => {
  const phase = automaticPhase(auto({ state: "completed", currentIndex: 2, savedPointCount: 1 }), shutdown(true))
  assert.equal(phase.point, 1)
  assert.equal(phase.total, 3)
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
