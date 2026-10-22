import assert from "node:assert/strict"
import test from "node:test"
import { acquirePresentationFullscreen, acquireWindowPresentationFullscreen } from "./presentation-fullscreen.ts"

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

test("maximized windows leave the work area before fullscreen and restore once", async () => {
  for (const originallyMaximized of [false, true]) {
    let fullscreen = false
    let maximized = originallyMaximized
    const calls: string[] = []
    const release = await acquireWindowPresentationFullscreen({
      isFullscreen: () => fullscreen,
      isMaximized: () => maximized,
      unmaximize: async () => { calls.push("unmaximize"); maximized = false },
      maximize: async () => { calls.push("maximize"); maximized = true },
      enter: async () => {
        assert.equal(maximized, false, "Windows otherwise keeps space for the taskbar")
        calls.push("enter")
        fullscreen = true
      },
      exit: async () => { calls.push("exit"); fullscreen = false },
    })
    assert.equal(fullscreen, true)
    await release()
    await release()
    assert.equal(fullscreen, false)
    assert.equal(maximized, originallyMaximized)
    assert.deepEqual(calls, originallyMaximized ? ["unmaximize", "enter", "exit", "maximize"] : ["enter", "exit"])
  }
})

test("pre-existing fullscreen never changes the original maximized state", async () => {
  const untouched = async () => { assert.fail("pre-existing fullscreen must be preserved") }
  const release = await acquireWindowPresentationFullscreen({
    isFullscreen: () => true,
    isMaximized: () => assert.fail("pre-existing fullscreen must be preserved"),
    unmaximize: untouched,
    maximize: untouched,
    enter: untouched,
    exit: untouched,
  })
  await release()
})

test("failed fullscreen entry rolls back the previous maximized window", async () => {
  let maximized = true
  await assert.rejects(acquireWindowPresentationFullscreen({
    isFullscreen: () => false,
    isMaximized: () => maximized,
    unmaximize: async () => { maximized = false },
    maximize: async () => { maximized = true },
    enter: async () => { throw new Error("fullscreen unavailable") },
    exit: async () => { assert.fail("entry failed") },
  }), /fullscreen unavailable/)
  assert.equal(maximized, true)
})

test("window restoration retries without exiting fullscreen a second time", async () => {
  let fullscreen = false
  let maximizeAttempts = 0
  let exits = 0
  const release = await acquireWindowPresentationFullscreen({
    isFullscreen: () => fullscreen,
    isMaximized: () => true,
    unmaximize: async () => {},
    maximize: async () => { if (++maximizeAttempts === 1) throw new Error("restore failed") },
    enter: async () => { fullscreen = true },
    exit: async () => { exits++; fullscreen = false },
  })
  await assert.rejects(release(), /restore failed/)
  await release()
  await release()
  assert.equal(maximizeAttempts, 2)
  assert.equal(exits, 1)
})
