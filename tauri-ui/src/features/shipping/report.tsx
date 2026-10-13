import "@/components/shipping-report.css"
import { ControlRow } from "@/components/control-layout"
import { Field } from "@/components/field"
import { NativeSelect } from "@/components/native-select"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { NumberField } from "@/features/shared/controls"
import { sendBackendRequest, type ShippingReportBatchInspection, type ShippingReportBatchItem, type ShippingReportFieldDefinition, type ShippingReportLanguage, type ShippingReportPointSuggestion, type ShippingReportType, type ShippingWorkbookInspection, type SpectrumAxisMode } from "@/lib/backend"
import { openResultFile } from "@/lib/result-file"
import { suggestedSpectrumAxisMaximum } from "@/lib/shipping-report-axis"
import { shippingReportFieldLabel } from "@/lib/shipping-report-labels"
import { invoke } from "@tauri-apps/api/core"
import { open as openDialog } from "@tauri-apps/plugin-dialog"
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener"
import { FileText, FolderOpen, LoaderCircle, Plus, RefreshCw, Save, X } from "lucide-react"
import { memo, useCallback, useEffect, useRef, useState } from "react"

export const SHIPPING_PRODUCT_NAME_STORAGE_KEY = "power-test.shipping-report.product-name"

export const SHIPPING_REPORT_SPLIT_LAYOUT = "grid min-w-0 gap-6 xl:grid-cols-[minmax(380px,0.72fr)_minmax(560px,1.28fr)]"

export const DEFAULT_ENCIRCLED_ENERGY_NA = "0.17"

export type ShippingFieldState = Record<string, { include: boolean; value: string }>

export function ShippingReportFieldSelector({
  checked,
  definition,
  disabled = false,
  encircledEnergyNa,
  reportLanguage,
  onCheckedChange,
  onEncircledEnergyNaChange,
}: {
  checked: boolean
  definition: ShippingReportFieldDefinition
  disabled?: boolean
  encircledEnergyNa: string
  reportLanguage: ShippingReportLanguage
  onCheckedChange: (checked: boolean) => void
  onEncircledEnergyNaChange: (value: string) => void
}) {
  if (definition.key !== "na017Energy") {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input checked={checked} disabled={disabled} onChange={(event) => onCheckedChange(event.target.checked)} type="checkbox" />
        <span>{definition.label}</span>
      </label>
    )
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm">
      <label className="flex items-center gap-2 whitespace-nowrap">
        <input checked={checked} disabled={disabled} onChange={(event) => onCheckedChange(event.target.checked)} type="checkbox" />
        <span>NA</span>
      </label>
      <Input
        aria-label={reportLanguage === "zh" ? "包围能量 NA" : "Encircled Energy NA"}
        className="h-7 w-16 px-2 text-sm"
        disabled={disabled}
        inputMode="decimal"
        max="1"
        min="0"
        step="0.01"
        type="number"
        value={encircledEnergyNa}
        onChange={(event) => onEncircledEnergyNaChange(event.target.value)}
      />
      <span>{reportLanguage === "zh" ? "包围能量" : "Encircled Energy"}</span>
    </div>
  )
}

export function shippingSuggestedValue(point: ShippingReportPointSuggestion | undefined, measuredKey?: string | null): string {
  if (!point || !measuredKey) return ""
  const value = (point as unknown as Record<string, unknown>)[measuredKey]
  if (typeof value !== "number" || !Number.isFinite(value)) return "N/A"
  if (measuredKey === "currentA") return String(value)
  if (measuredKey === "powerW") return value.toFixed(1)
  if (measuredKey === "voltageV" || measuredKey === "efficiencyPercent") return value.toFixed(2)
  return value.toFixed(3).replace(/\.?0+$/, "")
}

export function suggestedSpectrumAxis(inspection: ShippingWorkbookInspection, currentA: number) {
  const point = inspection.points.find((item) => Math.abs(item.currentA - currentA) < 1e-9)
  const wavelengthRange = inspection.spectrumWavelengthRange
  const centerWavelength = point?.peakWavelengthNm
    ?? point?.centroidNm
    ?? (wavelengthRange ? (wavelengthRange.minimum + wavelengthRange.maximum) / 2 : 0)
  return {
    mode: "counts" as const,
    minimum: 0,
    maximum: suggestedSpectrumAxisMaximum(inspection.points, currentA),
    wavelengthMinimum: centerWavelength - 15,
    wavelengthMaximum: centerWavelength + 15,
  }
}

export function preferredSpectrumAxis(inspection: ShippingWorkbookInspection, currentA: number) {
  const suggested = suggestedSpectrumAxis(inspection, currentA)
  const saved = inspection.preferences.spectrum?.spectrumAxis
  if (!saved) return suggested
  if (saved.mode === "counts") {
    return {
      ...suggested,
      wavelengthMinimum: saved.wavelengthMinimum ?? suggested.wavelengthMinimum,
      wavelengthMaximum: saved.wavelengthMaximum ?? suggested.wavelengthMaximum,
    }
  }
  return {
    mode: saved.mode,
    minimum: saved.minimum,
    maximum: saved.maximum,
    wavelengthMinimum: saved.wavelengthMinimum ?? suggested.wavelengthMinimum,
    wavelengthMaximum: saved.wavelengthMaximum ?? suggested.wavelengthMaximum,
  }
}

export function shippingDefaultOutputDirectory(sourcePath: string): string {
  const separatorIndex = Math.max(sourcePath.lastIndexOf("/"), sourcePath.lastIndexOf("\\"))
  return separatorIndex >= 0 ? sourcePath.slice(0, separatorIndex) : ""
}

export function shippingOutputFilePath(outputDirectory: string, sn: string): string {
  const trimmedDirectory = outputDirectory.trim()
  if (!trimmedDirectory) return ""
  const directory = trimmedDirectory.replace(/[\\/]+$/, "") || trimmedDirectory
  const separator = directory.includes("\\") ? "\\" : "/"
  const safeSn = (sn.trim() || "出货报告").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
  return `${directory}${directory.endsWith("/") || directory.endsWith("\\") ? "" : separator}${safeSn}_出货报告.pdf`
}

export function getStoredShippingProductName(): string {
  try {
    return window.localStorage.getItem(SHIPPING_PRODUCT_NAME_STORAGE_KEY) ?? ""
  } catch {
    return ""
  }
}

export function storeShippingProductName(value: string): void {
  try {
    if (value.trim()) window.localStorage.setItem(SHIPPING_PRODUCT_NAME_STORAGE_KEY, value)
    else window.localStorage.removeItem(SHIPPING_PRODUCT_NAME_STORAGE_KEY)
  } catch {
    // Local storage may be unavailable in restricted WebView environments.
  }
}

export function ShippingReportWorkflow({ disabled }: { disabled: boolean }) {
  const [inspection, setInspection] = useState<ShippingWorkbookInspection | null>(null)
  const [reportType, setReportType] = useState<ShippingReportType>("pole")
  const [reportLanguage, setReportLanguage] = useState<ShippingReportLanguage>("en")
  const [productName, setProductName] = useState(getStoredShippingProductName)
  const [sn, setSn] = useState("")
  const [operatingCurrentA, setOperatingCurrentA] = useState(0)
  const [fields, setFields] = useState<ShippingFieldState>({})
  const [customFields, setCustomFields] = useState<ShippingReportFieldDefinition[]>([])
  const [encircledEnergyNa, setEncircledEnergyNa] = useState(DEFAULT_ENCIRCLED_ENERGY_NA)
  const [axisMode, setAxisMode] = useState<SpectrumAxisMode>("relative_db")
  const [axisMinimum, setAxisMinimum] = useState(-80)
  const [axisMaximum, setAxisMaximum] = useState(0)
  const axisMaximumEditedRef = useRef(false)
  const [wavelengthMinimum, setWavelengthMinimum] = useState(0)
  const [wavelengthMaximum, setWavelengthMaximum] = useState(2000)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [lastOutputPath, setLastOutputPath] = useState("")
  const [openingResult, setOpeningResult] = useState(false)
  const [resultOpenNotice, setResultOpenNotice] = useState("")
  const [previewPages, setPreviewPages] = useState<string[]>([])
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState("")
  const [sourcePath, setSourcePath] = useState("")
  const [outputDirectory, setOutputDirectory] = useState("")
  const previewRequestRef = useRef(0)

  const initializeFields = useCallback((nextInspection: ShippingWorkbookInspection, nextType: ShippingReportType, currentA: number) => {
    const point = nextInspection.points.find((item) => Math.abs(item.currentA - currentA) < 1e-9)
    const preference = nextInspection.preferences[nextType] ?? { selectedFields: [], manualValues: {}, customFields: [] }
    const selected = new Set(preference.selectedFields)
    const nextCustomFields = preference.customFields ?? []
    const nextDefinitions = [...nextInspection.fieldDefinitions[nextType], ...nextCustomFields]
    const nextFields = Object.fromEntries(nextDefinitions.map((definition) => [
      definition.key,
      {
        include: selected.has(definition.key),
        value: definition.measuredKey
          ? shippingSuggestedValue(point, definition.measuredKey)
          : preference.manualValues[definition.key] ?? "",
      },
    ]))
    setFields(nextFields)
    setCustomFields(nextCustomFields)
    setReportLanguage(preference.reportLanguage ?? "en")
    if (nextType === "spectrum") {
      setEncircledEnergyNa(preference.encircledEnergyNa ?? DEFAULT_ENCIRCLED_ENERGY_NA)
      const axis = preferredSpectrumAxis(nextInspection, currentA)
      setAxisMode(axis.mode)
      setAxisMinimum(axis.minimum)
      setAxisMaximum(axis.maximum)
      axisMaximumEditedRef.current = false
      setWavelengthMinimum(Number(axis.wavelengthMinimum.toFixed(2)))
      setWavelengthMaximum(Number(axis.wavelengthMaximum.toFixed(2)))
    }
  }, [])

  const chooseSourcePath = async () => {
    if (disabled || busy) return
    try {
      const selected = await openDialog({
        multiple: false,
        directory: false,
        title: "选择出货报告测试数据",
        filters: [{ name: "Excel 测试文件", extensions: ["xlsx"] }],
      })
      if (typeof selected !== "string") return
      setSourcePath(selected)
      setOutputDirectory(shippingDefaultOutputDirectory(selected))
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const chooseOutputDirectory = async () => {
    if (disabled || busy) return
    try {
      const selected = await openDialog({
        multiple: false,
        directory: true,
        title: "选择出货报告保存文件夹",
      })
      if (typeof selected === "string") {
        setOutputDirectory(selected)
        setError("")
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const configureReport = async () => {
    if (disabled || busy) return
    const nextSourcePath = sourcePath.trim()
    const nextOutputDirectory = outputDirectory.trim()
    if (!nextSourcePath) {
      setError("请选择 Excel 测试数据路径")
      return
    }
    if (!nextOutputDirectory) {
      setError("请选择本地 PDF 保存文件夹")
      return
    }
    setError("")
    setPreviewPages([])
    setPreviewError("")
    try {
      setBusy(true)
      const result = await sendBackendRequest("shippingReport.inspect", { path: nextSourcePath })
      if (result.compatibility.kind === "rejected") throw new Error(result.compatibility.message)
      const nextType: ShippingReportType = result.allowedReportTypes.includes("spectrum") ? "spectrum" : "pole"
      const currentA = result.currents[result.currents.length - 1] ?? 0
      setInspection(result)
      setReportType(nextType)
      setProductName((current) => current.trim() || result.productName)
      setSn(result.sn)
      setOperatingCurrentA(currentA)
      initializeFields(result, nextType, currentA)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const changeReportType = (nextType: ShippingReportType) => {
    if (!inspection) return
    setReportType(nextType)
    initializeFields(inspection, nextType, operatingCurrentA)
    setError("")
  }

  const changeProductName = (value: string) => {
    setProductName(value)
    storeShippingProductName(value)
  }

  const changeOperatingCurrent = (currentA: number) => {
    if (!inspection) return
    setOperatingCurrentA(currentA)
    if (reportType === "spectrum" && axisMode === "counts" && !axisMaximumEditedRef.current) {
      setAxisMaximum(suggestedSpectrumAxisMaximum(inspection.points, currentA))
    }
    const point = inspection.points.find((item) => Math.abs(item.currentA - currentA) < 1e-9)
    const measuredKeys = new Map(inspection.fieldDefinitions[reportType].filter((item) => item.measuredKey).map((item) => [item.key, item.measuredKey]))
    setFields((previous) => Object.fromEntries(Object.entries(previous).map(([key, state]) => [
      key,
      measuredKeys.has(key) ? { ...state, value: shippingSuggestedValue(point, measuredKeys.get(key)) } : state,
    ])))
  }

  const addCustomField = () => {
    const key = `custom-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const definition: ShippingReportFieldDefinition = {
      key,
      label: "新参数",
      unit: "",
      side: reportType === "spectrum" ? "left" : "single",
    }
    setCustomFields((previous) => [...previous, definition])
    setFields((previous) => ({ ...previous, [key]: { include: true, value: "" } }))
  }

  const updateCustomField = (key: string, patch: Partial<Pick<ShippingReportFieldDefinition, "label" | "unit">>) => {
    setCustomFields((previous) => previous.map((field) => field.key === key ? { ...field, ...patch } : field))
  }

  const removeCustomField = (key: string) => {
    setCustomFields((previous) => previous.filter((field) => field.key !== key))
    setFields((previous) => {
      const next = { ...previous }
      delete next[key]
      return next
    })
  }

  const generate = async () => {
    if (!inspection || busy) return
    setError("")
    try {
      const targetPath = shippingOutputFilePath(outputDirectory, sn)
      if (!targetPath) throw new Error("请选择本地 PDF 保存文件夹")
      setBusy(true)
      const result = await sendBackendRequest("shippingReport.generate", {
        sourcePath: inspection.sourcePath,
        outputPath: targetPath,
        reportType,
        reportLanguage,
        productName,
        sn,
        operatingCurrentA,
        fields,
        customFields,
        encircledEnergyNa,
        legacyCompletionConfirmed: true,
        ...(reportType === "spectrum" ? { spectrumAxis: { mode: axisMode, minimum: axisMinimum, maximum: axisMaximum, wavelengthMinimum, wavelengthMaximum } } : {}),
      })
      setLastOutputPath(result.outputPath)
      setResultOpenNotice("")
      setInspection(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const openGeneratedReport = async (revealOnly = false) => {
    if (!lastOutputPath || openingResult) return
    setOpeningResult(true)
    setError("")
    setResultOpenNotice("")
    try {
      if (revealOnly) {
        await revealItemInDir(lastOutputPath)
      } else {
        const outcome = await openResultFile(lastOutputPath, openPath, revealItemInDir)
        if (outcome.status === "revealed") {
          setResultOpenNotice(`无法直接打开 PDF，已在文件夹中定位。${outcome.openError}`)
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setOpeningResult(false)
    }
  }

  useEffect(() => {
    if (!inspection) return
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) setInspection(null)
    }
    window.addEventListener("keydown", handleKey)
    return () => window.removeEventListener("keydown", handleKey)
  }, [inspection, busy])

  useEffect(() => {
    if (!inspection || busy) {
      setPreviewBusy(false)
      return
    }
    let cancelled = false
    const requestId = ++previewRequestRef.current
    setPreviewBusy(true)
    const timer = window.setTimeout(() => {
      if (cancelled) return
      void sendBackendRequest("shippingReport.preview", {
        sourcePath: inspection.sourcePath,
        reportType,
        reportLanguage,
        productName,
        sn,
        operatingCurrentA,
        fields,
        customFields,
        encircledEnergyNa,
        legacyCompletionConfirmed: true,
        ...(reportType === "spectrum" ? { spectrumAxis: { mode: axisMode, minimum: axisMinimum, maximum: axisMaximum, wavelengthMinimum, wavelengthMaximum } } : {}),
      })
        .then((result) => {
          if (cancelled || requestId !== previewRequestRef.current) return
          setPreviewPages(result.pages)
          setPreviewError("")
        })
        .catch((reason: unknown) => {
          if (cancelled || requestId !== previewRequestRef.current) return
          setPreviewError(reason instanceof Error ? reason.message : String(reason))
        })
        .finally(() => {
          if (!cancelled && requestId === previewRequestRef.current) setPreviewBusy(false)
        })
    }, 500)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [axisMaximum, axisMinimum, axisMode, busy, customFields, encircledEnergyNa, fields, inspection, operatingCurrentA, productName, reportLanguage, reportType, sn, wavelengthMaximum, wavelengthMinimum])

  const definitions: ShippingReportFieldDefinition[] = inspection ? [...inspection.fieldDefinitions[reportType].map((field) => ({ ...field, label: shippingReportFieldLabel(field, reportLanguage, reportType) })), ...customFields] : []
  const outputPath = shippingOutputFilePath(outputDirectory, sn)
  return (
    <>
      <div className="rounded-lg border border-border/70 bg-card/40 p-4">
        <div className="mb-3 flex items-center gap-2">
          <Badge variant="outline" className="text-xs bg-primary/10 text-primary border-primary/30">
            步骤 1 · 选择数据源与输出目录
          </Badge>
        </div>
        <div className="grid gap-4">
          <Field label="Excel 测试数据路径">
            <ControlRow actionWidth="path">
              <Input aria-label="Excel 测试数据路径" disabled={disabled || busy} onChange={(event) => { setSourcePath(event.target.value); setError("") }} placeholder="选择自动或手动测试生成的 .xlsx 文件" value={sourcePath} />
              <Button className="shrink-0" disabled={disabled || busy} onClick={() => void chooseSourcePath()} size="sm" type="button" variant="outline"><FolderOpen className="size-3.5" />浏览</Button>
            </ControlRow>
          </Field>
          <Field label="本地 PDF 保存文件夹">
            <ControlRow actionWidth="path">
              <Input aria-label="本地 PDF 保存文件夹" disabled={disabled || busy} onChange={(event) => { setOutputDirectory(event.target.value); setError("") }} placeholder="选择 PDF 保存文件夹，文件名将自动生成" value={outputDirectory} />
              <Button className="shrink-0" disabled={disabled || busy} onClick={() => void chooseOutputDirectory()} size="sm" type="button" variant="outline"><FolderOpen className="size-3.5" />选择文件夹</Button>
            </ControlRow>
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
          <div className="min-w-0 text-xs text-muted-foreground">
            {error && <p className="font-medium text-destructive">{error}</p>}
            {resultOpenNotice && <p role="status">{resultOpenNotice}</p>}
            {lastOutputPath && (
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="max-w-[520px] truncate" title={lastOutputPath}>已生成：{lastOutputPath}</span>
                <Button disabled={openingResult} onClick={() => void openGeneratedReport()} size="sm" variant="outline">
                  <FileText className="size-3.5" />{openingResult ? "正在打开…" : "打开 PDF"}
                </Button>
                <Button disabled={openingResult} onClick={() => void openGeneratedReport(true)} size="sm" variant="outline">
                  <FolderOpen className="size-3.5" />打开文件夹
                </Button>
              </div>
            )}
            {!error && !lastOutputPath && <p>填写数据路径与输出目录后，点击“读取数据”开始配置报告。</p>}
          </div>
          <Button disabled={disabled || busy} onClick={() => void configureReport()}><FileText className="size-4" />{busy && !inspection ? "正在读取…" : "读取并配置报告"}</Button>
        </div>
      </div>

      {inspection && (
        <section aria-labelledby="shipping-report-title" className="mt-5 rounded-lg border border-border/70 bg-card/40 p-4">
          <div className="mb-4 flex items-start justify-between gap-4">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-xs bg-primary/10 text-primary border-primary/30">
                步骤 2 · 报告配置与参数勾选
              </Badge>
              {inspection.testMode === "manual" && inspection.singlePoint && (
                <Badge variant="outline" className="text-xs border-emerald-500/30 bg-emerald-500/10 text-emerald-600">
                  手动单点 Excel · {Number.isInteger(inspection.currents[0]) ? inspection.currents[0].toFixed(0) : String(inspection.currents[0])} A
                </Badge>
              )}
              <span className="max-w-md truncate text-xs text-muted-foreground">{inspection.sourcePath}</span>
            </div>
            <button aria-label="收起报告配置" className="rounded-md p-1 text-muted-foreground hover:bg-muted" disabled={busy} onClick={() => setInspection(null)} type="button">
              <X className="size-4" />
            </button>
          </div>

          <div className={SHIPPING_REPORT_SPLIT_LAYOUT}>
            <div className="min-w-0">
              <div className="grid grid-cols-1 gap-4 2xl:grid-cols-3">
                <Field label="报告类型"><NativeSelect value={reportType} onChange={(value) => changeReportType(value as ShippingReportType)}>{inspection.allowedReportTypes.includes("spectrum") && <option value="spectrum">含光谱测试报告</option>}<option value="pole">Pole 无光谱测试报告</option></NativeSelect></Field>
                <Field label="报告语言"><NativeSelect value={reportLanguage} onChange={(value) => setReportLanguage(value as ShippingReportLanguage)}><option value="en">English</option><option value="zh">中文</option></NativeSelect></Field>
                <Field label="工作电流点"><NativeSelect value={String(operatingCurrentA)} onChange={(value) => changeOperatingCurrent(Number(value))}>{inspection.currents.map((current) => <option key={current} value={String(current)}>{Number.isInteger(current) ? current.toFixed(0) : String(current)} A</option>)}</NativeSelect></Field>
                <Field className="2xl:col-span-2" label="产品名称"><Input value={productName} onChange={(event) => changeProductName(event.target.value)} /></Field>
                <Field label="SN"><Input value={sn} onChange={(event) => setSn(event.target.value)} /></Field>
              </div>

              <Separator className="my-5" />

              <div>
                <div className="mb-3 flex items-end justify-between gap-4">
                  <div>
                    <h3 className="text-sm font-semibold">报告判定参数</h3>
                    <p className="mt-0.5 text-xs text-muted-foreground">勾选需要呈现在出货 PDF 中的参数</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <span className="text-xs text-muted-foreground">已选 {Object.values(fields).filter((item) => item.include).length} 项</span>
                    <Button disabled={busy} onClick={addCustomField} size="sm" type="button" variant="outline"><Plus className="size-3.5" />增加参数</Button>
                  </div>
                </div>
                <div className="pr-1">
                  <div className="shipping-report-parameters">
                    {definitions.filter((definition) => !customFields.some((field) => field.key === definition.key)).map((definition) => {
                      const state = fields[definition.key] ?? { include: false, value: "" }
                      return (
                        <div className="shipping-report-parameter-row" key={definition.key}>
                          <ShippingReportFieldSelector
                            checked={state.include}
                            definition={definition}
                            encircledEnergyNa={encircledEnergyNa} reportLanguage={reportLanguage}
                            onCheckedChange={(include) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, include } }))}
                            onEncircledEnergyNaChange={setEncircledEnergyNa}
                          />
                          <Input aria-label={`${definition.label}数值`} className="h-8" value={state.value} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, value: event.target.value } }))} />
                          <span className="text-xs text-muted-foreground">{definition.unit || "-"}</span>
                        </div>
                      )
                    })}
                    {customFields.map((definition) => {
                      const state = fields[definition.key] ?? { include: true, value: "" }
                      return (
                        <div className="shipping-report-custom-row grid items-center gap-2 rounded-md border border-dashed border-border bg-muted/20 p-2" key={definition.key}>
                          <input aria-label={`选择${definition.label}`} checked={state.include} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, include: event.target.checked } }))} type="checkbox" />
                          <Input aria-label="自定义参数名称" className="h-8" placeholder="参数名称" value={definition.label} onChange={(event) => updateCustomField(definition.key, { label: event.target.value })} />
                          <Input aria-label={`${definition.label}数值`} className="h-8" placeholder="参数值" value={state.value} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, value: event.target.value } }))} />
                          <Input aria-label={`${definition.label}单位`} className="h-8" placeholder="单位" value={definition.unit} onChange={(event) => updateCustomField(definition.key, { unit: event.target.value })} />
                          <button aria-label={`删除${definition.label}`} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" onClick={() => removeCustomField(definition.key)} type="button"><X className="size-4" /></button>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </div>

              {reportType === "spectrum" && (
                <>
                  <Separator className="my-5" />
                  <div>
                    <h3 className="text-sm font-semibold">光谱坐标轴配置</h3>
                    <div className="mt-3 grid grid-cols-3 gap-4">
                      <Field label="单位"><NativeSelect disabled value={axisMode} onChange={() => undefined}><option value="counts">原始强度 (counts)</option></NativeSelect></Field>
                      <NumberField label="下限" value={axisMinimum} onChange={setAxisMinimum} step="1" />
                      <NumberField label="上限" value={axisMaximum} onChange={(value) => { axisMaximumEditedRef.current = true; setAxisMaximum(value) }} step="1" />
                    </div>
                  </div>
                  <Separator className="my-5" />
                  <div>
                    <h3 className="text-sm font-semibold">光谱横轴</h3>
                    <div className="mt-3 grid grid-cols-2 gap-4">
                      <NumberField label="下限 (nm)" value={wavelengthMinimum} onChange={setWavelengthMinimum} step="0.1" />
                      <NumberField label="上限 (nm)" value={wavelengthMaximum} onChange={setWavelengthMaximum} step="0.1" />
                    </div>
                  </div>
                </>
              )}
              {error && <p className="mt-4 rounded-md bg-destructive/10 px-3 py-2 text-sm font-medium text-destructive">{error}</p>}
            </div>

            <aside aria-labelledby="shipping-report-preview-title" className="min-w-0 border-t border-border pt-5 pr-1 pb-4 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <Badge variant="outline" className="text-xs bg-emerald-500/10 text-emerald-600 border-emerald-500/30">
                    步骤 3 · 实时预览与导出
                  </Badge>
                  <p aria-live="polite" className="mt-1 text-xs text-muted-foreground">{previewBusy ? "正在更新预览…" : previewPages.length ? "与导出的 PDF 使用同一渲染器" : "填写完整后将自动生成预览"}</p>
                </div>
                {previewBusy && <LoaderCircle aria-label="正在更新预览" className="mt-0.5 size-4 shrink-0 animate-spin text-muted-foreground" />}
              </div>
              {previewError && <div className="mt-4 rounded-md border border-amber-300/70 bg-amber-50 px-3 py-2 text-xs text-amber-950"><p className="font-medium">当前输入暂无法预览</p><p className="mt-1 leading-relaxed">{previewError}</p></div>}
              {previewPages.length > 0 ? (
                <div className="mt-4 space-y-5">
                  {previewPages.map((page, index) => (
                    <figure className="overflow-hidden rounded-sm border border-border bg-white shadow-sm" key={`${index}-${page.slice(-24)}`}>
                      <img alt={`出货报告预览，第 ${index + 1} 页`} className="block h-auto w-full" src={`data:image/png;base64,${page}`} />
                    </figure>
                  ))}
                </div>
              ) : !previewError && !previewBusy && (
                <div className="mt-4 grid min-h-56 place-items-center border border-dashed border-border bg-muted/20 px-6 text-center text-xs text-muted-foreground">
                  <div>
                    <FileText className="mx-auto mb-2 size-5 opacity-60" />
                    <p>正在准备报告预览</p>
                  </div>
                </div>
              )}
            </aside>
          </div>

          <div className="mt-6 flex items-center justify-between gap-3 border-t border-border pt-4">
            <p className="max-w-[60%] truncate text-xs text-muted-foreground" title={outputPath}>将保存至：{outputPath}</p>
            <div className="flex gap-2">
              <Button disabled={busy} onClick={() => setInspection(null)} variant="outline">取消</Button>
              <Button disabled={busy} onClick={() => void generate()}>{busy ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}{busy ? "正在生成…" : "生成 PDF"}</Button>
            </div>
          </div>
        </section>
      )}
    </>
  )
}

export type BatchGenerationOutcome = {
  status: "generating" | "success" | "skipped" | "failed"
  message: string
  outputPath?: string
}

export function BatchShippingReportWorkflow({ disabled }: { disabled: boolean }) {
  const [rootPath, setRootPath] = useState("")
  const [outputDirectory, setOutputDirectory] = useState("")
  const [batch, setBatch] = useState<ShippingReportBatchInspection | null>(null)
  const [representative, setRepresentative] = useState<ShippingWorkbookInspection | null>(null)
  const [reportType, setReportType] = useState<ShippingReportType>("pole")
  const [reportLanguage, setReportLanguage] = useState<ShippingReportLanguage>("en")
  const [productName, setProductName] = useState(getStoredShippingProductName)
  const [operatingCurrentA, setOperatingCurrentA] = useState(Number.NaN)
  const [fields, setFields] = useState<ShippingFieldState>({})
  const [customFields, setCustomFields] = useState<ShippingReportFieldDefinition[]>([])
  const [encircledEnergyNa, setEncircledEnergyNa] = useState(DEFAULT_ENCIRCLED_ENERGY_NA)
  const [axisMode, setAxisMode] = useState<SpectrumAxisMode>("relative_db")
  const [axisMinimum, setAxisMinimum] = useState(-80)
  const [axisMaximum, setAxisMaximum] = useState(0)
  const axisMaximumEditedRef = useRef(false)
  const [wavelengthMinimum, setWavelengthMinimum] = useState(0)
  const [wavelengthMaximum, setWavelengthMaximum] = useState(2000)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [previewPages, setPreviewPages] = useState<string[]>([])
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState("")
  const [outcomes, setOutcomes] = useState<Record<string, BatchGenerationOutcome>>({})
  const [generationProgress, setGenerationProgress] = useState({ completed: 0, total: 0 })
  const [generating, setGenerating] = useState(false)
  const previewRequestRef = useRef(0)

  const initializeFields = useCallback((inspection: ShippingWorkbookInspection, nextType: ShippingReportType, currentA: number) => {
    const point = Number.isFinite(currentA)
      ? inspection.points.find((item) => Math.abs(item.currentA - currentA) < 1e-9)
      : undefined
    const preference = inspection.preferences[nextType] ?? { selectedFields: [], manualValues: {}, customFields: [] }
    const selected = new Set(preference.selectedFields)
    const nextCustomFields = preference.customFields ?? []
    const definitions = [...inspection.fieldDefinitions[nextType], ...nextCustomFields]
    setFields(Object.fromEntries(definitions.map((definition) => [
      definition.key,
      {
        include: selected.has(definition.key),
        value: definition.measuredKey
          ? shippingSuggestedValue(point, definition.measuredKey)
          : preference.manualValues[definition.key] ?? "",
      },
    ])))
    setCustomFields(nextCustomFields)
    setReportLanguage(preference.reportLanguage ?? "en")
    if (nextType === "spectrum") {
      setEncircledEnergyNa(preference.encircledEnergyNa ?? DEFAULT_ENCIRCLED_ENERGY_NA)
      const axis = preferredSpectrumAxis(inspection, currentA)
      setAxisMode(axis.mode)
      setAxisMinimum(axis.minimum)
      setAxisMaximum(axis.maximum)
      axisMaximumEditedRef.current = false
      setWavelengthMinimum(Number(axis.wavelengthMinimum.toFixed(2)))
      setWavelengthMaximum(Number(axis.wavelengthMaximum.toFixed(2)))
    }
  }, [])

  const updateMeasuredFields = useCallback((inspection: ShippingWorkbookInspection, nextType: ShippingReportType, currentA: number) => {
    const point = inspection.points.find((item) => Math.abs(item.currentA - currentA) < 1e-9)
    const measuredKeys = new Map(inspection.fieldDefinitions[nextType].filter((item) => item.measuredKey).map((item) => [item.key, item.measuredKey]))
    setFields((previous) => Object.fromEntries(Object.entries(previous).map(([key, state]) => [
      key,
      measuredKeys.has(key) ? { ...state, value: shippingSuggestedValue(point, measuredKeys.get(key)) } : state,
    ])))
  }, [])

  const inspectRepresentative = useCallback(async (item: ShippingReportBatchItem, nextType: ShippingReportType, currentA: number, resetFields: boolean) => {
    const inspection = await sendBackendRequest("shippingReport.inspect", { path: item.sourcePath })
    setRepresentative(inspection)
    if (resetFields) initializeFields(inspection, nextType, currentA)
    else if (Number.isFinite(currentA)) {
      updateMeasuredFields(inspection, nextType, currentA)
      if (nextType === "spectrum" && axisMode === "counts" && !axisMaximumEditedRef.current) {
        setAxisMaximum(suggestedSpectrumAxisMaximum(inspection.points, currentA))
      }
    }
    return inspection
  }, [axisMode, initializeFields, updateMeasuredFields])

  const chooseRootPath = async () => {
    if (disabled || busy) return
    try {
      const selected = await openDialog({ multiple: false, directory: true, title: "选择 Excel 批量根目录" })
      if (typeof selected !== "string") return
      setRootPath(selected)
      setOutputDirectory((current) => current.trim() || selected)
      setBatch(null)
      setRepresentative(null)
      setOutcomes({})
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const chooseOutputDirectory = async () => {
    if (disabled || busy) return
    try {
      const selected = await openDialog({ multiple: false, directory: true, title: "选择批量 PDF 保存文件夹" })
      if (typeof selected === "string") {
        setOutputDirectory(selected)
        setError("")
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const openOutputDirectory = async () => {
    const directory = outputDirectory.trim()
    if (!directory) {
      setError("请选择批量 PDF 保存文件夹")
      return
    }
    try {
      await invoke("open_directory", { path: directory })
      setError("")
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : String(reason)
      setError(`无法打开输出文件夹：${detail}`)
    }
  }

  const scan = async () => {
    const nextRoot = rootPath.trim()
    if (!nextRoot || busy) {
      if (!nextRoot) setError("请选择 Excel 批量根目录")
      return
    }
    setBusy(true)
    setError("")
    setOutcomes({})
    setGenerationProgress({ completed: 0, total: 0 })
    setPreviewPages([])
    setPreviewError("")
    try {
      const result = await sendBackendRequest("shippingReport.batchInspect", { rootPath: nextRoot })
      setBatch(result)
      const ready = result.items.filter((item) => item.status === "ready")
      if (!ready.length) {
        setRepresentative(null)
        setError(result.items.length ? "未找到可生成报告的 Excel，请查看预检结果。" : "所选目录中没有可处理的 .xlsx 文件。")
        return
      }
      const nextType: ShippingReportType = result.allReadySupportSpectrum ? "spectrum" : "pole"
      const currentA = result.commonCurrents.length ? result.commonCurrents[result.commonCurrents.length - 1] : Number.NaN
      const item = ready.find((candidate) => candidate.allowedReportTypes.includes(nextType) && (!Number.isFinite(currentA) || candidate.currents.includes(currentA))) ?? ready[0]
      const inspection = await inspectRepresentative(item, nextType, currentA, true)
      setReportType(nextType)
      setOperatingCurrentA(currentA)
      setProductName((current) => current.trim() || inspection.productName)
      if (!Number.isFinite(currentA)) setError("这些 Excel 没有共同电流点，请从全部候选电流中统一选择一个；缺少该测试点的文件会被跳过。")
    } catch (reason) {
      setBatch(null)
      setRepresentative(null)
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const changeProductName = (value: string) => {
    setProductName(value)
    storeShippingProductName(value)
    setOutcomes({})
  }

  const changeOperatingCurrent = async (currentA: number) => {
    if (!batch || busy) return
    setOperatingCurrentA(currentA)
    setOutcomes({})
    const item = batch.items.find((candidate) => candidate.status === "ready" && candidate.currents.includes(currentA) && candidate.allowedReportTypes.includes(reportType))
    if (!item) return
    try {
      setBusy(true)
      await inspectRepresentative(item, reportType, currentA, false)
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const changeReportType = async (nextType: ShippingReportType) => {
    if (!batch || busy) return
    setReportType(nextType)
    setOutcomes({})
    const item = batch.items.find((candidate) => candidate.status === "ready" && candidate.allowedReportTypes.includes(nextType) && (!Number.isFinite(operatingCurrentA) || candidate.currents.includes(operatingCurrentA)))
    if (!item) return
    try {
      setBusy(true)
      await inspectRepresentative(item, nextType, operatingCurrentA, true)
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const selectRepresentative = async (item: ShippingReportBatchItem) => {
    if (busy || item.status !== "ready" || !Number.isFinite(operatingCurrentA) || !item.currents.includes(operatingCurrentA) || !item.allowedReportTypes.includes(reportType)) return
    try {
      setBusy(true)
      await inspectRepresentative(item, reportType, operatingCurrentA, false)
      setError("")
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  const addCustomField = () => {
    const key = `custom-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    setCustomFields((previous) => [...previous, { key, label: "新参数", unit: "", side: reportType === "spectrum" ? "left" : "single" }])
    setFields((previous) => ({ ...previous, [key]: { include: true, value: "" } }))
  }

  const updateCustomField = (key: string, patch: Partial<Pick<ShippingReportFieldDefinition, "label" | "unit">>) => {
    setCustomFields((previous) => previous.map((field) => field.key === key ? { ...field, ...patch } : field))
  }

  const removeCustomField = (key: string) => {
    setCustomFields((previous) => previous.filter((field) => field.key !== key))
    setFields((previous) => {
      const next = { ...previous }
      delete next[key]
      return next
    })
  }

  const itemSkipReason = useCallback((item: ShippingReportBatchItem): string => {
    if (item.status !== "ready") return item.message
    if (!Number.isFinite(operatingCurrentA)) return "尚未选择统一工作电流"
    if (!item.currents.includes(operatingCurrentA)) return `缺少 ${Number.isInteger(operatingCurrentA) ? operatingCurrentA.toFixed(0) : operatingCurrentA} A 测试点`
    if (!item.allowedReportTypes.includes(reportType)) return "光谱数据不完整，不支持当前报告类型"
    return ""
  }, [operatingCurrentA, reportType])

  const generateBatch = async () => {
    if (!batch || busy) return
    if (!outputDirectory.trim()) {
      setError("请选择批量 PDF 保存文件夹")
      return
    }
    if (!productName.trim()) {
      setError("产品名称不能为空")
      return
    }
    if (!Number.isFinite(operatingCurrentA)) {
      setError("请选择统一工作电流")
      return
    }
    const readyItems = batch.items.filter((item) => !itemSkipReason(item))
    if (!readyItems.length) {
      setError("当前设置下没有可生成的 Excel")
      return
    }
    const initialOutcomes = Object.fromEntries(batch.items.filter((item) => itemSkipReason(item)).map((item) => [item.sourcePath, { status: "skipped" as const, message: itemSkipReason(item) }]))
    setOutcomes(initialOutcomes)
    setGenerationProgress({ completed: 0, total: readyItems.length })
    setGenerating(true)
    setBusy(true)
    setError("")
    let completed = 0
    for (const item of readyItems) {
      setOutcomes((previous) => ({ ...previous, [item.sourcePath]: { status: "generating", message: "正在生成…" } }))
      try {
        const result = await sendBackendRequest("shippingReport.batchGenerateItem", {
          sourcePath: item.sourcePath,
          outputDirectory,
          reportType,
          reportLanguage,
          productName,
          operatingCurrentA,
          fields,
          customFields,
          encircledEnergyNa,
          legacyCompletionConfirmed: true,
          ...(reportType === "spectrum" ? { spectrumAxis: { mode: axisMode, minimum: axisMinimum, maximum: axisMaximum, wavelengthMinimum, wavelengthMaximum } } : {}),
        })
        setOutcomes((previous) => ({ ...previous, [item.sourcePath]: { status: "success", message: "已生成", outputPath: result.outputPath } }))
      } catch (reason) {
        setOutcomes((previous) => ({ ...previous, [item.sourcePath]: { status: "failed", message: reason instanceof Error ? reason.message : String(reason) } }))
      }
      completed += 1
      setGenerationProgress({ completed, total: readyItems.length })
    }
    setGenerating(false)
    setBusy(false)
  }

  useEffect(() => {
    if (!representative || busy || !Number.isFinite(operatingCurrentA)) {
      setPreviewBusy(false)
      return
    }
    if (!representative.currents.some((current) => Math.abs(current - operatingCurrentA) < 1e-9) || !representative.allowedReportTypes.includes(reportType)) return
    let cancelled = false
    const requestId = ++previewRequestRef.current
    setPreviewBusy(true)
    const timer = window.setTimeout(() => {
      void sendBackendRequest("shippingReport.preview", {
        sourcePath: representative.sourcePath,
        reportType,
        reportLanguage,
        productName,
        sn: representative.sn,
        operatingCurrentA,
        fields,
        customFields,
        encircledEnergyNa,
        legacyCompletionConfirmed: true,
        ...(reportType === "spectrum" ? { spectrumAxis: { mode: axisMode, minimum: axisMinimum, maximum: axisMaximum, wavelengthMinimum, wavelengthMaximum } } : {}),
      }).then((result) => {
        if (cancelled || requestId !== previewRequestRef.current) return
        setPreviewPages(result.pages)
        setPreviewError("")
      }).catch((reason: unknown) => {
        if (cancelled || requestId !== previewRequestRef.current) return
        setPreviewError(reason instanceof Error ? reason.message : String(reason))
      }).finally(() => {
        if (!cancelled && requestId === previewRequestRef.current) setPreviewBusy(false)
      })
    }, 500)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [axisMaximum, axisMinimum, axisMode, busy, customFields, encircledEnergyNa, fields, operatingCurrentA, productName, reportLanguage, reportType, representative, wavelengthMaximum, wavelengthMinimum])

  const reportTypes: ShippingReportType[] = batch?.items.some((item) => item.status === "ready" && item.allowedReportTypes.includes("spectrum")) ? ["spectrum", "pole"] : ["pole"]
  const definitions = representative ? [...representative.fieldDefinitions[reportType].map((field) => ({ ...field, label: shippingReportFieldLabel(field, reportLanguage, reportType) })), ...customFields] : []
  const resultValues = Object.values(outcomes)
  const successCount = resultValues.filter((item) => item.status === "success").length
  const skippedCount = resultValues.filter((item) => item.status === "skipped").length
  const failedCount = resultValues.filter((item) => item.status === "failed").length
  const productMismatch = (batch?.productNames.length ?? 0) > 1

  return <>
    <div className="grid gap-4">
      <Field label="Excel 批量根目录"><ControlRow actionWidth="path"><Input aria-label="Excel 批量根目录" disabled={disabled || busy} onChange={(event) => { setRootPath(event.target.value); setBatch(null); setError("") }} placeholder="递归扫描目录中的 .xlsx 文件" value={rootPath} /><Button className="shrink-0" disabled={disabled || busy} onClick={() => void chooseRootPath()} size="sm" type="button" variant="outline"><FolderOpen className="size-3.5" />浏览</Button></ControlRow></Field>
      <Field label="本地 PDF 保存文件夹"><ControlRow actionWidth="path"><Input aria-label="批量 PDF 保存文件夹" disabled={disabled || busy} onChange={(event) => { setOutputDirectory(event.target.value); setError("") }} placeholder="已有同名文件时自动添加序号" value={outputDirectory} /><Button className="shrink-0" disabled={disabled || busy} onClick={() => void chooseOutputDirectory()} size="sm" type="button" variant="outline"><FolderOpen className="size-3.5" />选择文件夹</Button></ControlRow></Field>
    </div>
    <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4"><div className="text-xs text-muted-foreground">{error || "扫描后将按 Excel 内 SN 去重，并选择修改时间最新的测试文件。"}</div><Button disabled={disabled || busy} onClick={() => void scan()}><RefreshCw className={`size-4 ${busy && !batch ? "animate-spin" : ""}`} />{busy && !batch ? "正在扫描…" : "扫描并预检"}</Button></div>
    {batch && <>
      <section className="mt-6 border-t border-border pt-6">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-base font-semibold">批量预检</h2><p className="mt-1 text-xs text-muted-foreground">共发现 {batch.items.length} 个 Excel；点击可生成的行可切换预览样本。</p></div><div className="flex gap-2 text-xs"><Badge variant="outline">可候选 {batch.items.filter((item) => item.status === "ready").length}</Badge><Badge variant="outline">已忽略 {batch.items.filter((item) => item.status === "ignored_duplicate").length}</Badge><Badge variant="outline">无效 {batch.items.filter((item) => item.status === "invalid").length}</Badge></div></div>
        <div className="max-h-72 overflow-auto rounded-md border border-border"><table className="w-full min-w-[820px] border-collapse text-left text-xs"><thead className="sticky top-0 bg-muted"><tr><th className="px-3 py-2 font-medium">SN</th><th className="px-3 py-2 font-medium">Excel</th><th className="px-3 py-2 font-medium">修改时间</th><th className="px-3 py-2 font-medium">状态</th><th className="px-3 py-2 font-medium">结果</th></tr></thead><tbody>{batch.items.map((item) => { const reason = itemSkipReason(item); const outcome = outcomes[item.sourcePath]; const canPreview = item.status === "ready" && !reason; const activeRow = representative?.sourcePath === item.sourcePath; return <tr className={`border-t border-border ${canPreview ? "cursor-pointer hover:bg-muted/50" : "text-muted-foreground"} ${activeRow ? "bg-muted/60" : ""}`} key={item.sourcePath} onClick={() => canPreview && void selectRepresentative(item)}><td className="whitespace-nowrap px-3 py-2 font-medium">{item.sn || "-"}</td><td className="max-w-[360px] truncate px-3 py-2" title={item.sourcePath}>{item.sourcePath}</td><td className="whitespace-nowrap px-3 py-2">{new Date(item.modifiedAtMs).toLocaleString()}</td><td className="px-3 py-2"><span className={reason ? "text-amber-600" : "text-emerald-600"}>{reason || "可生成"}</span></td><td className="max-w-[300px] px-3 py-2">{outcome?.outputPath ? <button className="max-w-[280px] truncate text-primary underline-offset-2 hover:underline" onClick={(event) => { event.stopPropagation(); void openPath(outcome.outputPath!) }} title={outcome.outputPath}>{outcome.message}：{outcome.outputPath}</button> : outcome?.message ?? "-"}</td></tr>})}</tbody></table></div>
      </section>
      {representative && <section className="mt-6 border-t border-border pt-6">
        <div className="mb-5"><h2 className="text-base font-semibold">统一报告配置</h2><p className="mt-1 max-w-3xl truncate text-xs text-muted-foreground">预览样本：{representative.sourcePath}</p></div>
        {productMismatch && <p className="mb-4 rounded-md border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-950">Excel 中存在多个产品名称，本次统一使用下方填写的产品名称。</p>}
        <div className={SHIPPING_REPORT_SPLIT_LAYOUT}><div className="min-w-0">
          <div className="grid grid-cols-1 gap-4 2xl:grid-cols-3"><Field label="报告类型"><NativeSelect disabled={busy} value={reportType} onChange={(value) => void changeReportType(value as ShippingReportType)}>{reportTypes.includes("spectrum") && <option value="spectrum">含光谱测试报告</option>}<option value="pole">Pole 无光谱测试报告</option></NativeSelect></Field><Field label="报告语言"><NativeSelect disabled={busy} value={reportLanguage} onChange={(value) => setReportLanguage(value as ShippingReportLanguage)}><option value="en">English</option><option value="zh">中文</option></NativeSelect></Field><Field label="统一工作电流"><NativeSelect disabled={busy} value={Number.isFinite(operatingCurrentA) ? String(operatingCurrentA) : ""} onChange={(value) => void changeOperatingCurrent(Number(value))}><option disabled value="">请选择工作电流</option>{batch.unionCurrents.map((current) => <option key={current} value={String(current)}>{Number.isInteger(current) ? current.toFixed(0) : String(current)} A{batch.commonCurrents.includes(current) ? " · 全部共有" : ""}</option>)}</NativeSelect></Field><Field className="2xl:col-span-3" label="统一产品名称"><Input disabled={busy} value={productName} onChange={(event) => changeProductName(event.target.value)} /></Field></div>
          <Separator className="my-5" />
          <div><div className="mb-3 flex items-end justify-between gap-4"><div><h3 className="text-sm font-semibold">报告参数</h3><p className="mt-1 text-xs text-muted-foreground">测量参数会按每份 Excel 重新取值；手工参数统一应用。</p></div><div className="flex shrink-0 items-center gap-3"><span className="text-xs text-muted-foreground">已选 {Object.values(fields).filter((item) => item.include).length} 项</span><Button disabled={busy} onClick={addCustomField} size="sm" type="button" variant="outline"><Plus className="size-3.5" />手动增加参数</Button></div></div><div className="pr-1"><div className="shipping-report-parameters">{definitions.filter((definition) => !customFields.some((field) => field.key === definition.key)).map((definition) => { const state = fields[definition.key] ?? { include: false, value: "" }; return <div className="shipping-report-parameter-row" key={definition.key}><ShippingReportFieldSelector checked={state.include} definition={definition} disabled={busy} encircledEnergyNa={encircledEnergyNa} reportLanguage={reportLanguage} onCheckedChange={(include) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, include } }))} onEncircledEnergyNaChange={setEncircledEnergyNa} /><Input aria-label={`${definition.label}数值`} className="h-8" disabled={busy || Boolean(definition.measuredKey)} value={state.value} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, value: event.target.value } }))} /><span className="text-xs text-muted-foreground">{definition.unit || "-"}</span></div>})}{customFields.map((definition) => { const state = fields[definition.key] ?? { include: true, value: "" }; return <div className="shipping-report-custom-row grid items-center gap-2 rounded-md border border-dashed border-border bg-muted/20 p-2" key={definition.key}><input checked={state.include} disabled={busy} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, include: event.target.checked } }))} type="checkbox" /><Input className="h-8" disabled={busy} placeholder="参数名称" value={definition.label} onChange={(event) => updateCustomField(definition.key, { label: event.target.value })} /><Input className="h-8" disabled={busy} placeholder="参数值" value={state.value} onChange={(event) => setFields((previous) => ({ ...previous, [definition.key]: { ...state, value: event.target.value } }))} /><Input className="h-8" disabled={busy} placeholder="单位" value={definition.unit} onChange={(event) => updateCustomField(definition.key, { unit: event.target.value })} /><button aria-label={`删除${definition.label}`} disabled={busy} onClick={() => removeCustomField(definition.key)} type="button"><X className="size-4" /></button></div>})}</div></div></div>
          {reportType === "spectrum" && <><Separator className="my-5" /><div><h3 className="text-sm font-semibold">光谱纵轴</h3><div className="mt-3 grid grid-cols-3 gap-4"><Field label="单位"><NativeSelect disabled value={axisMode} onChange={() => undefined}><option value="counts">原始强度 (counts)</option></NativeSelect></Field><NumberField disabled={busy} label="下限" value={axisMinimum} onChange={setAxisMinimum} /><NumberField disabled={busy} label="上限" value={axisMaximum} onChange={(value) => { axisMaximumEditedRef.current = true; setAxisMaximum(value) }} /></div></div><Separator className="my-5" /><div><h3 className="text-sm font-semibold">光谱横轴</h3><div className="mt-3 grid grid-cols-2 gap-4"><NumberField disabled={busy} label="下限 (nm)" value={wavelengthMinimum} onChange={setWavelengthMinimum} step="0.1" /><NumberField disabled={busy} label="上限 (nm)" value={wavelengthMaximum} onChange={setWavelengthMaximum} step="0.1" /></div></div></>}
        </div><aside className="min-w-0 border-t border-border pt-5 pr-1 pb-4 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0"><div className="flex items-center justify-between"><div><h3 className="text-sm font-semibold">代表文件预览</h3><p className="mt-1 text-xs text-muted-foreground">SN：{representative.sn}</p></div>{previewBusy && <LoaderCircle className="size-4 animate-spin text-muted-foreground" />}</div>{previewError && <p className="mt-4 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-950">{previewError}</p>}{previewPages.length ? <div className="mt-4 space-y-5">{previewPages.map((page, index) => <img alt={`批量出货报告代表预览，第 ${index + 1} 页`} className="w-full border border-border bg-white" key={`${index}-${page.slice(-24)}`} src={`data:image/png;base64,${page}`} />)}</div> : !previewBusy && !previewError && <div className="mt-4 grid min-h-56 place-items-center border border-dashed border-border text-xs text-muted-foreground">等待有效配置</div>}</aside></div>
        <div className="mt-6 border-t border-border pt-4">{generationProgress.total > 0 && <div className="mb-4"><div className="mb-2 flex justify-between text-xs text-muted-foreground"><span>批量生成进度</span><span>{generationProgress.completed} / {generationProgress.total}</span></div><Progress value={generationProgress.total ? generationProgress.completed / generationProgress.total * 100 : 0} /></div>}<div className="flex flex-wrap items-center justify-between gap-3"><div className="text-xs text-muted-foreground">{resultValues.length ? `成功 ${successCount}，跳过 ${skippedCount}，失败 ${failedCount}` : `输出目录：${outputDirectory || "未选择"}`}</div><div className="flex gap-2">{resultValues.length > 0 && <Button disabled={!outputDirectory.trim()} onClick={() => void openOutputDirectory()} variant="outline"><FolderOpen className="size-4" />打开输出文件夹</Button>}<Button disabled={disabled || busy} onClick={() => void generateBatch()}>{generating ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}{generating ? "正在批量生成…" : "批量生成 PDF"}</Button></div></div></div>
      </section>}
    </>}
  </>
}

export const ShippingReportPage = memo(function ShippingReportPage({ active, controlsLocked, pending }: { active: boolean; controlsLocked: boolean; pending: boolean }) {
  const [mode, setMode] = useState<"single" | "batch">("single")
  const disabled = !active || controlsLocked || pending
  return (
    <Card className="shipping-report-page mb-6 shrink-0 shadow-none">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-4"><div><CardTitle className="text-base">出货报告</CardTitle><CardDescription>{mode === "single" ? "填写测试 Excel 和本地 PDF 保存文件夹，再配置报告参数并导出。" : "递归扫描同一批次 Excel，一次配置并逐份生成出货报告。"}</CardDescription></div><div aria-label="报告生成方式" role="group" className="inline-flex rounded-md border border-border bg-muted/30 p-1"><button aria-pressed={mode === "single"} className={`rounded px-3 py-1.5 text-xs font-medium ${mode === "single" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`} disabled={disabled} onClick={() => setMode("single")} type="button">单个生成</button><button aria-pressed={mode === "batch"} className={`rounded px-3 py-1.5 text-xs font-medium ${mode === "batch" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`} disabled={disabled} onClick={() => setMode("batch")} type="button">批量生成</button></div></div>
      </CardHeader>
      <CardContent>
        {mode === "single" ? <ShippingReportWorkflow disabled={disabled} /> : <BatchShippingReportWorkflow disabled={disabled} />}
      </CardContent>
    </Card>
  )
}, (previous, next) => (
  previous.active === next.active
  && previous.controlsLocked === next.controlsLocked
  && previous.pending === next.pending
))
