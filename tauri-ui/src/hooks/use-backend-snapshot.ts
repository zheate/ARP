import { useCallback, useEffect, useRef, useState } from "react"

import { fetchBackendSnapshot, mergeBackendSnapshot, sendBackendCommand, subscribeBackendSnapshots, type BackendSnapshot, type SeriesRevisions, type SnapshotView } from "@/lib/backend"

// Acquisition remains device-owned; the native WebView2 bridge streams compact
// snapshots without allocating a new command callback for every UI refresh.
const DEMO_PREVIEW_ENABLED = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("demo") === "1"
// Opt-in: the plain preview stays the static mid-run fixture that appearance captures compare
// against. `run=1` replays the staged sequence instead, including the 停留 dwell.
const DEMO_RUN_ENABLED = DEMO_PREVIEW_ENABLED && typeof window !== "undefined" && new URLSearchParams(window.location.search).get("run") === "1"

import { DEMO_RUN_SNAPSHOT, DEMO_SNAPSHOT, resetDemoRun } from "@/lib/demo-snapshot"
import { demoRunSnapshot } from "@/lib/demo-run"

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
  const demoSnapshotRef = useRef(DEMO_RUN_ENABLED ? DEMO_RUN_SNAPSHOT : DEMO_SNAPSHOT)
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
          const runBase = resetDemoRun({ ...base, configuration: runConfiguration })
          const advance = () => {
            if (!mountedRef.current || generation !== demoGenerationRef.current) return
            const next = demoRunSnapshot(runBase, (performance.now() - startedAt) / 1000)
            publishDemo(next)
            if (next.automaticTest.state !== "completed") window.setTimeout(advance, 200)
          }
          advance()
          return demoSnapshotRef.current
        }

        if (method === "automatic.end" || method === "app.stopAll") {
          demoGenerationRef.current += 1
          const savedPointCount = base.measurements?.stable.length ?? 0
          const nextSnap: BackendSnapshot = {
            ...base,
            devices: {
              ...base.devices,
              powerSupply: { ...base.devices.powerSupply, outputEnabled: false, activeCurrentA: 0, outputCurrentA: 0 },
              powerMeter: { ...base.devices.powerMeter, powerW: 0, stable: false, stableElapsedS: null },
            },
            automaticTest: {
              ...base.automaticTest,
              state: "completed",
              detail: "测试已手动终止并安全下电",
              currentA: 0,
              currentDwellS: 0,
              currentDwellElapsedS: null,
              canStart: false,
              canEnd: false,
              canNextDevice: true,
              terminalOutcome: "stopped_shutdown_confirmed",
              savedPointCount,
              exportedPointCount: savedPointCount,
              journalPath: savedPointCount ? "/preview/test-records/DEMO-001.jsonl" : "",
              canRetryExport: false,
              exportStatus: savedPointCount ? "completed" : "none",
              exportPath: savedPointCount ? "/preview/DEMO-001.xlsx" : "",
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
          const nextSnap = resetDemoRun({ ...base, configuration: { ...base.configuration!, sn: "" } })
          nextSnap.automaticTest.detail = "请输入下一台产品 SN"
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
