import type { AppConfiguration } from "./backend"

export function powerStabilityTolerance(config: AppConfiguration) {
  if ((config.powerStabilityTolerancePreset ?? "standard") === "standard") {
    return { absoluteW: 0, relativePercent: 0.2, referenceS: 20, extraNoisePercent: 0 }
  }
  return {
    absoluteW: config.powerStabilityAbsoluteDriftW ?? 0,
    relativePercent: config.powerStabilityRelativeDriftPercent ?? 0,
    referenceS: config.powerStabilityReferenceS ?? 20,
    extraNoisePercent: config.powerStabilityExtraNoisePercent ?? 0,
  }
}

const responseBindingKeys = [
  "powerMeterDriverKind", "powerMeterSerialNumber", "softwareGain", "powerMeterWavelengthNm",
  "powerMeterProfileId", "powerMeterProfileVersion", "powerMeterIntervalMs",
  "powerStabilityQuantizationW", "powerStabilityInstrumentSettings", "powerSupplyKind",
  "powerStabilityResponseS", "powerStabilitySettleS", "powerStabilityCurrentToleranceA",
] as const

/** Draft edits must not display the saved configuration's confirmation as current. */
export function powerStabilityResponseConfirmed(
  config: AppConfiguration, saved: AppConfiguration | null | undefined, confirmed: boolean | undefined,
): boolean {
  return Boolean(confirmed && config.powerStabilityParametersConfirmed && saved
    && responseBindingKeys.every(key => config[key] === saved[key]))
}
