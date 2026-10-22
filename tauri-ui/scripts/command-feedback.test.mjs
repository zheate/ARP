import assert from "node:assert/strict"
import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { createElement as h } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { build } from "esbuild"

const root = fileURLToPath(new URL("..", import.meta.url))
const result = await build({
  stdin: { contents: 'export { PdPage } from "./src/features/pd/page"; export { AutomaticTestControlCard } from "./src/features/shared/controls"; export { ShippingReportPage } from "./src/features/shipping/report"; export { DEMO_SNAPSHOT } from "./src/lib/demo-snapshot";', resolveDir: root },
  tsconfig: `${root}/tsconfig.json`, bundle: true, write: false, platform: "node", format: "cjs",
  loader: { ".css": "empty" }, external: ["react", "react-dom", "react-dom/*"],
})
const module = { exports: {} }
new Function("require", "module", "exports", result.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
const { PdPage, AutomaticTestControlCard, ShippingReportPage, DEMO_SNAPSHOT } = module.exports
const noop = () => {}
const controls = html => [...html.matchAll(/<(?:button|input|fieldset)\b[^>]*>/g)].map(([tag]) => ({
  disabled: /\sdisabled(?:=|\s|>)/.test(tag),
  className: tag.match(/class="([^"]*)"/)?.[1],
}))

test("PD transient commands preserve form availability while real acquisition locks parameters", () => {
  const snapshot = structuredClone(DEMO_SNAPSHOT)
  snapshot.pd = { devices: [], channels: [], ranges: [], points: [], ...snapshot.pd, state: "idle" }
  const props = { snapshot, active: true, run: noop }
  const idle = renderToStaticMarkup(h(PdPage, { ...props, pending: false }))
  const waiting = renderToStaticMarkup(h(PdPage, { ...props, pending: true }))
  assert.deepEqual(controls(waiting), controls(idle))
  assert.doesNotMatch(waiting, /<fieldset[^>]*\sdisabled=/)
  snapshot.pd.state = "running"
  const running = renderToStaticMarkup(h(PdPage, { ...props, pending: false }))
  assert.match(running, /<fieldset[^>]*\sdisabled=""/)
  assert.match(running, /停止并保存/)
})

test("automatic start and stop do not flash for an unrelated command", () => {
  for (const running of [false, true]) {
    const props = { active: true, config: DEMO_SNAPSHOT.configuration, auto: { ...DEMO_SNAPSHOT.automaticTest, state: running ? "waiting_stable" : "idle", canEnd: running }, controlsLocked: running, onStart: noop, onStop: noop }
    assert.deepEqual(controls(renderToStaticMarkup(h(AutomaticTestControlCard, { ...props, pending: true }))), controls(renderToStaticMarkup(h(AutomaticTestControlCard, { ...props, pending: false }))))
  }
})

test("shipping ignores device-command waiting and retains operational locks", () => {
  const props = { active: true, controlsLocked: false }
  const idle = renderToStaticMarkup(h(ShippingReportPage, props))
  assert.deepEqual(controls(renderToStaticMarkup(h(ShippingReportPage, { ...props, pending: true }))), controls(idle))
  const locked = renderToStaticMarkup(h(ShippingReportPage, { ...props, controlsLocked: true }))
  assert.match(locked, /\sdisabled=""/)
  assert.notDeepEqual(controls(locked), controls(idle))
})
