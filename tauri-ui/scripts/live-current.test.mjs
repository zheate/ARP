import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import { createRequire } from "node:module"
import test from "node:test"
import { fileURLToPath } from "node:url"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { build } from "esbuild"

// Render the actual page in both layouts, with snapshots that deliberately
// keep the planned test point at 5 A while the supply current changes.
const root = fileURLToPath(new URL("..", import.meta.url))
const bundled = await build({
  stdin: {
    contents: 'export { AutomaticPage } from "./src/App.tsx"; export { DEMO_SNAPSHOT } from "./src/hooks/use-backend-snapshot.ts";',
    resolveDir: root,
  },
  tsconfig: `${root}/tsconfig.json`,
  bundle: true, write: false, format: "cjs", platform: "node",
  external: ["react", "react-dom", "react-dom/*"],
  plugins: [{
    name: "expose-page-fixtures",
    setup(builder) {
      builder.onLoad({ filter: /(?:App\.tsx|use-backend-snapshot\.ts)$/ }, async ({ path }) => ({
        contents: `${await readFile(path, "utf8")}\nexport { ${path.endsWith("App.tsx") ? "AutomaticPage" : "DEMO_SNAPSHOT"} };`,
        loader: "tsx",
      }))
    },
  }],
})
const module = { exports: {} }
new Function("require", "module", "exports", bundled.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports)
const { AutomaticPage, DEMO_SNAPSHOT } = module.exports
const noop = () => {}

function pageProps({ measured = 0, active = 0, spectrum = false, kind = "tdk", connected = true, state = "completed" } = {}) {
  const snapshot = structuredClone(DEMO_SNAPSHOT)
  Object.assign(snapshot.automaticTest, { currentA: 5, state })
  Object.assign(snapshot.devices.powerSupply, { outputCurrentA: measured, activeCurrentA: active, connected })
  snapshot.devices.spectrometer.running = spectrum
  snapshot.configuration.powerSupplyKind = kind
  return {
    snapshot, config: snapshot.configuration, testScripts: snapshot.testScripts,
    active: true, controlsLocked: false, pending: false, stationSwitchDisabled: false,
    curveLineStyle: "solid", latestPointMarkerStyle: "point",
    update: noop, run: noop, saveConfiguration: noop, onScriptSelect: noop, onStationChange: noop,
  }
}

function currentText(props) {
  const html = renderToStaticMarkup(createElement(AutomaticPage, props))
  const match = html.match(/class="automatic-metric-card__current">(.*?)<\/span>/)
  assert.ok(match, "Live Power current must be rendered")
  return match[1].replace(/<!--.*?-->/g, "")
}

for (const spectrum of [false, true]) {
  test(`Live Power ${spectrum ? "spectrum" : "power-only"} layout follows measured ramp and completed zero`, () => {
    for (const measured of [3.98, 2.98, 1.98, 0]) {
      assert.equal(currentText(pageProps({ spectrum, measured, active: 5, state: measured ? "ramping_down" : "completed" })), `${measured} A`)
    }
  })
}

test("missing or disconnected TDK readback never substitutes the last test point or setpoint", () => {
  for (const props of [pageProps({ measured: null, active: 5 }), pageProps({ measured: 5, connected: false })]) {
    assert.equal(currentText(props), "-- A")
  }
})

test("CH341 display follows each successful command, including zero, without stale readback", () => {
  for (const active of [4, 3, 2, 1, 0]) {
    assert.equal(currentText(pageProps({ kind: "ch341", active, measured: 5 })), `${active} A`)
  }
})

test("memoized automatic page refreshes when only measured supply current changes", () => {
  const previous = pageProps({ measured: 4.98 })
  const next = {
    ...previous,
    snapshot: {
      ...previous.snapshot,
      devices: { ...previous.snapshot.devices, powerSupply: { ...previous.snapshot.devices.powerSupply, outputCurrentA: 3.98 } },
    },
  }
  assert.equal(AutomaticPage.compare(previous, next), false)
})

test("both automatic layouts display stable duration and clear it for stale readings", () => {
  for (const spectrum of [false, true]) {
    const props = pageProps({ spectrum, state: "waiting_voltage" })
    Object.assign(props.snapshot.devices.powerMeter, { stable: true, stableElapsedS: 7, quality: "live" })
    let html = renderToStaticMarkup(createElement(AutomaticPage, props))
    assert.match(html, /功率稳定状态：已稳定，持续 7 s/)
    props.snapshot.devices.powerMeter.quality = "stale"
    html = renderToStaticMarkup(createElement(AutomaticPage, props))
    assert.match(html, /功率稳定状态：采样中断，稳定时间 --/)
    assert.doesNotMatch(html, /持续 7 s/)
  }
})

test("memoized automatic page refreshes when only stable duration changes", () => {
  const previous = pageProps()
  const next = {
    ...previous,
    snapshot: {
      ...previous.snapshot,
      devices: { ...previous.snapshot.devices, powerMeter: { ...previous.snapshot.devices.powerMeter, stableElapsedS: 9 } },
    },
  }
  assert.equal(AutomaticPage.compare(previous, next), false)
})
