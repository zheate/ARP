import { CanvasLineChart, type CanvasChartAnnotation, type CanvasChartAnnotationLegendItem, type CanvasChartLine, type CanvasColumnarData } from "@/components/canvas-line-chart"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { sameNumberArray, sameRows } from "@/features/shared/snapshot-equality"
import { useAppearance } from "@/hooks/use-appearance"
import { isAnimatedLatestPointMarker, type CurveLineStyle, type LatestPointMarkerStyle } from "@/lib/appearance"
import { type BackendSnapshot } from "@/lib/backend"
import { spectrumPeakAnnotations, spectrumPeakLegend } from "@/lib/spectrum-annotations"
import { memo, type ReactNode, useMemo, useRef } from "react"

export const POWER_PLOT_HISTORY_S = 60

export const POWER_TIME_TICK_INTERVAL_S = 10

export const PD_PLOT_HISTORY_S = 60

export const PD_TIME_TICK_INTERVAL_S = 10

export const SPECTRUM_Y_AXIS_PADDING_FRACTION = 0.08

export const SPECTRUM_Y_AXIS_SHRINK_TRIGGER_FRACTION = 0.72

export const SPECTRUM_Y_AXIS_SHRINK_REQUIRED_FRAMES = 6

export const SPECTRUM_Y_AXIS_MINIMUM_LIMIT = -1000

export const PLM_CHART_SERIES = {
  accent: "var(--chart-1)",
  efficiency: "var(--chart-efficiency)",
  tan: "#d5a98b",
  olive: "#9a907c",
  green: "#70a58a",
  blue: "#4ca4ff",
  yellow: "#ffcc00",
} as const

export const HISTORY_CHART_SERIES = {
  power: "rgba(168, 171, 176, 0.46)",
} as const

export function curveLineDash(style: CurveLineStyle): number[] {
  if (style === "dashed") return [10, 6]
  if (style === "dotted") return [1, 5]
  return []
}

export type SpectrumYAxisState = { domain: [number, number]; compactFrameCount: number }

export function powerYAxisDomain(values: Array<number | null | undefined>): [number, number] | undefined {
  const finiteValues = values.filter((value): value is number => typeof value === "number" && Number.isFinite(value))
  if (finiteValues.length === 0) return undefined
  const minimum = Math.min(...finiteValues)
  const maximum = Math.max(...finiteValues)
  const padding = minimum === maximum ? Math.max(Math.abs(minimum) * 0.1, 0.001) : (maximum - minimum) * 0.12
  return [minimum >= 0 ? Math.max(0, minimum - padding) : minimum - padding, maximum <= 0 ? 0 : maximum + padding]
}

export function spectrumYAxisDomain(values: number[]): [number, number] {
  if (values.length === 0) return [0, 1]
  const minimum = Math.min(...values)
  const maximum = Math.max(...values)
  const measuredSpan = maximum - minimum
  const fallbackSpan = Math.max(Math.abs(minimum), Math.abs(maximum), 1) * 0.1
  const span = Math.max(measuredSpan, fallbackSpan, 1)
  const padding = span * SPECTRUM_Y_AXIS_PADDING_FRACTION
  const lowerPadding = minimum > 0 ? Math.min(padding, minimum * 0.25) : padding
  return [Math.max(SPECTRUM_Y_AXIS_MINIMUM_LIMIT, minimum - lowerPadding), maximum + padding]
}

export function stabilizeSpectrumYAxis(
  desiredDomain: [number, number],
  current: SpectrumYAxisState | null,
): SpectrumYAxisState {
  if (!current) return { domain: desiredDomain, compactFrameCount: 0 }
  const [desiredMinimum, desiredMaximum] = desiredDomain
  const [currentMinimum, currentMaximum] = current.domain
  if (desiredMinimum < currentMinimum || desiredMaximum > currentMaximum) {
    return {
      domain: [Math.min(desiredMinimum, currentMinimum), Math.max(desiredMaximum, currentMaximum)],
      compactFrameCount: 0,
    }
  }
  const currentSpan = currentMaximum - currentMinimum
  const desiredSpan = desiredMaximum - desiredMinimum
  const shouldCompact = desiredSpan <= currentSpan * SPECTRUM_Y_AXIS_SHRINK_TRIGGER_FRACTION
    || desiredMinimum - currentMinimum >= currentSpan * 0.15
    || currentMaximum - desiredMaximum >= currentSpan * 0.15
  if (!shouldCompact) {
    return current.compactFrameCount === 0 ? current : { ...current, compactFrameCount: 0 }
  }
  const compactFrameCount = current.compactFrameCount + 1
  return compactFrameCount >= SPECTRUM_Y_AXIS_SHRINK_REQUIRED_FRAMES
    ? { domain: desiredDomain, compactFrameCount: 0 }
    : { ...current, compactFrameCount }
}

export function ChartLegend({ lines, stacked = false }: { lines: CanvasChartLine[]; stacked?: boolean }) {
  return (
    <div aria-label="图例" className={`flex min-w-0 gap-x-4 gap-y-1 text-sm text-muted-foreground ${stacked ? "flex-col items-start" : "flex-wrap items-center"}`}>
      {lines.filter((line) => !line.hideLegend).map((line) => (
        <span className="flex items-center gap-1.5 whitespace-nowrap" key={line.key}>
          <svg aria-hidden="true" className="h-3.5 w-7 overflow-visible" viewBox="0 0 28 14">
            <line stroke={line.color} strokeDasharray={line.dash?.join(" ")} strokeLinecap="round" strokeWidth="2" x1="1" x2="27" y1="7" y2="7" />
            {line.showPoints && (line.pointShape === "triangle" ? (
              <path
                d="M14 2.5 18.2 10.5 9.8 10.5Z"
                fill={line.pointStyle === "hollow" ? "var(--card)" : line.color}
                stroke={line.color}
                strokeLinejoin="round"
                strokeWidth="2"
              />
            ) : (
              <circle
                cx="14"
                cy="7"
                fill={line.pointStyle === "hollow" ? "var(--card)" : line.color}
                r="4"
                stroke={line.color}
                strokeWidth="2"
              />
            ))}
          </svg>
          <span>{line.label}</span>
        </span>
      ))}
    </div>
  )
}

export const ChartPanel = memo(function ChartPanel({ title, subtitle, data = [], columns, xKey, lines, empty = "暂无实时数据", heightClassName, annotations, annotationLegend, stretch = false, compactHeader = false, hideHeader = false, insetLegend = false, sidebar, headerTrailing, xDomain, xTicks, yDomain, yMinimumLimit, rightYDomain, preserveAllPoints = false, xLabel, xTickSuffix, yLabel }: {
  title: string
  subtitle?: string
  data?: Array<Record<string, number | null>>
  columns?: CanvasColumnarData
  xKey: string
  lines: CanvasChartLine[]
  empty?: string
  heightClassName?: string
  annotations?: CanvasChartAnnotation[]
  annotationLegend?: CanvasChartAnnotationLegendItem[]
  stretch?: boolean
  compactHeader?: boolean
  hideHeader?: boolean
  insetLegend?: boolean
  sidebar?: ReactNode
  headerTrailing?: ReactNode
  xDomain?: [number, number]
  xTicks?: number[]
  yDomain?: [number, number]
  yMinimumLimit?: number
  rightYDomain?: [number, number]
  preserveAllPoints?: boolean
  xLabel?: string
  xTickSuffix?: string
  yLabel?: string
}) {
  const dataLength = columns
    ? Math.min(...Object.values(columns).map((values) => values.length))
    : data.length
  const plot = (
    <>
      {!hideHeader && (
        <CardHeader className="flex min-h-8 shrink-0 flex-row items-center px-3 py-1.5">
          <strong className="chart-panel-heading">{title}</strong>
          {headerTrailing ?? (subtitle ? <small className="chart-panel-subtitle">{subtitle}</small> : <ChartLegend lines={lines} />)}
        </CardHeader>
      )}
      <CardContent className={`relative ${stretch ? "min-h-0 flex-1" : heightClassName || (dataLength === 0 ? "h-40" : "h-56")} ${hideHeader && !sidebar ? "px-1.5 py-1.5" : "px-3 pb-1.5 pt-0"}`}>
        {dataLength === 0 ? (
          <div className="grid h-full min-h-[120px] place-items-center rounded-lg bg-background/30 px-4 text-center text-base text-muted-foreground/70">{empty}</div>
        ) : (
          <CanvasLineChart annotationLegend={annotationLegend} annotations={annotations} ariaLabel={`${title}曲线`} columns={columns} data={data} lines={lines} preserveAllPoints={preserveAllPoints} rightYDomain={rightYDomain} tightInsets={hideHeader && !sidebar} xDomain={xDomain} xKey={xKey} xLabel={xLabel} xTickSuffix={xTickSuffix} xTicks={xTicks} yDomain={yDomain} yLabel={yLabel} yMinimumLimit={yMinimumLimit} />
        )}
        {insetLegend && dataLength > 0 && (
          <div className={`chart-inset-legend pointer-events-none absolute isolate top-0 z-[1] px-3 py-2 ${hideHeader ? "left-[70px]" : "left-[88px]"}`}>
            <ChartLegend lines={lines} stacked />
          </div>
        )}
      </CardContent>
    </>
  )
  return (
    <Card aria-label={title} size="sm" className={`${compactHeader ? "gap-0" : ""} shadow-none ${stretch ? "h-full min-h-0 flex flex-col" : ""} ${sidebar ? "chart-panel-with-sidebar" : ""} ${headerTrailing ? "chart-panel-with-readout" : ""}`}>
      {sidebar ? <>
        <aside className="chart-panel__sidebar">{sidebar}</aside>
        <div className="chart-panel__plot">{plot}</div>
      </> : plot}
    </Card>
  )
}, (previous, next) => {
  const sameLines = previous.lines.length === next.lines.length
    && previous.lines.every((line, index) => {
      const other = next.lines[index]
      return line.key === other.key
        && line.label === other.label
        && line.color === other.color
        && line.axisColor === other.axisColor
        && line.yAxisId === other.yAxisId
        && line.showPoints === other.showPoints
        && line.showLatestPoint === other.showLatestPoint
        && line.latestPointStyle === other.latestPointStyle
        && line.pointShape === other.pointShape
        && line.pointStyle === other.pointStyle
        && line.pointSize === other.pointSize
        && line.lineWidth === other.lineWidth
        && line.interpolation === other.interpolation
        && line.hideLegend === other.hideLegend
        && line.trail === other.trail
        && line.animateLatestPoint === other.animateLatestPoint
        && sameNumberArray(line.dash, other.dash)
    })
  const sameNumbers = (left?: number[], right?: number[]) => (
    left === right || (
      left !== undefined
      && right !== undefined
      && left.length === right.length
      && left.every((value, index) => value === right[index])
    )
  )
  const dataKeys = [previous.xKey, ...previous.lines.map((line) => line.key)]
  const sameColumns = previous.columns === next.columns || Boolean(
    previous.columns
    && next.columns
    && Object.keys(previous.columns).length === Object.keys(next.columns).length
    && Object.keys(previous.columns).every((key) => sameNumberArray(previous.columns?.[key], next.columns?.[key])),
  )
  return sameRows(previous.data, next.data, dataKeys)
    && sameColumns
    && previous.title === next.title
    && previous.subtitle === next.subtitle
    && previous.xKey === next.xKey
    && previous.empty === next.empty
    && previous.heightClassName === next.heightClassName
    && previous.stretch === next.stretch
    && previous.compactHeader === next.compactHeader
    && previous.hideHeader === next.hideHeader
    && previous.insetLegend === next.insetLegend
    && previous.sidebar === next.sidebar
    && previous.headerTrailing === next.headerTrailing
    && previous.preserveAllPoints === next.preserveAllPoints
    && previous.xLabel === next.xLabel
    && previous.xTickSuffix === next.xTickSuffix
    && previous.yLabel === next.yLabel
    && sameLines
    && sameNumbers(previous.xDomain, next.xDomain)
    && sameNumbers(previous.xTicks, next.xTicks)
    && sameNumbers(previous.yDomain, next.yDomain)
    && previous.yMinimumLimit === next.yMinimumLimit
    && sameNumbers(previous.rightYDomain, next.rightYDomain)
    && sameRows(previous.annotations, next.annotations, ["label", "x", "y", "color"])
    && sameRows(previous.annotationLegend, next.annotationLegend, ["label", "color", "value"])
})

export const PowerRealtimeChart = memo(function PowerRealtimeChart({ snapshot, smoothing = true, curveLineStyle = "solid", latestPointMarkerStyle = "point", stretch = false, heightClassName, automatic = false, readout }: { snapshot: BackendSnapshot | null; automatic?: boolean; smoothing?: boolean; curveLineStyle?: CurveLineStyle; latestPointMarkerStyle?: LatestPointMarkerStyle; stretch?: boolean; heightClassName?: string; readout?: ReactNode }) {
  const { appearance } = useAppearance()
  const powerData = (snapshot?.measurements?.power ?? []) as Array<Record<string, number | null>>
  const latestElapsedS = powerData[powerData.length - 1]?.elapsedS
  const powerTimeDomain: [number, number] = typeof latestElapsedS === "number"
    ? [Math.max(0, latestElapsedS - POWER_PLOT_HISTORY_S), Math.max(10, latestElapsedS)]
    : [0, 10]
  const firstPowerTickS = Math.ceil(powerTimeDomain[0] / POWER_TIME_TICK_INTERVAL_S) * POWER_TIME_TICK_INTERVAL_S
  const powerTimeTicks = Array.from(
    { length: Math.floor((powerTimeDomain[1] - firstPowerTickS) / POWER_TIME_TICK_INTERVAL_S) + 1 },
    (_, index) => firstPowerTickS + index * POWER_TIME_TICK_INTERVAL_S,
  )
  const powerStabilityColor = PLM_CHART_SERIES.accent
  const powerYDomain = powerYAxisDomain(powerData.flatMap((row) => (
    typeof row.elapsedS === "number" && row.elapsedS >= powerTimeDomain[0]
      ? [row.powerW]
      : []
  )))
  const lines: CanvasChartLine[] = [{ key: "powerW", label: "实时功率", color: powerStabilityColor, interpolation: smoothing ? "monotone" : "linear", dash: curveLineDash(curveLineStyle), showLatestPoint: true, latestPointStyle: latestPointMarkerStyle, trail: automatic, animateLatestPoint: automatic && isAnimatedLatestPointMarker(latestPointMarkerStyle) }]

  return (
    <div className={`automatic-power-chart ${stretch ? "h-full" : ""}`}>
      <ChartPanel
        title="功率实时"
        subtitle={automatic ? `最近 60 s${appearance.trailEnabled ? ` · 尾迹 ${appearance.trailSeconds} s` : ""}` : undefined}
        compactHeader
        data={powerData}
        hideHeader
        xKey="elapsedS"
        lines={lines}
        stretch={stretch}
        heightClassName={heightClassName}
        xDomain={powerTimeDomain}
        xTickSuffix=" s"
        xTicks={powerTimeTicks}
        yDomain={powerYDomain}
      />
      {readout}
    </div>
  )
}, (previous, next) => {
  const previousPower = previous.snapshot?.measurements?.power
  const nextPower = next.snapshot?.measurements?.power
  return previous.stretch === next.stretch
    && previous.automatic === next.automatic
    && previous.readout === next.readout
    && previous.smoothing === next.smoothing
    && previous.curveLineStyle === next.curveLineStyle
    && previous.heightClassName === next.heightClassName
    && previous.latestPointMarkerStyle === next.latestPointMarkerStyle
    && previous.snapshot?.automaticTest.currentA === next.snapshot?.automaticTest.currentA
    && previous.snapshot?.devices.powerMeter.powerW === next.snapshot?.devices.powerMeter.powerW
    && sameRows(previousPower, nextPower, ["elapsedS", "powerW"])
})

export const agingPowerLines: CanvasChartLine[] = [
  { key: "powerW", label: "功率 (W)", color: PLM_CHART_SERIES.accent, axisColor: PLM_CHART_SERIES.accent, showPoints: true, pointShape: "circle", pointStyle: "hollow", pointSize: 7, lineWidth: 2.4 },
]

export const agingPowerTimeDomain: [number, number] = [0, 120]

export const agingPowerTimeTicks = [0, 20, 40, 60, 80, 100, 120]

export const AgingPowerChart = memo(function AgingPowerChart({ snapshot, smoothing = true, stretch = false, heightClassName }: { snapshot: BackendSnapshot | null; smoothing?: boolean; stretch?: boolean; heightClassName?: string }) {
  const chartData = snapshot?.measurements?.agingPower ?? []
  const powerDomain = useMemo(
    () => powerYAxisDomain(chartData.map((row) => row.powerW)),
    [chartData],
  )

  return (
    <ChartPanel
      compactHeader
      data={chartData}
      empty="AU 老化测试每 5 分钟记录一次功率"
      hideHeader
      lines={agingPowerLines.map((line) => ({ ...line, interpolation: smoothing ? "monotone" : "linear" }))}
      stretch={stretch}
      heightClassName={heightClassName}
      title="AU 老化功率"
      xDomain={agingPowerTimeDomain}
      xKey="elapsedMinutes"
      xTickSuffix=" min"
      xTicks={agingPowerTimeTicks}
      yDomain={powerDomain}
    />
  )
}, (previous, next) => previous.stretch === next.stretch
  && previous.smoothing === next.smoothing
  && previous.heightClassName === next.heightClassName
  && sameRows(previous.snapshot?.measurements?.agingPower, next.snapshot?.measurements?.agingPower, ["elapsedMinutes", "powerW"]))

export const powerEfficiencyLines: CanvasChartLine[] = [
  { key: "powerW", label: "功率 (W)", color: PLM_CHART_SERIES.accent, axisColor: PLM_CHART_SERIES.accent, showPoints: true, pointShape: "circle", pointStyle: "hollow", pointSize: 8, lineWidth: 2.4, connectNulls: true },
  { key: "efficiencyPercent", label: "效率 (%)", color: PLM_CHART_SERIES.efficiency, axisColor: PLM_CHART_SERIES.efficiency, yAxisId: "right", showPoints: true, pointShape: "circle", pointStyle: "hollow", pointSize: 8, lineWidth: 2.4, connectNulls: true },
]

export const PowerEfficiencyChart = memo(function PowerEfficiencyChart({ snapshot, smoothing = true, stretch = false, heightClassName }: { snapshot: BackendSnapshot | null; smoothing?: boolean; stretch?: boolean; heightClassName?: string }) {
  const stableData = (snapshot?.measurements?.stable ?? []) as Array<Record<string, number | null>>
  const referencePoints = snapshot?.automaticTest.reference?.points ?? []
  const chartData = useMemo<Array<Record<string, number | null>>>(() => {
    const rows = new Map<number, Record<string, number | null>>()
    stableData.forEach((row) => {
      if (typeof row.currentA === "number") rows.set(row.currentA, { ...row })
    })
    referencePoints.forEach((point) => {
      const row = rows.get(point.currentA) ?? { currentA: point.currentA, powerW: null, efficiencyPercent: null }
      rows.set(point.currentA, { ...row, referencePowerW: point.powerW })
    })
    return [...rows.values()].sort((left, right) => (left.currentA ?? 0) - (right.currentA ?? 0))
  }, [referencePoints, stableData])
  const lines: CanvasChartLine[] = [
    ...powerEfficiencyLines.map((line): CanvasChartLine => ({
      ...line,
      interpolation: line.key === "powerW" && smoothing ? "monotone" : "linear",
    })),
    ...(referencePoints.length ? [{ key: "referencePowerW", label: "历史参考", color: HISTORY_CHART_SERIES.power, interpolation: smoothing ? "monotone" as const : "linear" as const, lineWidth: 1.2, dash: [4, 4] }] : []),
  ]
  const currentDomain = useMemo<[number, number] | undefined>(() => {
    const currents = chartData
      .map((row) => row.currentA)
      .filter((value): value is number => typeof value === "number" && Number.isFinite(value))
    if (!currents.length) return undefined
    const minimumCurrent = Math.min(...currents)
    const maximumCurrent = Math.max(...currents)
    const padding = minimumCurrent === maximumCurrent
      ? Math.max(Math.abs(minimumCurrent) * 0.1, 1)
      : (maximumCurrent - minimumCurrent) * 0.12
    return [minimumCurrent - padding, maximumCurrent + padding]
  }, [chartData])
  const powerDomain = useMemo(
    () => powerYAxisDomain(chartData.flatMap((row) => [row.powerW, row.referencePowerW])),
    [chartData],
  )
  const currentTicks = useMemo(() => {
    const currents = chartData
      .map((row) => row.currentA)
      .filter((value): value is number => typeof value === "number" && Number.isFinite(value))
    if (currents.length <= 7) return currents
    return Array.from(
      new Set(Array.from({ length: 7 }, (_, index) => currents[Math.round(index * (currents.length - 1) / 6)])),
    )
  }, [chartData])

  return (
    <ChartPanel
      compactHeader
      data={chartData}
      empty="完成稳定测试点后显示功率 / 效率"
      hideHeader
      insetLegend
      lines={lines}
      stretch={stretch}
      heightClassName={heightClassName}
      title="功率 / 效率"
      xDomain={currentDomain}
      xKey="currentA"
      xTickSuffix=" A"
      xTicks={currentTicks}
      yDomain={powerDomain}
    />
  )
}, (previous, next) => previous.stretch === next.stretch
  && previous.smoothing === next.smoothing
  && previous.heightClassName === next.heightClassName
  && sameRows(previous.snapshot?.measurements?.stable, next.snapshot?.measurements?.stable, ["currentA", "powerW", "efficiencyPercent"])
  && previous.snapshot?.automaticTest.reference?.sourcePath === next.snapshot?.automaticTest.reference?.sourcePath
  && sameRows(previous.snapshot?.automaticTest.reference?.points, next.snapshot?.automaticTest.reference?.points, ["currentA", "powerW"]))

export const SpectrumRealtimeChart = memo(function SpectrumRealtimeChart({ snapshot, smoothing = true, stretch = false, heightClassName, metrics }: { snapshot: BackendSnapshot | null; smoothing?: boolean; stretch?: boolean; heightClassName?: string; metrics?: ReactNode }) {
  const spectrumSource = snapshot?.measurements?.spectrum
  const spectrumData = spectrumSource
    ? { wavelengthNm: spectrumSource.wavelengthNm, intensity: spectrumSource.intensity }
    : { wavelengthNm: [], intensity: [] }
  const spectrum = snapshot?.devices.spectrometer
  const spectrumPeaks = snapshot?.measurements?.spectrumPeaks ?? []
  const peakAnnotations = spectrumPeakAnnotations(spectrumPeaks)
  const spectrumYAxisStateRef = useRef<SpectrumYAxisState | null>(null)
  const spectrumXDomain = useMemo<[number, number] | undefined>(() => {
    const spectrumWindow = spectrum?.spectrumWindow
    if (
      spectrumWindow
      && Number.isFinite(spectrumWindow.centerNm)
      && Number.isFinite(spectrumWindow.halfRangeNm)
      && spectrumWindow.halfRangeNm > 0
    ) {
      return [
        spectrumWindow.centerNm - spectrumWindow.halfRangeNm,
        spectrumWindow.centerNm + spectrumWindow.halfRangeNm,
      ]
    }

    let minimum = Number.POSITIVE_INFINITY
    let maximum = Number.NEGATIVE_INFINITY
    for (const wavelength of spectrumData.wavelengthNm) {
      if (typeof wavelength !== "number" || !Number.isFinite(wavelength)) continue
      minimum = Math.min(minimum, wavelength)
      maximum = Math.max(maximum, wavelength)
    }
    return minimum < maximum ? [minimum, maximum] : undefined
  }, [spectrum?.spectrumWindow, spectrumData])
  const spectrumYDomain = useMemo<[number, number]>(() => {
    if (spectrumData.intensity.length === 0) {
      spectrumYAxisStateRef.current = null
      return [0, 1]
    }
    const intensities: number[] = []
    for (const intensity of spectrumData.intensity) {
      if (typeof intensity === "number" && Number.isFinite(intensity)) {
        intensities.push(intensity)
      }
    }
    spectrumYAxisStateRef.current = stabilizeSpectrumYAxis(
      spectrumYAxisDomain(intensities),
      spectrumYAxisStateRef.current,
    )
    return spectrumYAxisStateRef.current.domain
  }, [spectrumData])

  return (
    <ChartPanel
      title={metrics ? "实时光谱" : "光谱"}
      compactHeader
      hideHeader
      sidebar={metrics ? <><strong className="spectrum-readouts-heading">光谱</strong>{metrics}</> : undefined}
      columns={spectrumData}
      xKey="wavelengthNm"
      xDomain={spectrumXDomain}
      xTickSuffix=" nm"
      yDomain={spectrumYDomain}
      yMinimumLimit={SPECTRUM_Y_AXIS_MINIMUM_LIMIT}
      preserveAllPoints
      lines={[{ key: "intensity", label: "光谱（实时）", color: PLM_CHART_SERIES.accent, interpolation: smoothing ? "monotone" : "linear" }]}
      stretch={stretch}
      heightClassName={heightClassName}
      annotations={peakAnnotations}
      annotationLegend={spectrumPeakLegend(spectrumPeaks)}
    />
  )
}, (previous, next) => {
  const previousMeasurements = previous.snapshot?.measurements
  const nextMeasurements = next.snapshot?.measurements
  const previousSpectrum = previous.snapshot?.devices.spectrometer
  const nextSpectrum = next.snapshot?.devices.spectrometer
  return previous.stretch === next.stretch
    && previous.metrics === next.metrics
    && previous.smoothing === next.smoothing
    && previous.heightClassName === next.heightClassName
    && sameNumberArray(previousMeasurements?.spectrum.wavelengthNm, nextMeasurements?.spectrum.wavelengthNm)
    && sameNumberArray(previousMeasurements?.spectrum.intensity, nextMeasurements?.spectrum.intensity)
    && sameRows(previousMeasurements?.spectrumPeaks, nextMeasurements?.spectrumPeaks, ["label", "peakWavelengthNm", "peakIntensity"])
    && previousSpectrum?.spectrumWindow?.centerNm === nextSpectrum?.spectrumWindow?.centerNm
    && previousSpectrum?.spectrumWindow?.halfRangeNm === nextSpectrum?.spectrumWindow?.halfRangeNm
})
