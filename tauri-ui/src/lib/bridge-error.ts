export type BridgeErrorKind = "backend" | "timeout" | "transport" | "protocol" | "authorization"
export interface BridgeFailure {
  kind: BridgeErrorKind
  code: string
  message: string
  method?: string
  requestId?: string
  runtimeId?: string
  outcomeUnknown?: boolean
}
export class BridgeRequestError extends Error {
  readonly failure: BridgeFailure
  constructor(failure: BridgeFailure) {
    const unresolvedPowerCommand = failure.outcomeUnknown && ["powerSupply.setCurrent", "powerSupply.setVoltage", "powerSupply.setOutput"].includes(failure.method ?? "")
    super(unresolvedPowerCommand ? `${failure.message}。执行结果未确认，请读取设备状态；如无法读取，请检查电源面板。` : failure.message)
    this.name = "BridgeRequestError"
    this.failure = failure
  }
}
export function normalizeBridgeError(reason: unknown, method?: string): BridgeRequestError {
  if (reason instanceof BridgeRequestError) return reason
  if (reason && typeof reason === "object" && "message" in reason && typeof reason.message === "string") {
    const failure = reason as Partial<BridgeFailure>
    return new BridgeRequestError({ ...failure, kind: failure.kind ?? "transport", code: failure.code ?? "request_failed", message: reason.message, method: failure.method ?? method })
  }
  return new BridgeRequestError({ kind: "transport", code: "request_failed", message: reason instanceof Error ? reason.message : String(reason), method })
}
