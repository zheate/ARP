import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react"
import { traceMonotoneCurve, type CurvePoint } from "@/lib/monotone-curve"
import { useAppearance } from "@/hooks/use-appearance"
import { chartTrailBoundary, isAnimatedLatestPointMarker, latestPointMarkerSize, type AppearancePreferences, type LatestPointMarkerStyle } from "@/lib/appearance"
import { StaticLatestPointMarker } from "./static-latest-point-marker"
import { FlappyBirdMarker } from "./flappy-bird-marker"
import { CharacterSpriteMarker, DINOSAUR_FOOT_BASELINE } from "./character-sprite-marker"
import { sparseChartIndex } from "@/lib/sparse-chart-index"
import { curveRevealFrame, retargetCurveReveal, traceRevealedCurve, type CurveRevealMotion } from "@/lib/chart-curve-reveal"

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
  /** Display-only reveal for live endpoints. Never changes the source samples. */
  animateLatestPoint?: boolean
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
  seriesIndexes?: ReadonlyMap<string, readonly number[]> | null
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
  /** Scale axis text, gutters and latest-point markers for distant viewing. */
  displayScale?: number
  xLabel?: string
  xTickSuffix?: string
  yLabel?: string
  appearance?: Partial<AppearancePreferences>
  /** Opt-in analysis only. Selects native samples, never a point on an interpolated line. */
  onPointSelect?: (point: { rowIndex: number; seriesKey: string }) => void
  selectedPoints?: CanvasChartAnnotation[]
  xValueFormatter?: (value: number) => string
}

type ChartSize = { width: number; height: number }
type AnimatedMarkerPosition = { key: string; x: number; y: number; size: number; anchorY: number; angle?: number }

function latestPointMarkerAnchorY(style: LatestPointMarkerStyle | undefined, height: number): number {
  return height * (style === "dinosaur" || style === "green-dinosaur" ? DINOSAUR_FOOT_BASELINE : 0.5)
}
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
// Hover state only changes the overlay, not the base chart's effect inputs.
const EMPTY_ANNOTATIONS: CanvasChartAnnotation[] = []
const EMPTY_ANNOTATION_LEGEND: CanvasChartAnnotationLegendItem[] = []
const NUMERIC_ZERO_EPSILON = 1e-12
const TOOLTIP_ESTIMATED_WIDTH = 208
const TOOLTIP_ESTIMATED_HEIGHT = 120

function interpolateChartLayout(from: ChartLayout, to: ChartLayout, progress: number): ChartLayout {
  const interpolateScale = (left: AxisScale, right: AxisScale): AxisScale => {
    const min = left.min + (right.min - left.min) * progress
    const max = left.max + (right.max - left.max) * progress
    return { min, max, ticks: right.ticks.filter(tick => tick >= min && tick <= max) }
  }
  return {
    ...to,
    left: from.left + (to.left - from.left) * progress,
    right: from.right + (to.right - from.right) * progress,
    top: from.top + (to.top - from.top) * progress,
    bottom: from.bottom + (to.bottom - from.bottom) * progress,
    plotWidth: from.plotWidth + (to.plotWidth - from.plotWidth) * progress,
    plotHeight: from.plotHeight + (to.plotHeight - from.plotHeight) * progress,
    x: interpolateScale(from.x, to.x),
    leftY: interpolateScale(from.leftY, to.leftY),
    rightY: from.rightY && to.rightY ? interpolateScale(from.rightY, to.rightY) : to.rightY,
  }
}

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
  // Multi-spectrum overlays can contain hundreds of thousands of samples.
  // Passing them as function arguments exceeds the WebView's call-stack limit.
  let minimum = Infinity
  let maximum = -Infinity
  for (const value of values) {
    minimum = Math.min(minimum, value)
    maximum = Math.max(maximum, value)
  }
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
  displayScale = 1,
): ChartLayout {
  const hasRightAxis = lines.some((line) => line.yAxisId === "right")
  const left = (tightInsets ? 52 : 64) * displayScale
  // DOM sprites may extend beyond the clipped curve, including its top/right edges.
  let markerRightGutter = 0
  let markerTopGutter = 0
  for (const line of lines) {
    if (line.showPoints || !line.showLatestPoint) continue
    const markerSize = (line.pointSize ?? latestPointMarkerSize(line.latestPointStyle)) * displayScale
    const markerHeight = line.latestPointStyle === "flappy-bird" ? markerSize * 2 / 3 : markerSize
    const extent = markerSize * 0.65
    markerRightGutter = Math.max(markerRightGutter, extent + 2)
    markerTopGutter = Math.max(markerTopGutter, extent + 2, latestPointMarkerAnchorY(line.latestPointStyle, markerHeight) + 2)
  }
  const right = Math.max(markerRightGutter, (hasRightAxis ? (tightInsets ? 48 : 60) : (tightInsets ? 12 : 20)) * displayScale, displayScale > 1 ? 32 * displayScale : 0)
  const top = Math.max(markerTopGutter, (yLabel ? 28 : (tightInsets ? 8 : 12)) * displayScale, displayScale > 1 ? 32 * displayScale : 0)
  const bottom = (xLabel ? (tightInsets ? 34 : 44) : (tightInsets ? 20 : 30)) * displayScale
  const plotHeight = Math.max(1, size.height - top - bottom)
  const verticalTickTarget = Math.min(5, Math.max(2, Math.floor(plotHeight / (36 * displayScale))))
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
  if ((!yDomain || hasRightAxis) && data.seriesIndexes) {
    for (const line of lines) {
      for (const index of data.seriesIndexes.get(line.key) ?? []) {
        const xValue = data.valueAt(index, xKey)
        if (xValue === null || xValue < x.min || xValue > x.max) continue
        const value = data.valueAt(index, line.key)
        if (value === null) continue
        if (line.yAxisId === "right") rightValues.push(value)
        else if (!yDomain) leftValues.push(value)
      }
    }
  } else if (!yDomain || hasRightAxis) {
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
  xValueFormatter?: (value: number) => string,
  revealX?: number,
  displayScale = 1,
): AnimatedMarkerPosition[] {
  const context = prepareCanvas(canvas, size)
  if (!context) return []
  const animatedMarkers: AnimatedMarkerPosition[] = []
  const styles = getComputedStyle(container)
  lines = lines.map((line) => ({
    ...line,
    color: resolveChartColor(line.color, styles),
    axisColor: line.axisColor ? resolveChartColor(line.axisColor, styles) : undefined,
  }))
  const border = styles.getPropertyValue("--app-border-strong").trim() || "#686c70"
  const muted = styles.getPropertyValue("--muted-foreground").trim() || "hsl(40 8% 60%)"
  const chartBackground = styles.getPropertyValue("--card").trim() || "hsl(48 3% 17%)"
  const fontSize = (Number.parseFloat(styles.getPropertyValue("--chart-font-size")) || 12) * displayScale
  context.font = CHART_FONT.replace("12px", `${fontSize}px`)
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
    context.fillText(formatTick(tick, layout.leftY), layout.left - 7 * displayScale, y)
  }
  if (layout.rightY) {
    context.textAlign = "left"
    context.fillStyle = lines.find((line) => line.yAxisId === "right")?.axisColor || muted
    for (const tick of layout.rightY.ticks) {
      const y = mapY(tick, layout.rightY, layout)
      context.fillText(formatTick(tick, layout.rightY), layout.left + layout.plotWidth + 7 * displayScale, y)
    }
  }
  context.fillStyle = muted
  context.textAlign = "center"
  context.textBaseline = "top"
  for (const tick of layout.x.ticks) {
    // Long date labels stay inside the plot; the default numerical axes keep their alignment.
    if (xValueFormatter && layout.x.ticks.length > 1) {
      context.textAlign = tick === layout.x.ticks[0] ? "left" : tick === layout.x.ticks[layout.x.ticks.length - 1] ? "right" : "center"
    }
    context.fillText(`${xValueFormatter ? xValueFormatter(tick) : formatTick(tick, layout.x)}${xTickSuffix ?? ""}`, mapX(tick, layout), layout.top + layout.plotHeight + 7 * displayScale)
  }
  context.textAlign = "center"
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
  // The mask retains the exact visible/downsampled row selection. In particular,
  // an excluded x value must not create a new path break between visible rows.
  const visibleMask = data.seriesIndexes ? new Uint8Array(data.length) : null
  if (visibleMask) for (const index of indexes) visibleMask[index] = 1
  for (const line of lines) {
    const lineIndexes = data.seriesIndexes?.get(line.key) ?? indexes
    const scale = line.yAxisId === "right" && layout.rightY ? layout.rightY : layout.leftY
    context.beginPath()
    context.strokeStyle = line.color
    context.lineWidth = (line.lineWidth ?? 2.4) * (appearance?.lineWidth ?? 2.4) / 2.4
    context.setLineDash(line.dash ?? [])
    context.lineJoin = "round"
    context.lineCap = "round"
    let drawing = false
    let latestX: number | null = null
    // Smooth following belongs to the live trace, including plain SVG dots.
    // Character frame animation remains independent of this display reveal.
    const animated = !line.showPoints && line.showLatestPoint && (line.animateLatestPoint || isAnimatedLatestPointMarker(line.latestPointStyle))
    const revealing = animated && line.animateLatestPoint && revealX !== undefined
    const curvePoints: (CurvePoint | null)[] | null = line.interpolation === "monotone" || animated ? [] : null
    for (const index of lineIndexes) {
      if (visibleMask && !visibleMask[index]) continue
      const xValue = data.valueAt(index, xKey)
      const yValue = data.valueAt(index, line.key)
      // During axis expansion the next sample lies beyond the DISPLAYED bound.
      // Retain it to split that segment; the prefix and plot clip bound pixels.
      if (xValue === null || xValue < layout.x.min || (!revealing && xValue > layout.x.max)) {
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
    if (curvePoints && animated) {
      const cutoff = line.animateLatestPoint && revealX !== undefined ? mapX(revealX, layout) : Infinity
      let angle = 0
      const endpoint = traceRevealedCurve(context, curvePoints, line.interpolation === "monotone", cutoff,
        line.latestPointStyle === "paper-plane" ? (direction) => { angle = direction } : undefined)
      if (endpoint) {
        const markerSize = (line.pointSize ?? latestPointMarkerSize(line.latestPointStyle)) * displayScale
        const markerHeight = line.latestPointStyle === "flappy-bird" ? markerSize * 2 / 3 : markerSize
        animatedMarkers.push({ key: line.key, ...endpoint, size: markerSize, anchorY: latestPointMarkerAnchorY(line.latestPointStyle, markerHeight), angle })
      }
      if (line.animateLatestPoint && revealX !== undefined && latestX !== null) latestX = Math.min(latestX, revealX)
    } else if (curvePoints) traceMonotoneCurve(context, curvePoints)
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
    context.setLineDash([])

    if (line.showPoints) {
      for (const index of lineIndexes) {
        if (visibleMask && !visibleMask[index]) continue
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
    if (line.showPoints || !line.showLatestPoint || line.animateLatestPoint || isAnimatedLatestPointMarker(line.latestPointStyle)) continue
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
    const angle = previousPoint ? Math.atan2(latestPoint.y - previousPoint.y, latestPoint.x - previousPoint.x) : 0
    const markerSize = (line.pointSize ?? latestPointMarkerSize(line.latestPointStyle)) * displayScale
    animatedMarkers.push({ key: line.key, ...latestPoint, size: markerSize, anchorY: markerSize / 2,
      angle: line.latestPointStyle === "paper-plane" ? angle : 0 })
  }

  return animatedMarkers
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

export function CanvasLineChart({ data = EMPTY_ROWS, columns, xKey, lines, xDomain, xTicks, yDomain, yMinimumLimit, rightYDomain, annotations = EMPTY_ANNOTATIONS, annotationLegend = EMPTY_ANNOTATION_LEGEND, ariaLabel, preserveAllPoints = false, tightInsets = false, displayScale = 1, xLabel, xTickSuffix, yLabel, appearance: appearanceOverride, onPointSelect, selectedPoints, xValueFormatter }: CanvasLineChartProps) {
  const { appearance } = useAppearance()
  const chartAppearance = useMemo(() => appearanceOverride ? { ...appearance, ...appearanceOverride } : appearance, [appearance, appearanceOverride])
  const containerRef = useRef<HTMLDivElement>(null)
  const baseCanvasRef = useRef<HTMLCanvasElement>(null)
  const animatedMarkerElementsRef = useRef(new Map<string, HTMLDivElement>())
  const hoverCanvasRef = useRef<HTMLCanvasElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const hoverFrameRef = useRef<number | null>(null)
  const pendingHoverRef = useRef<PendingHover>(null)
  const pointerRef = useRef({ x: 0, y: 0 })
  const [size, setSize] = useState<ChartSize>(EMPTY_SIZE)
  const [hover, setHover] = useState<HoverState>(null)
  const [appearanceVersion, setAppearanceVersion] = useState(0)
  const hoverDrawnRef = useRef(false)
  const baseChartData = useMemo(() => chartDataFrom(data, columns), [columns, data])
  const seriesIndexes = useMemo(
    () => !columns && preserveAllPoints && data.length >= 10_000 && lines.length >= 4 && lines.every((line) => line.connectNulls)
      ? sparseChartIndex(data, lines.map((line) => line.key)) : null,
    [columns, data, lines, preserveAllPoints],
  )
  const chartData = useMemo(() => seriesIndexes ? { ...baseChartData, seriesIndexes } : baseChartData, [baseChartData, seriesIndexes])
  useEffect(() => {
    const observer = new MutationObserver(() => setAppearanceVersion((version) => version + 1))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-accent"] })
    return () => observer.disconnect()
  }, [])
  const layout = useMemo(
    () => buildLayout(chartData, xKey, lines, size, xDomain, xTicks, yDomain, yMinimumLimit, rightYDomain, xLabel, yLabel, tightInsets, displayScale),
    [chartData, displayScale, lines, rightYDomain, size, tightInsets, xDomain, xKey, xLabel, xTicks, yDomain, yLabel, yMinimumLimit],
  )
  const markerLines = useMemo(() => lines.filter((line) => !line.showPoints && line.showLatestPoint), [lines])
  const animatedLines = useMemo(() => markerLines.filter((line) => line.animateLatestPoint), [markerLines])
  const latestRevealX = useMemo(() => {
    let latest: number | null = null
    for (const line of animatedLines) {
      if (!line.animateLatestPoint) continue
      for (let index = chartData.length - 1; index >= 0; index -= 1) {
        const x = chartData.valueAt(index, xKey)
        const y = chartData.valueAt(index, line.key)
        if (x === null || y === null || x < layout.x.min || x > layout.x.max) continue
        latest = latest === null ? x : Math.max(latest, x)
        break
      }
    }
    return latest
  }, [animatedLines, chartData, layout, size, xKey])
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
    // ResizeObserver delivers the initial size as well as later changes before
    // paint. Reading the rect here used to force layout for each newly mounted
    // chart during navigation, then repeat that read in the observer callback.
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return
      const rectangle = entry.contentRect
      setSize((current) => {
        const next = { width: Math.round(rectangle.width), height: Math.round(rectangle.height) }
        return current.width === next.width && current.height === next.height ? current : next
      })
    })
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
    xValueFormatter?: (value: number) => string
    displayScale: number
  }>(null)
  const baseFrameRef = useRef<number | null>(null)
  const revealMotionRef = useRef<CurveRevealMotion | null>(null)
  const revealFromLayoutRef = useRef<ChartLayout | null>(null)
  const presentedLayoutRef = useRef<ChartLayout | null>(null)
  const reducedMotionRef = useRef(false)
  const flushBaseDraw = useCallback((): void => {
    baseFrameRef.current = null
    const args = baseDrawArgsRef.current
    const canvas = baseCanvasRef.current
    const container = containerRef.current
    if (!args || !canvas || !container || args.size.width <= 0 || args.size.height <= 0) return
    let motion = revealMotionRef.current
    if (motion && (reducedMotionRef.current || document.visibilityState === "hidden")) {
      motion = retargetCurveReveal(motion, motion.toX, performance.now(), false)
      revealMotionRef.current = motion
    }
    const frame = motion ? curveRevealFrame(motion, performance.now()) : null
    const drawLayout = frame?.active && revealFromLayoutRef.current
      ? interpolateChartLayout(revealFromLayoutRef.current, args.layout, frame.progress) : args.layout
    presentedLayoutRef.current = drawLayout
    const animatedMarkers = drawBaseChart(canvas, container, args.size, drawLayout, args.chartData, args.displayIndexes, args.xKey, args.lines, args.annotations, args.xLabel, args.xTickSuffix, args.yLabel, args.appearance, args.xValueFormatter, frame?.x, args.displayScale)
    // Publish the curve and its DOM markers together, using the same snapshot.
    // Keep nodes mounted for empty data so hiding also waits for the curve draw.
    for (const element of animatedMarkerElementsRef.current.values()) element.style.visibility = "hidden"
    for (const marker of animatedMarkers) {
      const element = animatedMarkerElementsRef.current.get(marker.key)
      if (!element) continue
      element.style.transform = `translate(${marker.x - marker.size / 2}px, ${marker.y - marker.anchorY}px)`
      element.style.setProperty("--marker-angle", `${marker.angle ?? 0}rad`)
      element.style.visibility = "visible"
    }
    // Hover continues to show actual samples, mapped through the displayed axes.
    if (hoverDrawnRef.current && hoverCanvasRef.current) {
      const index = pendingHoverRef.current?.index
      drawHover(hoverCanvasRef.current, container, args.size, drawLayout, index === undefined ? undefined : args.chartData.rowAt(index), args.xKey, args.lines)
    }
    if (frame?.active) baseFrameRef.current = window.requestAnimationFrame(flushBaseDraw)
  }, [])

  useLayoutEffect(() => {
    const previous = baseDrawArgsRef.current
    const motion = revealMotionRef.current
    const now = performance.now()
    const sameSize = previous?.size.width === size.width && previous.size.height === size.height
    let continuous = false
    if (motion && latestRevealX !== null && latestRevealX > motion.toX && motion.toX >= layout.x.min) {
      // A new recording or replacement data must not animate across sessions.
      for (let index = chartData.length - 1; index >= 0; index--) {
        const x = chartData.valueAt(index, xKey)
        if (x === motion.toX) { continuous = true; break }
      }
    }
    const animate = Boolean(sameSize && !reducedMotionRef.current && document.visibilityState !== "hidden" && (continuous || latestRevealX === motion?.toX))
    if (latestRevealX === null) revealMotionRef.current = null
    else {
      if (latestRevealX !== motion?.toX || !animate) revealFromLayoutRef.current = presentedLayoutRef.current ?? layout
      revealMotionRef.current = retargetCurveReveal(motion, latestRevealX, now, animate)
    }
    baseDrawArgsRef.current = { size, layout, chartData, displayIndexes, xKey, lines, annotations, xLabel, xTickSuffix, yLabel, appearance: chartAppearance, xValueFormatter, displayScale }
    if (document.visibilityState === "hidden") {
      if (baseFrameRef.current !== null) {
        window.cancelAnimationFrame(baseFrameRef.current)
        baseFrameRef.current = null
      }
      flushBaseDraw()
    } else if (baseFrameRef.current === null) {
      baseFrameRef.current = window.requestAnimationFrame(flushBaseDraw)
    }
  }, [annotations, appearanceVersion, displayScale, latestRevealX, chartAppearance, chartData, displayIndexes, layout, lines, size, xKey, xLabel, xTickSuffix, yLabel, flushBaseDraw, xValueFormatter])

  useLayoutEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)")
    const finishIfNeeded = () => {
      reducedMotionRef.current = media.matches
      if (!media.matches && document.visibilityState !== "hidden") return
      if (baseFrameRef.current !== null) window.cancelAnimationFrame(baseFrameRef.current)
      flushBaseDraw()
    }
    finishIfNeeded()
    media.addEventListener("change", finishIfNeeded)
    document.addEventListener("visibilitychange", finishIfNeeded)
    return () => {
      media.removeEventListener("change", finishIfNeeded)
      document.removeEventListener("visibilitychange", finishIfNeeded)
    }
  }, [flushBaseDraw])

  useEffect(() => () => {
    if (baseFrameRef.current !== null) window.cancelAnimationFrame(baseFrameRef.current)
  }, [])

  const hoverRow = hover ? chartData.rowAt(hover.index) : undefined

  useEffect(() => {
    const canvas = hoverCanvasRef.current
    const container = containerRef.current
    if (!canvas || !container || size.width <= 0 || size.height <= 0) return
    if (!hoverRow && !hoverDrawnRef.current) return
    drawHover(canvas, container, size, presentedLayoutRef.current ?? layout, hoverRow, xKey, lines)
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
    const pointerLayout = presentedLayoutRef.current ?? layout
    const pointerX = event.nativeEvent.offsetX
    const pointerY = event.nativeEvent.offsetY
    if (pointerX < pointerLayout.left || pointerX > pointerLayout.left + pointerLayout.plotWidth || pointerY < pointerLayout.top || pointerY > pointerLayout.top + pointerLayout.plotHeight) {
      scheduleHover(null)
      return
    }
    const target = pointerLayout.x.min + ((pointerX - pointerLayout.left) / pointerLayout.plotWidth) * (pointerLayout.x.max - pointerLayout.x.min)
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
      onPointerDown={onPointSelect ? (event) => {
        if (event.button !== 0) return
        const rect = event.currentTarget.getBoundingClientRect()
        const px = event.clientX - rect.left, py = event.clientY - rect.top
        if (px < layout.left || px > layout.left + layout.plotWidth || py < layout.top || py > layout.top + layout.plotHeight) return
        let nearest: { rowIndex: number; seriesKey: string } | null = null
        let distance = 12 ** 2 // Click target radius in CSS pixels, not a data-matching tolerance.
        for (const index of visibleIndexes) for (const line of lines) {
          if (!line.showPoints) continue
          const x = chartData.valueAt(index, xKey), y = chartData.valueAt(index, line.key)
          if (x === null || y === null) continue
          const scale = line.yAxisId === "right" && layout.rightY ? layout.rightY : layout.leftY
          const d = (mapX(x, layout) - px) ** 2 + (mapY(y, scale, layout) - py) ** 2
          if (d < distance) { distance = d; nearest = { rowIndex: index, seriesKey: line.key } }
        }
        if (nearest) onPointSelect(nearest)
      } : undefined}
      ref={containerRef}
    >
      <canvas aria-label={ariaLabel} className="absolute inset-0 size-full" ref={baseCanvasRef} role="img" />
      {markerLines.map((line) => {
        const markerSize = (line.pointSize ?? latestPointMarkerSize(line.latestPointStyle)) * displayScale
        const setMarkerRef = (element: HTMLDivElement | null) => {
          if (element) animatedMarkerElementsRef.current.set(line.key, element)
          else animatedMarkerElementsRef.current.delete(line.key)
        }
        return line.latestPointStyle === "bee" || line.latestPointStyle === "dinosaur" || line.latestPointStyle === "green-dinosaur"
          ? <CharacterSpriteMarker key={line.key} style={line.latestPointStyle} size={markerSize} ref={setMarkerRef} />
          : line.latestPointStyle === "flappy-bird"
            ? <FlappyBirdMarker key={line.key} size={markerSize} ref={setMarkerRef} />
            : <StaticLatestPointMarker key={line.key} line={line} size={markerSize} ref={setMarkerRef} />
      })}
      <canvas aria-hidden="true" className="pointer-events-none absolute inset-0 size-full" ref={hoverCanvasRef} />
      {selectedPoints && size.width > 0 && size.height > 0 && <svg aria-label="实测点游标" className="pointer-events-none absolute inset-0 size-full" viewBox={`0 0 ${size.width} ${size.height}`}>
        {selectedPoints.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y) && p.x >= layout.x.min && p.x <= layout.x.max).map((point, index) => {
          const x = mapX(point.x, layout), y = mapY(point.y, layout.leftY, layout)
          return <g key={point.label} style={{ color: point.color ?? "var(--foreground)" }}>
            <line x1={x} x2={x} y1={layout.top} y2={layout.top + layout.plotHeight} stroke="currentColor" strokeDasharray={index ? "2 4" : "5 4"} opacity={0.6} />
            <circle cx={x} cy={y} r={5} stroke="currentColor" fill="var(--card)" strokeWidth={2} />
            <text x={Math.min(x + 8, size.width - 20)} y={layout.top + 16 + index * 18} fill="currentColor" fontSize={14}>{point.label}</text>
          </g>
        })}
      </svg>}
      {annotationLegend.length > 0 && (
        <div aria-label="峰值图例" className="pointer-events-none absolute right-3 top-2 z-[1] flex min-w-48 flex-col gap-1.5 rounded-lg border border-border/55 bg-card/90 px-3.5 py-2.5 text-[15px] font-medium leading-5 text-foreground/80 shadow-sm backdrop-blur-sm">
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
          <p className="mb-1.5 font-semibold">{xValueFormatter ? xValueFormatter(finiteValue(hoverRow[xKey]) ?? 0) : `${xKey}: ${tooltipNumber(finiteValue(hoverRow[xKey]) ?? 0)}`}</p>
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
