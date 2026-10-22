import assert from "node:assert/strict"
import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"
import test from "node:test"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { build } from "esbuild"

const root = fileURLToPath(new URL("..", import.meta.url))
const result = await build({
  stdin: { contents: 'export { ManualPage } from "./src/features/manual/page"; export { ManualSampleControls } from "./src/features/manual/sample-controls"; export { DEMO_SNAPSHOT } from "./src/lib/demo-snapshot";', resolveDir: root },
  tsconfig: `${root}/tsconfig.json`, bundle: true, write: false, platform: "node", format: "cjs",
  loader: { ".css": "empty" }, external: ["react", "react-dom", "react-dom/*"],
})
const module = { exports: {} }
new Function("require", "module", "exports", result.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
const { ManualPage, ManualSampleControls, DEMO_SNAPSHOT } = module.exports
const noop = () => {}
const availability = html => [...html.matchAll(/<(?:button|input)\b[^>]*>/g)].map(([tag]) => ({
  disabled: /\sdisabled(?:=|\s|>)/.test(tag),
  className: tag.match(/class="([^"]*)"/)?.[1],
}))

test("manual command waiting preserves unrelated controls and the save icon", () => {
  for (const powerSupplyKind of ["ch341", "tdk", "ru"]) {
    const snapshot = structuredClone(DEMO_SNAPSHOT)
    snapshot.configuration.powerSupplyKind = powerSupplyKind
    snapshot.safety.manualControlsAllowed = true
    snapshot.automaticTest.state = "idle"
    snapshot.devices.powerMeter.running = false
    snapshot.devices.spectrometer.running = false
    const props = { snapshot, config: snapshot.configuration, active: true, controlsAllowed: true, pendingPointCount: 1, run: noop, update: noop }
    for (const Component of [ManualPage, ManualSampleControls]) {
      const idle = renderToStaticMarkup(createElement(Component, { ...props, pending: false }))
      const busy = renderToStaticMarkup(createElement(Component, { ...props, pending: true }))
      assert.deepEqual(availability(busy), availability(idle), `${powerSupplyKind}: ${Component.displayName ?? "manual controls"}`)
      assert.doesNotMatch(busy, /lucide-loader-circle/)
    }
  }
})

test("hardware and automatic-workflow locks still disable manual controls", () => {
  const snapshot = structuredClone(DEMO_SNAPSHOT)
  snapshot.safety.manualControlsAllowed = false
  snapshot.devices.powerSupply.connected = false
  const html = renderToStaticMarkup(createElement(ManualPage, { snapshot, config: snapshot.configuration, active: true, pending: true, run: noop, update: noop }))
  assert.match(html, /<fieldset[^>]*disabled=""/)
  assert.doesNotMatch(html, /归零下电|下电中…/)
})

test("manual card titles distinguish temporary commands from unavailable hardware controls", () => {
  for (const allowed of [true, false]) {
    const snapshot = structuredClone(DEMO_SNAPSHOT)
    snapshot.safety.manualControlsAllowed = allowed
    const html = renderToStaticMarkup(createElement(ManualPage, { snapshot, config: snapshot.configuration, active: true, pending: true, run: noop, update: noop }))
    const headers = [...html.matchAll(/<div\b[^>]*class="[^"]*manual-device-card__header[^>]*>/g)]
    assert.equal(headers.length, 3)
    for (const [header] of headers) {
      assert.match(header, /aria-disabled="true"/)
      assert.ok(header.includes(`data-unavailable="${!allowed}"`))
    }
  }
})
