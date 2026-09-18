import { applyTestScript, BUILTIN_TEST_SCRIPT_OPTIONS, configurationPayload, configurationsEqual, emptyConfig, type UpdateConfig } from "@/features/configuration/model"
import type { AppConfiguration, BackendSnapshot } from "@/lib/backend"
import { ConfigurationDraft } from "@/lib/configuration-draft"
import { mergeAuthoritativePowerMeterConfiguration } from "@/lib/power-meter-configuration"
import { applyScriptPreset, captureScriptParameters, scriptPresetCompatible } from "@/lib/test-script-presets"
import { useCallback, useEffect, useRef, useSyncExternalStore } from "react"

type Configure = (method: "app.configure", params: Record<string, unknown>) => Promise<BackendSnapshot>

export function useConfigurationEditor(snapshot: BackendSnapshot | null, operational: boolean, command: Configure) {
  const storeRef = useRef<ConfigurationDraft<AppConfiguration> | null>(null)
  if (!storeRef.current) storeRef.current = new ConfigurationDraft(emptyConfig, configurationsEqual)
  const store = storeRef.current
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot)
  const flight = useRef<Promise<void> | null>(null)

  useEffect(() => {
    if (snapshot?.configuration) store.hydrate(snapshot.configuration)
  }, [snapshot?.configuration, state.dirty, store])

  const saveConfiguration = useCallback((): Promise<void> => {
    if (flight.current) return flight.current
    if (!operational) return Promise.resolve()
    const { revision, value: current } = store.getSnapshot()
    const configuration = current.selectedScriptPresetId ? {
      ...current,
      testScriptPresets: (current.testScriptPresets ?? []).map((preset) => preset.id === current.selectedScriptPresetId
        ? { ...preset, parameters: captureScriptParameters(current) } : preset),
    } : current
    const pending = command("app.configure", configurationPayload(configuration)).then((saved) => {
      store.acknowledge(revision, saved.configuration ?? configuration)
    }).finally(() => { flight.current = null })
    flight.current = pending
    return pending
  }, [command, operational, store])

  const createTestScript = useCallback(async (name: string) => {
    if (!operational) throw new Error("当前无法保存脚本，请检查后端连接和软件授权")
    await flight.current
    const { value: current, revision } = store.getSnapshot()
    const presets = current.testScriptPresets ?? []
    const normalizedName = name.trim()
    if (!normalizedName || normalizedName.length > 60) throw new Error("脚本名称不能为空或超过 60 个字符")
    if ([...presets, ...(snapshot?.testScripts ?? BUILTIN_TEST_SCRIPT_OPTIONS)].some((item) => item.name.toLocaleLowerCase() === normalizedName.toLocaleLowerCase())) {
      throw new Error("已有同名脚本，请换一个名称")
    }
    const preset = { id: `preset-${crypto.randomUUID()}`, name: normalizedName, parameters: captureScriptParameters(current) }
    const next = { ...current, testScriptPresets: [...presets, preset], selectedScriptPresetId: preset.id }
    const saved = await command("app.configure", configurationPayload(next))
    store.acknowledge(revision, saved.configuration ?? next)
  }, [command, operational, snapshot?.testScripts, store])

  const update: UpdateConfig = useCallback((key, value) => {
    store.edit({ ...store.getSnapshot().value, [key]: value })
  }, [store])

  const selectTestScript = useCallback((scriptId: string) => {
    const current = store.getSnapshot().value
    const preset = current.testScriptPresets?.find((item) => item.id === scriptId)
    const script = (snapshot?.testScripts ?? BUILTIN_TEST_SCRIPT_OPTIONS).find((item) => item.id === scriptId)
    if (preset ? !scriptPresetCompatible(current, preset) : !script) return
    store.edit(preset ? applyScriptPreset(current, preset) : { ...applyTestScript(current, script!), selectedScriptPresetId: "" })
  }, [snapshot?.testScripts, store])

  const acceptCommand = useCallback((method: string, result: BackendSnapshot) => {
    if (method.startsWith("powerMeter.profile.") && result.configuration) {
      store.mergeAuthoritative((current) => mergeAuthoritativePowerMeterConfiguration(current, result.configuration!))
    }
    if (method === "automatic.start") store.accept(store.getSnapshot().value)
  }, [store])
  const current = useCallback(() => store.getSnapshot(), [store])
  const waitForSave = useCallback(async () => { await flight.current }, [])
  const accept = useCallback((patch: Partial<AppConfiguration>) => store.accept({ ...store.getSnapshot().value, ...patch }), [store])
  return { config: state.value, dirty: state.dirty, current, waitForSave, accept, acceptCommand, saveConfiguration, createTestScript, update, selectTestScript }
}
