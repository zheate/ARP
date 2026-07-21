import { Channel, invoke } from "@tauri-apps/api/core"

export type BackendMode = "read_only" | "active"
export type DeviceConnectionState = "disconnected" | "connecting" | "connected" | "error"
export type SnapshotView = "automatic" | "manual" | "pd"
export type SeriesRevisions = { power: number; stable: number; spectrum: number; pd: number }
export type SeriesCursors = { power?: number; pd?: number }

export interface Notice {
  level: "info" | "warning" | "error"
  title: string
  message: string
}

export interface DeviceSnapshot {
  state: DeviceConnectionState
  label: string
  detail: string
  connected?: boolean
  running?: boolean
  ready?: boolean
  resources?: string[]
  outputEnabled?: boolean
  activeCurrentA?: number | null
  powerW?: number | null
  stable?: boolean
  peakWavelengthNm?: number | null
  centroidNm?: number | null
  fwhmNm?: number | null
  smsrDb?: number | null
  saturated?: boolean
}

export interface AppConfiguration {
  sn: string
  productModel: string
  batch: string
  station: string
  outputDir: string
  powerSupplyKind: "ch341" | "tdk"
  tdkResource: string
  setCurrentA: number
  tdkVoltageV: number
  powerMeterResource: string
  powerMeterWavelengthNm: number
  softwareGain: number
  powerMeterIntervalMs: number
  spectrometerResource: string
  integrationTimeUs: number
  autoIntegration: boolean
  spectrometerIntervalMs: number
  stableWindowS: number
  stableToleranceW: number
  initialCurrentA: number
  targetCurrentA: number
  currentStepA: number
  pointTimeoutS: number
  rampDownStepA: number
  rampDownIntervalS: number
  pauseRampDownTimeoutS: number
  useSpectrometer: boolean
}

export interface BackendSnapshot {
  capturedAt: string
  seriesRevisions?: SeriesRevisions
  backend: {
    connected: boolean
    mode: BackendMode
    protocolVersion?: number
    pythonVersion: string
    notices?: Notice[]
  }
  configuration?: AppConfiguration
  devices: {
    powerSupply: DeviceSnapshot
    powerMeter: DeviceSnapshot
    spectrometer: DeviceSnapshot
  }
  automaticTest: {
    state: string
    detail: string
    controlsEnabled: boolean
    canStart?: boolean
    canRetry?: boolean
    canEnd?: boolean
    settingsError?: string
    currents?: number[]
    currentIndex?: number
    currentA?: number | null
    progress?: number
    pauseReason?: string
    terminalOutcome?: string | null
    terminalReason?: string
  }
  measurements?: {
    power: Array<{ elapsedS: number; powerW: number }>
    stable: Array<{ currentA: number; powerW: number | null; efficiencyPercent: number | null }>
    spectrum: Array<{ wavelengthNm: number; intensity: number }>
    spectrumPeaks: Array<{ label: string; centroidNm: number; peakWavelengthNm: number; peakIntensity: number }>
  }
  pd?: {
    state: "idle" | "running"
    status: string
    devices: string[]
    channels: string[]
    ranges: Array<{ label: string; value: number | string }>
    settings: {
      device: string
      channel: string
      terminal: string
      range: number | string
      sampleRateHz: number
      blockSize: number
      scale: number
      offset: number
      unit: string
      save: boolean
      outputDir: string
    }
    currentValue: string
    voltage: string
    mean: string
    standardDeviation: string
    rangeText: string
    sampleCount: string
    points: Array<{ elapsedS: number; value: number }>
  }
  safety: {
    hardwareAccess: boolean
    commandMode: "read_only" | "controller_owned"
    detail: string
    outputShutdownUnconfirmed?: boolean
  }
  status?: { message: string }
}

export type BackendSnapshotPatch = Omit<BackendSnapshot, "measurements" | "pd"> & {
  measurements?: Partial<NonNullable<BackendSnapshot["measurements"]>>
  pd?: Omit<NonNullable<BackendSnapshot["pd"]>, "points"> & {
    points?: NonNullable<BackendSnapshot["pd"]>["points"]
  }
  seriesPatches?: {
    power?: { startX: number; points: NonNullable<BackendSnapshot["measurements"]>["power"] }
    pd?: { startX: number; points: NonNullable<BackendSnapshot["pd"]>["points"] }
  }
}

function mergeAppendSeries<T>(
  previous: T[] | undefined,
  patch: { startX: number; points: T[] } | undefined,
  getX: (point: T) => number,
): T[] | undefined {
  if (!patch) return previous
  return [...(previous ?? []).filter((point) => getX(point) >= patch.startX), ...patch.points]
}

export function mergeBackendSnapshot(previous: BackendSnapshot | null, patch: BackendSnapshotPatch): BackendSnapshot {
  const { measurements: patchMeasurements, pd: patchPd, seriesPatches, ...base } = patch
  const previousMeasurements = previous?.measurements
  const hasMeasurements = patchMeasurements !== undefined || previousMeasurements !== undefined || seriesPatches?.power !== undefined
  const measurements = hasMeasurements ? {
    power: patchMeasurements?.power
      ?? mergeAppendSeries(previousMeasurements?.power, seriesPatches?.power, (point) => point.elapsedS)
      ?? [],
    stable: patchMeasurements?.stable ?? previousMeasurements?.stable ?? [],
    spectrum: patchMeasurements?.spectrum ?? previousMeasurements?.spectrum ?? [],
    spectrumPeaks: patchMeasurements?.spectrumPeaks ?? previousMeasurements?.spectrumPeaks ?? [],
  } : undefined
  const pd = patchPd ? {
    ...patchPd,
    points: patchPd.points
      ?? mergeAppendSeries(previous?.pd?.points, seriesPatches?.pd, (point) => point.elapsedS)
      ?? [],
  } : undefined
  return {
    ...base,
    ...(measurements ? { measurements } : {}),
    ...(pd ? { pd } : {}),
  }
}

function ensureTauri(): void {
  if (typeof window !== "undefined" && !("__TAURI_INTERNALS__" in window)) {
    throw new Error("浏览器预览模式不会启动 Python 后端")
  }
}

export async function fetchBackendSnapshot(view: SnapshotView, since?: SeriesRevisions, cursors?: SeriesCursors): Promise<BackendSnapshotPatch> {
  ensureTauri()
  return invoke<BackendSnapshotPatch>("bridge_request", {
    method: "app.snapshot",
    params: { view, ...(since ? { since } : {}), ...(cursors ? { cursors } : {}) },
  })
}

type BackendSnapshotStreamMessage = {
  snapshot?: BackendSnapshotPatch
  error?: string
}

export async function subscribeBackendSnapshots(
  view: SnapshotView,
  onSnapshot: (snapshot: BackendSnapshotPatch) => void,
  onError: (message: string) => void,
): Promise<() => Promise<void>> {
  ensureTauri()
  const channel = new Channel<BackendSnapshotStreamMessage>()
  channel.onmessage = (message) => {
    if (message.snapshot) onSnapshot(message.snapshot)
    if (message.error) onError(message.error)
  }
  const generation = await invoke<number>("bridge_subscribe", { view, onEvent: channel })
  return async () => {
    await invoke("bridge_unsubscribe", { generation })
    void channel.id
  }
}

export async function sendBackendCommand(
  method: string,
  params: Record<string, unknown> = {},
): Promise<BackendSnapshot> {
  ensureTauri()
  return invoke<BackendSnapshot>("bridge_request", { method, params })
}
