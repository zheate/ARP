import type { AppConfiguration, AppShutdownResult, BackendSnapshot, BackendSnapshotPatch, ShippingReportBatchGenerationResult, ShippingReportBatchInspection, ShippingReportGenerationResult, ShippingReportLanguage, ShippingReportFieldDefinition, ShippingReportPreviewResult, ShippingReportType, ShippingWorkbookInspection, SnapshotView, SpectrumAxisMode } from "./backend"
import type { DataDetail, DataList } from "./data-browser"

type Context = { view?: SnapshotView }
type Entry<P, R = BackendSnapshot> = { params: P & Context; result: R }
type Resume = { integrationTimeUs?: number; autoIntegration?: boolean }
type Profile = { driverKind: string; serialNumber: string; profileId?: string; expectedVersion?: number; restoreVersion?: number; name?: string; softwareGain?: number; wavelengthNm?: number; notes?: string; operator?: string; reason?: string }
type Report = {
  sourcePath: string; reportType: ShippingReportType; reportLanguage: ShippingReportLanguage
  productName: string; sn?: string; operatingCurrentA: number
  fields: Array<{ key: string; value: string }> | Record<string, unknown>
  customFields: ShippingReportFieldDefinition[]; encircledEnergyNa: number | string
  legacyCompletionConfirmed?: boolean
  spectrumAxis?: { mode: SpectrumAxisMode; minimum: number; maximum: number; wavelengthMinimum: number; wavelengthMaximum: number }
}
export interface CommandMap {
  "system.ping": Entry<{}, { status: string; mode: string; protocolVersion: number }>
  "app.snapshot": Entry<{ since?: Record<string, number>; cursors?: Record<string, number> }, BackendSnapshotPatch>
  "app.configure": Entry<Partial<AppConfiguration>>
  "app.stopAll": Entry<{}>
  "app.shutdown": Entry<{}, AppShutdownResult>
  "device.refresh": Entry<{ device: "powerSupply" | "powerMeter" | "spectrometer"; fail?: boolean }>
  "powerSupply.connect": Entry<{}>
  "powerSupply.disconnect": Entry<{}>
  "powerSupply.safeRampDown": Entry<{}>
  "powerSupply.setCurrent": Entry<{ currentA: number }>
  "powerSupply.setVoltage": Entry<{ voltageV: number }>
  "powerSupply.setOutput": Entry<{ enabled: boolean }>
  "powerSupply.read": Entry<{ value: "outputVoltage" | "outputCurrent" | "inputVoltage" | "temperature" }>
  "powerMeter.start": Entry<{}>
  "powerMeter.stop": Entry<{}>
  "powerMeter.setRelativeZero": Entry<{ enabled: boolean }>
  "powerMeter.background.learn": Entry<{ confirmedDark: boolean }>
  "powerMeter.background.verify": Entry<{ confirmedDark: boolean }>
  "powerMeter.background.cancel": Entry<{}>
  "powerMeter.profile.create": Entry<Profile>
  "powerMeter.profile.save": Entry<Profile>
  "powerMeter.profile.select": Entry<Profile>
  "powerMeter.profile.restore": Entry<Profile>
  "powerMeter.profile.delete": Entry<Profile>
  "spectrometer.start": Entry<{}>
  "spectrometer.stop": Entry<{}>
  "spectrometer.saveCsv": Entry<{ path?: string }>
  "manual.saveExcel": Entry<{}>
  "automatic.start": Entry<Partial<AppConfiguration>>
  "automatic.pause": Entry<{}>
  "automatic.resume": Entry<Resume>
  "automatic.retryPoint": Entry<Resume>
  "automatic.recoverPowerMeter": Entry<Resume>
  "automatic.recoverSpectrometer": Entry<Resume>
  "automatic.adjustSpectrum": Entry<Resume>
  "automatic.retryPersist": Entry<Resume>
  "automatic.end": Entry<{}>
  "automatic.nextDevice": Entry<{}>
  "automatic.retryExport": Entry<{ outputDir?: string }>
  "pd.refresh": Entry<{}>
  "pd.configure": Entry<Partial<NonNullable<BackendSnapshot["pd"]>["settings"]>>
  "pd.start": Entry<Partial<NonNullable<BackendSnapshot["pd"]>["settings"]>>
  "pd.stop": Entry<{}>
  "charts.reset": Entry<{}>
  "dataBrowser.list": Entry<{ source?: string; rootPath?: string; page?: number; pageSize?: number; refresh?: boolean; search?: string; sn?: string; station?: string; status?: string; sort?: string; sortBy?: string; sortOrder?: string; fromMs?: number; toMs?: number }, DataList>
  "dataBrowser.inspect": Entry<{ recordId?: string; path?: string; version?: string; includeSpectra?: boolean }, DataDetail>
  "dataBrowser.import": Entry<{ rootPath: string }, { imported: number; skipped: number; warnings: string[] }>
  "shippingReport.inspect": Entry<{ path: string }, ShippingWorkbookInspection>
  "shippingReport.batchInspect": Entry<{ rootPath: string }, ShippingReportBatchInspection>
  "shippingReport.preview": Entry<Report, ShippingReportPreviewResult>
  "shippingReport.generate": Entry<Report & { outputPath: string }, ShippingReportGenerationResult>
  "shippingReport.batchGenerateItem": Entry<Report & { outputDirectory: string }, ShippingReportBatchGenerationResult>
}
export type CommandName = keyof CommandMap
export type CommandParams<M extends CommandName> = CommandMap[M]["params"]
export type CommandResult<M extends CommandName> = CommandMap[M]["result"]
export type SnapshotCommand = { [M in CommandName]: CommandResult<M> extends BackendSnapshot ? M : never }[CommandName]
export type SnapshotRequest = <M extends SnapshotCommand>(method: M, params?: CommandParams<M>) => Promise<BackendSnapshot>
export type RunArgs = { [M in SnapshotCommand]: {} extends CommandParams<M>
  ? [method: M, params?: CommandParams<M>, sync?: boolean]
  : [method: M, params: CommandParams<M>, sync?: boolean]
}[SnapshotCommand]
export type RunCommand = (...args: RunArgs) => Promise<BackendSnapshot | void>
