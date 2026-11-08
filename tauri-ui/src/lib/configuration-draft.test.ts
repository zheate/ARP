import assert from "node:assert/strict"
import test from "node:test"
import { ConfigurationDraft } from "./configuration-draft.ts"
const create = () => new ConfigurationDraft({ current: 1, gain: 1 }, (a, b) => a.current === b.current && a.gain === b.gain)
test("live refresh cannot overwrite an unsaved form", () => {
  const draft = create()
  draft.hydrate({ current: 2, gain: 1 })
  draft.edit({ current: 3, gain: 1 })
  draft.hydrate({ current: 2, gain: 1 })
  assert.equal(draft.getSnapshot().value.current, 3)
  assert.equal(draft.getSnapshot().dirty, true)
})
test("late save and old snapshots cannot clear newer edits", () => {
  const draft = create()
  draft.edit({ current: 2, gain: 1 })
  const revision = draft.getSnapshot().revision
  draft.edit({ current: 3, gain: 1 })
  assert.equal(draft.acknowledge(revision, { current: 2, gain: 1 }), false)
  assert.equal(draft.getSnapshot().dirty, true)
  draft.acknowledge(draft.getSnapshot().revision, { current: 3, gain: 1 })
  draft.hydrate({ current: 2, gain: 1 })
  assert.equal(draft.getSnapshot().value.current, 3)
  draft.hydrate({ current: 3, gain: 1 })
  draft.hydrate({ current: 4, gain: 1 })
  assert.equal(draft.getSnapshot().value.current, 4)
})
test("derived calibration updates preserve draft and failed-save state", () => {
  const draft = create()
  draft.edit({ current: 4, gain: 1 })
  draft.mergeAuthoritative((value) => ({ ...value, gain: 2 }))
  assert.deepEqual(draft.getSnapshot().value, { current: 4, gain: 2 })
  assert.equal(draft.getSnapshot().dirty, true)
})
test("unchanged snapshots preserve identity and unsubscribe stops notifications", () => {
  const draft = create()
  draft.hydrate({ current: 1, gain: 1 })
  const before = draft.getSnapshot()
  let count = 0
  const unsubscribe = draft.subscribe(() => { count++ })
  draft.hydrate({ current: 1, gain: 1 })
  assert.equal(draft.getSnapshot(), before)
  assert.equal(count, 0)
  draft.accept({ current: 2, gain: 1 })
  unsubscribe()
  draft.accept({ current: 3, gain: 1 })
  assert.equal(count, 1)
})
