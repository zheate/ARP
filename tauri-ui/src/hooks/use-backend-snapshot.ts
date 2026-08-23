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
  seriesRevisions: { power: 1, stable: 1, aging: 1, spectrum: 1, pd: 0 },
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
    station: "站别 1",
    outputDir: "/data/test/DEMO-001",
    powerSupplyKind: "tdk",
    ch341SelectedLocationPath: "",
    ch341PortBindings: [],
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
    pibCenterWavelengthNm: 976,
    pibHalfRangeNm: 1.5,
    centerWavelengthSpecNm: 976,
    centerWavelengthToleranceNm: 1.5,
    fwhmMaximumNm: 0.6,
    pibMinimumPercent: 95,
    stableWindowS: 3,
    stableToleranceW: 0.15,
    initialCurrentA: 2,
    targetCurrentA: 12,
    currentStepA: 2,
    selectedScriptId: "standard",
    currentPlanMode: "range",
    customCurrentPoints: [{ currentA: 3, dwellS: 300 }, { currentA: 5, dwellS: 300 }],
    pointTimeoutS: 120,
    rampDownStepA: 5,
    rampDownIntervalS: 1.1,
    pauseRampDownTimeoutS: 30,
  },
  testScripts: [
    { id: "standard", name: "标准测试", builtin: true, currentPlanMode: "range" },
    { id: "hold-points", name: "增加停留点", builtin: true, currentPlanMode: "custom" },
    { id: "aging", name: "AU 老化测试", builtin: true, currentPlanMode: "custom", powerSupplyKinds: ["ch341"] },
  ],
  devices: {
    powerSupply: {
      state: "connected",
      label: "电源",
      detail: "TDK RS232 · 示例",
      connected: true,
      outputEnabled: true,
      activeCurrentA: 8,
      ch341: {
        discoveryState: "ready",
        error: "",
        ports: [{ locationPath: "PCIROOT(0)#USB(1)", name: "1号工位", online: true, deviceIndex: 0 }],
        connectedPort: { locationPath: "PCIROOT(0)#USB(1)", name: "1号工位", deviceIndex: 0 },
      },
    },
    powerMeter: { state: "connected", label: "功率计", detail: "彩煌功率计 (ASRL3::INSTR)", connected: true, running: true, ready: true, stable: true, powerW: 0.86, sampledAt: "2026-07-19T12:00:00.000Z", ageMs: 0, quality: "live", resources: ["彩煌功率计 | ASRL3::INSTR | 通道 1", "Coherent功率计 | ASRL4::INSTR"] },
    spectrometer: { state: "connected", label: "光谱仪", detail: "Ocean Insight · 示例", connected: true, running: true, ready: true, resources: ["自动选择第一台 Ocean Insight"], peakWavelengthNm: 976.2, centroidNm: 976.2, fwhmNm: 0.42, pib: 96.85, smsrDb: null, sampledAt: "2026-07-19T12:00:00.000Z", ageMs: 0, quality: "live", spectrumWindow: { centerNm: 976.2, halfRangeNm: 6.2 } },
  },
  automaticTest: {
    state: "waiting_stable",
    detail: "示例数据预览",
    controlsEnabled: false,
    canStart: false,
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
        { currentA: 2, powerW: 0.204 },
        { currentA: 4, powerW: 0.408 },
        { currentA: 6, powerW: 0.612 },
        { currentA: 8, powerW: 0.816 },
        { currentA: 10, powerW: 1.02 },
        { currentA: 12, powerW: 1.224 },
      ],
    },
  },
  measurements: {
    power: Array.from({ length: 61 }, (_, elapsedS) => ({
      elapsedS,
      powerW: Number((0.86 + Math.sin(elapsedS * 1.7) * 0.0012 + Math.sin(elapsedS * 0.43) * 0.0005).toFixed(4)),
    })),
    stable: [
      { currentA: 2, powerW: 0.204, efficiencyPercent: 4.2 },
      { currentA: 4, powerW: 0.408, efficiencyPercent: 7.4 },
      { currentA: 6, powerW: 0.612, efficiencyPercent: 10.2 },
    ],
    agingPower: Array.from({ length: 24 }, (_, index) => ({
      elapsedMinutes: (index + 1) * 5,
      powerW: Number((0.86 - index * 0.0007 + Math.sin(index * 0.8) * 0.0018).toFixed(4)),
    })),
    spectrum: DEMO_SPECTRUM,
    spectrumPeaks: [{ label: "P1", centroidNm: 976.2, peakWavelengthNm: 976.2, peakIntensity: 12400 }],
  },
  safety: {
    hardwareAccess: false,
    commandMode: "controller_owned",
    detail: "示例数据模式",
    controlOwner: "automatic",
    manualControlsAllowed: false,
    shutdown: {
      requested: false,
      confirmed: false,
      measuredCurrentA: null,
      outputOffConfirmed: false,
      reason: "",
    },
  },
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
        if (method === "automatic.start") {
          let step = 0
          const stages: Array<{ state: string; currentA: number; currentIndex: number; detail: string; powerW: number; stable: boolean; stablePoint?: { currentA: number; powerW: number; efficiencyPercent: number }; terminalOutcome?: string }> = [
            { state: "setting_current", currentA: 2.0, currentIndex: 0, detail: "第 1 点：正在平滑加载电流至 2.0 A...", powerW: 0.15, stable: false },
            { state: "waiting_stable", currentA: 2.0, currentIndex: 0, detail: "第 1 点：正在判定功率稳态 (0.28 W)...", powerW: 0.28, stable: false },
            { state: "waiting_voltage", currentA: 2.0, currentIndex: 0, detail: "第 1 点：稳态达成，正在读取输出电压并记录数据...", powerW: 0.28, stable: true, stablePoint: { currentA: 2, powerW: 0.28, efficiencyPercent: 6.2 } },
            { state: "setting_current", currentA: 6.0, currentIndex: 1, detail: "第 2 点：正在平滑加载电流至 6.0 A...", powerW: 0.42, stable: false },
            { state: "waiting_stable", currentA: 6.0, currentIndex: 1, detail: "第 2 点：正在判定功率稳态 (0.61 W)...", powerW: 0.61, stable: false },
            { state: "waiting_voltage", currentA: 6.0, currentIndex: 1, detail: "第 2 点：稳态达成，正在读取输出电压并记录数据...", powerW: 0.61, stable: true, stablePoint: { currentA: 6, powerW: 0.61, efficiencyPercent: 8.0 } },
            { state: "setting_current", currentA: 10.0, currentIndex: 2, detail: "第 3 点：正在平滑加载电流至 10.0 A...", powerW: 0.68, stable: false },
            { state: "waiting_stable", currentA: 10.0, currentIndex: 2, detail: "第 3 点：正在判定功率稳态 (0.76 W)...", powerW: 0.76, stable: false },
            { state: "waiting_voltage", currentA: 10.0, currentIndex: 2, detail: "第 3 点：稳态达成，正在读取输出电压并记录数据...", powerW: 0.76, stable: true, stablePoint: { currentA: 10, powerW: 0.76, efficiencyPercent: 8.5 } },
            { state: "saving_point", currentA: 10.0, currentIndex: 2, detail: "全部测试点完成，正在归档数据...", powerW: 0.76, stable: true },
            { state: "ramping_down", currentA: 2.0, currentIndex: 2, detail: "全部点完成，正在安全降电归零...", powerW: 0.05, stable: false },
            { state: "completed", currentA: 0.0, currentIndex: 2, detail: "测试完成，数据已归档并安全下电", powerW: 0.0, stable: true, terminalOutcome: "succeeded" as const },
          ]
          const stablePoints: Array<{ currentA: number; powerW: number; efficiencyPercent: number }> = []

          const advance = () => {
            if (step >= stages.length) return
            const stage = stages[step]
            step += 1
            if (stage.stablePoint) stablePoints.push(stage.stablePoint)
            const nextSnap: BackendSnapshot = {
              ...DEMO_SNAPSHOT,
              seriesRevisions: {
                ...DEMO_SNAPSHOT.seriesRevisions!,
                stable: 1 + stablePoints.length,
              },
              devices: {
                ...DEMO_SNAPSHOT.devices,
                powerSupply: { ...DEMO_SNAPSHOT.devices.powerSupply, activeCurrentA: stage.currentA },
                powerMeter: { ...DEMO_SNAPSHOT.devices.powerMeter, powerW: stage.powerW, stable: stage.stable },
              },
              automaticTest: {
                ...DEMO_SNAPSHOT.automaticTest,
                state: stage.state,
                detail: stage.detail,
                currentA: stage.currentA,
                currentIndex: stage.currentIndex,
                currents: [2, 6, 10],
                canStart: stage.state === "idle",
                canEnd: stage.state !== "completed" && stage.state !== "idle",
                terminalOutcome: stage.terminalOutcome as BackendSnapshot["automaticTest"]["terminalOutcome"],
                savedPointCount: stage.state === "completed" ? stablePoints.length : 0,
                exportedPointCount: stage.state === "completed" ? stablePoints.length : 0,
                journalPath: "/preview/test-records/demo-session.jsonl",
                canNextDevice: stage.state === "completed",
                canRetryExport: false,
                exportStatus: stage.state === "completed" ? "completed" : "recording",
                exportPath: stage.state === "completed" ? "/preview/DEMO-001.xlsx" : "",
              },
              safety: stage.state === "completed"
                ? {
                    ...DEMO_SNAPSHOT.safety,
                    controlOwner: "manual",
                    manualControlsAllowed: true,
                    shutdown: {
                      requested: true,
                      confirmed: true,
                      measuredCurrentA: 0,
                      outputOffConfirmed: true,
                      reason: "示例数据已确认安全下电",
                    },
                  }
                : DEMO_SNAPSHOT.safety,
              measurements: {
                ...DEMO_SNAPSHOT.measurements!,
                stable: [...stablePoints],
              },
            }
            if (mountedRef.current) setSnapshot(nextSnap)
            if (step < stages.length) {
              window.setTimeout(advance, 1300)
            }
          }
          advance()
          return DEMO_SNAPSHOT
        }

        if (method === "automatic.end") {
          const nextSnap: BackendSnapshot = {
            ...DEMO_SNAPSHOT,
            automaticTest: {
              ...DEMO_SNAPSHOT.automaticTest,
              state: "completed",
              detail: "测试已手动终止并安全下电",
              canStart: false,
              canEnd: false,
              canNextDevice: true,
              terminalOutcome: "stopped_shutdown_confirmed",
              savedPointCount: 3,
              exportedPointCount: 3,
              journalPath: "/preview/test-records/DEMO-001.jsonl",
              canRetryExport: false,
              exportStatus: "completed",
              exportPath: "/preview/DEMO-001.xlsx",
            },
            safety: {
              ...DEMO_SNAPSHOT.safety,
              controlOwner: "manual",
              manualControlsAllowed: true,
              shutdown: {
                requested: true,
                confirmed: true,
                measuredCurrentA: 0,
                outputOffConfirmed: true,
                reason: "示例数据已确认安全下电",
              },
            },
          }
          if (mountedRef.current) setSnapshot(nextSnap)
          return nextSnap
        }

        if (method === "automatic.nextDevice") {
          const nextSnap: BackendSnapshot = {
            ...DEMO_SNAPSHOT,
            configuration: { ...DEMO_SNAPSHOT.configuration!, sn: "" },
            configurations: {
              ...DEMO_SNAPSHOT.configurations,
              "1": { ...(DEMO_SNAPSHOT.configurations?.["1"] ?? DEMO_SNAPSHOT.configuration!), sn: "" },
            },
            automaticTest: {
              ...DEMO_SNAPSHOT.automaticTest,
              state: "idle",
              detail: "请输入下一台产品 SN",
              canStart: true,
              canEnd: false,
              terminalOutcome: null,
              savedPointCount: 0,
              exportedPointCount: 0,
              canNextDevice: false,
              exportStatus: "idle",
              exportPath: "",
            },
            measurements: {
              ...DEMO_SNAPSHOT.measurements!,
              power: [],
              stable: [],
              agingPower: [],
              spectrum: { wavelengthNm: [], intensity: [] },
              spectrumPeaks: [],
            },
          }
          if (mountedRef.current) setSnapshot(nextSnap)
          return nextSnap
        }

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
      if (cancelled) return
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

    // The automatic test and its live charts must continue updating while the
    // native window is hidden, minimized, or covered by another application.
    void start()
    return () => {
      cancelled = true
      stop()
    }
  }, [loadSnapshot, view])

  return { snapshot, error, loading, commandPending, pendingCommand, refresh, command }
}
