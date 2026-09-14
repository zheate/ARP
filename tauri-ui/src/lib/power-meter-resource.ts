import type { DeviceSnapshot, PowerMeterCoefficientLibrary, PowerMeterResourceProfile } from "./backend.ts"

const ophirResourcePattern = /OPHIR::USB::[^|\s]+/i
const visaSerialResourcePattern = /ASRL\d+::INSTR/i

export function powerMeterResourceValue(label: string): string {
  const value = label.trim()
  return value.match(ophirResourcePattern)?.[0].toUpperCase()
    ?? value.match(visaSerialResourcePattern)?.[0].toUpperCase()
    ?? value
}

export function powerMeterProfileForResource(
  profiles: readonly PowerMeterResourceProfile[] | undefined,
  resource: string,
): PowerMeterResourceProfile | undefined {
  const normalizedResource = powerMeterResourceValue(resource)
  return profiles?.find((profile) => profile.resource === normalizedResource)
}

export function powerMeterCoefficientLibraryForResource(
  device: DeviceSnapshot | null | undefined,
  resource: string,
  configuredResource: string | undefined,
): PowerMeterCoefficientLibrary | null {
  const resourceProfile = powerMeterProfileForResource(device?.resourceProfiles, resource)
  if (resourceProfile?.coefficientLibrary) return resourceProfile.coefficientLibrary
  return powerMeterResourceValue(resource) === powerMeterResourceValue(configuredResource ?? "")
    ? device?.coefficientLibrary ?? null
    : null
}

export function powerMeterIdentityForResource(
  device: DeviceSnapshot | null | undefined,
  resource: string,
  configuredResource: string | undefined,
  configuredIdentity: { driverKind?: string; serialNumber?: string },
): { driverKind: string; serialNumber: string } {
  const library = powerMeterCoefficientLibraryForResource(device, resource, configuredResource)
  const resourceProfile = powerMeterProfileForResource(device?.resourceProfiles, resource)
  const isAppliedResource = powerMeterResourceValue(resource) === powerMeterResourceValue(configuredResource ?? "")
  return {
    driverKind: resourceProfile?.driverKind
      ?? library?.driverKind
      ?? (isAppliedResource ? configuredIdentity.driverKind : undefined)
      ?? "",
    serialNumber: resourceProfile?.serialNumber
      ?? library?.serialNumber
      ?? (isAppliedResource ? configuredIdentity.serialNumber : undefined)
      ?? "",
  }
}
