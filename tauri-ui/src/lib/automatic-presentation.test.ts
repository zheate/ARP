import assert from "node:assert/strict"
import test from "node:test"
import { automaticPhase, automaticStoragePresentation, formatTestDuration } from "./automatic-presentation.ts"
import type { BackendSnapshot } from "./backend"

type Automatic = BackendSnapshot["automaticTest"]
const auto = (patch: Partial<Automatic>) => ({ state: "idle", currents: [2, 4, 6], currentIndex: 0, ...patch }) as Automatic
const shutdown = (confirmed: boolean) => ({ requested: true, confirmed, measuredCurrentA: null, outputOffConfirmed: confirmed, reason: "" })

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
