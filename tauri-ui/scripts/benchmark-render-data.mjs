// Run from the repository root with node --expose-gc --experimental-strip-types.
// The optional source directory allows the same workload against an old snapshot.
import { createHash } from "node:crypto"
import { resolve } from "node:path"
import { performance } from "node:perf_hooks"
import { pathToFileURL } from "node:url"

if (!global.gc) throw new Error("Run with --expose-gc to measure per-call heap growth")
const sourceDirectory = process.argv[2]
  ? pathToFileURL(`${resolve(process.argv[2])}/`)
  : new URL("../src/lib/", import.meta.url)
const { traceMonotoneCurve } = await import(new URL("monotone-curve.ts", sourceDirectory))
const { comparisonRows, metricComparisonRows } = await import(new URL("data-browser.ts", sourceDirectory))

const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
function measure(run, iterations = 10) {
  for (let index = 0; index < 5; index += 1) run()
  const times = []
  for (let batch = 0; batch < 15; batch += 1) {
    const start = performance.now()
    for (let index = 0; index < iterations; index += 1) run()
    times.push((performance.now() - start) / iterations)
  }
  return { medianMs: median(times), p95BatchMs: Math.max(...times) }
}

const curves = []
for (const length of [1024, 3648, 8192]) {
  const points = Array.from({ length }, (_, index) => {
    const x = 972 + index * 8 / (length - 1)
    return { x, y: 100 + 12000 * Math.exp(-(((x - 976.2) / 0.4) ** 2)) }
  })
  let commands = 0
  const sink = {
    moveTo() { commands += 1 },
    lineTo() { commands += 1 },
    bezierCurveTo() { commands += 1 },
  }
  const run = () => traceMonotoneCurve(sink, points)
  const timing = measure(run, 100)
  const bytes = []
  for (let index = 0; index < 7; index += 1) {
    global.gc()
    const start = process.memoryUsage().heapUsed
    run()
    bytes.push(process.memoryUsage().heapUsed - start)
  }
  commands = 0
  run()
  const hash = createHash("sha256")
  const path = Object.fromEntries(["moveTo", "lineTo", "bezierCurveTo"].map((key) => [
    key, (...args) => hash.update(JSON.stringify([key, ...args])),
  ]))
  traceMonotoneCurve(path, points)
  // Heap growth is an allocation probe, not retained memory or a leak test.
  curves.push({ length, ...timing, heapIncreaseBytes: median(bytes), commandsPerDraw: commands, hash: hash.digest("hex") })
}

const comparisons = []
for (const length of [601, 4000]) {
  for (const duplicate of [false, true]) {
    const points = Array.from({ length }, (_, index) => ({
      currentA: duplicate ? 2 : index / 10,
      powerW: index / 100,
      voltageV: 1,
      rowId: index,
    }))
    const other = points.map((point) => ({ ...point, powerW: point.powerW + 1 }))
    const details = [{ points }, { points: other }]
    const run = () => [comparisonRows(points, other), metricComparisonRows(details, "powerW")]
    const result = run()
    comparisons.push({
      length, duplicate, ...measure(run, 3),
      hash: createHash("sha256").update(JSON.stringify(result)).digest("hex"),
    })
  }
}
console.log(JSON.stringify({ curves, comparisons }, null, 2))
