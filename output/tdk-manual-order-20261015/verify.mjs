import { chromium } from '/Users/zh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
const out = new URL('.', import.meta.url).pathname
const base = 'http://127.0.0.1:1447/.tdk-manual-order-qa-20261015/index.html'
await mkdir(out + 'browser', { recursive: true })
const report = { hardwareConnected: false, runs: [], layouts: [], errors: [] }
const browser = await chromium.launch({ headless: true, executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' })
try {
 const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
 page.on('pageerror', error => report.errors.push(error.message))
 const go = async query => { await page.goto(base + query); await page.locator('#manual-psu-current').waitFor(); await page.evaluate(() => document.fonts.ready) }
 const input = page.getByRole('textbox', { name: '设置电流 (A)', exact: true })
 const apply = page.getByRole('button', { name: '加电', exact: true })
 const stop = page.getByRole('button', { name: '归零下电', exact: true })
 const enable = page.getByRole('button', { name: '开启输出', exact: true })
 for (let run = 1; run <= 3; run++) {
  await go('?state=disconnected')
  assert(await input.isDisabled()); assert(await enable.isDisabled()); assert(await apply.isDisabled()); assert(await stop.isDisabled())
  const connect = page.getByRole('button', { name: '连接 TDK', exact: true })
  assert(await connect.isEnabled())
  const positions = await Promise.all([connect, enable, input, apply, stop].map(locator => locator.boundingBox()))
  assert(positions[0].y === positions[1].y && positions[1].y < positions[2].y && positions[2].y === positions[3].y && positions[3].y < positions[4].y)
  await connect.click()
  await page.getByRole('button', { name: '断开连接', exact: true }).waitFor()
  assert(await input.isDisabled()); assert(await enable.isEnabled()); assert(await apply.isDisabled())
  await enable.click()
  await page.getByRole('button', { name: '输出已开启', exact: true }).waitFor()
  assert(await input.isEnabled()); assert(await apply.isEnabled()); assert(await stop.isEnabled())
  assert.equal(await input.inputValue(), '5', 'zero confirmation must preserve the target draft')
  await input.fill('7.5'); await apply.click()
  await page.waitForFunction(() => window.__tdkQa.calls.some(c => c.method === 'powerSupply.setCurrent'))
  assert.deepEqual(await page.evaluate(() => window.__tdkQa.calls), [
   { method: 'powerSupply.connect', args: {} }, { method: 'powerSupply.setOutput', args: { enabled: true } }, { method: 'powerSupply.setCurrent', args: { currentA: 7.5 } },
  ])
  await stop.click(); await page.getByText('已确认安全下电', { exact: true }).waitFor()
  assert(await input.isDisabled()); assert(await apply.isDisabled()); assert.equal(await input.inputValue(), '7.5'); assert(await enable.isEnabled())
  await enable.click(); await page.evaluate(() => window.__tdkQa.holdApply = true); await apply.click()
  await page.waitForFunction(() => typeof window.__tdkQa.releaseApply === 'function')
  assert(await apply.isDisabled()); assert(await stop.isEnabled(), 'priority shutdown must bypass an apply command')
  const applyCount = await page.evaluate(() => window.__tdkQa.calls.filter(c => c.method === 'powerSupply.setCurrent').length)
  await apply.evaluate(button => button.click())
  assert.equal(await page.evaluate(() => window.__tdkQa.calls.filter(c => c.method === 'powerSupply.setCurrent').length), applyCount)
  await stop.click(); await page.getByText('已确认安全下电', { exact: true }).waitFor()
  await page.evaluate(() => window.__tdkQa.releaseApply()); await page.waitForFunction(() => !document.querySelector('#manual-psu-current').disabled === false)
  assert.equal(await input.inputValue(), '7.5')
  await page.evaluate(() => window.__tdkQa.failStop = true); await stop.click()
  await page.getByRole('status').filter({ hasText: '下电未确认：无法确认输出电流归零' }).waitFor()
  assert(await stop.isEnabled())
  await page.evaluate(() => window.__tdkQa.failStop = false); await stop.click()
  await page.getByText('已确认安全下电', { exact: true }).waitFor()
  await page.evaluate(() => window.__tdkQa.patch(s => s.safety.manualControlsAllowed = false))
  await page.getByText('手动控制已锁定', { exact: true }).waitFor(); assert(await stop.isDisabled()); assert(await enable.isDisabled())
  report.runs.push({ run, passed: true, checks: 'ordered controls, staged unlock, exact command order, draft retention, priority stop, duplicate block, failed-stop retry, automatic ownership lock' })
 }
 for (const theme of ['dark', 'light']) for (const [width, height] of [[1440, 900], [1180, 720], [960, 768]]) {
  await page.setViewportSize({ width, height })
  for (const state of ['disconnected', 'connected', 'enabled', 'powered']) {
   await go(`?state=${state}&theme=${theme}`)
   const boxes = await page.locator('.manual-device-card').evaluateAll(cards => cards.map(card => {
    const box = card.getBoundingClientRect(), footer = card.querySelector('.manual-device-card__footer').getBoundingClientRect()
    return { x: box.x, y: box.y, width: box.width, height: box.height, footerY: footer.y, overflow: card.scrollWidth > card.clientWidth }
   }))
   assert(boxes.every(box => box.x >= 0 && box.x + box.width <= width + 1 && !box.overflow))
   assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
   if (width > 1100) assert(boxes.every(box => Math.abs(box.y - boxes[0].y) < 1 && Math.abs(box.width - boxes[0].width) < 1 && Math.abs(box.height - boxes[0].height) < 1 && Math.abs(box.footerY - boxes[0].footerY) < 1))
   report.layouts.push({ theme, width, height, state, passed: true, boxes })
   if (state === 'enabled') await page.screenshot({ path: `${out}browser/${theme}-${width}.png` })
  }
 }
 await page.setViewportSize({ width: 1440, height: 900 }); await go('?state=enabled')
 await page.locator('.manual-device-card').first().screenshot({ path: out + 'browser/tdk-card.png' })
 for (const state of ['disconnected', 'enabled']) {
  await go('?kind=ch341&state=' + state)
  assert.equal(await page.locator('.manual-tdk-prepare').count(), 0)
  assert.equal(await page.locator('.manual-tdk-step').count(), 0)
  assert(await page.getByRole('textbox', { name: '目标电流 (A)', exact: true }).isEnabled())
  assert(await page.getByRole('button', { name: '施加电流', exact: true }).isVisible())
 }
 assert.equal(report.errors.length, 0)
 console.log(JSON.stringify({ runs: report.runs, layoutCases: report.layouts.length, ch341: 'PASS', errors: report.errors }, null, 2))
} finally { await browser.close(); await writeFile(out + 'verification.json', JSON.stringify(report, null, 2)) }
