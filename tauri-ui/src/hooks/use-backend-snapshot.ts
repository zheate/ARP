import { useCallback, useEffect, useRef, useState } from "react"

import { fetchBackendSnapshot, mergeBackendSnapshot, sendBackendCommand, subscribeBackendSnapshots, type BackendSnapshot, type SeriesRevisions, type SnapshotView } from "@/lib/backend"

// Acquisition remains device-owned; the native WebView2 bridge streams compact
// snapshots without allocating a new command callback for every UI refresh.
const DEMO_PREVIEW_ENABLED = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("demo") === "1"
const WEBVIEW_GC_INTERVAL_MS = 30_000

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
    controlsEnabled: false,
    canStart: false,
    canRetry: false,
    canEnd: true,
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

export function useBackendSnapshot(view: SnapshotView) {
  const [snapshot, setSnapshot] = useState<BackendSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [commandPending, setCommandPending] = useState(false)
  const [pendingCommand, setPendingCommand] = useState<string | null>(null)
  const mountedRef = useRef(true)
  const commandPendingRef = useRef(false)
  const latestViewRef = useRef(view)
  const snapshotRef = useRef<BackendSnapshot | null>(null)
  const revisionsRef = useRef<SeriesRevisions | null>(null)
  latestViewRef.current = view

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    const collectGarbage = (globalThis as typeof globalThis & { gc?: () => void }).gc
    if (typeof collectGarbage !== "function") return
    const timer = window.setInterval(() => collectGarbage(), WEBVIEW_GC_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [])

  const loadSnapshot = useCallback(async (showLoading: boolean, requestedView: SnapshotView) => {
    if (showLoading) {
      snapshotRef.current = null
      revisionsRef.current = null
      setLoading(true)
    }
    try {
      if (DEMO_PREVIEW_ENABLED) {
        if (mountedRef.current && latestViewRef.current === requestedView) {
          snapshotRef.current = DEMO_SNAPSHOT
          revisionsRef.current = DEMO_SNAPSHOT.seriesRevisions ?? null
          setSnapshot(DEMO_SNAPSHOT)
          setError(null)
        }
        return
      }
      const previousSnapshot = showLoading ? null : snapshotRef.current
      const previousPower = previousSnapshot?.measurements?.power
      const previousPd = previousSnapshot?.pd?.points
      const powerCursor = previousPower?.[previousPower.length - 1]?.elapsedS
      const pdCursor = previousPd?.[previousPd.length - 1]?.elapsedS
      const cursors = powerCursor === undefined && pdCursor === undefined ? undefined : {
        ...(powerCursor === undefined ? {} : { power: powerCursor }),
        ...(pdCursor === undefined ? {} : { pd: pdCursor }),
      }
      const patch = await fetchBackendSnapshot(
        requestedView,
        showLoading ? undefined : revisionsRef.current ?? undefined,
        cursors,
      )
      if (!mountedRef.current || latestViewRef.current !== requestedView) return
      const nextSnapshot = mergeBackendSnapshot(previousSnapshot, patch)
      snapshotRef.current = nextSnapshot
      revisionsRef.current = nextSnapshot.seriesRevisions ?? null
      setSnapshot(nextSnapshot)
      setError(null)
    } catch (reason) {
      if (!mountedRef.current || latestViewRef.current !== requestedView) return
      if (showLoading) setSnapshot(null)
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      if (showLoading && mountedRef.current && latestViewRef.current === requestedView) setLoading(false)
    }
  }, [])

  const refresh = useCallback(() => loadSnapshot(true, view), [loadSnapshot, view])

  const command = useCallback(async (method: string, params: Record<string, unknown> = {}) => {
    if (commandPendingRef.current) {
      if (snapshotRef.current) return snapshotRef.current
      throw new Error("上一项操作仍在处理中")
    }
    commandPendingRef.current = true
    setCommandPending(true)
    setPendingCommand(method)
    try {
      if (DEMO_PREVIEW_ENABLED) {
        setSnapshot(DEMO_SNAPSHOT)
        setError(null)
        return DEMO_SNAPSHOT
      }
      const requestedView = view
      const nextSnapshot = await sendBackendCommand(method, params)
      if (mountedRef.current && latestViewRef.current === requestedView) {
        snapshotRef.current = nextSnapshot
        revisionsRef.current = nextSnapshot.seriesRevisions ?? null
        setSnapshot(nextSnapshot)
        setError(null)
      }
      return nextSnapshot
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason)
      if (mountedRef.current) setError(message)
      throw reason
    } finally {
      commandPendingRef.current = false
      if (mountedRef.current) {
        setCommandPending(false)
        setPendingCommand(null)
      }
    }
  }, [view])

  useEffect(() => {
    let cancelled = false
    let streamEpoch = 0
    let stopStream: (() => Promise<void>) | undefined

    if (DEMO_PREVIEW_ENABLED) {
      void loadSnapshot(true, view)
      return () => { cancelled = true }
    }

    const stop = () => {
      streamEpoch += 1
      const currentStop = stopStream
      stopStream = undefined
      if (currentStop) void currentStop()
    }

    const start = async () => {
      if (cancelled || document.visibilityState === "hidden") return
      const epoch = ++streamEpoch
      setLoading(true)
      try {
        const unsubscribe = await subscribeBackendSnapshots(
          view,
          (patch) => {
            if (cancelled || epoch !== streamEpoch || latestViewRef.current !== view) return
            const nextSnapshot = mergeBackendSnapshot(snapshotRef.current, patch)
            snapshotRef.current = nextSnapshot
            revisionsRef.current = nextSnapshot.seriesRevisions ?? null
            setSnapshot(nextSnapshot)
            setError(null)
            setLoading(false)
          },
          (message) => {
            if (cancelled || epoch !== streamEpoch) return
            setError(message)
            setLoading(false)
          },
        )
        if (cancelled || epoch !== streamEpoch) void unsubscribe()
        else stopStream = unsubscribe
      } catch (reason) {
        if (cancelled || epoch !== streamEpoch) return
        setError(reason instanceof Error ? reason.message : String(reason))
        setLoading(false)
      }
    }

    const handleVisibilityChange = () => document.visibilityState === "hidden" ? stop() : void start()
    document.addEventListener("visibilitychange", handleVisibilityChange)
    void start()
    return () => {
      cancelled = true
      stop()
      document.removeEventListener("visibilitychange", handleVisibilityChange)
    }
  }, [loadSnapshot, view])

  return { snapshot, error, loading, commandPending, pendingCommand, refresh, command }
}
