import { invoke } from "@tauri-apps/api/core"

export type LicenseStatusReason =
  | "valid"
  | "machine_exempt"
  | "missing"
  | "invalid_format"
  | "invalid_signature"
  | "wrong_product"
  | "machine_mismatch"
  | "not_yet_valid"
  | "expired"
  | "machine_id_unavailable"

export interface LicensePayload {
  product: string
  customer: string
  machineHash: string
  issuedAt: string
  expiresAt: string
  features: string[]
}

export interface LicenseStatus {
  valid: boolean
  reason: LicenseStatusReason
  machineHash: string
  machineCode: string
  licensePath: string
  payload?: LicensePayload
}

export const licenseReasonLabels: Record<LicenseStatusReason, string> = {
  valid: "许可证有效",
  machine_exempt: "本机免授权",
  missing: "未安装许可证",
  invalid_format: "许可证格式不正确",
  invalid_signature: "许可证签名无效",
  wrong_product: "许可证不属于当前软件",
  machine_mismatch: "许可证不属于当前设备",
  not_yet_valid: "许可证尚未生效",
  expired: "许可证已过期",
  machine_id_unavailable: "无法读取当前设备机器码",
}

export function getLicenseStatus(): Promise<LicenseStatus> {
  return invoke<LicenseStatus>("license_get_status")
}

export function installLicense(sourcePath: string): Promise<LicenseStatus> {
  return invoke<LicenseStatus>("license_install", { sourcePath })
}

export function formatLicenseDate(value: string): string {
  return value.slice(0, 10)
}
