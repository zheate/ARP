import { Channel, invoke } from "@tauri-apps/api/core"

import { decodeColumnarSpectrum, mergeSpectrumColumns, type SpectrumColumns, type SpectrumColumnsPatch } from "@/lib/spectrum-columns"
import { isOlderSeriesRevision, mergeSnapshotSeries } from "@/lib/snapshot-series"

export type BackendMode = "read_only" | "active"
export type DeviceConnectionState = "disconnected" | "connecting" | "connected" | "error"
export type MeasurementQuality = "live" | "stale" | "invalid" | "saturated"
export type AutomaticTerminalOutcome = "succeeded" | "stopped_shutdown_confirmed" | "aborted_shutdown_confirmed" | "shutdown_unconfirmed"
export type ControlOwner = "automatic" | "manual" | "none"
export type AutomaticPauseKind = "operator" | "spectrum_saturated" | "power_meter_disconnected" | "spectrometer_disconnected" | "record_persistence_failed" | "measurement_timeout" | "device_error"
export type AutomaticPauseAction = "resume" | "adjust_spectrum" | "recover_power_meter" | "recover_spectrometer" | "retry_persist" | "retry_point" | "safe_end"
export type SnapshotView = "automatic" | "manual" | "pd" | "shipping" | "data"
export type SeriesRevisions = { power: number; stable: number; aging: number; spectrum: number; pd: number }
export type SeriesCursors = { power?: number; pd?: number }
export type { SpectrumColumns } from "@/lib/spectrum-columns"

export interface Notice {
  id?: string
  level: "info" | "warning" | "error"
  title: string
  message: string
  code?: "power_protection" | "spectrum_saturation" | string
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
  resourceProfiles?: PowerMeterResourceProfile[]
  ch341?: Ch341State
  outputEnabled?: boolean
  activeCurrentA?: number | null
  inputVoltageV?: number | null
  outputVoltageV?: number | null
  outputCurrentA?: number | null
  temperatureC?: number | null
  powerW?: number | null
  stable?: boolean
  stabilityPercent?: number | null
  stabilityLimitPercent?: number | null
  peakWavelengthNm?: number | null
  centroidNm?: number | null
  fwhmNm?: number | null
  pib?: number | null
  pibPercent?: number | null
  smsrDb?: number | null
  saturated?: boolean
  sampledAt?: string | null
  ageMs?: number | null
  quality?: MeasurementQuality
  spectrumWindow?: { centerNm: number; halfRangeNm: number } | null
}

export interface PowerMeterResourceProfile {
  resource: string
  driverKind: string
  serialNumber: string
  softwareGain: number
}

export interface AutomaticReadinessBlocker {
  code: string
  message: string
}

export interface AutomaticReadiness {
  ready: boolean
  blockers: AutomaticReadinessBlocker[]
}

export interface AppConfiguration {
  sn: string
  productModel: string
  batch: string
  station: string
  testTemperatureC: string
  outputDir: string
  powerSupplyKind: "ch341" | "tdk"
  ch341SelectedLocationPath: string
  ch341PortBindings: Ch341PortBinding[]
  tdkResource: string
  setCurrentA: number
  tdkVoltageV: number
  powerMeterResource: string
  powerMeterSerialNumber: string
  powerMeterWavelengthNm: number
  softwareGain: number
  powerMeterIntervalMs: number
  powerSmoothing: boolean
  spectrometerResource: string
  spectrometerSerialNumber: string
  integrationTimeUs: number
  autoIntegration: boolean
  spectrometerIntervalMs: number
  spectrumAverageFrames: number
  spectrumSmoothing: boolean
  pibCenterWavelengthNm: number
  pibHalfRangeNm: number
  centerWavelengthSpecNm: number
  centerWavelengthToleranceNm: number
  fwhmMaximumNm: number
  pibMinimumPercent: number
  stableWindowS: number
  stabilityLimitPercent: number
  initialCurrentA: number
  targetCurrentA: number
  currentStepA: number
  selectedScriptId: string
  currentPlanMode: "range" | "custom"
  customCurrentPoints: Array<{ currentA: number; dwellS: number }>
  pointTimeoutS: number
  rampDownStepA: number
  rampDownIntervalS: number
  pauseRampDownTimeoutS: number
}

export interface SafetyShutdownResult {
  requested: boolean
  confirmed: boolean
  measuredCurrentA: number | null
  outputOffConfirmed: boolean
  reason: string
}

export interface AppShutdownResult {
  shutdownConfirmed: boolean
  exportsDrained: boolean
  shutdown: SafetyShutdownResult
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

export interface TestScript {
  id: string
  name: string
  builtin: boolean
  currentPlanMode: "range" | "custom"
  powerSupplyKinds?: AppConfiguration["powerSupplyKind"][]
}

export interface BackendSnapshot {
  capturedAt: string
  seriesRevisions?: SeriesRevisions
  testScripts?: TestScript[]
  backend: {
    connected: boolean
    mode: BackendMode
    protocolVersion?: number
    pythonVersion: string
    runtimeId?: string
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
    readiness?: AutomaticReadiness
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
    /** Backend monotonic duration, frozen when the worker finishes. */
    elapsedS?: number | null
    runContext?: Pick<AppConfiguration, "sn" | "station" | "testTemperatureC" | "selectedScriptId"> | null
    progress?: number
    pauseReason?: string
    pauseKind?: AutomaticPauseKind | null
    pauseStartedAtUtc?: string | null
    pauseDeadlineAtUtc?: string | null
    pauseActions?: AutomaticPauseAction[]
    terminalOutcome?: AutomaticTerminalOutcome | null
    terminalReason?: string
    savedPointCount?: number
    exportedPointCount?: number
    journalPath?: string
    canNextDevice?: boolean
    canRetryExport?: boolean
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
    agingPower: Array<{ elapsedMinutes: number; powerW: number }>
    spectrum: SpectrumColumns
    spectrumPeaks: Array<{ label: string; peakWavelengthNm: number; peakIntensity: number }>
  }
  pd?: {
    state: "idle" | "running"
    detecting?: boolean
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
    controlOwner: ControlOwner
    manualControlsAllowed: boolean
    shutdown: SafetyShutdownResult
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

export function mergeBackendSnapshot(previous: BackendSnapshot | null, patch: BackendSnapshotPatch): BackendSnapshot {
  // Revisions are local to a backend runtime. View changes start a new cache;
  // never compare counters or reuse histories across sources.
  if (previous && (
    previous.backend.mode !== patch.backend.mode
    || previous.backend.runtimeId !== patch.backend.runtimeId
  )) previous = null
  const { measurements: patchMeasurements, pd: patchPd, seriesPatches, ...base } = patch
  const acceptsSeries = (name: keyof SeriesRevisions) => !isOlderSeriesRevision(previous?.seriesRevisions?.[name], patch.seriesRevisions?.[name])
  const seriesRevisions = patch.seriesRevisions ? { ...patch.seriesRevisions } : previous?.seriesRevisions
  if (seriesRevisions && previous?.seriesRevisions) {
    for (const name of Object.keys(seriesRevisions) as Array<keyof SeriesRevisions>) {
      if (!acceptsSeries(name)) seriesRevisions[name] = previous.seriesRevisions[name]
    }
  }
  const previousMeasurements = previous?.measurements
  const hasMeasurements = patchMeasurements !== undefined || previousMeasurements !== undefined || seriesPatches?.power !== undefined
  const measurements = hasMeasurements ? {
    power: mergeSnapshotSeries(previousMeasurements?.power, patchMeasurements?.power, seriesPatches?.power, (point) => point.elapsedS, previous?.seriesRevisions?.power, patch.seriesRevisions?.power)
      ?? [],
    stable: (acceptsSeries("stable") ? patchMeasurements?.stable : undefined) ?? previousMeasurements?.stable ?? [],
    agingPower: (acceptsSeries("aging") ? patchMeasurements?.agingPower : undefined) ?? previousMeasurements?.agingPower ?? [],
    spectrum: mergeSpectrumColumns(previousMeasurements?.spectrum, acceptsSeries("spectrum") ? patchMeasurements?.spectrum : undefined),
    spectrumPeaks: (acceptsSeries("spectrum") ? patchMeasurements?.spectrumPeaks : undefined) ?? previousMeasurements?.spectrumPeaks ?? [],
  } : undefined
  const pd = patchPd ? {
    ...patchPd,
    points: mergeSnapshotSeries(previous?.pd?.points, patchPd.points, seriesPatches?.pd, (point) => point.elapsedS, previous?.seriesRevisions?.pd, patch.seriesRevisions?.pd)
      ?? [],
  } : undefined
  return {
    ...base,
    ...(seriesRevisions ? { seriesRevisions } : {}),
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
export type ShippingReportLanguage = "en" | "zh"
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
  reportLanguage?: ShippingReportLanguage
  encircledEnergyNa?: string
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
  testMode: string
  singlePoint: boolean
  currents: number[]
  points: ShippingReportPointSuggestion[]
  spectrumComplete: boolean
  spectrumWavelengthRange?: { minimum: number; maximum: number } | null
  allowedReportTypes: ShippingReportType[]
  compatibility: {
    kind: "verified_success" | "verified_manual_single_point" | "legacy_needs_confirmation" | "rejected"
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
