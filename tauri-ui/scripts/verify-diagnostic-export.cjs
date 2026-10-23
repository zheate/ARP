// Run through playwright-cli against the isolated fixture; never a live bridge.
async (page) => {
  const base = 'http://127.0.0.1:1432/scripts/fixtures/diagnostic-export.html';
  const report = [];
  const requests = () => page.evaluate(() => window.diagnosticFixtureCalls.filter(call => call.command === 'bridge_request').length);
  async function bounded(locator) {
    const box = await locator.boundingBox();
    if (!box || box.x < 0 || box.x + box.width > (await page.viewportSize()).width + 1) throw Error('Horizontal clipping');
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw Error('Page overflow');
    return box;
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const width of [1440, 1180, 760, 560]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(base);
    await page.evaluate(() => document.fonts.ready);
    const summary = page.getByRole('region', { name: '测试结果摘要' });
    const button = summary.getByRole('button', { name: '一键导出诊断包', exact: true });
    await button.waitFor();
    const before = await bounded(button);
    await button.evaluate(el => { el.click(); el.click(); });
    const pending = summary.getByRole('button', { name: '正在打包…', exact: true });
    await pending.waitFor();
    const during = await bounded(pending);
    if (before.width !== during.width || before.height !== during.height) throw Error('Button size changed');
    if (!await pending.isDisabled()) throw Error('Duplicate clicks allowed');
    await summary.getByRole('button', { name: '定位诊断包', exact: true }).waitFor();
    const actionBoxes = await summary.locator('.automatic-completion-summary__actions button').evaluateAll(buttons => buttons.map(button => button.getBoundingClientRect().toJSON()));
    if (width >= 1180 && actionBoxes.some(box => Math.abs(box.y - actionBoxes[0].y) > 1)) throw Error('Action button top edges differ');
    if (await requests() !== 1) throw Error('Duplicate ZIP requested');
    await summary.getByRole('button', { name: '定位诊断包', exact: true }).click();
    if (await requests() !== 1) throw Error('Reveal recreated ZIP');
    await bounded(summary);
    await page.screenshot({ path: `output/playwright/diagnostic-export-20261023/summary-${width}.png` });
    await summary.getByRole('button', { name: '详情', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.waitFor();
    await dialog.getByRole('button', { name: '一键导出诊断包', exact: true }).click();
    await dialog.getByRole('button', { name: '定位诊断包', exact: true }).waitFor();
    const box = await bounded(dialog);
    if (box.y < 0 || box.y + box.height > 901) throw Error('Completion dialog clipped');
    await page.screenshot({ path: `output/playwright/diagnostic-export-20261023/dialog-${width}.png` });
    await page.keyboard.press('Escape');
    if (await dialog.count()) throw Error('Escape did not close completion');
    await page.getByRole('button', { name: '下一轮示例', exact: true }).click();
    await summary.getByRole('button', { name: '一键导出诊断包', exact: true }).waitFor();
    if (await summary.getByText('ZIP 已生成，可用于问题分析', { exact: true }).count()) throw Error('Previous round package persisted');
    report.push({ width, summary: 'passed', dialog: 'passed', duplicateClick: 'blocked', nextRound: 'reset' });
  }
  for (const mode of ['exporting', 'write-failed', 'partial', 'reveal-failed']) {
    await page.goto(`${base}?mode=${mode}`);
    const button = page.getByRole('button', { name: '一键导出诊断包', exact: true });
    if (mode === 'exporting') {
      if (!await button.isDisabled()) throw Error('Export permitted during Excel write');
    } else {
      await button.click();
      if (mode === 'write-failed') {
        await page.getByRole('alert').filter({ hasText: '磁盘空间不足' }).waitFor();
        await button.click();
        await page.getByRole('alert').filter({ hasText: '磁盘空间不足' }).waitFor();
        if (await requests() !== 2) throw Error('ZIP failure cannot be retried');
      } else {
        const reveal = page.getByRole('button', { name: '定位诊断包', exact: true });
        await reveal.waitFor();
        if (mode === 'partial') await page.getByRole('status').filter({ hasText: '缺失项' }).waitFor();
        if (mode === 'reveal-failed') {
          await page.getByRole('alert').filter({ hasText: 'ZIP 已生成' }).waitFor();
          await reveal.click();
          await page.getByRole('alert').filter({ hasText: 'ZIP 已生成' }).waitFor();
          if (await requests() !== 1) throw Error('Folder failure recreated archive');
        }
      }
    }
    await bounded(page.getByRole('region', { name: '测试结果摘要' }));
    await page.screenshot({ path: `output/playwright/diagnostic-export-20261023/${mode}.png` });
    report.push({ mode, result: 'passed' });
  }
  await page.goto(base);
  await page.getByRole('button', { name: '一键导出诊断包', exact: true }).click();
  await page.getByRole('button', { name: '下一轮示例', exact: true }).click();
  await page.getByRole('button', { name: '一键导出诊断包', exact: true }).waitFor();
  const reveals = await page.evaluate(() => window.diagnosticFixtureCalls.filter(call => call.command === 'plugin:opener|reveal_item_in_dir').length);
  if (reveals || await page.getByRole('button', { name: '定位诊断包', exact: true }).count()) throw Error('Stale round export displayed or revealed');
  report.push({ mode: 'round-changed-during-export', result: 'ignored stale result' });
  await page.goto(base);
  await page.getByRole('button', { name: '一键导出诊断包', exact: true }).click();
  await page.getByRole('button', { name: '隐藏结果示例', exact: true }).click();
  await page.waitForTimeout(550); // Simulated bridge latency is 400 ms.
  const detachedReveals = await page.evaluate(() => window.diagnosticFixtureCalls.filter(call => call.command === 'plugin:opener|reveal_item_in_dir').length);
  if (detachedReveals) throw Error('An unmounted result control revealed an old package');
  report.push({ mode: 'result-unmounted-during-export', result: 'ignored stale result' });
  return report;
}
