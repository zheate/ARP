import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react"
import { traceMonotoneCurve, type CurvePoint } from "@/lib/monotone-curve"
import { useAppearance } from "@/hooks/use-appearance"
import { chartTrailBoundary, type AppearancePreferences, type LatestPointMarkerStyle } from "@/lib/appearance"

export type CanvasChartLine = {
  key: string
  label: string
  color: string
  axisColor?: string
  yAxisId?: "left" | "right"
  showPoints?: boolean
  showLatestPoint?: boolean
  latestPointStyle?: LatestPointMarkerStyle
  pointShape?: "circle" | "triangle"
  pointStyle?: "solid" | "hollow"
  pointSize?: number
  lineWidth?: number
  interpolation?: "linear" | "monotone"
  dash?: number[]
  connectNulls?: boolean
  hideLegend?: boolean
  /** Only time-series plots that explicitly opt in receive the live tail. */
  trail?: boolean
}

export type CanvasChartAnnotation = {
  label: string
  x: number
  y: number
  color?: string
}

export type CanvasChartAnnotationLegendItem = {
  label: string
  color: string
  value: string
}

type NumericRow = Record<string, number | null>
export type CanvasColumnarData = Record<string, readonly number[]>

type ChartData = {
  length: number
  valueAt: (index: number, key: string) => number | null
  rowAt: (index: number) => NumericRow | undefined
}

type CanvasLineChartProps = {
  data?: NumericRow[]
  columns?: CanvasColumnarData
  xKey: string
  lines: CanvasChartLine[]
  xDomain?: [number, number]
  xTicks?: number[]
  yDomain?: [number, number]
  yMinimumLimit?: number
  rightYDomain?: [number, number]
  annotations?: CanvasChartAnnotation[]
  annotationLegend?: CanvasChartAnnotationLegendItem[]
  ariaLabel: string
  preserveAllPoints?: boolean
  tightInsets?: boolean
  xLabel?: string
  xTickSuffix?: string
  yLabel?: string
  appearance?: Partial<AppearancePreferences>
}

type ChartSize = { width: number; height: number }
const CHART_FONT = "12px 'ARP Interface', 'DIN', 'MiSans', sans-serif"
type HoverState = { index: number } | null
type PendingHover = { index: number; pointerX: number; pointerY: number } | null
type AxisScale = { min: number; max: number; ticks: number[] }
type ChartLayout = {
  left: number
  right: number
  top: number
  bottom: number
  plotWidth: number
  plotHeight: number
  x: AxisScale
  leftY: AxisScale
  rightY: AxisScale | null
}

const EMPTY_SIZE: ChartSize = { width: 0, height: 0 }
const EMPTY_ROWS: NumericRow[] = []
const NUMERIC_ZERO_EPSILON = 1e-12
const ECG_MARKER_COLOR = "#ff375f"
const TOOLTIP_ESTIMATED_WIDTH = 208
const TOOLTIP_ESTIMATED_HEIGHT = 120

function finiteValue(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function chartDataFrom(rows: NumericRow[], columns?: CanvasColumnarData): ChartData {
  if (!columns) {
    return {
      length: rows.length,
      valueAt: (index, key) => finiteValue(rows[index]?.[key]),
      rowAt: (index) => rows[index],
    }
  }
  const lengths = Object.values(columns).map((values) => values.length)
  const length = lengths.length ? Math.min(...lengths) : 0
  return {
    length,
    valueAt: (index, key) => finiteValue(columns[key]?.[index]),
    rowAt: (index) => {
      if (index < 0 || index >= length) return undefined
      return Object.fromEntries(
        Object.entries(columns).map(([key, values]) => [key, finiteValue(values[index])]),
      )
    },
  }
}

function paddedExtent(values: number[], includeZero: boolean): [number, number] {
  if (!values.length) return [0, 1]
  let minimum = Math.min(...values)
  let maximum = Math.max(...values)
  if (Math.abs(minimum) < NUMERIC_ZERO_EPSILON) minimum = 0
  if (Math.abs(maximum) < NUMERIC_ZERO_EPSILON) maximum = 0
  if (includeZero) {
    if (minimum >= 0) minimum = 0
    if (maximum <= 0) maximum = 0
  }
  if (minimum === maximum) {
    const padding = Math.max(Math.abs(minimum) * 0.1, 1)
    return [minimum - padding, maximum + padding]
  }
  const padding = (maximum - minimum) * 0.08
  return [minimum < 0 ? minimum - padding : minimum, maximum > 0 ? maximum + padding : maximum]
}

function niceStep(span: number, targetTickCount: number): number {
  if (!Number.isFinite(span) || span <= 0) return 1
  const rawStep = span / Math.max(1, targetTickCount)
  const magnitude = 10 ** Math.floor(Math.log10(rawStep))
  const normalized = rawStep / magnitude
  const multiplier = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10
  return multiplier * magnitude
}

function nextNiceStep(step: number): number {
  if (!Number.isFinite(step) || step <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(step))
  const normalized = step / magnitude
  const multiplier = normalized < 1.5 ? 2 : normalized < 3.5 ? 5 : 10
  return multiplier * magnitude
}

function createScale(minimum: number, maximum: number, targetTickCount = 5): AxisScale {
  const safeMinimum = Number.isFinite(minimum) ? minimum : 0
  const safeMaximum = Number.isFinite(maximum) ? maximum : safeMinimum + 1
  const normalizedMaximum = safeMaximum === safeMinimum ? safeMinimum + 1 : safeMaximum
  let step = niceStep(normalizedMaximum - safeMinimum, targetTickCount)
  let tickMinimum = Math.floor(safeMinimum / step) * step
  let tickMaximum = Math.ceil(normalizedMaximum / step) * step
  const maximumTickCount = Math.max(4, targetTickCount + 1)
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const tickCount = Math.round((tickMaximum - tickMinimum) / step) + 1
    if (tickCount <= maximumTickCount) break
    step = nextNiceStep(step)
    tickMinimum = Math.floor(safeMinimum / step) * step
    tickMaximum = Math.ceil(normalizedMaximum / step) * step
  }
  const tickCount = Math.min(24, Math.max(2, Math.round((tickMaximum - tickMinimum) / step) + 1))
  const ticks = Array.from({ length: tickCount }, (_, index) => {
    const value = Number((tickMinimum + index * step).toPrecision(12))
    return Math.abs(value) < NUMERIC_ZERO_EPSILON ? 0 : value
  })
  return { min: tickMinimum, max: tickMaximum, ticks }
}

function fixedScale(domain: [number, number], ticks?: number[], targetTickCount = 5, anchorBounds = false): AxisScale {
  let [minimum, maximum] = domain
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return createScale(0, 1)
  if (minimum > maximum) [minimum, maximum] = [maximum, minimum]
  if (minimum === maximum) maximum = minimum + 1
  if (ticks) {
    return {
      min: minimum,
      max: maximum,
      ticks: ticks.filter((value) => value >= minimum && value <= maximum),
    }
  }
  if (anchorBounds) {
    return {
      min: minimum,
      max: maximum,
      ticks: Array.from(
        { length: Math.max(2, targetTickCount) },
        (_, index) => minimum + ((maximum - minimum) * index) / Math.max(1, targetTickCount - 1),
      ),
    }
  }
  const scale = createScale(minimum, maximum, targetTickCount)
  return {
    min: Math.min(minimum, scale.min),
    max: Math.max(maximum, scale.max),
    ticks: scale.ticks,
  }
}

function applyMinimumLimit(scale: AxisScale, minimumLimit?: number): AxisScale {
  if (!Number.isFinite(minimumLimit) || minimumLimit === undefined || scale.min >= minimumLimit) return scale
  const ticks = scale.ticks.filter((value) => value >= minimumLimit)
  if (!ticks.some((value) => Math.abs(value - minimumLimit) < NUMERIC_ZERO_EPSILON)) ticks.unshift(minimumLimit)
  return { ...scale, min: minimumLimit, ticks }
}

function formatTick(value: number, scale: AxisScale): string {
  const span = Math.abs(scale.max - scale.min)
  const step = scale.ticks.length > 1 ? Math.abs(scale.ticks[1] - scale.ticks[0]) : span
  const normalizedValue = Math.abs(value) < Math.max(NUMERIC_ZERO_EPSILON, step * 1e-6) ? 0 : value
  if (normalizedValue === 0) return "0"
  const absolute = Math.abs(normalizedValue)
  if ((absolute >= 100_000 || (absolute < 1e-6 && step < 1e-5)) && span > 0) return normalizedValue.toExponential(1)
  const digits = step >= 10 ? 0 : step >= 1 ? 1 : step >= 0.1 ? 2 : step >= 0.01 ? 2 : step >= 0.001 ? 3 : step >= 0.0001 ? 4 : 6
  return normalizedValue.toFixed(digits).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1")
}

function buildLayout(
  data: ChartData,
  xKey: string,
  lines: CanvasChartLine[],
  size: ChartSize,
  xDomain?: [number, number],
  xTicks?: number[],
  yDomain?: [number, number],
  yMinimumLimit?: number,
  rightYDomain?: [number, number],
  xLabel?: string,
  yLabel?: string,
  tightInsets = false,
): ChartLayout {
  const hasRightAxis = lines.some((line) => line.yAxisId === "right")
  const left = tightInsets ? 52 : 64
  const right = hasRightAxis ? (tightInsets ? 48 : 60) : (tightInsets ? 12 : 20)
  const top = yLabel ? 28 : (tightInsets ? 8 : 12)
  const bottom = xLabel ? (tightInsets ? 34 : 44) : (tightInsets ? 20 : 30)
  const plotHeight = Math.max(1, size.height - top - bottom)
  const verticalTickTarget = Math.min(5, Math.max(2, Math.floor(plotHeight / 36)))
  const xValues: number[] = []
  if (!xDomain) {
    for (let index = 0; index < data.length; index += 1) {
      const value = data.valueAt(index, xKey)
      if (value !== null) xValues.push(value)
    }
  }
  const automaticXExtent = paddedExtent(xValues, false)
  const x = xDomain ? fixedScale(xDomain, xTicks) : createScale(automaticXExtent[0], automaticXExtent[1], Math.max(3, Math.floor(size.width / 110)))
  const leftValues: number[] = []
  const rightValues: number[] = []
  if (!yDomain || hasRightAxis) {
    for (let index = 0; index < data.length; index += 1) {
      const xValue = data.valueAt(index, xKey)
      if (xValue === null || xValue < x.min || xValue > x.max) continue
      for (const line of lines) {
        const value = data.valueAt(index, line.key)
        if (value === null) continue
        if (line.yAxisId === "right") rightValues.push(value)
        else if (!yDomain) leftValues.push(value)
      }
    }
  }
  const leftExtent = paddedExtent(leftValues, true)
  const rightExtent = paddedExtent(rightValues, true)
  return {
    left,
    right,
    top,
    bottom,
    plotWidth: Math.max(1, size.width - left - right),
    plotHeight,
    x,
    leftY: applyMinimumLimit(
      yDomain ? fixedScale(yDomain, undefined, verticalTickTarget, false) : createScale(leftExtent[0], leftExtent[1], verticalTickTarget),
      yMinimumLimit,
    ),
    rightY: hasRightAxis
      ? rightYDomain
        ? fixedScale(rightYDomain, undefined, verticalTickTarget, false)
        : createScale(rightExtent[0], rightExtent[1], verticalTickTarget)
      : null,
  }
}

function mapX(value: number, layout: ChartLayout): number {
  return layout.left + ((value - layout.x.min) / (layout.x.max - layout.x.min)) * layout.plotWidth
}

function mapY(value: number, scale: AxisScale, layout: ChartLayout): number {
  return layout.top + (1 - (value - scale.min) / (scale.max - scale.min)) * layout.plotHeight
}

function downsampleIndexes(data: ChartData, indexes: number[], lines: CanvasChartLine[], limit: number): number[] {
  if (indexes.length <= limit || limit < 4 || lines.length === 0) return indexes
  const valueKey = lines[0].key
  const bucketCount = Math.max(1, Math.floor((limit - 2) / 2))
  const bucketSize = Math.ceil((indexes.length - 2) / bucketCount)
  const selectedIndexes = [indexes[0]]
  for (let start = 1; start < indexes.length - 1; start += bucketSize) {
    const stop = Math.min(indexes.length - 1, start + bucketSize)
    let lowIndex = start
    let highIndex = start
    for (let index = start + 1; index < stop; index += 1) {
      const value = data.valueAt(indexes[index], valueKey)
      const lowValue = data.valueAt(indexes[lowIndex], valueKey)
      const highValue = data.valueAt(indexes[highIndex], valueKey)
      if (value !== null && (lowValue === null || value < lowValue)) lowIndex = index
      if (value !== null && (highValue === null || value > highValue)) highIndex = index
    }
    if (lowIndex === highIndex) selectedIndexes.push(indexes[lowIndex])
    else selectedIndexes.push(...([lowIndex, highIndex].sort((left, right) => left - right).map((index) => indexes[index])))
  }
  selectedIndexes.push(indexes[indexes.length - 1])
  return selectedIndexes.slice(0, limit)
}

function prepareCanvas(canvas: HTMLCanvasElement, size: ChartSize): CanvasRenderingContext2D | null {
  const ratio = Math.min(Math.max(window.devicePixelRatio || 1, 1), 2)
  const pixelWidth = Math.max(1, Math.round(size.width * ratio))
  const pixelHeight = Math.max(1, Math.round(size.height * ratio))
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth
    canvas.height = pixelHeight
  }
  const context = canvas.getContext("2d")
  if (!context) return null
  context.setTransform(ratio, 0, 0, ratio, 0, 0)
  context.clearRect(0, 0, size.width, size.height)
  return context
}

function drawPoint(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  shape: "circle" | "triangle",
  size: number,
  style: "solid" | "hollow" = "solid",
  backgroundColor = "hsl(48 3% 17%)",
  emphasized = false,
): void {
  const radius = size / 2
  context.save()
  context.beginPath()
  if (shape === "triangle") {
    context.moveTo(x, y - radius)
    context.lineTo(x + radius * 0.9, y + radius * 0.75)
    context.lineTo(x - radius * 0.9, y + radius * 0.75)
    context.closePath()
  } else {
    context.arc(x, y, radius, 0, Math.PI * 2)
  }
  const hollow = style === "hollow"
  context.fillStyle = hollow ? backgroundColor : color
  context.strokeStyle = hollow ? color : "hsl(42 7% 25%)"
  context.lineWidth = hollow ? 2 : 1.2
  if (emphasized) {
    context.shadowColor = color
    context.shadowBlur = 6
  }
  context.fill()
  context.stroke()
  context.restore()
}

function drawPaperPlane(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  size: number,
  angle: number,
): void {
  const radius = size / 2
  context.save()
  context.translate(x, y)
  context.rotate(angle)
  context.lineJoin = "round"
  context.lineCap = "round"
  context.fillStyle = color
  context.strokeStyle = "hsl(42 7% 25%)"
  context.lineWidth = 1.2
  context.shadowColor = color
  context.shadowBlur = 4
  context.beginPath()
  context.moveTo(radius * 1.15, 0)
  context.lineTo(-radius * 0.9, -radius * 0.72)
  context.lineTo(-radius * 0.28, 0)
  context.lineTo(-radius * 0.9, radius * 0.72)
  context.closePath()
  context.fill()
  context.stroke()
  context.shadowBlur = 0
  context.beginPath()
  context.moveTo(-radius * 0.9, -radius * 0.72)
  context.lineTo(-radius * 0.28, 0)
  context.lineTo(radius * 1.15, 0)
  context.strokeStyle = "hsl(42 7% 25% / 0.72)"
  context.lineWidth = 0.9
  context.stroke()
  context.restore()
}

function drawComet(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  size: number,
  angle: number,
): void {
  context.save()
  context.translate(x, y)
  context.rotate(angle)
  context.scale(size / 12, size / 12)

  const tail = context.createLinearGradient(-32, 0, 0, 0)
  tail.addColorStop(0, "transparent")
  tail.addColorStop(1, color)
  context.fillStyle = tail
  context.globalAlpha = 0.8
  context.beginPath()
  context.moveTo(0, -3.6)
  context.bezierCurveTo(-10, -4.5, -23, -1.5, -32, 0)
  context.bezierCurveTo(-23, 1.5, -10, 4.5, 0, 3.6)
  context.closePath()
  context.fill()

  context.globalAlpha = 1
  context.fillStyle = color
  context.shadowColor = color
  context.shadowBlur = 9
  context.beginPath()
  context.arc(0, 0, 4.2, 0, Math.PI * 2)
  context.fill()
  context.shadowBlur = 0
  context.fillStyle = "#fff"
  context.beginPath()
  context.arc(0, 0, 2, 0, Math.PI * 2)
  context.fill()
  context.restore()
}

function drawEcgPoint(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
): void {
  context.save()
  context.translate(x, y)
  context.scale(size / 7, size / 7)
  context.fillStyle = ECG_MARKER_COLOR
  context.shadowColor = ECG_MARKER_COLOR
  context.shadowBlur = 8
  context.beginPath()
  context.arc(0, 0, 3, 0, Math.PI * 2)
  context.fill()
  context.shadowBlur = 0
  context.fillStyle = "#fff"
  context.beginPath()
  context.arc(0, 0, 1.4, 0, Math.PI * 2)
  context.fill()
  context.restore()
}

function drawPawPrint(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  size: number,
): void {
  const scale = size / 18
  context.save()
  context.translate(x, y)
  context.scale(scale, scale)
  context.fillStyle = color
  context.strokeStyle = "hsl(42 7% 25%)"
  context.lineWidth = 1.1
  context.shadowColor = color
  context.shadowBlur = 4

  const toes = [
    { x: -5.3, y: -3.2, radius: 1.8 },
    { x: -1.8, y: -5.2, radius: 1.9 },
    { x: 1.8, y: -5.2, radius: 1.9 },
    { x: 5.3, y: -3.2, radius: 1.8 },
  ]
  for (const toe of toes) {
    context.beginPath()
    context.arc(toe.x, toe.y, toe.radius, 0, Math.PI * 2)
    context.fill()
    context.stroke()
  }

  context.beginPath()
  context.moveTo(0, -0.7)
  context.bezierCurveTo(2.5, -0.7, 4.8, 1.4, 4.4, 3.5)
  context.bezierCurveTo(4, 5.5, 1.8, 5.5, 0, 4.4)
  context.bezierCurveTo(-1.8, 5.5, -4, 5.5, -4.4, 3.5)
  context.bezierCurveTo(-4.8, 1.4, -2.5, -0.7, 0, -0.7)
  context.closePath()
  context.fill()
  context.stroke()
  context.restore()
}

// Canvas does not resolve CSS var() expressions like SVG and DOM styles do.
function resolveChartColor(color: string, styles: CSSStyleDeclaration): string {
  const variable = /^var\((--[\w-]+)\)$/.exec(color)
  return variable ? styles.getPropertyValue(variable[1]).trim() || styles.getPropertyValue("--foreground").trim() : color
}

function drawBaseChart(
  canvas: HTMLCanvasElement,
  container: HTMLDivElement,
  size: ChartSize,
  layout: ChartLayout,
  data: ChartData,
  indexes: number[],
  xKey: string,
  lines: CanvasChartLine[],
  annotations: CanvasChartAnnotation[],
  xLabel?: string,
  xTickSuffix?: string,
  yLabel?: string,
  appearance?: AppearancePreferences,
): void {
  const context = prepareCanvas(canvas, size)
  if (!context) return
  const styles = getComputedStyle(container)
  lines = lines.map((line) => ({
    ...line,
    color: resolveChartColor(line.color, styles),
    axisColor: line.axisColor ? resolveChartColor(line.axisColor, styles) : undefined,
  }))
  const border = styles.getPropertyValue("--app-border-strong").trim() || "#686c70"
  const muted = styles.getPropertyValue("--muted-foreground").trim() || "hsl(40 8% 60%)"
  const chartBackground = styles.getPropertyValue("--card").trim() || "hsl(48 3% 17%)"
  context.font = CHART_FONT
  context.lineWidth = 1
  context.strokeStyle = border
  context.fillStyle = muted

  context.save()
  context.strokeStyle = resolveChartColor(muted, styles)
  context.globalAlpha = appearance?.gridOpacity ?? 0.3
  context.setLineDash(appearance?.canvas === "engineering" ? [] : [2, 4])
  for (const tick of appearance?.canvas === "clean" ? [] : layout.leftY.ticks) {
    const y = mapY(tick, layout.leftY, layout)
    context.beginPath()
    context.moveTo(layout.left, y)
    context.lineTo(layout.left + layout.plotWidth, y)
    context.stroke()
  }
  for (const tick of appearance?.canvas === "clean" ? [] : layout.x.ticks) {
    const x = mapX(tick, layout)
    context.beginPath()
    context.moveTo(x, layout.top)
    context.lineTo(x, layout.top + layout.plotHeight)
    context.stroke()
  }
  if (appearance?.canvas === "engineering") {
    context.globalAlpha = appearance.gridOpacity * 0.4
    for (const [ticks, vertical] of [[layout.x.ticks, true], [layout.leftY.ticks, false]] as const) {
      for (let index = 1; index < ticks.length; index += 1) {
        for (let subdivision = 1; subdivision < 5; subdivision += 1) {
          const value = ticks[index - 1] + (ticks[index] - ticks[index - 1]) * subdivision / 5
          const position = vertical ? mapX(value, layout) : mapY(value, layout.leftY, layout)
          context.beginPath()
          context.moveTo(vertical ? position : layout.left, vertical ? layout.top : position)
          context.lineTo(vertical ? position : layout.left + layout.plotWidth, vertical ? layout.top + layout.plotHeight : position)
          context.stroke()
        }
      }
    }
  }
  context.restore()

  // Axes remain visible even when the internal grid is disabled.
  context.save()
  context.strokeStyle = border
  context.globalAlpha = 0.65
  context.beginPath()
  context.moveTo(layout.left, layout.top)
  context.lineTo(layout.left, layout.top + layout.plotHeight)
  context.lineTo(layout.left + layout.plotWidth, layout.top + layout.plotHeight)
  context.stroke()
  context.restore()

  context.textAlign = "right"
  context.textBaseline = "middle"
  context.fillStyle = lines.find((line) => line.yAxisId !== "right")?.axisColor || muted
  for (const tick of layout.leftY.ticks) {
    const y = mapY(tick, layout.leftY, layout)
    context.fillText(formatTick(tick, layout.leftY), layout.left - 7, y)
  }
  if (layout.rightY) {
    context.textAlign = "left"
    context.fillStyle = lines.find((line) => line.yAxisId === "right")?.axisColor || muted
    for (const tick of layout.rightY.ticks) {
      const y = mapY(tick, layout.rightY, layout)
      context.fillText(formatTick(tick, layout.rightY), layout.left + layout.plotWidth + 7, y)
    }
  }
  context.fillStyle = muted
  context.textAlign = "center"
  context.textBaseline = "top"
  for (const tick of layout.x.ticks) {
    context.fillText(`${formatTick(tick, layout.x)}${xTickSuffix ?? ""}`, mapX(tick, layout), layout.top + layout.plotHeight + 7)
  }
  if (xLabel) {
    context.fillText(xLabel, layout.left + layout.plotWidth / 2, size.height - 2)
  }
  if (yLabel) {
    context.textAlign = "left"
    context.textBaseline = "top"
    context.fillText(yLabel, 0, 2)
  }

  context.save()
  context.beginPath()
  context.rect(layout.left, layout.top, layout.plotWidth, layout.plotHeight)
  context.clip()
  for (const line of lines) {
    const scale = line.yAxisId === "right" && layout.rightY ? layout.rightY : layout.leftY
    context.beginPath()
    context.strokeStyle = line.color
    context.lineWidth = (line.lineWidth ?? 2.4) * (appearance?.lineWidth ?? 2.4) / 2.4
    context.setLineDash(line.dash ?? [])
    context.lineJoin = "round"
    context.lineCap = "round"
    let drawing = false
    let latestX: number | null = null
    const curvePoints: (CurvePoint | null)[] | null = line.interpolation === "monotone" ? [] : null
    for (const index of indexes) {
      const xValue = data.valueAt(index, xKey)
      const yValue = data.valueAt(index, line.key)
      if (xValue === null || xValue < layout.x.min || xValue > layout.x.max) {
        curvePoints?.push(null)
        drawing = false
        continue
      }
      if (yValue === null) {
        if (!line.connectNulls) {
          curvePoints?.push(null)
          drawing = false
        }
        continue
      }
      const x = mapX(xValue, layout)
      const y = mapY(yValue, scale, layout)
      latestX = xValue
      if (curvePoints) curvePoints.push({ x, y })
      else if (drawing) context.lineTo(x, y)
      else context.moveTo(x, y)
      drawing = true
    }
    if (curvePoints) traceMonotoneCurve(context, curvePoints)
    const tailBoundary = line.trail && appearance?.trailEnabled
      ? chartTrailBoundary(latestX, appearance.trailSeconds, [layout.x.min, layout.x.max])
      : null
    context.globalAlpha = tailBoundary !== null ? 0.6 : 1
    context.stroke()
    context.globalAlpha = 1
    if (tailBoundary !== null && latestX !== null && latestX > tailBoundary) {
      const tail = context.createLinearGradient(mapX(tailBoundary, layout), 0, mapX(latestX, layout), 0)
      tail.addColorStop(0, "transparent")
      tail.addColorStop(0.45, line.color)
      tail.addColorStop(1, line.color)
      context.strokeStyle = tail
      // Re-stroke the SAME path. No extra samples, interpolation or thinning.
      context.stroke()
    }
    if (line.showLatestPoint && !line.showPoints && line.latestPointStyle === "ecg" && latestX !== null) {
      // Follow the measured path, including its gaps and interpolation. Only
      // the last 72 screen pixels glow; never invent an ECG-shaped signal.
      const tipX = mapX(latestX, layout)
      const trace = context.createLinearGradient(tipX - 72, 0, tipX, 0)
      trace.addColorStop(0, "rgba(255, 55, 95, 0)")
      trace.addColorStop(0.45, "rgba(255, 55, 95, 0.4)")
      trace.addColorStop(0.85, ECG_MARKER_COLOR)
      trace.addColorStop(1, "#ff8da4")
      context.save()
      context.strokeStyle = trace
      context.shadowColor = ECG_MARKER_COLOR
      context.shadowBlur = 6
      context.stroke()
      context.restore()
    }
    context.setLineDash([])

    if (line.showPoints) {
      for (const index of indexes) {
        const xValue = data.valueAt(index, xKey)
        const yValue = data.valueAt(index, line.key)
        if (xValue === null || yValue === null || xValue < layout.x.min || xValue > layout.x.max) continue
        drawPoint(
          context,
          mapX(xValue, layout),
          mapY(yValue, scale, layout),
          line.color,
          line.pointShape ?? "circle",
          line.pointSize ?? 8,
          line.pointStyle,
          chartBackground,
        )
      }
    }
  }

  annotations.forEach((annotation) => {
    if (!Number.isFinite(annotation.x) || !Number.isFinite(annotation.y) || annotation.x < layout.x.min || annotation.x > layout.x.max) return
    const label = annotation.label.replace(/^PEAK\s*(\d+)$/i, "P$1").toUpperCase()
    const color = resolveChartColor(annotation.color ?? (label === "P1" ? "#3b82f6" : label === "P2" ? "var(--chart-1)" : "#9ca3af"), styles)
    const x = mapX(annotation.x, layout)
    const peakY = mapY(annotation.y, layout.leftY, layout)
    const triangleTipY = Math.max(layout.top + 10, peakY - 3)
    const triangleHalfWidth = 5
    const triangleHeight = 8
    context.save()
    context.fillStyle = color
    context.beginPath()
    context.moveTo(x - triangleHalfWidth, triangleTipY - triangleHeight)
    context.lineTo(x + triangleHalfWidth, triangleTipY - triangleHeight)
    context.lineTo(x, triangleTipY)
    context.closePath()
    context.fill()
    context.restore()
  })
  context.restore()

  // Keep the latest-sample marker whole when it lands on a plot boundary.
  // The line stays clipped to the plotting area, while the marker can use the
  // chart gutter that is already reserved for axis labels.
  for (const line of lines) {
    if (line.showPoints || !line.showLatestPoint) continue
    const scale = line.yAxisId === "right" && layout.rightY ? layout.rightY : layout.leftY
    let latestPoint: { x: number; y: number } | null = null
    let previousPoint: { x: number; y: number } | null = null
    for (let index = data.length - 1; index >= 0; index -= 1) {
      const xValue = data.valueAt(index, xKey)
      const yValue = data.valueAt(index, line.key)
      if (xValue === null || yValue === null) continue
      if (xValue < layout.x.min || xValue > layout.x.max) continue
      const point = { x: mapX(xValue, layout), y: mapY(yValue, scale, layout) }
      if (!latestPoint) {
        latestPoint = point
        continue
      }
      previousPoint = point
      break
    }
    if (!latestPoint) continue
    if (line.latestPointStyle === "paper-plane" || line.latestPointStyle === "comet") {
      const angle = previousPoint
        ? Math.atan2(latestPoint.y - previousPoint.y, latestPoint.x - previousPoint.x)
        : 0
      if (line.latestPointStyle === "comet") {
        drawComet(context, latestPoint.x, latestPoint.y, line.color, line.pointSize ?? 12, angle)
      } else {
        drawPaperPlane(context, latestPoint.x, latestPoint.y, line.color, line.pointSize ?? 15, angle)
      }
    } else if (line.latestPointStyle === "ecg") {
      drawEcgPoint(context, latestPoint.x, latestPoint.y, line.pointSize ?? 7)
    } else if (line.latestPointStyle === "paw") {
      drawPawPrint(context, latestPoint.x, latestPoint.y, line.color, line.pointSize ?? 18)
    } else {
      drawPoint(
        context,
        latestPoint.x,
        latestPoint.y,
        line.color,
        line.pointShape ?? "circle",
        line.pointSize ?? 7,
        line.pointStyle ?? "solid",
        chartBackground,
      )
    }
  }

}

function nearestRowIndex(data: ChartData, xKey: string, target: number): number {
  let low = 0
  let high = data.length - 1
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    const value = data.valueAt(middle, xKey) ?? Number.POSITIVE_INFINITY
    if (value < target) low = middle + 1
    else high = middle
  }
  const current = data.valueAt(low, xKey)
  const previous = low > 0 ? data.valueAt(low - 1, xKey) : null
  if (current === null) return Math.max(0, low - 1)
  if (previous !== null && Math.abs(previous - target) <= Math.abs(current - target)) return low - 1
  return low
}

function drawHover(
  canvas: HTMLCanvasElement,
  container: HTMLDivElement,
  size: ChartSize,
  layout: ChartLayout,
  row: NumericRow | undefined,
  xKey: string,
  lines: CanvasChartLine[],
): void {
  const context = prepareCanvas(canvas, size)
  if (!context || !row) return
  const containerStyles = getComputedStyle(container)
  const chartBackground = containerStyles.getPropertyValue("--card").trim() || "hsl(48 3% 17%)"
  const xValue = finiteValue(row[xKey])
  if (xValue === null) return
  const x = mapX(xValue, layout)
  context.save()
  context.strokeStyle = containerStyles.getPropertyValue("--muted-foreground").trim() || "hsl(40 8% 60% / 0.72)"
  context.lineWidth = 1
  context.setLineDash([4, 4])
  context.beginPath()
  context.moveTo(x, layout.top)
  context.lineTo(x, layout.top + layout.plotHeight)
  context.stroke()
  context.setLineDash([])
  lines.forEach((line) => {
    const value = finiteValue(row[line.key])
    if (value === null) return
    const scale = line.yAxisId === "right" && layout.rightY ? layout.rightY : layout.leftY
    drawPoint(
      context,
      x,
      mapY(value, scale, layout),
      resolveChartColor(line.color, containerStyles),
      line.pointShape ?? "circle",
      line.showPoints ? (line.pointSize ?? 8) * 1.12 : 7,
      line.pointStyle,
      chartBackground,
      true,
    )
  })
  context.restore()
}

function tooltipNumber(value: number): string {
  const absolute = Math.abs(value)
  if (absolute < NUMERIC_ZERO_EPSILON) return "0"
  if (absolute >= 100_000 || (absolute > 0 && absolute < 0.001)) return value.toExponential(3)
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 })
}

export function CanvasLineChart({ data = EMPTY_ROWS, columns, xKey, lines, xDomain, xTicks, yDomain, yMinimumLimit, rightYDomain, annotations = [], annotationLegend = [], ariaLabel, preserveAllPoints = false, tightInsets = false, xLabel, xTickSuffix, yLabel, appearance: appearanceOverride }: CanvasLineChartProps) {
  const { appearance } = useAppearance()
  const chartAppearance = useMemo(() => appearanceOverride ? { ...appearance, ...appearanceOverride } : appearance, [appearance, appearanceOverride])
  const containerRef = useRef<HTMLDivElement>(null)
  const baseCanvasRef = useRef<HTMLCanvasElement>(null)
  const hoverCanvasRef = useRef<HTMLCanvasElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const hoverFrameRef = useRef<number | null>(null)
  const pendingHoverRef = useRef<PendingHover>(null)
  const pointerRef = useRef({ x: 0, y: 0 })
  const [size, setSize] = useState<ChartSize>(EMPTY_SIZE)
  const [hover, setHover] = useState<HoverState>(null)
  const [appearanceVersion, setAppearanceVersion] = useState(0)
  const hoverDrawnRef = useRef(false)
  const chartData = useMemo(() => chartDataFrom(data, columns), [columns, data])
  useEffect(() => {
    const observer = new MutationObserver(() => setAppearanceVersion((version) => version + 1))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-accent"] })
    return () => observer.disconnect()
  }, [])
  const layout = useMemo(
    () => buildLayout(chartData, xKey, lines, size, xDomain, xTicks, yDomain, yMinimumLimit, rightYDomain, xLabel, yLabel, tightInsets),
    [chartData, lines, rightYDomain, size, tightInsets, xDomain, xKey, xLabel, xTicks, yDomain, yLabel, yMinimumLimit],
  )
  const visibleIndexes = useMemo(
    () => {
      const indexes: number[] = []
      for (let index = 0; index < chartData.length; index += 1) {
        const xValue = chartData.valueAt(index, xKey)
        if (xValue !== null && xValue >= layout.x.min && xValue <= layout.x.max) indexes.push(index)
      }
      return indexes
    },
    [chartData, layout.x.max, layout.x.min, xKey],
  )
  const displayIndexes = useMemo(
    () => preserveAllPoints
      ? visibleIndexes
      : downsampleIndexes(chartData, visibleIndexes, lines, Math.max(96, Math.floor(layout.plotWidth * 1.25))),
    [chartData, layout.plotWidth, lines, preserveAllPoints, visibleIndexes],
  )

  useLayoutEffect(() => {
    const container = containerRef.current
    if (!container) return
    const update = () => {
      const rectangle = container.getBoundingClientRect()
      setSize((current) => {
        const next = { width: Math.round(rectangle.width), height: Math.round(rectangle.height) }
        return current.width === next.width && current.height === next.height ? current : next
      })
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  // Coalesce visible redraws through requestAnimationFrame. Chromium pauses
  // requestAnimationFrame for hidden/occluded windows, so draw synchronously
  // for background updates to keep the offscreen canvas current.
  const baseDrawArgsRef = useRef<null | {
    size: ChartSize
    layout: ChartLayout
    chartData: ChartData
    displayIndexes: number[]
    xKey: string
    lines: CanvasChartLine[]
    annotations: CanvasChartAnnotation[]
    xLabel?: string
    xTickSuffix?: string
    yLabel?: string
    appearance: AppearancePreferences
  }>(null)
  const baseFrameRef = useRef<number | null>(null)
  const flushBaseDraw = useCallback(() => {
    baseFrameRef.current = null
    const args = baseDrawArgsRef.current
    const canvas = baseCanvasRef.current
    const container = containerRef.current
    if (!args || !canvas || !container || args.size.width <= 0 || args.size.height <= 0) return
    drawBaseChart(canvas, container, args.size, args.layout, args.chartData, args.displayIndexes, args.xKey, args.lines, args.annotations, args.xLabel, args.xTickSuffix, args.yLabel, args.appearance)
  }, [])

  useEffect(() => {
    baseDrawArgsRef.current = { size, layout, chartData, displayIndexes, xKey, lines, annotations, xLabel, xTickSuffix, yLabel, appearance: chartAppearance }
    if (document.visibilityState === "hidden") {
      if (baseFrameRef.current !== null) {
        window.cancelAnimationFrame(baseFrameRef.current)
        baseFrameRef.current = null
      }
      flushBaseDraw()
    } else if (baseFrameRef.current === null) {
      baseFrameRef.current = window.requestAnimationFrame(flushBaseDraw)
    }
  }, [annotations, appearanceVersion, chartAppearance, chartData, displayIndexes, layout, lines, size, xKey, xLabel, xTickSuffix, yLabel, flushBaseDraw])

  useEffect(() => () => {
    if (baseFrameRef.current !== null) window.cancelAnimationFrame(baseFrameRef.current)
  }, [])

  const hoverRow = hover ? chartData.rowAt(hover.index) : undefined

  useEffect(() => {
    const canvas = hoverCanvasRef.current
    const container = containerRef.current
    if (!canvas || !container || size.width <= 0 || size.height <= 0) return
    if (!hoverRow && !hoverDrawnRef.current) return
    drawHover(canvas, container, size, layout, hoverRow, xKey, lines)
    hoverDrawnRef.current = Boolean(hoverRow)
  }, [appearanceVersion, hoverRow, layout, lines, size, xKey])

  const positionTooltip = useCallback((pointerX: number, pointerY: number) => {
    const tooltip = tooltipRef.current
    if (!tooltip) return
    tooltip.style.left = `${Math.min(Math.max(8, pointerX + 14), Math.max(8, size.width - tooltip.offsetWidth - 8))}px`
    tooltip.style.top = `${Math.min(Math.max(8, pointerY - 22), Math.max(8, size.height - tooltip.offsetHeight - 8))}px`
  }, [size.height, size.width])

  const flushHover = useCallback(() => {
    hoverFrameRef.current = null
    const next = pendingHoverRef.current
    if (!next) {
      setHover((current) => current === null ? current : null)
      return
    }
    pointerRef.current = { x: next.pointerX, y: next.pointerY }
    setHover((current) => current?.index === next.index ? current : { index: next.index })
    positionTooltip(next.pointerX, next.pointerY)
  }, [positionTooltip])

  const scheduleHover = useCallback((next: PendingHover) => {
    pendingHoverRef.current = next
    if (hoverFrameRef.current === null) hoverFrameRef.current = window.requestAnimationFrame(flushHover)
  }, [flushHover])

  useEffect(() => () => {
    if (hoverFrameRef.current !== null) window.cancelAnimationFrame(hoverFrameRef.current)
  }, [])

  useEffect(() => {
    if (hover) positionTooltip(pointerRef.current.x, pointerRef.current.y)
  }, [hover, positionTooltip])

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const pointerX = event.nativeEvent.offsetX
    const pointerY = event.nativeEvent.offsetY
    if (pointerX < layout.left || pointerX > layout.left + layout.plotWidth || pointerY < layout.top || pointerY > layout.top + layout.plotHeight) {
      scheduleHover(null)
      return
    }
    const target = layout.x.min + ((pointerX - layout.left) / layout.plotWidth) * (layout.x.max - layout.x.min)
    if (chartData.length === 0) {
      scheduleHover(null)
      return
    }
    scheduleHover({ index: nearestRowIndex(chartData, xKey, target), pointerX, pointerY })
  }

  const tooltipLeft = Math.min(Math.max(8, pointerRef.current.x + 14), Math.max(8, size.width - TOOLTIP_ESTIMATED_WIDTH))
  const tooltipTop = Math.min(Math.max(8, pointerRef.current.y - 22), Math.max(8, size.height - TOOLTIP_ESTIMATED_HEIGHT))

  return (
    <div
      className="relative h-full min-h-0 w-full touch-none overflow-hidden"
      onPointerLeave={() => scheduleHover(null)}
      onPointerMove={handlePointerMove}
      ref={containerRef}
    >
      <canvas aria-label={ariaLabel} className="absolute inset-0 size-full" ref={baseCanvasRef} role="img" />
      <canvas aria-hidden="true" className="pointer-events-none absolute inset-0 size-full" ref={hoverCanvasRef} />
      {annotationLegend.length > 0 && (
        <div aria-label="峰值图例" className="pointer-events-none absolute right-3 top-2 z-[1] flex min-w-48 flex-col gap-1.5 rounded-lg border border-border/55 bg-card/90 px-3.5 py-2.5 text-[13px] font-medium leading-5 text-foreground/80 shadow-sm backdrop-blur-sm">
          {annotationLegend.map((item) => (
            <span className="grid grid-cols-[0.75rem_1fr_auto] items-center gap-2 whitespace-nowrap" key={item.label}>
              <svg aria-hidden="true" className="size-3 overflow-visible" viewBox="0 0 10 10">
                <path d="M0.5 1.5h9L5 9Z" fill={item.color} />
              </svg>
              <span>{item.label}</span>
              <span className="text-right font-semibold tabular-nums text-foreground">{item.value}</span>
            </span>
          ))}
        </div>
      )}
      {hover && hoverRow && (
        <div
          className="chart-tooltip-enter pointer-events-none absolute z-10 min-w-48 rounded-lg border border-[var(--plm-flat-border-strong)] bg-popover/95 px-4 py-3 text-sm leading-5 text-popover-foreground shadow-md shadow-black/20 backdrop-blur-sm"
          ref={tooltipRef}
          role="tooltip"
          style={{ left: tooltipLeft, top: tooltipTop }}
        >
          <p className="mb-1.5 font-semibold">{xKey}: {tooltipNumber(finiteValue(hoverRow[xKey]) ?? 0)}</p>
          {lines.map((line) => {
            const value = finiteValue(hoverRow[line.key])
            return value === null ? null : <div className="flex items-center justify-between gap-5" key={line.key}><span className="flex items-center gap-2"><span className="size-2.5 rounded-full" style={{ backgroundColor: line.color }} />{line.label}</span><span className="font-semibold tabular-nums">{tooltipNumber(value)}</span></div>
          })}
        </div>
      )}
      <div className="sr-only">{lines.map((line) => line.label).join("、")}</div>
    </div>
  )
}
