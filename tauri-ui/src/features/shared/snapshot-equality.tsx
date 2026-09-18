import { type BackendSnapshot, type DeviceSnapshot } from "@/lib/backend"


export function sameRows<T>(
  left: readonly T[] | undefined,
  right: readonly T[] | undefined,
  keys: readonly (keyof T)[],
): boolean {
  if (left === right) return true
  if (!left || !right || left.length !== right.length) return false
  return left.every((row, index) => {
    const other = right[index]
    return keys.every((key) => row[key] === other[key])
  })
}

export function sameNumberArray(left: readonly number[] | undefined, right: readonly number[] | undefined): boolean {
  return left === right || Boolean(
    left
    && right
    && left.length === right.length
    && left.every((value, index) => value === right[index]),
  )
}

export function sameStringArray(left: readonly string[] | undefined, right: readonly string[] | undefined): boolean {
  return left === right || Boolean(
    left
    && right
    && left.length === right.length
    && left.every((value, index) => value === right[index]),
  )
}

export function sameCh341State(left: DeviceSnapshot["ch341"], right: DeviceSnapshot["ch341"]): boolean {
  if (left === right) return true
  if (!left || !right) return false
  const leftConnected = left.connectedPort
  const rightConnected = right.connectedPort
  const connectedPortEqual = leftConnected === rightConnected || Boolean(
    leftConnected
    && rightConnected
    && leftConnected.locationPath === rightConnected.locationPath
    && leftConnected.name === rightConnected.name
    && leftConnected.deviceIndex === rightConnected.deviceIndex,
  )
  return left.discoveryState === right.discoveryState
    && left.error === right.error
    && connectedPortEqual
    && sameRows(left.ports, right.ports, ["locationPath", "name", "online", "deviceIndex"])
}

export function sameSpectrumWindow(
  left: DeviceSnapshot["spectrumWindow"],
  right: DeviceSnapshot["spectrumWindow"],
): boolean {
  return left === right || Boolean(
    left
    && right
    && left.centerNm === right.centerNm
    && left.halfRangeNm === right.halfRangeNm,
  )
}

export function samePowerMeterResourceProfiles(
  left: DeviceSnapshot["resourceProfiles"],
  right: DeviceSnapshot["resourceProfiles"],
): boolean {
  if (left === right) return true
  if (!left || !right || left.length !== right.length) return false
  return left.every((profile, index) => {
    const other = right[index]
    return profile.resource === other?.resource
      && profile.driverKind === other.driverKind
      && profile.serialNumber === other.serialNumber
      && profile.softwareGain === other.softwareGain
      && JSON.stringify(profile.coefficientLibrary ?? null) === JSON.stringify(other.coefficientLibrary ?? null)
  })
}

export function sameDeviceSnapshot(left: DeviceSnapshot | undefined, right: DeviceSnapshot | undefined): boolean {
  if (left === right) return true
  if (!left || !right) return false
  return left.state === right.state
    && left.label === right.label
    && left.detail === right.detail
    && left.connected === right.connected
    && left.running === right.running
    && left.detecting === right.detecting
    && left.ready === right.ready
    && sameStringArray(left.resources, right.resources)
    && samePowerMeterResourceProfiles(left.resourceProfiles, right.resourceProfiles)
    && JSON.stringify(left.coefficientLibrary ?? null) === JSON.stringify(right.coefficientLibrary ?? null)
    && sameCh341State(left.ch341, right.ch341)
    && left.outputEnabled === right.outputEnabled
    && left.activeCurrentA === right.activeCurrentA
    && left.inputVoltageV === right.inputVoltageV
    && left.outputVoltageV === right.outputVoltageV
    && left.outputCurrentA === right.outputCurrentA
    && left.temperatureC === right.temperatureC
    && left.powerW === right.powerW
    && left.stable === right.stable
    && left.stableElapsedS === right.stableElapsedS
    && left.sampledAt === right.sampledAt
    && left.ageMs === right.ageMs
    && left.quality === right.quality
    && left.peakWavelengthNm === right.peakWavelengthNm
    && left.centroidNm === right.centroidNm
    && left.fwhmNm === right.fwhmNm
    && left.smsrDb === right.smsrDb
    && left.pib === right.pib
    && left.pibPercent === right.pibPercent
    && left.saturated === right.saturated
    && sameSpectrumWindow(left.spectrumWindow, right.spectrumWindow)
}

export function sameAutomaticReadiness(
  left: BackendSnapshot["automaticTest"]["readiness"],
  right: BackendSnapshot["automaticTest"]["readiness"],
): boolean {
  if (left === right) return true
  if (!left || !right || left.ready !== right.ready || left.blockers.length !== right.blockers.length) return false
  return left.blockers.every((blocker, index) => (
    blocker.code === right.blockers[index]?.code
    && blocker.message === right.blockers[index]?.message
  ))
}

export function sameMeasurements(left: BackendSnapshot["measurements"], right: BackendSnapshot["measurements"]): boolean {
  if (left === right) return true
  if (!left || !right) return false
  return left.power === right.power
    && left.stable === right.stable
    && left.agingPower === right.agingPower
    && left.spectrum === right.spectrum
    && left.spectrumPeaks === right.spectrumPeaks
}

export function sameAutomaticStability(
  left: BackendSnapshot["automaticTest"]["stability"],
  right: BackendSnapshot["automaticTest"]["stability"],
): boolean {
  if (left === right) return true
  if (!left || !right) return false
  return left.elapsedS === right.elapsedS
    && left.requiredS === right.requiredS
    && left.holdElapsedS === right.holdElapsedS
    && left.holdRequiredS === right.holdRequiredS
    && left.powerStable === right.powerStable
    && left.spanW === right.spanW
    && left.spanLimitW === right.spanLimitW
    && left.percent === right.percent
    && left.limitPercent === right.limitPercent
    && left.quality === right.quality
    && left.hasReading === right.hasReading
    && left.wavelengthRequired === right.wavelengthRequired
    && left.wavelengthStable === right.wavelengthStable
}

export function sameAutomaticTestState(
  left: BackendSnapshot["automaticTest"] | undefined,
  right: BackendSnapshot["automaticTest"] | undefined,
): boolean {
  if (left === right) return true
  if (!left || !right) return false
  return left.state === right.state
    && left.detail === right.detail
    && left.elapsedS === right.elapsedS
    && sameAutomaticStability(left.stability, right.stability)
    && left.runContext?.sn === right.runContext?.sn
    && left.runContext?.station === right.runContext?.station
    && left.runContext?.testTemperatureC === right.runContext?.testTemperatureC
    && left.runContext?.selectedScriptId === right.runContext?.selectedScriptId
    && left.currentA === right.currentA
    && left.currentIndex === right.currentIndex
    && left.currentDwellS === right.currentDwellS
    && left.currentDwellElapsedS === right.currentDwellElapsedS
    && left.progress === right.progress
    && left.pauseReason === right.pauseReason
    && left.terminalOutcome === right.terminalOutcome
    && left.terminalReason === right.terminalReason
    && left.savedPointCount === right.savedPointCount
    && left.exportStatus === right.exportStatus
    && left.exportPath === right.exportPath
    && left.exportError === right.exportError
    && left.settingsError === right.settingsError
    && sameAutomaticReadiness(left.readiness, right.readiness)
    && left.canStart === right.canStart
    && left.exportedPointCount === right.exportedPointCount
    && left.journalPath === right.journalPath
    && left.canNextDevice === right.canNextDevice
    && left.canRetryExport === right.canRetryExport
    && left.pauseKind === right.pauseKind
    && left.pauseStartedAtUtc === right.pauseStartedAtUtc
    && left.pauseDeadlineAtUtc === right.pauseDeadlineAtUtc
    && sameStringArray(left.pauseActions, right.pauseActions)
    && left.canEnd === right.canEnd
    && sameNumberArray(left.currents, right.currents)
}

export function samePdSection(
  left: BackendSnapshot["pd"] | undefined,
  right: BackendSnapshot["pd"] | undefined,
): boolean {
  if (left === right) return true
  if (!left || !right) return false
  return left.state === right.state
    && left.status === right.status
    && left.devices === right.devices
    && left.channels === right.channels
    && left.ranges === right.ranges
    && left.settings === right.settings
    && left.currentValue === right.currentValue
    && left.voltage === right.voltage
    && left.mean === right.mean
    && left.standardDeviation === right.standardDeviation
    && left.rangeText === right.rangeText
    && left.sampleCount === right.sampleCount
    && left.points === right.points
}
