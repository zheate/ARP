import { chromium } from '/Users/zh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs'
import { writeFile } from 'node:fs/promises'
import assert from 'node:assert/strict'

const base = 'http://localhost:1420/.automatic-control-qa-20261015/index.html?demo=1'
const browser = await chromium.launch({channel:'chrome',headless:true})
const report = { browserOnly:true, hardwareConnected:false, checks:[], errors:[] }
function check(name, value, detail) { report.checks.push({name,passed:!!value,detail}); assert.ok(value,name) }
try {
  const page = await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'})
  page.on('pageerror', error=>report.errors.push(String(error)))
  await page.goto(base+'&component=1&state=done')
  await page.waitForFunction(()=>window.__controlQA)
  await page.evaluate(async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)})
  const control = page.locator('.automatic-test-control-card')
  const actions = control.locator('.automatic-test-control-card__actions')
  const title = control.locator('.automatic-test-progress__current strong')
  const start = control.getByRole('button',{name:'开始',exact:true})
  const stop = control.getByRole('button',{name:'停止',exact:true})
  let position
  for (const [state, expected] of [['done','提前结束'],['running','第 4 / 6 点'],['uncertain','下电待确认'],['success','完成'],['failed','异常结束'],['idle','待测 6 点'],['paused','第 4 / 6 点'],['ramping','第 4 / 6 点']]) {
    await page.evaluate(state=>window.__controlQA.setState(state),state)
    await title.filter({hasText:expected}).waitFor()
    const geometry = await control.evaluate(element=>{
      const rect = selector=>{const b=element.querySelector(selector).getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height,bottom:b.bottom,right:b.right}}
      const box = element.getBoundingClientRect()
      return {box:{x:box.x,y:box.y,bottom:box.bottom,right:box.right},progress:rect('.automatic-test-progress'),actions:rect('.automatic-test-control-card__actions'),titleColor:getComputedStyle(element.querySelector('strong')).color,tone:element.querySelector('.automatic-test-progress').dataset.tone}
    })
    check(state+'-status-above-actions',geometry.progress.bottom+8<=geometry.actions.y,geometry)
    check(state+'-shared-left-and-right-columns',Math.abs(geometry.progress.x-geometry.actions.x)<.5&&Math.abs(geometry.progress.right-geometry.actions.right)<.5)
    if (!position) position = geometry.actions
    check(state+'-actions-stay-in-place',JSON.stringify(position)===JSON.stringify(geometry.actions),geometry.actions)
    check(state+'-record-and-time',await control.locator('.automatic-test-progress__recorded').innerText()==='已记录 3 点\n·\n用时 02:36')
    if (['done','uncertain','success','failed'].includes(state)) check(state+'-single-terminal-title',await control.locator('.automatic-test-progress__current > span').count()===0 && !(await control.innerText()).includes('本轮已结束'))
    if (state==='done') {
      check('confirmed-stop-is-neutral',geometry.tone==='normal')
      check('confirmed-stop-button-availability',await start.isEnabled()&&await stop.isDisabled())
      await start.focus(); await page.keyboard.press('Enter')
      check('keyboard-start-reaches-existing-callback',await page.evaluate(()=>window.__qaCommands.at(-1)==='start'))
    }
    if (state==='running') {
      check('running-button-availability',await start.isDisabled()&&await stop.isEnabled())
      await stop.click()
      check('stop-reaches-existing-callback',await page.evaluate(()=>window.__qaCommands.at(-1)==='stop'))
    }
    if (state==='uncertain'||state==='failed') check(state+'-danger-remains-visible',geometry.tone==='danger')
  }
  await page.evaluate(()=>{window.__controlQA.setState('done');window.__controlQA.setPending(true)})
  await page.waitForFunction(()=>document.querySelector('.automatic-test-control-card__actions button').disabled)
  check('pending-retains-command-lock',await start.isDisabled()&&await stop.isDisabled())
  await page.evaluate(()=>{window.__controlQA.setPending(false);window.__controlQA.setActive(false)})
  check('inactive-retains-command-lock',await start.isDisabled()&&await stop.isDisabled())
  const rects = () => page.evaluate(()=>[...document.querySelectorAll('.device-rail-item,.automatic-power-chart,.automatic-chart-grid__power-row > [data-slot="card"],.chart-panel-with-sidebar')].map(element=>{const b=element.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height}}))
  for (const [width,height] of [[1440,900],[1180,720],[960,720],[800,720]]) {
    await page.setViewportSize({width,height})
    for (const theme of ['light','dark']) {
      for (const density of ['compact','comfortable','distant']) {
        const url=base+`&state=done&theme=${theme}&density=${density}`
        await page.goto(url+'&before=1');await page.waitForFunction(()=>window.__controlQA);await page.evaluate(async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)})
        const beforeRects = await rects()
        await page.goto(url);await page.waitForFunction(()=>window.__controlQA);await page.evaluate(async()=>{await document.fonts.ready;await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame)})
        const afterRects = await rects()
        const key=`${width}x${height}-${theme}-${density}`
        if (width>1100) check(key+'-neighbors-and-charts-unchanged',JSON.stringify(beforeRects)===JSON.stringify(afterRects),{beforeRects,afterRects})
        const details=await control.evaluate(element=>{
          const box=element.getBoundingClientRect()
          const nodes=[element,...element.querySelectorAll('.automatic-test-progress,.automatic-test-progress__current,.automatic-test-progress__recorded,.automatic-test-control-card__actions,button')]
          return {box:{x:box.x,y:box.y,right:box.right,bottom:box.bottom},overflow:nodes.filter(node=>{const b=node.getBoundingClientRect();return b.x<box.x-.5||b.right>box.right+.5||b.y<box.y-.5||b.bottom>box.bottom+.5||node.scrollWidth>node.clientWidth+1}).map(node=>node.className)}
        })
        check(key+'-control-content-bounded',details.overflow.length===0,details)
      }
    }
  }
  check('no-runtime-errors',report.errors.length===0,report.errors)
} finally {
  await browser.close()
  await writeFile('/Users/zh/Documents/test/ARP/output/playwright/automatic-control-20261015/verification.json',JSON.stringify(report,null,2))
}
console.log(JSON.stringify({passed:report.checks.filter(x=>x.passed).length,failed:report.checks.filter(x=>!x.passed).length,errors:report.errors}))
