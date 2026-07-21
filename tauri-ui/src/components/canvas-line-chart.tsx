import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react"

export type CanvasChartLine = {
  key: string
  label: string
  color: string
  yAxisId?: "left" | "right"
  showPoints?: boolean
  pointShape?: "circle" | "triangle"
  pointStyle?: "solid" | "hollow"
  pointSize?: number
  lineWidth?: number
}

export type CanvasChartAnnotation = {
  label: string
  x: number
  y: number
  color?: string
}

type NumericRow = Record<string, number | null>

type CanvasLineChartProps = {
  data: NumericRow[]
  xKey: string
  lines: CanvasChartLine[]
  xDomain?: [number, number]
  xTicks?: number[]
  annotations?: CanvasChartAnnotation[]
  ariaLabel: string
}

type ChartSize = { width: number; height: number }
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

function finiteValue(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function paddedExtent(values: number[], includeZero: boolean): [number, number] {
  if (!values.length) return [0, 1]
  let minimum = Math.min(...values)
  let maximum = Math.max(...values)
  if (includeZero && minimum >= 0) minimum = 0
  if (minimum === maximum) {
    const padding = Math.max(Math.abs(minimum) * 0.1, 1)
    return [minimum - padding, maximum + padding]
  }
  const padding = (maximum - minimum) * 0.08
  return [minimum < 0 ? minimum - padding : minimum, maximum + padding]
}

function niceStep(span: number, targetTickCount: number): number {
  if (!Number.isFinite(span) || span <= 0) return 1
  const rawStep = span / Math.max(1, targetTickCount)
  const magnitude = 10 ** Math.floor(Math.log10(rawStep))
  const normalized = rawStep / magnitude
  const multiplier = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10
  return multiplier * magnitude
}

function createScale(minimum: number, maximum: number, targetTickCount = 5): AxisScale {
  const safeMinimum = Number.isFinite(minimum) ? minimum : 0
  const safeMaximum = Number.isFinite(maximum) ? maximum : safeMinimum + 1
  const normalizedMaximum = safeMaximum === safeMinimum ? safeMinimum + 1 : safeMaximum
  const step = niceStep(normalizedMaximum - safeMinimum, targetTickCount)
  const tickMinimum = Math.floor(safeMinimum / step) * step
  const tickMaximum = Math.ceil(normalizedMaximum / step) * step
  const ticks: number[] = []
  const tickLimit = 24
  for (let value = tickMinimum, index = 0; value <= tickMaximum + step * 0.25 && index < tickLimit; value += step, index += 1) {
    ticks.push(Number(value.toPrecision(12)))
  }
  return { min: tickMinimum, max: tickMaximum, ticks }
}

function fixedScale(domain: [number, number], ticks?: number[]): AxisScale {
  let [minimum, maximum] = domain
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return createScale(0, 1)
  if (minimum > maximum) [minimum, maximum] = [maximum, minimum]
  if (minimum === maximum) maximum = minimum + 1
  return {
    min: minimum,
    max: maximum,
    ticks: ticks?.filter((value) => value >= minimum && value <= maximum) ?? createScale(minimum, maximum, 6).ticks,
  }
}

function formatTick(value: number, scale: AxisScale): string {
  const absolute = Math.abs(value)
  const span = Math.abs(scale.max - scale.min)
  if ((absolute >= 100_000 || (absolute > 0 && absolute < 0.001)) && span > 0) return value.toExponential(1)
  const step = scale.ticks.length > 1 ? Math.abs(scale.ticks[1] - scale.ticks[0]) : span
  const digits = step >= 10 ? 0 : step >= 1 ? 1 : step >= 0.1 ? 2 : 3
  return value.toFixed(digits).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1")
}

function buildLayout(
  data: NumericRow[],
  xKey: string,
  lines: CanvasChartLine[],
  size: ChartSize,
  xDomain?: [number, number],
  xTicks?: number[],
): ChartLayout {
  const hasRightAxis = lines.some((line) => line.yAxisId === "right")
  const left = 52
  const right = hasRightAxis ? 50 : 16
  const top = 30
  const bottom = 30
  const xValues = data.map((row) => finiteValue(row[xKey])).filter((value): value is number => value !== null)
  const automaticXExtent = paddedExtent(xValues, false)
  const x = xDomain ? fixedScale(xDomain, xTicks) : createScale(automaticXExtent[0], automaticXExtent[1], Math.max(3, Math.floor(size.width / 110)))
  const visibleRows = data.filter((row) => {
    const value = finiteValue(row[xKey])
    return value !== null && value >= x.min && value <= x.max
  })
  const valuesFor = (axis: "left" | "right") => lines
    .filter((line) => (line.yAxisId ?? "left") === axis)
    .flatMap((line) => visibleRows.map((row) => finiteValue(row[line.key])).filter((value): value is number => value !== null))
  const leftExtent = paddedExtent(valuesFor("left"), true)
  const rightValues = valuesFor("right")
  const rightExtent = paddedExtent(rightValues, true)
  return {
    left,
    right,
    top,
    bottom,
    plotWidth: Math.max(1, size.width - left - right),
    plotHeight: Math.max(1, size.height - top - bottom),
    x,
    leftY: createScale(leftExtent[0], leftExtent[1]),
    rightY: hasRightAxis ? createScale(rightExtent[0], rightExtent[1]) : null,
  }
}

function mapX(value: number, layout: ChartLayout): number {
  return layout.left + ((value - layout.x.min) / (layout.x.max - layout.x.min)) * layout.plotWidth
}

function mapY(value: number, scale: AxisScale, layout: ChartLayout): number {
  return layout.top + (1 - (value - scale.min) / (scale.max - scale.min)) * layout.plotHeight
}

function downsampleRows(data: NumericRow[], lines: CanvasChartLine[], limit: number): NumericRow[] {
  if (data.length <= limit || limit < 4 || lines.length === 0) return data
  const valueKey = lines[0].key
  const bucketCount = Math.max(1, Math.floor((limit - 2) / 2))
  const bucketSize = Math.ceil((data.length - 2) / bucketCount)
  const selectedIndexes = [0]
  for (let start = 1; start < data.length - 1; start += bucketSize) {
    const stop = Math.min(data.length - 1, start + bucketSize)
    let lowIndex = start
    let highIndex = start
    for (let index = start + 1; index < stop; index += 1) {
      const value = finiteValue(data[index]?.[valueKey])
      const lowValue = finiteValue(data[lowIndex]?.[valueKey])
      const highValue = finiteValue(data[highIndex]?.[valueKey])
      if (value !== null && (lowValue === null || value < lowValue)) lowIndex = index
      if (value !== null && (highValue === null || value > highValue)) highIndex = index
    }
    if (lowIndex === highIndex) selectedIndexes.push(lowIndex)
    else selectedIndexes.push(...([lowIndex, highIndex].sort((left, right) => left - right)))
  }
  selectedIndexes.push(data.length - 1)
  return selectedIndexes.slice(0, limit).map((index) => data[index])
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

function drawBaseChart(
  canvas: HTMLCanvasElement,
  container: HTMLDivElement,
  size: ChartSize,
  layout: ChartLayout,
  data: NumericRow[],
  xKey: string,
  lines: CanvasChartLine[],
  annotations: CanvasChartAnnotation[],
): void {
  const context = prepareCanvas(canvas, size)
  if (!context) return
  const styles = getComputedStyle(container)
  const border = styles.getPropertyValue("--chart-grid").trim() || "hsl(42 7% 25% / 0.52)"
  const muted = styles.getPropertyValue("--muted-foreground").trim() || "hsl(40 8% 60%)"
  const chartBackground = styles.getPropertyValue("--card").trim() || "hsl(48 3% 17%)"
  context.font = "10px 'Geist Variable', 'PingFang SC', sans-serif"
  context.lineWidth = 1
  context.strokeStyle = border
  context.fillStyle = muted

  context.save()
  context.setLineDash([3, 3])
  for (const tick of layout.leftY.ticks) {
    const y = mapY(tick, layout.leftY, layout)
    context.beginPath()
    context.moveTo(layout.left, y)
    context.lineTo(layout.left + layout.plotWidth, y)
    context.stroke()
  }
  for (const tick of layout.x.ticks) {
    const x = mapX(tick, layout)
    context.beginPath()
    context.moveTo(x, layout.top)
    context.lineTo(x, layout.top + layout.plotHeight)
    context.stroke()
  }
  context.restore()

  context.textAlign = "right"
  context.textBaseline = "middle"
  for (const tick of layout.leftY.ticks) {
    const y = mapY(tick, layout.leftY, layout)
    context.fillText(formatTick(tick, layout.leftY), layout.left - 7, y)
  }
  if (layout.rightY) {
    context.textAlign = "left"
    for (const tick of layout.rightY.ticks) {
      const y = mapY(tick, layout.rightY, layout)
      context.fillText(formatTick(tick, layout.rightY), layout.left + layout.plotWidth + 7, y)
    }
  }
  context.textAlign = "center"
  context.textBaseline = "top"
  for (const tick of layout.x.ticks) {
    context.fillText(formatTick(tick, layout.x), mapX(tick, layout), layout.top + layout.plotHeight + 7)
  }

  context.save()
  context.beginPath()
  context.rect(layout.left, layout.top, layout.plotWidth, layout.plotHeight)
  context.clip()
  for (const line of lines) {
    const scale = line.yAxisId === "right" && layout.rightY ? layout.rightY : layout.leftY
    context.beginPath()
    context.strokeStyle = line.color
    context.lineWidth = line.lineWidth ?? 1.8
    context.lineJoin = "round"
    context.lineCap = "round"
    let drawing = false
    for (const row of data) {
      const xValue = finiteValue(row[xKey])
      const yValue = finiteValue(row[line.key])
      if (xValue === null || yValue === null || xValue < layout.x.min || xValue > layout.x.max) {
        drawing = false
        continue
      }
      const x = mapX(xValue, layout)
      const y = mapY(yValue, scale, layout)
      if (drawing) context.lineTo(x, y)
      else context.moveTo(x, y)
      drawing = true
    }
    context.stroke()

    if (line.showPoints) {
      for (const row of data) {
        const xValue = finiteValue(row[xKey])
        const yValue = finiteValue(row[line.key])
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

  annotations.forEach((annotation, index) => {
    if (!Number.isFinite(annotation.x) || !Number.isFinite(annotation.y) || annotation.x < layout.x.min || annotation.x > layout.x.max) return
    const color = annotation.color || "#8298b8"
    const x = mapX(annotation.x, layout)
    const y = mapY(annotation.y, layout.leftY, layout)
    context.save()
    context.strokeStyle = color
    context.fillStyle = color
    context.globalAlpha = 0.72
    context.setLineDash([3, 3])
    context.beginPath()
    context.moveTo(x, layout.top)
    context.lineTo(x, layout.top + layout.plotHeight)
    context.stroke()
    context.setLineDash([])
    context.beginPath()
    context.arc(x, y, 3, 0, Math.PI * 2)
    context.fill()
    context.globalAlpha = 0.95
    context.font = "10px 'Geist Variable', 'PingFang SC', sans-serif"
    context.textAlign = index % 2 === 0 ? "left" : "right"
    context.textBaseline = "top"
    const labelX = index % 2 === 0 ? x + 5 : x - 5
    context.fillText(`${annotation.label} ${annotation.x.toFixed(3)} nm`, labelX, layout.top + 4 + (index % 3) * 13)
    context.restore()
  })
  context.restore()

  context.textBaseline = "middle"
  context.font = "11px 'Geist Variable', 'PingFang SC', sans-serif"
  let legendX = layout.left
  for (const line of lines) {
    context.strokeStyle = line.color
    context.lineWidth = 2
    context.beginPath()
    context.moveTo(legendX, 13)
    context.lineTo(legendX + 16, 13)
    context.stroke()
    if (line.showPoints) {
      drawPoint(
        context,
        legendX + 8,
        13,
        line.color,
        line.pointShape ?? "circle",
        Math.min(line.pointSize ?? 8, 7),
        line.pointStyle,
        chartBackground,
      )
    }
    context.fillStyle = muted
    context.textAlign = "left"
    context.fillText(line.label, legendX + 21, 13)
    legendX += 29 + context.measureText(line.label).width
  }
}

function nearestRowIndex(data: NumericRow[], xKey: string, target: number): number {
  let low = 0
  let high = data.length - 1
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    const value = finiteValue(data[middle]?.[xKey]) ?? Number.POSITIVE_INFINITY
    if (value < target) low = middle + 1
    else high = middle
  }
  const current = finiteValue(data[low]?.[xKey])
  const previous = low > 0 ? finiteValue(data[low - 1]?.[xKey]) : null
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
      line.color,
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
  if (absolute >= 100_000 || (absolute > 0 && absolute < 0.001)) return value.toExponential(3)
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 })
}

export function CanvasLineChart({ data, xKey, lines, xDomain, xTicks, annotations = [], ariaLabel }: CanvasLineChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const baseCanvasRef = useRef<HTMLCanvasElement>(null)
  const hoverCanvasRef = useRef<HTMLCanvasElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const hoverFrameRef = useRef<number | null>(null)
  const pendingHoverRef = useRef<PendingHover>(null)
  const pointerRef = useRef({ x: 0, y: 0 })
  const [size, setSize] = useState<ChartSize>(EMPTY_SIZE)
  const [hover, setHover] = useState<HoverState>(null)
  const layout = useMemo(
    () => buildLayout(data, xKey, lines, size, xDomain, xTicks),
    [data, lines, size, xDomain, xKey, xTicks],
  )
  const displayData = useMemo(
    () => downsampleRows(data, lines, Math.max(96, Math.floor(layout.plotWidth * 1.25))),
    [data, layout.plotWidth, lines],
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

  useEffect(() => {
    const canvas = baseCanvasRef.current
    const container = containerRef.current
    if (!canvas || !container || size.width <= 0 || size.height <= 0) return
    drawBaseChart(canvas, container, size, layout, displayData, xKey, lines, annotations)
  }, [annotations, displayData, layout, lines, size, xKey])

  useEffect(() => {
    const canvas = hoverCanvasRef.current
    const container = containerRef.current
    if (!canvas || !container || size.width <= 0 || size.height <= 0) return
    drawHover(canvas, container, size, layout, hover ? data[hover.index] : undefined, xKey, lines)
  }, [data, hover, layout, lines, size, xKey])

  const positionTooltip = useCallback((pointerX: number, pointerY: number) => {
    const tooltip = tooltipRef.current
    if (!tooltip) return
    tooltip.style.left = `${Math.min(Math.max(8, pointerX + 12), Math.max(8, size.width - 172))}px`
    tooltip.style.top = `${Math.min(Math.max(8, pointerY - 18), Math.max(8, size.height - 96))}px`
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
    scheduleHover({ index: nearestRowIndex(data, xKey, target), pointerX, pointerY })
  }

  const hoverRow = hover ? data[hover.index] : undefined
  const tooltipLeft = Math.min(Math.max(8, pointerRef.current.x + 12), Math.max(8, size.width - 172))
  const tooltipTop = Math.min(Math.max(8, pointerRef.current.y - 18), Math.max(8, size.height - 96))

  return (
    <div
      className="relative h-full min-h-0 w-full touch-none overflow-hidden"
      onPointerLeave={() => scheduleHover(null)}
      onPointerMove={handlePointerMove}
      ref={containerRef}
    >
      <canvas aria-label={ariaLabel} className="absolute inset-0 size-full" ref={baseCanvasRef} role="img" />
      <canvas aria-hidden="true" className="pointer-events-none absolute inset-0 size-full" ref={hoverCanvasRef} />
      {hover && hoverRow && (
        <div
          className="pointer-events-none absolute z-10 min-w-40 rounded-lg border border-[var(--plm-flat-border-strong)] bg-popover/95 px-3 py-2 text-xs text-popover-foreground shadow-md shadow-black/20 backdrop-blur-sm"
          ref={tooltipRef}
          role="tooltip"
          style={{ left: tooltipLeft, top: tooltipTop }}
        >
          <p className="mb-1 font-medium">{xKey}: {tooltipNumber(finiteValue(hoverRow[xKey]) ?? 0)}</p>
          {lines.map((line) => {
            const value = finiteValue(hoverRow[line.key])
            return value === null ? null : <div className="flex items-center justify-between gap-4" key={line.key}><span className="flex items-center gap-1.5"><span className="size-2 rounded-full" style={{ backgroundColor: line.color }} />{line.label}</span><span className="font-medium tabular-nums">{tooltipNumber(value)}</span></div>
          })}
        </div>
      )}
      <div className="sr-only">{lines.map((line) => line.label).join("、")}</div>
    </div>
  )
}
