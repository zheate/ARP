import { readFileSync } from 'node:fs'
import { sign } from 'node:crypto'

export const LICENSE_PRODUCT_ID = 'arp-power-test'
export const DEFAULT_PRIVATE_KEY_PATH = '.license/arp-private-key.pem'

export function canonicalizePayload(payload) {
  return JSON.stringify({
    product: payload.product,
    customer: payload.customer,
    machineHash: payload.machineHash,
    issuedAt: payload.issuedAt,
    expiresAt: payload.expiresAt,
    features: [...payload.features].sort(),
  })
}

export function normalizeMachineHash(value) {
  return String(value || '')
    .trim()
    .replace(/^ARP-/i, '')
    .replace(/-/g, '')
    .toLowerCase()
}

export function normalizeExpiresAt(value) {
  const raw = String(value || '').trim()
  if (!raw) throw new Error('到期日不能为空')
  const date = /^\d{4}-\d{2}-\d{2}$/.test(raw)
    ? new Date(`${raw}T23:59:59.999Z`)
    : new Date(raw)
  if (Number.isNaN(date.getTime())) throw new Error('到期日格式不正确')
  return date.toISOString()
}

export function createLicenseDocument({
  machineHash,
  customer,
  expiresAt,
  privateKeyPath = DEFAULT_PRIVATE_KEY_PATH,
  issuedAt = new Date().toISOString(),
}) {
  const normalizedMachineHash = normalizeMachineHash(machineHash)
  if (!/^[a-f0-9]{64}$/.test(normalizedMachineHash)) {
    throw new Error('机器码必须是激活页复制出来的完整 ARP 机器码')
  }
  const normalizedCustomer = String(customer || '').trim()
  if (!normalizedCustomer) throw new Error('客户名不能为空')

  const payload = {
    product: LICENSE_PRODUCT_ID,
    customer: normalizedCustomer,
    machineHash: normalizedMachineHash,
    issuedAt,
    expiresAt: normalizeExpiresAt(expiresAt),
    features: ['standard'],
  }
  const privateKey = readFileSync(privateKeyPath, 'utf8')
  const signature = sign(null, Buffer.from(canonicalizePayload(payload)), privateKey).toString('base64')
  return { payload, signature }
}
