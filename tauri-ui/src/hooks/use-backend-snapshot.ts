import { useCallback, useEffect, useRef, useState } from "react"

import { fetchBackendSnapshot, mergeBackendSnapshot, sendBackendCommand, subscribeBackendSnapshots, type BackendSnapshot, type SeriesRevisions, type SnapshotView } from "@/lib/backend"

// Acquisition remains device-owned; the native WebView2 bridge streams compact
// snapshots without allocating a new command callback for every UI refresh.
const DEMO_PREVIEW_ENABLED = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("demo") === "1"

import { DEMO_SNAPSHOT } from "@/lib/demo-snapshot"

export function useBackendSnapshot(view: SnapshotView) {
  const [snapshot, setSnapshot] = useState<BackendSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [streamError, setStreamError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [commandPending, setCommandPending] = useState(false)
  const [pendingCommand, setPendingCommand] = useState<string | null>(null)
  const mountedRef = useRef(true)
  const commandPendingRef = useRef(false)
  const latestViewRef = useRef(view)
  const snapshotRef = useRef<BackendSnapshot | null>(null)
  const demoSnapshotRef = useRef(DEMO_SNAPSHOT)
  const demoGenerationRef = useRef(0)
  const revisionsRef = useRef<SeriesRevisions | null>(null)
  latestViewRef.current = view

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false; demoGenerationRef.current += 1 }
  }, [])

  const loadSnapshot = useCallback(async (showLoading: boolean, requestedView: SnapshotView) => {
    if (showLoading) {
      revisionsRef.current = null
      setLoading(true)
    }
    try {
      if (DEMO_PREVIEW_ENABLED) {
        if (mountedRef.current && latestViewRef.current === requestedView) {
          snapshotRef.current = demoSnapshotRef.current
          revisionsRef.current = demoSnapshotRef.current.seriesRevisions ?? null
          setSnapshot(demoSnapshotRef.current)
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
    if (method === "app.stopAll" && !DEMO_PREVIEW_ENABLED) {
      try {
        const requestedView = view
        const response = await sendBackendCommand(method, params)
        const matchesView = latestViewRef.current === requestedView
        const nextSnapshot = matchesView ? mergeBackendSnapshot(snapshotRef.current, response) : response
        if (mountedRef.current && matchesView) {
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
        const base = demoSnapshotRef.current
        const publishDemo = (next: BackendSnapshot) => {
          demoSnapshotRef.current = next
          snapshotRef.current = next
          if (mountedRef.current) setSnapshot(next)
          setError(null)
          return next
        }
        if (method === "app.configure") {
          return publishDemo({ ...base, configuration: { ...base.configuration!, ...params } })
        }
        if (method === "automatic.start") {
          const runConfiguration = { ...base.configuration!, ...params }
          const generation = ++demoGenerationRef.current
          const startedAt = performance.now()
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
            if (!mountedRef.current || generation !== demoGenerationRef.current || step >= stages.length) return
            const stage = stages[step]
            step += 1
            if (stage.stablePoint) stablePoints.push(stage.stablePoint)
            const nextSnap: BackendSnapshot = {
              ...base,
              configuration: runConfiguration,
              seriesRevisions: {
                ...DEMO_SNAPSHOT.seriesRevisions!,
                stable: 1 + stablePoints.length,
              },
              devices: {
                ...DEMO_SNAPSHOT.devices,
                powerSupply: { ...DEMO_SNAPSHOT.devices.powerSupply, activeCurrentA: stage.currentA, outputCurrentA: stage.currentA, outputEnabled: stage.state !== "completed" },
                powerMeter: { ...DEMO_SNAPSHOT.devices.powerMeter, powerW: stage.powerW, stable: stage.stable },
              },
              automaticTest: {
                ...DEMO_SNAPSHOT.automaticTest,
                elapsedS: Math.floor((performance.now() - startedAt) / 1000),
                runContext: { sn: runConfiguration.sn, station: runConfiguration.station, testTemperatureC: runConfiguration.testTemperatureC, selectedScriptId: runConfiguration.selectedScriptId },
                state: stage.state,
                detail: stage.detail,
                currentA: stage.currentA,
                currentIndex: stage.currentIndex,
                currents: [2, 6, 10],
                canStart: stage.state === "idle",
                canEnd: stage.state !== "completed" && stage.state !== "idle",
                terminalOutcome: stage.terminalOutcome as BackendSnapshot["automaticTest"]["terminalOutcome"],
                savedPointCount: stablePoints.length,
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
            publishDemo(nextSnap)
            if (step < stages.length) {
              window.setTimeout(advance, 1300)
            }
          }
          advance()
          return demoSnapshotRef.current
        }

        if (method === "automatic.end" || method === "app.stopAll") {
          demoGenerationRef.current += 1
          const nextSnap: BackendSnapshot = {
            ...base,
            devices: { ...base.devices, powerSupply: { ...base.devices.powerSupply, outputEnabled: false, activeCurrentA: 0, outputCurrentA: 0 } },
            automaticTest: {
              ...base.automaticTest,
              state: "completed",
              detail: "测试已手动终止并安全下电",
              canStart: false,
              canEnd: false,
              canNextDevice: true,
              terminalOutcome: "stopped_shutdown_confirmed",
              savedPointCount: base.measurements?.stable.length ?? 0,
              exportedPointCount: base.measurements?.stable.length ?? 0,
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
          return publishDemo(nextSnap)
        }

        if (method === "automatic.nextDevice") {
          demoGenerationRef.current += 1
          const nextSnap: BackendSnapshot = {
            ...base,
            configuration: { ...base.configuration!, sn: "" },
            automaticTest: {
              ...DEMO_SNAPSHOT.automaticTest,
              state: "idle",
              elapsedS: null,
              runContext: null,
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
          return publishDemo(nextSnap)
        }

        return publishDemo(base)
      }
      const requestedView = view
      const response = await sendBackendCommand(method, params)
      const matchesView = latestViewRef.current === requestedView
      const nextSnapshot = matchesView ? mergeBackendSnapshot(snapshotRef.current, response) : response
      if (mountedRef.current && matchesView) {
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
    setStreamError(null)
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
            setStreamError(null)
            setLoading(false)
          },
          (message) => {
            if (cancelled || epoch !== streamEpoch) return
            setStreamError(message)
            setLoading(false)
          },
        )
        if (cancelled || epoch !== streamEpoch) void unsubscribe()
        else stopStream = unsubscribe
      } catch (reason) {
        if (cancelled || epoch !== streamEpoch) return
        setStreamError(reason instanceof Error ? reason.message : String(reason))
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

  return { snapshot, error: streamError ?? error, loading, commandPending, pendingCommand, refresh, command }
}
