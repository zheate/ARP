import assert from "node:assert/strict"
import test from "node:test"
import { automaticTerminalTitle, shutdownPresentation } from "./safety-state.ts"

test("unconfirmed shutdown never fabricates a zero-current success", () => {
  const presentation = shutdownPresentation("shutdown_unconfirmed", {
    requested: true,
    confirmed: false,
    measuredCurrentA: 0.37,
    outputOffConfirmed: false,
    reason: "电源通信超时",
  })

  assert.deepEqual(presentation, { confirmed: false, label: "下电状态未确认" })
  assert.equal(presentation.label.includes("0.00 A"), false)
})

test("confirmed measured current is shown only after backend confirmation", () => {
  assert.deepEqual(
    shutdownPresentation("stopped_shutdown_confirmed", {
      requested: true,
      confirmed: true,
      measuredCurrentA: 0,
      outputOffConfirmed: true,
      reason: "电源输出已确认关闭",
    }),
    { confirmed: true, label: "已确认安全下电 (0.00 A)" },
  )
})

test("terminal titles distinguish confirmed stop from unconfirmed shutdown", () => {
  assert.equal(automaticTerminalTitle("stopped_shutdown_confirmed"), "测试已提前结束")
  assert.equal(automaticTerminalTitle("shutdown_unconfirmed"), "测试已停止，但下电状态未确认")
})
