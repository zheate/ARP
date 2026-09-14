// Keep built-in editor labels consistent with the PDF renderer.
const chineseLabels: Record<string, string> = {
  power: "输出功率",
  operatingCurrent: "工作电流",
  centerWavelength: "中心波长",
  fwhm: "光谱宽度（FWHM）",
  wavelengthCurrentCoefficient: "波长-电流系数",
  coolingTemperature: "冷却温度",
  powerFluctuation: "功率波动",
  thresholdCurrent: "阈值电流",
  operatingVoltage: "工作电压",
  slopeEfficiency: "斜率效率",
  electroOpticalEfficiency: "电光效率",
  fiberCoreDiameter: "光纤芯径",
  fiberNa: "光纤 NA",
}

export function shippingReportFieldLabel(
  field: { key: string; label: string },
  language: "en" | "zh",
  reportType: "spectrum" | "pole",
): string {
  if (language !== "zh") return field.label
  if (field.key === "power" && reportType === "spectrum") return "输出功率（CW）"
  if (field.key === "na017Energy") return "包围能量"
  return chineseLabels[field.key] ?? field.label
}
