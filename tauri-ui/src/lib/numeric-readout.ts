type ReadoutFormat = Pick<Intl.NumberFormatOptions, "useGrouping" | "minimumFractionDigits" | "maximumFractionDigits" | "minimumIntegerDigits">

export function numberFlowReadout(text: string, minimumIntegerDigits = 1): { value: number; format: ReadoutFormat } | null {
  // Only animate ordinary decimal readouts. Preserve placeholders, notation,
  // explicit signs and strings that Intl would round or reformat.
  if (!/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(text)) return null
  const value = Number(text)
  if (!Number.isFinite(value)) return null
  const fractionDigits = text.split(".")[1]?.length ?? 0
  if (fractionDigits > 20) return null
  const format: ReadoutFormat = {
    useGrouping: false,
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }
  if (new Intl.NumberFormat("en-US", format).format(value) !== text) return null
  return { value, format: { ...format, minimumIntegerDigits } }
}
