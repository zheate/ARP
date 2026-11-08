import { commandRuntimeIsCurrent } from "@/lib/bridge-validation"
import { BridgeRequestError } from "@/lib/bridge-error"
import type { SnapshotCommand } from "@/lib/command-contract"
import { useCallback, useEffect, useRef, useState } from "react"

import { fetchBackendSnapshot, mergeBackendSnapshot, sendBackendCommand, subscribeBackendSnapshots, type AppConfiguration, type BackendSnapshot, type PowerMeterCoefficientLibrary, type PowerMeterCoefficientRevision, type SeriesRevisions, type SnapshotView } from "@/lib/backend"
import { powerMeterProfileForResource } from "@/lib/power-meter-resource"

// Acquisition remains device-owned; the native WebView2 bridge streams compact
// snapshots without allocating a new command callback for every UI refresh.
const DEMO_PREVIEW_ENABLED = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("demo") === "1"
// Opt-in: the plain preview stays the static mid-run fixture that appearance captures compare
// against. `run=1` replays the staged sequence instead, including the 停留 dwell.
const DEMO_RUN_ENABLED = DEMO_PREVIEW_ENABLED && typeof window !== "undefined" && new URLSearchParams(window.location.search).get("run") === "1"

// Device operation never needs the preview fixtures or replay timeline.
async function loadDemoRuntime() {
  const [snapshots, replay] = await Promise.all([
    import("@/lib/demo-snapshot"),
    import("@/lib/demo-run"),
  ])
  return { ...snapshots, ...replay }
}

function selectedDemoRevision(library: PowerMeterCoefficientLibrary): PowerMeterCoefficientRevision | null {
  const profile = library.profiles.find((item) => item.id === library.selectedProfileId)
  return profile?.versions.find((item) => item.version === library.selectedVersion)
    ?? profile?.versions[profile.versions.length - 1]
    ?? null
}

function demoConfigurationForLibrary(
  config: AppConfiguration,
  library: PowerMeterCoefficientLibrary,
): AppConfiguration {
  const revision = selectedDemoRevision(library)
  return {
    ...config,
    powerMeterDriverKind: library.driverKind,
    powerMeterSerialNumber: library.serialNumber,
    powerMeterProfileId: library.selectedProfileId,
    powerMeterProfileName: revision?.name ?? "未绑定方案",
    powerMeterProfileVersion: revision?.version ?? 0,
    powerMeterProfileNotes: revision?.notes ?? "",
    powerMeterProfileWavelengthPending: Boolean(revision && revision.wavelengthNm == null),
    softwareGain: revision?.softwareGain ?? 1,
    powerMeterWavelengthNm: revision?.wavelengthNm ?? 976,
  }
}

function demoProfileRevision(params: Record<string, unknown>, version: number): PowerMeterCoefficientRevision {
  const name = String(params.name ?? "").trim()
  const softwareGain = Number(params.softwareGain)
  const wavelengthNm = Number(params.wavelengthNm)
  if (!name) throw new Error("请填写方案名称")
  if (![softwareGain, wavelengthNm].every((value) => Number.isFinite(value) && value > 0)) {
    throw new Error("功率系数和校准波长必须是大于零的数值")
  }
  return {
    version,
    name,
    softwareGain,
    wavelengthNm,
    notes: String(params.notes ?? "").trim(),
    operator: String(params.operator ?? "").trim(),
    reason: String(params.reason ?? "").trim(),
    createdAt: new Date().toISOString(),
  }
}

export function runDemoPowerMeterProfileCommand(
  base: BackendSnapshot,
  method: string,
  params: Record<string, unknown>,
): BackendSnapshot {
  const current = base.devices.powerMeter.coefficientLibrary
  if (!current) throw new Error("请先识别并选择具有探头 SN 的功率计")
  if (current.driverKind !== params.driverKind || current.serialNumber !== params.serialNumber) {
    throw new Error("探头已切换，请重新打开系数方案")
  }
  const library = JSON.parse(JSON.stringify(current)) as PowerMeterCoefficientLibrary
  const profileId = String(params.profileId ?? "")
  let profile = library.profiles.find((item) => item.id === profileId)
  let revision: PowerMeterCoefficientRevision | undefined
  if (method === "powerMeter.profile.create") {
    revision = demoProfileRevision(params, 1)
    profile = { id: `demo-profile-${Date.now()}-${library.profiles.length}`, versions: [revision] }
    library.profiles.push(profile)
  } else {
    if (!profile) throw new Error("方案不存在")
    const latest = profile.versions[profile.versions.length - 1]
    if (!latest || params.expectedVersion !== latest.version) {
      throw new Error("方案已被修改，请刷新后查看最新版本；本次编辑未覆盖已有修改")
    }
    if (method === "powerMeter.profile.select") {
      revision = latest
    } else if (method === "powerMeter.profile.delete") {
      library.profiles = library.profiles.filter((item) => item.id !== profileId)
      if (library.selectedProfileId === profileId) {
        const replacement = library.profiles[0]
        library.selectedProfileId = replacement?.id ?? ""
        library.selectedVersion = replacement?.versions[replacement.versions.length - 1]?.version ?? 0
      }
    } else if (method === "powerMeter.profile.save") {
      revision = demoProfileRevision(params, latest.version + 1)
      profile.versions.push(revision)
    } else if (method === "powerMeter.profile.restore") {
      const target = profile.versions.find((item) => item.version === params.restoreVersion)
      if (!target) throw new Error("历史版本不存在")
      revision = demoProfileRevision({
        ...target,
        operator: params.operator ?? "",
        reason: params.reason || `恢复 v${target.version}`,
      }, latest.version + 1)
      profile.versions.push(revision)
    } else {
      throw new Error("未知系数方案操作")
    }
  }
  if (method !== "powerMeter.profile.delete" && revision) {
    if (library.profiles.some((item) => item.id !== profile!.id && item.versions[item.versions.length - 1]?.name === revision!.name)) {
      throw new Error("同一探头已有同名方案，请使用不同名称")
    }
    library.selectedProfileId = profile.id
    library.selectedVersion = revision.version
  }
  const configuration = demoConfigurationForLibrary(base.configuration!, library)
  return {
    ...base,
    configuration,
    devices: {
      ...base.devices,
      powerMeter: {
        ...base.devices.powerMeter,
        coefficientLibrary: library,
        resourceProfiles: base.devices.powerMeter.resourceProfiles?.map((item) => (
          item.driverKind === library.driverKind && item.serialNumber === library.serialNumber
            ? { ...item, softwareGain: configuration.softwareGain, coefficientLibrary: library }
            : item
        )),
      },
    },
  }
}

export function useBackendSnapshot(view: SnapshotView) {
  const [snapshot, setSnapshot] = useState<BackendSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [streamError, setStreamError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [commandPending, setCommandPending] = useState(false)
  const [pendingCommand, setPendingCommand] = useState<string | null>(null)
  const mountedRef = useRef(true)
  const commandPendingRef = useRef(false)
  const safetyGenerationRef = useRef(0)
  const latestViewRef = useRef(view)
  const snapshotRef = useRef<BackendSnapshot | null>(null)
  const demoSnapshotRef = useRef<BackendSnapshot | null>(null)
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
        const { DEMO_RUN_SNAPSHOT, DEMO_SNAPSHOT } = await loadDemoRuntime()
        const demo = demoSnapshotRef.current ?? (DEMO_RUN_ENABLED ? DEMO_RUN_SNAPSHOT : DEMO_SNAPSHOT)
        demoSnapshotRef.current = demo
        if (mountedRef.current && latestViewRef.current === requestedView) {
          snapshotRef.current = demo
          revisionsRef.current = demo.seriesRevisions ?? null
          setSnapshot(demo)
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

  const command = useCallback(async (method: SnapshotCommand, params: Record<string, unknown> = {}) => {
    const safetyCommand = method === "app.stopAll" || method === "powerSupply.safeRampDown"
    if (safetyCommand) safetyGenerationRef.current += 1
    const startedSafetyGeneration = safetyGenerationRef.current
    if (safetyCommand && !DEMO_PREVIEW_ENABLED) {
      try {
        const requestedView = view
        const startedRuntime = snapshotRef.current?.backend.runtimeId
        const response = await sendBackendCommand(method, params)
        if (!commandRuntimeIsCurrent(startedRuntime, snapshotRef.current?.backend.runtimeId, response.backend.runtimeId)) {
          throw new BridgeRequestError({ kind: "protocol", code: "stale_response", method, message: "后端会话已变化，已忽略旧命令响应" })
        }
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
    if (commandPendingRef.current && !safetyCommand) {
      if (snapshotRef.current) return snapshotRef.current
      throw new Error("上一项操作仍在处理中")
    }
    if (!safetyCommand) {
      commandPendingRef.current = true
      setCommandPending(true)
      setPendingCommand(method)
    }
    try {
      if (DEMO_PREVIEW_ENABLED) {
        const { DEMO_RUN_SNAPSHOT, DEMO_SNAPSHOT, resetDemoRun, demoRunSnapshot } = await loadDemoRuntime()
        const base = demoSnapshotRef.current ?? (DEMO_RUN_ENABLED ? DEMO_RUN_SNAPSHOT : DEMO_SNAPSHOT)
        demoSnapshotRef.current = base
        const publishDemo = (next: BackendSnapshot) => {
          if (!safetyCommand && startedSafetyGeneration !== safetyGenerationRef.current) {
            throw new Error("操作已因安全下电取消，请重新操作")
          }
          demoSnapshotRef.current = next
          snapshotRef.current = next
          if (mountedRef.current) setSnapshot(next)
          setError(null)
          return next
        }
        if (method === "app.configure") {
          const configured = { ...base.configuration!, ...params } as AppConfiguration
          const resourceProfile = powerMeterProfileForResource(
            base.devices.powerMeter.resourceProfiles,
            configured.powerMeterResource,
          )
          const library = resourceProfile?.coefficientLibrary
            ?? (configured.powerMeterResource === base.configuration?.powerMeterResource
              ? base.devices.powerMeter.coefficientLibrary
              : null)
          const configuration = library ? demoConfigurationForLibrary(configured, library) : configured
          return publishDemo({
            ...base,
            configuration,
            devices: {
              ...base.devices,
              powerMeter: {
                ...base.devices.powerMeter,
                coefficientLibrary: library,
                detail: resourceProfile
                  ? `${resourceProfile.driverKind} · 探头 SN ${resourceProfile.serialNumber}`
                  : base.devices.powerMeter.detail,
              },
            },
          })
        }
        if (method.startsWith("powerMeter.profile.")) {
          return publishDemo(runDemoPowerMeterProfileCommand(base, method, params))
        }
        if (method === "device.refresh") {
          const device = params.device
          if (device !== "powerSupply" && device !== "powerMeter" && device !== "spectrometer") {
            throw new Error("未知示例设备")
          }
          const detecting = publishDemo({
            ...base,
            devices: {
              ...base.devices,
              [device]: { ...base.devices[device], detecting: true, detail: "正在识别示例设备…" },
            },
          })
          await new Promise((resolve) => window.setTimeout(resolve, 300))
          const failed = Boolean(params.fail || (typeof window !== "undefined" && (window as unknown as { __SIMULATE_DEVICE_FAIL?: boolean }).__SIMULATE_DEVICE_FAIL))
          return publishDemo({
            ...detecting,
            devices: {
              ...detecting.devices,
              [device]: {
                ...detecting.devices[device],
                detecting: false,
                detail: failed
                  ? "识别失败：未找到 OceanDirect.dll: /Users/zh/Documents/test/ARP/assets/libs/ocean_direct/OceanDirect.dll"
                  : "识别完成：示例设备可用",
              },
            },
          })
        }
        if (method === "powerMeter.start" || method === "powerMeter.stop") {
          return publishDemo({
            ...base,
            devices: {
              ...base.devices,
              powerMeter: { ...base.devices.powerMeter, connected: true, state: "connected", running: method.endsWith("start") },
            },
          })
        }
        if (method === "spectrometer.start" || method === "spectrometer.stop") {
          return publishDemo({
            ...base,
            devices: {
              ...base.devices,
              spectrometer: { ...base.devices.spectrometer, connected: true, state: "connected", running: method.endsWith("start") },
            },
          })
        }
        if (method === "powerSupply.connect" || method === "powerSupply.disconnect") {
          const connected = method.endsWith("connect") && !method.endsWith("disconnect")
          return publishDemo({
            ...base,
            devices: {
              ...base.devices,
              powerSupply: { ...base.devices.powerSupply, connected, state: connected ? "connected" : "disconnected", outputEnabled: connected ? base.devices.powerSupply.outputEnabled : false },
            },
          })
        }
        if (method === "powerSupply.setCurrent") {
          const currentA = Number(params.currentA)
          return publishDemo({
            ...base,
            configuration: { ...base.configuration!, setCurrentA: currentA },
            devices: { ...base.devices, powerSupply: { ...base.devices.powerSupply, activeCurrentA: currentA, outputCurrentA: currentA } },
            manualTest: { pendingCount: base.manualTest?.pendingCount ?? 0, exportStatus: "recording", exportPath: "", exportError: "",
              capture: { state: "waiting_stable", detail: "等待功率稳定（示例）", currentA, remainingS: null } },
            safety: { ...base.safety, shutdown: { requested: false, confirmed: false, measuredCurrentA: null, outputOffConfirmed: false, reason: "" } },
          })
        }
        if (method === "powerSupply.setOutput" || method === "powerSupply.safeRampDown") {
          const enabled = method === "powerSupply.setOutput" && params.enabled === true
          return publishDemo({
            ...base,
            configuration: { ...base.configuration!, setCurrentA: 0 },
            devices: { ...base.devices, powerSupply: { ...base.devices.powerSupply, outputEnabled: enabled, activeCurrentA: 0, outputCurrentA: 0 } },
            manualTest: { pendingCount: base.manualTest?.pendingCount ?? 0, exportStatus: base.manualTest?.exportStatus ?? "idle", exportPath: "", exportError: "",
              capture: { state: "cancelled", detail: enabled ? "开启输出前已归零（示例）" : "安全下电，未完成的测试点已取消（示例）", currentA: null, remainingS: null } },
            safety: { ...base.safety, shutdown: { requested: !enabled, confirmed: !enabled, measuredCurrentA: 0, outputOffConfirmed: !enabled, reason: enabled ? "" : "示例数据已确认安全下电" } },
          })
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
          return demoSnapshotRef.current ?? base
        }

        if (method === "automatic.end" || method === "app.stopAll") {
          demoGenerationRef.current += 1
          const savedPointCount = base.measurements?.stable.length ?? 0
          const nextSnap: BackendSnapshot = {
            ...base,
            devices: {
              ...base.devices,
              powerSupply: { ...base.devices.powerSupply, outputEnabled: false, activeCurrentA: 0, outputCurrentA: 0 },
              powerMeter: { ...base.devices.powerMeter, running: false, powerW: 0, stable: false, stableElapsedS: null },
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
      const startedRuntime = snapshotRef.current?.backend.runtimeId
      const response = await sendBackendCommand(method, params)
      if (startedSafetyGeneration !== safetyGenerationRef.current) {
        throw new Error("操作已因安全下电取消，请重新操作")
      }
      if (!commandRuntimeIsCurrent(startedRuntime, snapshotRef.current?.backend.runtimeId, response.backend.runtimeId)) {
        throw new BridgeRequestError({ kind: "protocol", code: "stale_response", method, message: "后端会话已变化，已忽略旧命令响应" })
      }
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
      if (mountedRef.current && startedSafetyGeneration === safetyGenerationRef.current) setError(message)
      throw reason
    } finally {
      if (!safetyCommand) commandPendingRef.current = false
      if (!safetyCommand && mountedRef.current) {
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
