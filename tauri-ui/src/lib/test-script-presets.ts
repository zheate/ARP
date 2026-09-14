import type { AppConfiguration } from "./backend.ts"

export const scriptParameterKeys = [
  "selectedScriptId", "initialCurrentA", "targetCurrentA", "currentStepA",
  "currentPlanMode", "customCurrentPoints", "pointTimeoutS", "rampDownStepA",
  "rampDownIntervalS",
] as const

export type ScriptParameters = Pick<AppConfiguration, typeof scriptParameterKeys[number]>
export interface TestScriptPreset {
  id: string
  name: string
  parameters: ScriptParameters
}

export function captureScriptParameters(config: ScriptParameters): ScriptParameters {
  return Object.fromEntries(scriptParameterKeys.map((key) => [key,
    key === "customCurrentPoints" ? config.customCurrentPoints.map((point) => ({ ...point })) : config[key],
  ])) as ScriptParameters
}

export function scriptPresetChanged(config: AppConfiguration, preset: TestScriptPreset): boolean {
  return JSON.stringify(captureScriptParameters(config)) !== JSON.stringify(captureScriptParameters(preset.parameters))
}

export function scriptPresetCompatible(config: AppConfiguration, preset: TestScriptPreset): boolean {
  const p = preset.parameters
  if (p.selectedScriptId === "aging" && config.powerSupplyKind !== "ch341") return false
  return config.powerSupplyKind !== "ch341" || (
    [p.initialCurrentA, p.targetCurrentA, p.currentStepA, p.rampDownStepA, ...p.customCurrentPoints.map((point) => point.currentA)]
      .every((value) => value <= 20)
  )
}

export function applyScriptPreset(config: AppConfiguration, preset: TestScriptPreset): AppConfiguration {
  return { ...config, ...captureScriptParameters(preset.parameters), selectedScriptPresetId: preset.id }
}
