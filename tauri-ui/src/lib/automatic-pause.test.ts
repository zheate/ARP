import assert from "node:assert/strict"
import test from "node:test"

import { automaticPauseCommand, automaticPauseCopy, pauseRemainingSeconds } from "./automatic-pause.ts"

test("pause reasons map to explicit operator copy", () => {
  assert.equal(automaticPauseCopy("record_persistence_failed")[0], "本地测试记录写入失败")
  assert.equal(automaticPauseCopy("power_meter_disconnected")[0], "功率计连接中断")
})

test("pause actions map to controller-owned commands", () => {
  assert.equal(automaticPauseCommand("adjust_spectrum"), "automatic.adjustSpectrum")
  assert.equal(automaticPauseCommand("safe_end"), "automatic.end")
})

test("pause countdown clamps at zero and supports no deadline", () => {
  assert.equal(pauseRemainingSeconds("2026-08-23T00:00:10.000Z", Date.parse("2026-08-23T00:00:04.100Z")), 6)
  assert.equal(pauseRemainingSeconds("2026-08-23T00:00:10.000Z", Date.parse("2026-08-23T00:00:11.000Z")), 0)
  assert.equal(pauseRemainingSeconds(null, Date.now()), null)
})
