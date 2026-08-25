import test from 'node:test'
import assert from 'node:assert/strict'
import { generateKeyPairSync, verify } from 'node:crypto'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  canonicalizePayload,
  createLicenseDocument,
  normalizeExpiresAt,
  normalizeMachineHash,
} from '../../scripts/license-generator-core.mjs'

test('normalizes the full ARP machine code and end-of-day expiry', () => {
  const hash = 'ab'.repeat(32)
  const code = `ARP-${hash.toUpperCase().match(/.{1,4}/g).join('-')}`
  assert.equal(normalizeMachineHash(code), hash)
  assert.equal(normalizeExpiresAt('2027-08-24'), '2027-08-24T23:59:59.999Z')
})

test('creates an Ed25519 license with the canonical signed payload', () => {
  const { publicKey, privateKey } = generateKeyPairSync('ed25519')
  const directory = mkdtempSync(join(tmpdir(), 'arp-license-'))
  const keyPath = join(directory, 'private.pem')
  writeFileSync(keyPath, privateKey.export({ type: 'pkcs8', format: 'pem' }))
  const document = createLicenseDocument({
    machineHash: 'ab'.repeat(32),
    customer: '测试客户',
    issuedAt: '2026-08-24T00:00:00.000Z',
    expiresAt: '2027-08-24',
    privateKeyPath: keyPath,
  })
  assert.equal(document.payload.product, 'arp-power-test')
  assert.equal(
    verify(
      null,
      Buffer.from(canonicalizePayload(document.payload)),
      publicKey,
      Buffer.from(document.signature, 'base64'),
    ),
    true,
  )
})
