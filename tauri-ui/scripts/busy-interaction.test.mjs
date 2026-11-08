import assert from "node:assert/strict"
import test from "node:test"
import { busyInteraction } from "../src/components/busy-interaction.ts"

const event = key => ({ key, prevented: false, stopped: false,
  preventDefault() { this.prevented = true }, stopPropagation() { this.stopped = true },
})

test("a pending request blocks actions and edits while preserving Tab and Escape", () => {
  const handlers = busyInteraction(true)
  for (const name of ["onClickCapture", "onBeforeInputCapture", "onChangeCapture", "onPasteCapture", "onSubmitCapture"]) {
    const input = event()
    handlers[name](input)
    assert.ok(input.prevented && input.stopped, name)
  }
  for (const key of ["Enter", " ", "ArrowUp", "5"]) {
    const input = event(key)
    handlers.onKeyDownCapture(input)
    assert.ok(input.prevented && input.stopped, key)
  }
  for (const key of ["Tab", "Escape"]) {
    const input = event(key)
    handlers.onKeyDownCapture(input)
    assert.ok(!input.prevented && !input.stopped, key)
  }
})

test("the synchronous lock blocks a second action before React rerenders and releases after failure", () => {
  let pending = false
  const handlers = busyInteraction(() => pending)
  const first = event()
  handlers.onClickCapture(first)
  assert.equal(first.prevented, false)
  pending = true
  const duplicate = event()
  handlers.onClickCapture(duplicate)
  assert.equal(duplicate.prevented, true)
  pending = false
  const retry = event()
  handlers.onClickCapture(retry)
  assert.equal(retry.prevented, false)
})
