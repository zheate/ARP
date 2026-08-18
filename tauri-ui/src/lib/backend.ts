import { Channel, invoke } from "@tauri-apps/api/core"

import { decodeColumnarSpectrum, mergeSpectrumColumns, type SpectrumColumns, type SpectrumColumnsPatch } from "@/lib/spectrum-columns"

export type BackendMode = "read_only" | "active"
export type DeviceConnectionState = "disconnected" | "connecting" | "connected" | "error"
export type SnapshotView = "automatic" | "automatic_1" | "automatic_2" | "manual" | "manual_1" | "manual_2" | "pd" | "pd_1" | "pd_2" | "shipping"
export type SeriesRevisions = { power: number; stable: number; spectrum: number; pd: number }
export type SeriesCursors = { power?: number; pd?: number }
export type { SpectrumColumns } from "@/lib/spectrum-columns"

export interface Notice {
  id?: string
  level: "info" | "warning" | "error"
  title: string
  message: string
  code?: "power_protection" | "spectrum_saturation" | string
  stationId?: "1" | "2" | string
}

export interface Ch341PortBinding {
  locationPath: string
  name: string
}

export interface Ch341PortInfo {
  locationPath: string
  name: string | null
  online: boolean
  deviceIndex: number | null
}

export interface Ch341State {
  discoveryState: "not_scanned" | "ready" | "empty" | "error"
  error: string
  ports: Ch341PortInfo[]
  connectedPort: {
    locationPath: string
    name: string
    deviceIndex: number
  } | null
}

export interface DeviceSnapshot {
  state: DeviceConnectionState
  label: string
  detail: string
  connected?: boolean
  running?: boolean
  detecting?: boolean
  ready?: boolean
  resources?: string[]
  ch341?: Ch341State
  outputEnabled?: boolean
  activeCurrentA?: number | null
  inputVoltageV?: number | null
  outputVoltageV?: number | null
  outputCurrentA?: number | null
  temperatureC?: number | null
  powerW?: number | null
  stable?: boolean
  peakWavelengthNm?: number | null
  centroidNm?: number | null
  fwhmNm?: number | null
  pib?: number | null
  pibPercent?: number | null
  smsrDb?: number | null
  saturated?: boolean
  spectrumWindow?: { centerNm: number; halfRangeNm: number } | null
}

export interface AppConfiguration {
  sn: string
  productModel: string
  batch: string
  station: string
  outputDir: string
  powerSupplyKind: "ch341" | "tdk"
  ch341SelectedLocationPath: string
  ch341PortBindings: Ch341PortBinding[]
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
  spectrumAverageFrames: number
  pibCenterWavelengthNm: number
  pibHalfRangeNm: number
  centerWavelengthSpecNm: number
  centerWavelengthToleranceNm: number
  fwhmMaximumNm: number
  pibMinimumPercent: number
  stableWindowS: number
  stableToleranceW: number
  initialCurrentA: number
  targetCurrentA: number
  currentStepA: number
  currentPlanMode: "range" | "custom"
  customCurrentPoints: Array<{ currentA: number; dwellS: number }>
  pointTimeoutS: number
  rampDownStepA: number
  rampDownIntervalS: number
  pauseRampDownTimeoutS: number
  useSpectrometer: boolean
}

export interface StationSummary {
  stationId: string
  station: string
  sn: string
  productModel: string
  batch: string
  state: string
  detail: string
  progress: number
  activeCurrentA?: number | null
  powerW?: number | null
  powerSupplyConnected: boolean
  powerMeterRunning: boolean
  spectrometerRunning: boolean
  canStart?: boolean
  canPause?: boolean
  canRetry?: boolean
  canEnd?: boolean
  terminalOutcome?: string | null
  terminalReason?: string
  exportStatus?: string
  exportPath?: string
  exportError?: string
}

export interface TestHistoryCandidate {
  sourcePath: string
  station: string
  startedAtUtc: string
  modifiedAtMs: number
  pointCount: number
  minimumCurrentA: number
  maximumCurrentA: number
}

export interface TestHistoryReference extends TestHistoryCandidate {
  points: Array<{ currentA: number; powerW: number }>
}

export interface BackendSnapshot {
  capturedAt: string
  seriesRevisions?: SeriesRevisions
  activeStation?: string
  stations?: Record<string, StationSummary>
  configurations?: Record<string, AppConfiguration>
  backend: {
    connected: boolean
    mode: BackendMode
    protocolVersion?: number
    pythonVersion: string
    runtimeLogPath?: string
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
    canPause?: boolean
    canEnd?: boolean
    settingsError?: string
    currents?: number[]
    dwellTimesS?: number[]
    currentIndex?: number
    currentA?: number | null
    currentDwellS?: number | null
    currentDwellElapsedS?: number | null
    samplePath?: string
    progress?: number
    pauseReason?: string
    terminalOutcome?: string | null
    terminalReason?: string
    exportStatus?: string
    exportPath?: string
    exportError?: string
    reference?: TestHistoryReference | null
  }
  manualTest?: {
    pendingCount: number
    exportStatus: string
    exportPath: string
    exportError: string
  }
  measurements?: {
    power: Array<{ elapsedS: number; powerW: number }>
    stable: Array<{ currentA: number; powerW: number | null; efficiencyPercent: number | null }>
    spectrum: SpectrumColumns
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
  measurements?: Omit<Partial<NonNullable<BackendSnapshot["measurements"]>>, "spectrum"> & {
    spectrum?: SpectrumColumnsPatch
  }
  pd?: Omit<NonNullable<BackendSnapshot["pd"]>, "points"> & {
    points?: NonNullable<BackendSnapshot["pd"]>["points"]
  }
  seriesPatches?: {
    power?: { startX: number; points: NonNullable<BackendSnapshot["measurements"]>["power"] }
    pd?: { startX: number; points: NonNullable<BackendSnapshot["pd"]>["points"] }
  }
}

export function decodeBackendSnapshotPatch(value: unknown): BackendSnapshotPatch {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("后端快照格式无效")
  }
  const patch = value as BackendSnapshotPatch
  if (patch.measurements?.spectrum !== undefined) {
    decodeColumnarSpectrum(patch.measurements.spectrum)
  }
  return patch
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
    spectrum: mergeSpectrumColumns(previousMeasurements?.spectrum, patchMeasurements?.spectrum),
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
  const value = await invoke<unknown>("bridge_request", {
    method: "app.snapshot",
    params: { view, ...(since ? { since } : {}), ...(cursors ? { cursors } : {}) },
  })
  return decodeBackendSnapshotPatch(value)
}

export type ShippingReportType = "spectrum" | "pole"
export type SpectrumAxisMode = "counts" | "relative_db"

export interface ShippingReportFieldDefinition {
  key: string
  label: string
  unit: string
  side: "left" | "right" | "single"
  measuredKey?: string | null
}

export interface ShippingReportPointSuggestion {
  currentA: number
  voltageV: number
  powerW: number
  efficiencyPercent: number
  peakWavelengthNm?: number | null
  centroidNm?: number | null
  fwhmNm?: number | null
  pibPercent?: number | null
  smsrDb?: number | null
  spectrumPeakCounts?: number | null
  wavelengthCurrentCoefficientNmPerA?: number | null
  hasSpectrum: boolean
}

export interface ShippingReportPreferences {
  selectedFields: string[]
  manualValues: Record<string, string>
  customFields: ShippingReportFieldDefinition[]
  spectrumAxis?: {
    mode: SpectrumAxisMode
    minimum: number
    maximum: number
    wavelengthMinimum?: number
    wavelengthMaximum?: number
  }
}

export interface ShippingWorkbookInspection {
  sourcePath: string
  schemaVersion: string
  sn: string
  productName: string
  currents: number[]
  points: ShippingReportPointSuggestion[]
  spectrumComplete: boolean
  spectrumWavelengthRange?: { minimum: number; maximum: number } | null
  allowedReportTypes: ShippingReportType[]
  compatibility: {
    kind: "verified_success" | "legacy_needs_confirmation" | "rejected"
    status: string
    message: string
    requiresLegacyConfirmation: boolean
  }
  fieldDefinitions: Record<ShippingReportType, ShippingReportFieldDefinition[]>
  preferences: Record<ShippingReportType, ShippingReportPreferences>
}

export interface ShippingReportGenerationResult {
  outputPath: string
}

export type ShippingReportBatchItemStatus = "ready" | "invalid" | "ignored_duplicate"

export interface ShippingReportBatchItem {
  sourcePath: string
  modifiedAtMs: number
  sn: string
  productName: string
  currents: number[]
  allowedReportTypes: ShippingReportType[]
  status: ShippingReportBatchItemStatus
  message: string
  compatibility: ShippingWorkbookInspection["compatibility"] | null
}

export interface ShippingReportBatchInspection {
  rootPath: string
  items: ShippingReportBatchItem[]
  unionCurrents: number[]
  commonCurrents: number[]
  productNames: string[]
  allReadySupportSpectrum: boolean
  preferences: Record<ShippingReportType, ShippingReportPreferences>
}

export interface ShippingReportBatchGenerationResult extends ShippingReportGenerationResult {
  sourcePath: string
  sn: string
}

export interface ShippingReportPreviewResult {
  pages: string[]
}

type BackendSnapshotStreamMessage = {
  snapshot?: BackendSnapshotPatch
  error?: string
}

type BackendSnapshotStreamEnvelope = {
  __arpSnapshotStream?: string
  message?: BackendSnapshotStreamMessage
}

type WebView2Host = {
  addEventListener: (type: "message", listener: (event: { data: unknown }) => void) => void
  removeEventListener: (type: "message", listener: (event: { data: unknown }) => void) => void
}

function getWebView2Host(): WebView2Host | undefined {
  return (window as unknown as { chrome?: { webview?: WebView2Host } }).chrome?.webview
}

export async function subscribeBackendSnapshots(
  view: SnapshotView,
  onSnapshot: (snapshot: BackendSnapshotPatch) => void,
  onError: (message: string) => void,
): Promise<() => Promise<void>> {
  ensureTauri()
  const subscriptionId = `${view}-${crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`
  const webView2Host = getWebView2Host()
  let active = true

  const dispatch = (message: BackendSnapshotStreamMessage) => {
    if (!active) return
    if (message.snapshot) {
      try {
        onSnapshot(decodeBackendSnapshotPatch(message.snapshot))
      } catch (reason) {
        onError(reason instanceof Error ? reason.message : String(reason))
      }
    }
    if (message.error) onError(message.error)
  }

  const acceptEnvelope = (value: unknown) => {
    if (!value || typeof value !== "object") return
    const envelope = value as BackendSnapshotStreamEnvelope
    if (envelope.__arpSnapshotStream !== subscriptionId || !envelope.message) return
    dispatch(envelope.message)
  }

  const handleWebView2Message = (event: { data: unknown }) => acceptEnvelope(event.data)
  webView2Host?.addEventListener("message", handleWebView2Message)

  const channel = webView2Host ? undefined : new Channel<BackendSnapshotStreamEnvelope>()
  if (channel) channel.onmessage = acceptEnvelope

  let generation: number
  try {
    generation = await invoke<number>("bridge_subscribe", {
      view,
      subscriptionId,
      ...(channel ? { onEvent: channel } : {}),
    })
  } catch (reason) {
    active = false
    webView2Host?.removeEventListener("message", handleWebView2Message)
    if (channel) channel.onmessage = () => undefined
    throw reason
  }

  return async () => {
    active = false
    webView2Host?.removeEventListener("message", handleWebView2Message)
    if (channel) channel.onmessage = () => undefined
    await invoke("bridge_unsubscribe", { generation })
  }
}

export async function sendBackendCommand(
  method: string,
  params: Record<string, unknown> = {},
): Promise<BackendSnapshot> {
  const value = await sendBackendRequest<unknown>(method, params)
  return decodeBackendSnapshotPatch(value) as BackendSnapshot
}

export async function sendBackendRequest<T>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  ensureTauri()
  return invoke<T>("bridge_request", { method, params })
}
