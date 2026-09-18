import { BridgeRequestError } from "./bridge-error.ts"
export function objectValue(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}
function invalid(method: string): never {
  throw new BridgeRequestError({ kind: "protocol", code: "invalid_response", method, message: `后端响应格式无效：${method}` })
}
export function validateCommandResponse(method: string, value: unknown): void {
  if (!objectValue(value)) invalid(method)
  if (method === "system.ping") {
    if (value.protocolVersion !== 2) throw new BridgeRequestError({ kind: "protocol", code: "unsupported_version", method, message: "前后端协议版本不兼容" })
    if (value.status !== "ok" || !["active", "read_only"].includes(String(value.mode))) invalid(method)
  }
  if (method === "app.shutdown") {
    if (typeof value.shutdownConfirmed !== "boolean" || typeof value.exportsDrained !== "boolean" || !objectValue(value.shutdown) || typeof value.shutdown.confirmed !== "boolean") invalid(method)
    if (value.shutdownConfirmed !== value.shutdown.confirmed) invalid(method)
  }
  if (method === "app.configure" && !objectValue(value.configuration)) invalid(method)
}
export function validateSnapshot(value: unknown): void {
  if (!objectValue(value) || !objectValue(value.backend) || !objectValue(value.devices) || !objectValue(value.automaticTest) || !objectValue(value.safety)) invalid("app.snapshot")
  if (value.backend.protocolVersion !== undefined && value.backend.protocolVersion !== 2) invalid("app.snapshot")
  if (typeof value.backend.connected !== "boolean" || !["active", "read_only"].includes(String(value.backend.mode)) || typeof value.automaticTest.state !== "string") invalid("app.snapshot")
  for (const name of ["powerSupply", "powerMeter", "spectrometer"]) if (!objectValue(value.devices[name])) invalid("app.snapshot")
  if (!objectValue(value.safety.shutdown) || typeof value.safety.shutdown.confirmed !== "boolean") invalid("app.snapshot")
}
/** Reject a command response from a runtime superseded while it was pending. */
export function commandRuntimeIsCurrent(started: string | undefined, current: string | undefined, response: string | undefined): boolean {
  return !started || !current || started === current || response === current
}
