import { invoke } from "@tauri-apps/api/core"

export type BackendMode = "read_only" | "active"
export type DeviceConnectionState = "disconnected" | "connecting" | "connected" | "error"

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

export interface HistorySession {
  sessionId: string
  sn: string
  productModel: string
  batch: string
  station: string
  mode: string
  startedAt: string
  endedAt: string | null
  status: string
  terminationReason: string
  shutdownConfirmed: boolean | null
  workbookPath: string
  exportState: string
  exportError: string
}

export interface HistoryAttempt {
  attemptId: string
  sequenceIndex: number
  targetCurrentA: number | null
  attemptNo: number
  createdAt: string
  validity: string
  invalidReason: string
  selected: boolean
  currentA: number | null
  voltageV: number | null
  powerW: number | null
  efficiency: number | null
  peakWavelengthNm: number | null
  centroidNm: number | null
  fwhmNm: number | null
  pib: number | null
  smsrDb: number | null
}

export interface BackendSnapshot {
  capturedAt: string
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
  records?: {
    current: Array<Record<string, number | null>>
    unsavedCount: number
    pendingDatabaseCount: number
    workbookPath: string
    sessionId: string
    history: HistorySession[]
    detail: Record<string, unknown> | null
    attempts: HistoryAttempt[]
    comparison: Array<{ sessionId: string; label: string; points: HistoryAttempt[] }>
    filters: Record<string, string>
    summary: {
      sessions: number
      completionRate: number | null
      invalidAttemptRate: number | null
      retestRate: number | null
      medianDurationS: number | null
    }
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
  status?: { message: string; log: string }
}

function ensureTauri(): void {
  if (typeof window !== "undefined" && !("__TAURI_INTERNALS__" in window)) {
    throw new Error("浏览器预览模式不会启动 Python 后端")
  }
}

export async function fetchBackendSnapshot(): Promise<BackendSnapshot> {
  ensureTauri()
  return invoke<BackendSnapshot>("bridge_snapshot")
}

export async function sendBackendCommand(
  method: string,
  params: Record<string, unknown> = {},
): Promise<BackendSnapshot> {
  ensureTauri()
  return invoke<BackendSnapshot>("bridge_request", { method, params })
}
