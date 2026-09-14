import type { AppConfiguration } from "./backend.ts"
import { powerMeterResourceValue } from "./power-meter-resource.ts"

export const powerMeterDerivedConfigurationKeys = [
  "powerMeterDriverKind",
  "powerMeterSerialNumber",
  "powerMeterWavelengthNm",
  "softwareGain",
  "powerMeterProfileId",
  "powerMeterProfileName",
  "powerMeterProfileVersion",
  "powerMeterProfileNotes",
  "powerMeterProfileWavelengthPending",
] as const satisfies ReadonlyArray<keyof AppConfiguration>

export function mergeAuthoritativePowerMeterConfiguration(
  current: AppConfiguration,
  authoritative: AppConfiguration,
): AppConfiguration {
  if (powerMeterResourceValue(current.powerMeterResource) !== powerMeterResourceValue(authoritative.powerMeterResource)) {
    return current
  }
  const next = { ...current }
  for (const key of powerMeterDerivedConfigurationKeys) {
    const value = authoritative[key]
    if (value !== undefined) (next as unknown as Record<string, unknown>)[key] = value
  }
  return next
}
