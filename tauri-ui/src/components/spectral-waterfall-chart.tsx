import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { Activity } from "lucide-react"
import type { SpectrumSeries } from "@/lib/data-browser"
import "./spectral-waterfall-chart.css"

interface SpectralWaterfallChartProps {
  spectra?: SpectrumSeries[] | null
  sn?: string
}

type WaterfallMode = "heatmap" | "ridge"
type ZoomMode = "focus" | "full"

const CHART_FONT_FAMILY = "ui-monospace, SFMono-Regular, monospace"

// Rendering and hit testing must use the same margins after the font grows.
function chartLayout(canvas: HTMLCanvasElement, series: SpectrumSeries[]) {
  const rect = canvas.getBoundingClientRect()
  const fontSize = Math.max(14, Number.parseFloat(getComputedStyle(canvas).getPropertyValue("--chart-font-size")) || 14)
  const ctx = canvas.getContext("2d")!
  ctx.save()
  ctx.font = `${fontSize}px ${CHART_FONT_FAMILY}`
  const currentWidth = Math.max(...series.map((item) => ctx.measureText(`${item.currentA.toFixed(2)}A`).width), 0)
  ctx.restore()
  const padLeft = Math.ceil(currentWidth) + 20
  const padBottom = fontSize * 2 + 24
  const padTop = fontSize + 12
  const padRight = 36
  return {
    fontSize, padLeft, padBottom, padTop, padRight,
    plotW: Math.max(10, rect.width - padLeft - padRight),
    plotH: Math.max(10, rect.height - padTop - padBottom),
  }
}

// Inferno colormap: 0 (black/dark-blue) -> 0.35 (purple) -> 0.7 (orange-red) -> 1.0 (bright yellow)
function getInfernoColor(t: number): string {
  const clamped = Math.max(0, Math.min(1, t))
  let r: number, g: number, b: number
  if (clamped < 0.25) {
    const f = clamped / 0.25
    r = Math.floor(10 + f * 60)
    g = Math.floor(5 + f * 10)
    b = Math.floor(40 + f * 120)
  } else if (clamped < 0.55) {
    const f = (clamped - 0.25) / 0.3
    r = Math.floor(70 + f * 140)
    g = Math.floor(15 + f * 40)
    b = Math.floor(160 - f * 80)
  } else if (clamped < 0.85) {
    const f = (clamped - 0.55) / 0.3
    r = Math.floor(210 + f * 40)
    g = Math.floor(55 + f * 130)
    b = Math.floor(80 - f * 60)
  } else {
    const f = (clamped - 0.85) / 0.15
    r = Math.min(255, 250 + Math.floor(f * 5))
    g = Math.min(255, Math.floor(185 + f * 70))
    b = Math.min(255, Math.floor(20 + f * 180))
  }
  return `rgb(${r},${g},${b})`
}

export const SpectralWaterfallChart = memo(function SpectralWaterfallChart({
  spectra,
}: SpectralWaterfallChartProps) {
  const [mode, setMode] = useState<WaterfallMode>("heatmap")
  const [zoomMode, setZoomMode] = useState<ZoomMode>("focus")
  const [hoveredData, setHoveredData] = useState<{
    currentA: number
    wavelengthNm: number
    centerWl: number
    intensity: number
    x: number
    y: number
  } | null>(null)

  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const tooltipRef = useRef<HTMLDivElement | null>(null)

  // Sort and filter valid series
  const validSeries = useMemo(() => {
    if (!spectra || spectra.length === 0) return []
    return [...spectra]
      .filter((s) => s.wavelengthNm.length > 0 && s.intensity.length > 0)
      .sort((a, b) => a.currentA - b.currentA)
  }, [spectra])

  // Compute full wavelength range and peak-focus range
  const domain = useMemo(() => {
    if (validSeries.length === 0) {
      return { minWl: 960, maxWl: 990, globalMaxInt: 1 }
    }

    let minFullWl = Infinity
    let maxFullWl = -Infinity
    let globalMaxInt = 0
    let minPeakWl = Infinity
    let maxPeakWl = -Infinity

    for (const s of validSeries) {
      const firstWl = s.wavelengthNm[0] ?? 0
      const lastWl = s.wavelengthNm[s.wavelengthNm.length - 1] ?? 0
      minFullWl = Math.min(minFullWl, Math.min(firstWl, lastWl))
      maxFullWl = Math.max(maxFullWl, Math.max(firstWl, lastWl))

      let localMax = 0
      let localPeakWl = firstWl
      for (let i = 0; i < s.intensity.length; i++) {
        const val = s.intensity[i] ?? 0
        if (val > localMax) {
          localMax = val
          localPeakWl = s.wavelengthNm[i] ?? localPeakWl
        }
      }
      globalMaxInt = Math.max(globalMaxInt, localMax)
      minPeakWl = Math.min(minPeakWl, localPeakWl)
      maxPeakWl = Math.max(maxPeakWl, localPeakWl)
    }

    if (!Number.isFinite(minFullWl)) minFullWl = 960
    if (!Number.isFinite(maxFullWl)) maxFullWl = 990
    if (globalMaxInt <= 0) globalMaxInt = 1

    // Focus range: around all detected peak wavelengths ± 5nm padding
    const focusCenter = (minPeakWl + maxPeakWl) / 2
    const focusSpan = Math.max(8, (maxPeakWl - minPeakWl) + 8)
    const focusMin = Math.max(minFullWl, focusCenter - focusSpan / 2)
    const focusMax = Math.min(maxFullWl, focusCenter + focusSpan / 2)

    return {
      minWl: zoomMode === "focus" ? focusMin : minFullWl,
      maxWl: zoomMode === "focus" ? focusMax : maxFullWl,
      fullMinWl: minFullWl,
      fullMaxWl: maxFullWl,
      globalMaxInt,
    }
  }, [validSeries, zoomMode])

  // Key physical metrics: Center Wavelength (λc) Drift, Least-Squares Linear Fit, Slope dλc/dI, and R²
  const physicsMetrics = useMemo(() => {
    if (validSeries.length < 2) return null
    const first = validSeries[0]
    const last = validSeries[validSeries.length - 1]
    if (!first || !last) return null

    const n = validSeries.length
    let sumI = 0
    let sumWl = 0
    for (const s of validSeries) {
      sumI += s.currentA
      sumWl += s.peakWavelengthNm
    }
    const meanI = sumI / n
    const meanWl = sumWl / n

    let sII = 0
    let sIWl = 0
    let sWlWl = 0
    for (const s of validSeries) {
      const dI = s.currentA - meanI
      const dWl = s.peakWavelengthNm - meanWl
      sII += dI * dI
      sIWl += dI * dWl
      sWlWl += dWl * dWl
    }

    const slope = sII > 1e-6 ? sIWl / sII : 0
    const r2 = sII > 1e-6 && sWlWl > 1e-6 ? Math.min(1, Math.max(0, (sIWl * sIWl) / (sII * sWlWl))) : 1
    const deltaWl = last.peakWavelengthNm - first.peakWavelengthNm
    const deltaCurrentA = last.currentA - first.currentA

    return {
      firstCurrentA: first.currentA,
      lastCurrentA: last.currentA,
      firstPeakWl: first.peakWavelengthNm,
      lastPeakWl: last.peakWavelengthNm,
      deltaWl,
      deltaCurrentA,
      slope,
      r2,
      maxIntensity: domain.globalMaxInt,
      stepCount: n,
    }
  }, [validSeries, domain.globalMaxInt])

  // Canvas render function
  const renderCanvas = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || validSeries.length === 0) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const rect = canvas.getBoundingClientRect()
    if (canvas.width !== Math.floor(rect.width * dpr) || canvas.height !== Math.floor(rect.height * dpr)) {
      canvas.width = Math.floor(rect.width * dpr)
      canvas.height = Math.floor(rect.height * dpr)
    }

    ctx.save()
    ctx.scale(dpr, dpr)
    const w = rect.width
    const h = rect.height
    ctx.clearRect(0, 0, w, h)

    const { fontSize, padLeft, padTop, plotW, plotH } = chartLayout(canvas, validSeries)

    // Dark plot background
    ctx.fillStyle = "#0c0d10"
    ctx.fillRect(padLeft, padTop, plotW, plotH)

    const minWl = domain.minWl
    const maxWl = domain.maxWl
    const wlSpan = Math.max(0.001, maxWl - minWl)
    const maxInt = Math.max(1, domain.globalMaxInt)

    // Draw X-axis ticks (Wavelength)
    ctx.fillStyle = "#b1b6c0"
    ctx.font = `${fontSize}px ${CHART_FONT_FAMILY}`
    ctx.textAlign = "center"

    const tickCount = Math.max(2, Math.min(8, Math.floor(plotW / (fontSize * 6))))
    const wlStep = wlSpan / (tickCount - 1)
    for (let i = 0; i < tickCount; i++) {
      const wlVal = minWl + i * wlStep
      const x = padLeft + ((wlVal - minWl) / wlSpan) * plotW
      ctx.fillText(`${wlVal.toFixed(1)}`, x, padTop + plotH + fontSize + 8)
      ctx.strokeStyle = "rgba(255, 255, 255, 0.07)"
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(x, padTop)
      ctx.lineTo(x, padTop + plotH)
      ctx.stroke()
    }
    ctx.fillText("中心波长 λ (nm)", padLeft + plotW / 2, padTop + plotH + fontSize * 2 + 16)

    if (mode === "heatmap") {
      // --- 2D HEATMAP ---
      const rowCount = validSeries.length
      const rowH = plotH / rowCount

      // Y-axis ticks (Current)
      ctx.textAlign = "right"
      validSeries.forEach((series, idx) => {
        const yCenter = padTop + plotH - (idx + 0.5) * rowH
        // Show tick label on every few rows to prevent overlapping
        if (rowCount <= 10 || idx === 0 || idx === rowCount - 1 || idx % Math.ceil(rowCount / 8) === 0) {
          ctx.fillStyle = "#b1b6c0"
          ctx.fillText(`${series.currentA.toFixed(2)}A`, padLeft - 8, yCenter + 4)
          ctx.strokeStyle = "rgba(255, 255, 255, 0.05)"
          ctx.beginPath()
          ctx.moveTo(padLeft, yCenter)
          ctx.lineTo(padLeft + plotW, yCenter)
          ctx.stroke()
        }

        // Render heatmap row
        const wls = series.wavelengthNm
        const ints = series.intensity
        const pointCount = wls.length
        if (pointCount < 2) return

        const yTop = padTop + plotH - (idx + 1) * rowH
        const cellH = rowH + 0.5

        // Draw discrete cells across visible range
        for (let i = 0; i < pointCount; i++) {
          const wl = wls[i] ?? 0
          if (wl < minWl || wl > maxWl) continue

          const nextWl = (i < pointCount - 1 ? wls[i + 1] : wl) ?? wl
          const cellW = Math.max(1, ((nextWl - wl) / wlSpan) * plotW + 0.5)
          const cellX = padLeft + ((wl - minWl) / wlSpan) * plotW

          const norm = Math.max(0, Math.min(1, (ints[i] ?? 0) / maxInt))
          ctx.fillStyle = getInfernoColor(norm)
          ctx.fillRect(cellX, yTop, cellW, cellH)
        }
      })

      // Initial Center Wavelength baseline reference line (λc初值)
      const initialPeakWl = validSeries[0]?.peakWavelengthNm
      if (initialPeakWl && initialPeakWl >= minWl && initialPeakWl <= maxWl) {
        const initX = padLeft + ((initialPeakWl - minWl) / wlSpan) * plotW
        ctx.strokeStyle = "rgba(148, 163, 184, 0.45)"
        ctx.lineWidth = 1
        ctx.setLineDash([2, 4])
        ctx.beginPath()
        ctx.moveTo(initX, padTop)
        ctx.lineTo(initX, padTop + plotH)
        ctx.stroke()
        ctx.setLineDash([])

        ctx.fillStyle = "#b1b6c0"
        ctx.font = `${fontSize}px ${CHART_FONT_FAMILY}`
        ctx.textAlign = "left"
        const initialLabel = `λc初值: ${initialPeakWl.toFixed(2)} nm`
        const labelX = Math.max(padLeft, Math.min(initX + 4, padLeft + plotW - ctx.measureText(initialLabel).width))
        ctx.fillText(initialLabel, labelX, padTop - 8)
      }

      // Center wavelength drift trajectory connecting peaks
      ctx.strokeStyle = "rgba(255, 255, 255, 0.7)"
      ctx.lineWidth = 1.5
      ctx.setLineDash([3, 3])
      ctx.beginPath()
      validSeries.forEach((series, idx) => {
        const px = padLeft + ((series.peakWavelengthNm - minWl) / wlSpan) * plotW
        const py = padTop + plotH - (idx + 0.5) * rowH
        if (idx === 0) ctx.moveTo(px, py)
        else ctx.lineTo(px, py)
      })
      ctx.stroke()
      ctx.setLineDash([])

      // Draw small circle on each center wavelength peak
      ctx.fillStyle = "#ffffff"
      validSeries.forEach((series, idx) => {
        const px = padLeft + ((series.peakWavelengthNm - minWl) / wlSpan) * plotW
        const py = padTop + plotH - (idx + 0.5) * rowH
        if (px >= padLeft && px <= padLeft + plotW) {
          ctx.beginPath()
          ctx.arc(px, py, 2.5, 0, Math.PI * 2)
          ctx.fill()
        }
      })

      // In-chart slope callout badge directly along the center wavelength drift trajectory
      if (validSeries.length >= 2 && physicsMetrics) {
        const midIdx = Math.floor(validSeries.length / 2)
        const midSeries = validSeries[midIdx]
        if (midSeries) {
          const px = padLeft + ((midSeries.peakWavelengthNm - minWl) / wlSpan) * plotW
          const py = padTop + plotH - (midIdx + 0.5) * rowH
          const slopeSign = physicsMetrics.slope >= 0 ? "+" : ""
          ctx.font = `bold ${fontSize}px ${CHART_FONT_FAMILY}`
          const slopeValue = `dλc/dI = ${slopeSign}${physicsMetrics.slope.toFixed(3)} nm/A`
          const fullSlopeText = `中心波长斜率 ${slopeValue}`
          const slopeText = ctx.measureText(fullSlopeText).width + 24 <= plotW ? fullSlopeText : slopeValue
          const textW = ctx.measureText(slopeText).width
          const badgeW = textW + 16
          const badgeH = fontSize + 16
          const badgeX = Math.max(padLeft + 4, Math.min(px + 18 + badgeW < padLeft + plotW ? px + 18 : px - 18 - badgeW, padLeft + plotW - badgeW - 4))
          const badgeY = py - badgeH / 2

          // Leader line
          ctx.strokeStyle = "rgba(251, 191, 36, 0.7)"
          ctx.lineWidth = 1
          ctx.setLineDash([])
          ctx.beginPath()
          ctx.moveTo(px, py)
          ctx.lineTo(badgeX + (px < badgeX ? 0 : badgeW), py)
          ctx.stroke()

          // Badge box
          ctx.fillStyle = "rgba(12, 13, 16, 0.94)"
          ctx.strokeStyle = "rgba(251, 191, 36, 0.9)"
          ctx.lineWidth = 1.2
          ctx.beginPath()
          if (typeof ctx.roundRect === "function") {
            ctx.roundRect(badgeX, badgeY, badgeW, badgeH, 4)
          } else {
            ctx.rect(badgeX, badgeY, badgeW, badgeH)
          }
          ctx.fill()
          ctx.stroke()

          // Badge text
          ctx.fillStyle = "#fbbf24"
          ctx.textAlign = "center"
          ctx.textBaseline = "middle"
          ctx.fillText(slopeText, badgeX + badgeW / 2, py)
        }
      }
    } else {
      // --- 3D RIDGE WATERFALL ---
      ctx.textAlign = "right"
      ctx.fillStyle = "#b1b6c0"
      const first = validSeries[0]
      const last = validSeries[validSeries.length - 1]
      if (last) ctx.fillText(`${last.currentA.toFixed(2)}A`, padLeft - 8, padTop + 24)
      if (first) ctx.fillText(`${first.currentA.toFixed(2)}A`, padLeft - 8, padTop + plotH - 10)

      const rowCount = validSeries.length
      // Painter's algorithm: draw from back (highest current / top of plot)
      // to front (lowest current / bottom of plot) so peaks in foreground
      // rise up naturally over the background curves without getting clipped
      // by the background baseline fill.
      for (let idx = rowCount - 1; idx >= 0; idx--) {
        const series = validSeries[idx]
        if (!series) continue
        const progress = idx / Math.max(1, rowCount - 1)
        const yBase = padTop + plotH - 14 - progress * (plotH - 52)
        const xOffset = progress * 14

        const wls = series.wavelengthNm
        const ints = series.intensity
        const pointCount = wls.length
        if (pointCount < 2) continue

        ctx.beginPath()
        ctx.moveTo(padLeft + xOffset, yBase)

        for (let i = 0; i < pointCount; i++) {
          const wl = wls[i] ?? 0
          if (wl < minWl || wl > maxWl) continue
          const x = padLeft + xOffset + ((wl - minWl) / wlSpan) * (plotW - 20)
          const norm = Math.max(0, Math.min(1, (ints[i] ?? 0) / maxInt))
          const y = yBase - norm * 36
          ctx.lineTo(x, y)
        }

        ctx.lineTo(padLeft + xOffset + (plotW - 20), yBase)
        ctx.closePath()

        // Fill background to occlude layers behind
        ctx.fillStyle = "#0c0d10"
        ctx.fill()

        // Gradient colored outline
        const strokeColor = getInfernoColor(0.25 + progress * 0.7)
        ctx.strokeStyle = strokeColor
        ctx.lineWidth = idx === rowCount - 1 ? 2 : 1.2
        ctx.stroke()
      }

      // Center wavelength peak trajectory line across 3D ridges
      ctx.strokeStyle = "rgba(251, 191, 36, 0.75)"
      ctx.lineWidth = 1.5
      ctx.setLineDash([3, 3])
      ctx.beginPath()
      validSeries.forEach((series, idx) => {
        const progress = idx / Math.max(1, rowCount - 1)
        const yBase = padTop + plotH - 14 - progress * (plotH - 52)
        const xOffset = progress * 14
        const px = padLeft + xOffset + ((series.peakWavelengthNm - minWl) / wlSpan) * (plotW - 20)
        const norm = Math.max(0, Math.min(1, series.maxIntensity / maxInt))
        const py = yBase - norm * 36
        if (idx === 0) ctx.moveTo(px, py)
        else ctx.lineTo(px, py)
      })
      ctx.stroke()
      ctx.setLineDash([])

      // Draw dot on each 3D ridge center wavelength peak
      ctx.fillStyle = "#ffffff"
      validSeries.forEach((series, idx) => {
        const progress = idx / Math.max(1, rowCount - 1)
        const yBase = padTop + plotH - 14 - progress * (plotH - 52)
        const xOffset = progress * 14
        const px = padLeft + xOffset + ((series.peakWavelengthNm - minWl) / wlSpan) * (plotW - 20)
        const norm = Math.max(0, Math.min(1, series.maxIntensity / maxInt))
        const py = yBase - norm * 36
        if (px >= padLeft && px <= padLeft + plotW) {
          ctx.beginPath()
          ctx.arc(px, py, 2.5, 0, Math.PI * 2)
          ctx.fill()
        }
      })
    }

    // Border
    ctx.strokeStyle = "rgba(255, 255, 255, 0.15)"
    ctx.lineWidth = 1
    ctx.strokeRect(padLeft, padTop, plotW, plotH)

    ctx.restore()
  }, [domain, mode, validSeries, physicsMetrics])

  // Mouse inspector handler
  const handleMouseMove = useCallback((event: React.MouseEvent<HTMLCanvasElement>) => {
    if (mode !== "heatmap" || validSeries.length === 0) {
      setHoveredData(null)
      return
    }
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const x = event.clientX - rect.left
    const y = event.clientY - rect.top

    const { padLeft, padTop, plotW, plotH } = chartLayout(canvas, validSeries)

    if (x >= padLeft && x <= padLeft + plotW && y >= padTop && y <= padTop + plotH) {
      const rowCount = validSeries.length
      const rowH = plotH / rowCount
      const rowIdx = Math.floor((padTop + plotH - y) / rowH)
      const clampedIdx = Math.max(0, Math.min(rowCount - 1, rowIdx))
      const series = validSeries[clampedIdx]
      if (!series) return

      const wlSpan = domain.maxWl - domain.minWl
      const wl = domain.minWl + ((x - padLeft) / plotW) * wlSpan

      // Find closest intensity in series
      let closestInt = 0
      let minDiff = Infinity
      for (let i = 0; i < series.wavelengthNm.length; i++) {
        const curWl = series.wavelengthNm[i] ?? 0
        const diff = Math.abs(curWl - wl)
        if (diff < minDiff) {
          minDiff = diff
          closestInt = series.intensity[i] ?? 0
        }
      }

      setHoveredData({
        currentA: series.currentA,
        wavelengthNm: wl,
        centerWl: series.peakWavelengthNm,
        intensity: closestInt,
        x,
        y,
      })
    } else {
      setHoveredData(null)
    }
  }, [domain, mode, validSeries])

  useEffect(() => {
    renderCanvas()
  }, [renderCanvas])

  useLayoutEffect(() => {
    const tooltip = tooltipRef.current
    const canvas = canvasRef.current
    if (!tooltip || !canvas || !hoveredData) return
    const { width, height } = tooltip.getBoundingClientRect()
    const x = hoveredData.x + width + 12 < canvas.clientWidth
      ? hoveredData.x + 12 : hoveredData.x - width - 12
    tooltip.style.left = `${Math.max(8, Math.min(x, canvas.clientWidth - width - 8))}px`
    tooltip.style.top = `${Math.max(8, Math.min(hoveredData.y - height / 2, canvas.clientHeight - height - 8))}px`
  }, [hoveredData])

  // Detail expansion changes the canvas width without a window resize.
  useEffect(() => {
    const handleResize = () => renderCanvas()
    const observer = new ResizeObserver(handleResize)
    if (canvasRef.current) observer.observe(canvasRef.current)
    window.addEventListener("resize", handleResize)
    return () => {
      observer.disconnect()
      window.removeEventListener("resize", handleResize)
    }
  }, [renderCanvas])

  if (validSeries.length === 0) {
    return (
      <div className="spectral-waterfall db-empty p-8 text-center border border-dashed border-border rounded-xl bg-card/40 my-4">
        <Activity size={32} className="mx-auto text-muted-foreground/60 mb-2" />
        <h4 className="sw-title font-semibold text-foreground">此记录未包含完整光谱数据</h4>
        <p className="sw-caption text-muted-foreground mt-1 max-w-md mx-auto">
          当前测试文件仅保存了标量 LIV 指标（功率、电压、效率等），未在测试时启用或记录光谱仪波长阵列。可在后续测试时确保光谱仪已勾选保存。
        </p>
      </div>
    )
  }

  return (
    <div className="spectral-waterfall space-y-3" ref={containerRef}>
      {/* Top Toolbar */}
      <div className="sw-toolbar flex flex-wrap items-center justify-between gap-3 bg-card border border-border px-3 py-3 rounded-lg">
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          <Activity size={16} className="text-primary" />
          <span className="sw-title font-medium text-foreground">光谱演化瀑布图</span>
          <span className="sw-caption text-muted-foreground font-mono">
            {validSeries.length} 个电流步进 · {domain.minWl.toFixed(1)}~{domain.maxWl.toFixed(1)} nm
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2 min-w-0">
          {/* Zoom toggle */}
          <div className="inline-flex rounded-md border border-border bg-muted/30 p-0.5 sw-controls">
            <button
              type="button"
              className={`px-2 py-1 rounded font-medium transition-all ${
                zoomMode === "focus"
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setZoomMode("focus")}
              title="聚焦在中心波长与边模漂移核心区域"
            >
              主模聚焦
            </button>
            <button
              type="button"
              className={`px-2 py-1 rounded font-medium transition-all ${
                zoomMode === "full"
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setZoomMode("full")}
              title="展开查看全波段光谱"
            >
              全波段
            </button>
          </div>

          {/* Mode Switcher */}
          <div className="inline-flex rounded-md border border-border bg-muted/30 p-0.5 sw-controls">
            <button
              type="button"
              className={`px-2.5 py-1 rounded font-medium transition-all ${
                mode === "heatmap"
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setMode("heatmap")}
            >
              2D 热力图
            </button>
            <button
              type="button"
              className={`px-2.5 py-1 rounded font-medium transition-all ${
                mode === "ridge"
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setMode("ridge")}
            >
              3D 瀑布脊线
            </button>
          </div>
        </div>
      </div>

      {/* Canvas Area */}
      <div className="sw-plot relative bg-[#0c0d10] border border-border rounded-xl overflow-hidden shadow-inner">
        <canvas
          ref={canvasRef}
          role="img"
          aria-label={mode === "heatmap" ? "光谱演化热力图" : "光谱演化瀑布脊线图"}
          className="w-full h-full block cursor-crosshair"
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHoveredData(null)}
        />

        {/* Top-Right In-Chart HUD Badge */}
        {physicsMetrics && (
          <div className="absolute top-3 right-3 flex flex-col items-end gap-1.5 pointer-events-none select-none">
            <div className="bg-zinc-950/85 backdrop-blur-md border border-zinc-700/80 px-3 py-2 rounded-lg shadow-xl text-right">
              <div className="sw-caption text-zinc-400 font-medium tracking-wide">中心波长温漂斜率 dλc/dI</div>
              <div className="sw-hud-value font-mono font-bold text-amber-400">
                {physicsMetrics.slope >= 0 ? "+" : ""}{physicsMetrics.slope.toFixed(3)} <span className="sw-caption font-normal text-amber-300/80">nm/A</span>
              </div>
              <div className="flex items-center justify-end gap-2 sw-caption font-mono text-zinc-400 mt-0.5">
                <span>Δλc: <strong className="text-cyan-400">{physicsMetrics.deltaWl >= 0 ? "+" : ""}{physicsMetrics.deltaWl.toFixed(3)} nm</strong></span>
                <span className="text-zinc-600">|</span>
                <span>R²: <strong className="text-emerald-400">{physicsMetrics.r2.toFixed(3)}</strong></span>
              </div>
            </div>
          </div>
        )}

        {/* Hover Inspector Tooltip */}
        {hoveredData && (
          <div
            className="sw-tooltip absolute pointer-events-none bg-zinc-950/95 border border-zinc-700/90 text-zinc-100 px-3 py-2 rounded-md shadow-xl font-mono z-10"
            ref={tooltipRef}
            role="tooltip"
          >
            <div>电流: <span className="text-amber-400 font-semibold">{hoveredData.currentA.toFixed(2)} A</span></div>
            <div>中心波长 (λc): <span className="text-cyan-400 font-semibold">{hoveredData.centerWl.toFixed(2)} nm</span></div>
            <div>光标波长: <span className="text-zinc-400">{hoveredData.wavelengthNm.toFixed(2)} nm</span></div>
            <div>光强: <span className="text-emerald-400 font-semibold">{Math.round(hoveredData.intensity).toLocaleString()} cts</span></div>
          </div>
        )}
      </div>

      {/* Physical Analysis Metric Cards */}
      {physicsMetrics && (
        <div className="sw-metrics">
          <div className="sw-metric bg-card border border-border rounded-lg">
            <span className="text-muted-foreground block sw-caption">中心波长漂移量 Δλc</span>
            <strong className="sw-metric-value font-mono text-cyan-400 mt-0.5 block">
              {physicsMetrics.deltaWl >= 0 ? "+" : ""}{physicsMetrics.deltaWl.toFixed(3)} <small className="sw-caption text-muted-foreground font-normal">nm</small>
            </strong>
            <span className="sw-caption text-muted-foreground mt-0.5 block">
              {physicsMetrics.firstPeakWl.toFixed(2)} → {physicsMetrics.lastPeakWl.toFixed(2)} nm
            </span>
          </div>

          <div className="sw-metric bg-card border border-border rounded-lg">
            <span className="text-muted-foreground block sw-caption">中心波长温漂斜率 dλc/dI</span>
            <strong className="sw-metric-value font-mono text-amber-400 mt-0.5 block">
              {physicsMetrics.slope >= 0 ? "+" : ""}{physicsMetrics.slope.toFixed(3)} <small className="sw-caption text-muted-foreground font-normal">nm/A</small>
            </strong>
            <span className="sw-caption text-muted-foreground mt-0.5 block">
              线性度 R² = {physicsMetrics.r2.toFixed(3)} · 反映结温热阻
            </span>
          </div>

          <div className="sw-metric bg-card border border-border rounded-lg">
            <span className="text-muted-foreground block sw-caption">测试电流覆盖区间</span>
            <strong className="sw-metric-value font-mono text-foreground mt-0.5 block">
              {physicsMetrics.firstCurrentA.toFixed(2)} ~ {physicsMetrics.lastCurrentA.toFixed(2)} <small className="sw-caption text-muted-foreground font-normal">A</small>
            </strong>
            <span className="sw-caption text-muted-foreground mt-0.5 block">
              共采集 {physicsMetrics.stepCount} 个电流点
            </span>
          </div>

          <div className="sw-metric bg-card border border-border rounded-lg">
            <span className="text-muted-foreground block sw-caption">全场最高峰值光强</span>
            <strong className="sw-metric-value font-mono text-emerald-400 mt-0.5 block">
              {Math.round(physicsMetrics.maxIntensity).toLocaleString()} <small className="sw-caption text-muted-foreground font-normal">cts</small>
            </strong>
            <span className="sw-caption text-muted-foreground mt-0.5 block">
              未饱和正常读数
            </span>
          </div>
        </div>
      )}
    </div>
  )
})
