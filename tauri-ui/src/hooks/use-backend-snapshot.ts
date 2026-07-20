import { useCallback, useEffect, useState } from "react"

import { fetchBackendSnapshot, sendBackendCommand, type BackendSnapshot } from "@/lib/backend"

const REFRESH_INTERVAL_MS = 1000
const DEMO_PREVIEW_ENABLED = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("demo") === "1"

const DEMO_SPECTRUM = [
  { wavelengthNm: 972, intensity: 180 },
  { wavelengthNm: 973, intensity: 240 },
  { wavelengthNm: 974, intensity: 320 },
  { wavelengthNm: 975, intensity: 560 },
  { wavelengthNm: 975.5, intensity: 1200 },
  { wavelengthNm: 975.8, intensity: 4200 },
  { wavelengthNm: 976, intensity: 9800 },
  { wavelengthNm: 976.2, intensity: 12400 },
  { wavelengthNm: 976.4, intensity: 10400 },
  { wavelengthNm: 976.7, intensity: 5300 },
  { wavelengthNm: 977, intensity: 1700 },
  { wavelengthNm: 977.5, intensity: 650 },
  { wavelengthNm: 978, intensity: 360 },
  { wavelengthNm: 979, intensity: 220 },
  { wavelengthNm: 980, intensity: 160 },
]

const DEMO_SNAPSHOT: BackendSnapshot = {
  capturedAt: "2026-07-19T12:00:00.000Z",
  backend: {
    connected: true,
    mode: "active",
    protocolVersion: 1,
    pythonVersion: "demo-preview",
    notices: [{ level: "info", title: "示例数据", message: "当前显示的是图表预览数据，不会连接真实仪器。" }],
  },
  configuration: {
    sn: "DEMO-001",
    productModel: "Power Test Demo",
    batch: "DEMO",
    station: "预览工位",
    outputDir: "",
    powerSupplyKind: "tdk",
    tdkResource: "TDK RS232",
    setCurrentA: 8,
    tdkVoltageV: 12,
    powerMeterResource: "ASRL3::INSTR",
    powerMeterWavelengthNm: 976,
    softwareGain: 1,
    powerMeterIntervalMs: 100,
    spectrometerResource: "",
    integrationTimeUs: 10000,
    autoIntegration: false,
    spectrometerIntervalMs: 100,
    stableWindowS: 3,
    stableToleranceW: 0.15,
    initialCurrentA: 2,
    targetCurrentA: 12,
    currentStepA: 2,
    pointTimeoutS: 120,
    rampDownStepA: 5,
    rampDownIntervalS: 1.1,
    pauseRampDownTimeoutS: 30,
    useSpectrometer: true,
  },
  devices: {
    powerSupply: { state: "connected", label: "电源", detail: "TDK RS232 · 示例", connected: true, outputEnabled: true, activeCurrentA: 8 },
    powerMeter: { state: "connected", label: "功率计", detail: "ASRL3::INSTR · 示例", connected: true, running: true, ready: true, stable: true, powerW: 0.86 },
    spectrometer: { state: "connected", label: "光谱仪", detail: "Ocean Insight · 示例", connected: true, running: true, ready: true, resources: ["自动选择第一台 Ocean Insight"], centroidNm: 976.2, fwhmNm: 0.42, smsrDb: 41.8 },
  },
  automaticTest: {
    state: "running",
    detail: "示例数据预览",
    controlsEnabled: true,
    canStart: false,
    canRetry: false,
    canEnd: false,
    currents: [2, 4, 6, 8, 10, 12],
    currentIndex: 3,
    currentA: 8,
    progress: 0.67,
  },
  measurements: {
    power: [
      { elapsedS: 0, powerW: 0.12 },
      { elapsedS: 2, powerW: 0.22 },
      { elapsedS: 4, powerW: 0.34 },
      { elapsedS: 6, powerW: 0.48 },
      { elapsedS: 8, powerW: 0.63 },
      { elapsedS: 10, powerW: 0.75 },
      { elapsedS: 12, powerW: 0.86 },
    ],
    stable: [
      { currentA: 2, powerW: 0.12, efficiencyPercent: 6.0 },
      { currentA: 4, powerW: 0.27, efficiencyPercent: 6.8 },
      { currentA: 6, powerW: 0.43, efficiencyPercent: 7.4 },
      { currentA: 8, powerW: 0.61, efficiencyPercent: 8.0 },
      { currentA: 10, powerW: 0.76, efficiencyPercent: 8.4 },
      { currentA: 12, powerW: 0.9, efficiencyPercent: 8.7 },
    ],
    spectrum: DEMO_SPECTRUM,
    spectrumPeaks: [{ label: "主峰", centroidNm: 976.2, peakWavelengthNm: 976.2, peakIntensity: 12400 }],
  },
  safety: { hardwareAccess: false, commandMode: "controller_owned", detail: "示例数据模式" },
}

export function useBackendSnapshot() {
  const [snapshot, setSnapshot] = useState<BackendSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [commandPending, setCommandPending] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      if (DEMO_PREVIEW_ENABLED) {
        setSnapshot(DEMO_SNAPSHOT)
        setError(null)
        return
      }
      const nextSnapshot = await fetchBackendSnapshot()
      setSnapshot(nextSnapshot)
      setError(null)
    } catch (reason) {
      setSnapshot(null)
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLoading(false)
    }
  }, [])

  const command = useCallback(async (method: string, params: Record<string, unknown> = {}) => {
    setCommandPending(true)
    try {
      if (DEMO_PREVIEW_ENABLED) {
        setSnapshot(DEMO_SNAPSHOT)
        setError(null)
        return DEMO_SNAPSHOT
      }
      const nextSnapshot = await sendBackendCommand(method, params)
      setSnapshot(nextSnapshot)
      setError(null)
      return nextSnapshot
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason)
      setError(message)
      throw reason
    } finally {
      setCommandPending(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const interval = window.setInterval(() => void refresh(), REFRESH_INTERVAL_MS)
    return () => window.clearInterval(interval)
  }, [refresh])

  return { snapshot, error, loading, commandPending, refresh, command }
}
