import { useCallback, useEffect, useRef, useState } from "react"

import { fetchBackendSnapshot, mergeBackendSnapshot, sendBackendCommand, subscribeBackendSnapshots, type BackendSnapshot, type SeriesRevisions, type SnapshotView } from "@/lib/backend"

// Acquisition remains device-owned; the native WebView2 bridge streams compact
// snapshots without allocating a new command callback for every UI refresh.
const DEMO_PREVIEW_ENABLED = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("demo") === "1"

const DEMO_SPECTRUM = {
  wavelengthNm: [972, 973, 974, 975, 975.5, 975.8, 976, 976.2, 976.4, 976.7, 977, 977.5, 978, 979, 980],
  intensity: [180, 240, 320, 560, 1200, 4200, 9800, 12400, 10400, 5300, 1700, 650, 360, 220, 160],
}

const DEMO_SNAPSHOT: BackendSnapshot = {
  capturedAt: "2026-07-19T12:00:00.000Z",
  backend: {
    connected: true,
    mode: "active",
    protocolVersion: 2,
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
    spectrumAverageFrames: 5,
    stableWindowS: 3,
    stableToleranceW: 0.15,
    initialCurrentA: 2,
    targetCurrentA: 12,
    currentStepA: 2,
    currentPlanMode: "range",
    customCurrentPoints: [{ currentA: 3, dwellS: 300 }, { currentA: 5, dwellS: 300 }],
    pointTimeoutS: 120,
    rampDownStepA: 5,
    rampDownIntervalS: 1.1,
    pauseRampDownTimeoutS: 30,
    useSpectrometer: true,
  },
  devices: {
    powerSupply: { state: "connected", label: "电源", detail: "TDK RS232 · 示例", connected: true, outputEnabled: true, activeCurrentA: 8 },
    powerMeter: { state: "connected", label: "功率计", detail: "ASRL3::INSTR · 示例", connected: true, running: true, ready: true, stable: true, powerW: 0.86 },
    spectrometer: { state: "connected", label: "光谱仪", detail: "Ocean Insight · 示例", connected: true, running: true, ready: true, resources: ["自动选择第一台 Ocean Insight"], peakWavelengthNm: 976.2, centroidNm: 976.2, fwhmNm: 0.42, smsrDb: null, spectrumWindow: { centerNm: 976.2, halfRangeNm: 20 } },
  },
  automaticTest: {
    state: "waiting_stable",
    detail: "示例数据预览",
    controlsEnabled: false,
    canStart: false,
    canRetry: false,
    canEnd: true,
    currents: [2, 4, 6, 8, 10, 12],
    dwellTimesS: [0, 0, 0, 0, 0, 0],
    currentIndex: 3,
    currentA: 8,
    currentDwellS: 0,
    currentDwellElapsedS: null,
    samplePath: "",
    progress: 0.67,
    reference: {
      sourcePath: "/preview/DEMO-001/老化站 1/2026_07_18_14_30.xlsx",
      station: "老化站 1",
      startedAtUtc: "2026-07-18T06:30:00.000Z",
      modifiedAtMs: 1752810600000,
      pointCount: 6,
      minimumCurrentA: 2,
      maximumCurrentA: 12,
      points: [
        { currentA: 2, powerW: 0.11 },
        { currentA: 4, powerW: 0.25 },
        { currentA: 6, powerW: 0.4 },
        { currentA: 8, powerW: 0.57 },
        { currentA: 10, powerW: 0.71 },
        { currentA: 12, powerW: 0.84 },
      ],
    },
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
    spectrumPeaks: [{ label: "P1", centroidNm: 976.2, peakWavelengthNm: 976.2, peakIntensity: 12400 }],
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

  const loadSnapshot = useCallback(async (showLoading: boolean, requestedView: SnapshotView) => {
    if (showLoading) {
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
      const previousSnapshot = snapshotRef.current
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
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      if (showLoading && mountedRef.current && latestViewRef.current === requestedView) setLoading(false)
    }
  }, [])

  const refresh = useCallback(() => loadSnapshot(true, view), [loadSnapshot, view])

  const command = useCallback(async (method: string, params: Record<string, unknown> = {}) => {
    if (method === "app.stopAll") {
      try {
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
        if (mountedRef.current) setError(reason instanceof Error ? reason.message : String(reason))
        throw reason
      }
    }
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

    // A view owns a different set of large chart histories. Drop the previous
    // view before its first full stream message so hidden arrays are not kept
    // alive by merge fallbacks after navigation.
    snapshotRef.current = null
    revisionsRef.current = null
    setSnapshot(null)
    setError(null)
    setLoading(true)

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
