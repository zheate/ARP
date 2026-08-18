const ophirResourcePattern = /OPHIR::USB::[^|\s]+/i
const visaSerialResourcePattern = /ASRL\d+::INSTR/i

export function powerMeterResourceValue(label: string): string {
  const value = label.trim()
  return value.match(ophirResourcePattern)?.[0].toUpperCase()
    ?? value.match(visaSerialResourcePattern)?.[0].toUpperCase()
    ?? value
}
