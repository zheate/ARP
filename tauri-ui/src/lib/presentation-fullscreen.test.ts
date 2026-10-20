import assert from "node:assert/strict"
import test from "node:test"
import { acquirePresentationFullscreen } from "./presentation-fullscreen.ts"

test("presentation restores only its own fullscreen and release is idempotent", async () => {
  for (const original of [false, true]) {
    let fullscreen = original
    const calls: string[] = []
    const release = await acquirePresentationFullscreen({
      isFullscreen: () => fullscreen,
      enter: async () => { calls.push("enter"); fullscreen = true },
      exit: async () => { calls.push("exit"); fullscreen = false },
    })
    assert.equal(fullscreen, true)
    await release()
    await release()
    assert.equal(fullscreen, original)
    assert.deepEqual(calls, original ? [] : ["enter", "exit"])
  }
})

test("external fullscreen exit is respected and failed entry is reported", async () => {
  let fullscreen = false
  const release = await acquirePresentationFullscreen({
    isFullscreen: () => fullscreen,
    enter: async () => { fullscreen = true },
    exit: async () => { assert.fail("must not exit fullscreen twice") },
  })
  fullscreen = false
  await release()
  await assert.rejects(acquirePresentationFullscreen({
    isFullscreen: () => false,
    enter: async () => { throw new Error("fullscreen unavailable") },
    exit: async () => { assert.fail("failed entry owns no fullscreen") },
  }), /fullscreen unavailable/)
})

test("fullscreen release can be retried after an exit failure", async () => {
  let tries = 0
  let fullscreen = false
  const release = await acquirePresentationFullscreen({
    isFullscreen: () => fullscreen,
    enter: async () => { fullscreen = true },
    exit: async () => {
      tries += 1
      if (tries === 1) throw new Error("exit failed")
      fullscreen = false
    },
  })
  await assert.rejects(release(), /exit failed/)
  await release()
  assert.equal(tries, 2)
  assert.equal(fullscreen, false)
})
