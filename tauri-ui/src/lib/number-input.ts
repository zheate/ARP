export function normalizeNumericInput(rawValue: string): string {
  let cleaned = rawValue.replace(/[。．，]/g, ".")

  const isNegative = cleaned.startsWith("-")
  let body = cleaned.replace(/-/g, "")
  if (isNegative) body = "-" + body

  const dotIndex = body.indexOf(".")
  if (dotIndex !== -1) {
    body = body.slice(0, dotIndex + 1) + body.slice(dotIndex + 1).replace(/\./g, "")
  }

  body = body.replace(/[^\d.-]/g, "")

  if (body.startsWith(".")) {
    body = "0" + body
  } else if (body.startsWith("-.")) {
    body = "-0." + body.slice(2)
  }

  return body.replace(/^(-?)0+(\d)/, "$1$2")
}

export function stepNumericValue(
  currentText: string,
  stepProp: string | undefined,
  direction: 1 | -1,
  min?: number,
  max?: number
): number {
  const stepVal = stepProp || "1"
  const stepNum = Number(stepVal)
  const validStep = Number.isFinite(stepNum) && stepNum > 0 ? stepNum : 1
  const currentNum = Number.isFinite(Number(currentText)) ? Number(currentText) : (min ?? 0)

  const stepDecimals = (stepVal.split(".")[1] || "").length
  const currentDecimals = (currentText.split(".")[1] || "").length
  const precision = Math.min(6, Math.max(stepDecimals, currentDecimals))

  let next = Number((currentNum + direction * validStep).toFixed(precision))
  if (min !== undefined && Number.isFinite(min)) next = Math.max(min, next)
  if (max !== undefined && Number.isFinite(max)) next = Math.min(max, next)
  return next
}
