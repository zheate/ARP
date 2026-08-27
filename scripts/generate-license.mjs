import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import {
  DEFAULT_PRIVATE_KEY_PATH,
  createLicenseDocument,
} from './license-generator-core.mjs'

function readArguments(argv) {
  const options = {}
  for (let index = 0; index < argv.length; index += 1) {
    const name = argv[index]
    if (!name.startsWith('--')) throw new Error(`无法识别的参数: ${name}`)
    const value = argv[index + 1]
    if (!value || value.startsWith('--')) throw new Error(`${name} 缺少参数值`)
    options[name.slice(2)] = value
    index += 1
  }
  return options
}

function usage() {
  return [
    '生成 ARP 离线许可证：',
    'npm run license:generate -- --machine <ARP机器码> --customer <客户名> --expires <YYYY-MM-DD> [--out <路径>]',
    '',
    `默认私钥: ${DEFAULT_PRIVATE_KEY_PATH}`,
    '默认输出: license.json',
  ].join('\n')
}

try {
  if (process.argv.includes('--help')) {
    console.log(usage())
    process.exit(0)
  }
  const options = readArguments(process.argv.slice(2))
  const outputPath = resolve(options.out || 'license.json')
  const license = createLicenseDocument({
    machineHash: options.machine,
    customer: options.customer,
    expiresAt: options.expires,
    privateKeyPath: resolve(options.key || DEFAULT_PRIVATE_KEY_PATH),
  })
  mkdirSync(dirname(outputPath), { recursive: true })
  writeFileSync(outputPath, `${JSON.stringify(license, null, 2)}\n`, { mode: 0o600 })
  console.log(`许可证已生成: ${outputPath}`)
  console.log(`客户: ${license.payload.customer}`)
  console.log(`到期: ${license.payload.expiresAt}`)
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error))
  console.error('')
  console.error(usage())
  process.exit(1)
}
