import { sameRows } from "@/features/shared/snapshot-equality"
import { type AppConfiguration, type TestScript } from "@/lib/backend"
import { powerMeterDerivedConfigurationKeys } from "@/lib/power-meter-configuration"

export const emptyConfig: AppConfiguration = {
  sn: "",
  productModel: "",
  batch: "",
  station: "",
  testTemperatureC: "",
  outputDir: "",
  powerSupplyKind: "ch341",
  ch341SelectedLocationPath: "",
  ch341PortBindings: [],
  ruResource: "",
  ruBaudRate: 9600,
  ruAddress: 1,
  ruVoltageV: 12,
  tdkResource: "",
  setCurrentA: 0,
  tdkVoltageV: 0,
  powerMeterResource: "",
  powerMeterSerialNumber: "",
  powerMeterDriverKind: "",
  powerMeterWavelengthNm: 976,
  softwareGain: 1,
  powerMeterProfileId: "",
  powerMeterProfileName: "未绑定方案",
  powerMeterProfileVersion: 0,
  powerMeterProfileNotes: "",
  powerMeterProfileWavelengthPending: false,
  powerMeterIntervalMs: 100,
  powerSmoothing: true,
  spectrometerResource: "",
  spectrometerSerialNumber: "",
  integrationTimeUs: 1000,
  autoIntegration: false,
  spectrometerIntervalMs: 100,
  spectrumAverageFrames: 5,
  spectrumSmoothing: true,
  pibCenterWavelengthNm: 976,
  pibHalfRangeNm: 1.5,
  centerWavelengthSpecNm: 976,
  centerWavelengthToleranceNm: 1.5,
  fwhmMaximumNm: 0.6,
  pibMinimumPercent: 95,
  stableWindowS: 3,
  stabilityLimitPercent: 0.2,
  powerStabilityAbsoluteFloorW: 0.2,
  powerStabilityMode: "shadow",
  powerStabilityTolerancePreset: "standard",
  powerStabilityQuantizationW: 0,
  powerStabilityInstrumentSettings: "",
  powerStabilityAbsoluteDriftW: 0,
  powerStabilityRelativeDriftPercent: 0,
  powerStabilityReferenceS: 20,
  powerStabilityExtraNoisePercent: 0,
  powerStabilityResponseS: 0,
  powerStabilitySettleS: 0,
  powerStabilityCurrentToleranceA: 0,
  powerStabilityParametersConfirmed: false,
  powerStabilityResponseConfirmationRevision: 0,
  initialCurrentA: 1,
  targetCurrentA: 20,
  currentStepA: 3,
  selectedScriptId: "standard",
  currentPlanMode: "range",
  customCurrentPoints: [
    { currentA: 3, dwellS: 300 },
    { currentA: 5, dwellS: 300 },
  ],
  pointTimeoutS: 120,
  rampDownStepA: 5,
  rampDownIntervalS: 1.1,
  pauseRampDownTimeoutS: 30,
}

export const configurationKeys = Object.keys(emptyConfig) as Array<keyof AppConfiguration>

export function configurationPayload(config: AppConfiguration): Record<string, unknown> {
  const payload = { ...config } as Record<string, unknown>
  for (const key of powerMeterDerivedConfigurationKeys) delete payload[key]
  return payload
}

export function configurationsEqual(left: AppConfiguration, right: AppConfiguration): boolean {
  if ((left.selectedScriptPresetId ?? "") !== (right.selectedScriptPresetId ?? "")) return false
  if (JSON.stringify(left.testScriptPresets ?? []) !== JSON.stringify(right.testScriptPresets ?? [])) return false
  if (!sameRows(left.customCurrentPoints, right.customCurrentPoints, ["currentA", "dwellS"])) return false
  if (!sameRows(left.ch341PortBindings, right.ch341PortBindings, ["locationPath", "name"])) return false
  return configurationKeys.every((key) => (
    key === "customCurrentPoints" || key === "ch341PortBindings" || left[key] === right[key]
  ))
}

export const BUILTIN_TEST_SCRIPT_OPTIONS: TestScript[] = [
  { id: "standard", name: "标准测试", builtin: true, currentPlanMode: "range" },
  { id: "hold-points", name: "增加停留点", builtin: true, currentPlanMode: "custom" },
  { id: "aging", name: "AU 老化测试", builtin: true, currentPlanMode: "custom", powerSupplyKinds: ["ch341"] },
]

export function applyTestScript(current: AppConfiguration, script: TestScript): AppConfiguration {
  if (script.id === "aging") {
    return {
      ...current,
      selectedScriptId: script.id,
      initialCurrentA: 2,
      targetCurrentA: 20,
      currentStepA: 2,
      currentPlanMode: "custom",
      customCurrentPoints: [{ currentA: 20, dwellS: 2 * 60 * 60 }],
    }
  }
  if (script.currentPlanMode === "custom") {
    return {
      ...current,
      selectedScriptId: script.id,
      currentPlanMode: "custom",
      customCurrentPoints: current.customCurrentPoints.length
        ? current.customCurrentPoints
        : [{ currentA: current.initialCurrentA, dwellS: 300 }],
    }
  }
  return { ...current, selectedScriptId: script.id, currentPlanMode: "range" }
}

export type UpdateConfig = <K extends keyof AppConfiguration>(key: K, value: AppConfiguration[K]) => void
