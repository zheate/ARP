import type { PowerMeterResourceProfile } from "./backend.ts"

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
