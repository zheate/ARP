// Run via playwright-cli run-code --filename against the isolated fixture.
async (page) => {
  await page.goto('http://127.0.0.1:1420/scripts/fixtures/power-background.html');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.evaluate(() => document.fonts.ready);
  const report = [];
  async function box(locator) { return locator.boundingBox(); }
  function same(a, b) { return a && b && ['x', 'y', 'width', 'height'].every(key => Math.abs(a[key] - b[key]) < 0.5); }
  for (const width of [1440, 1180, 760]) {
    await page.setViewportSize({ width, height: width === 760 ? 600 : 900 });
    await page.goto('http://127.0.0.1:1420/scripts/fixtures/power-background.html');
    const cards = await box(page.getByTestId('cards'));
    for (const name of ['电源设置', '功率计设置', '光谱仪设置']) {
      await page.getByRole('button', { name, exact: true }).click();
      const dialog = page.getByRole('dialog');
      const before = await box(dialog);
      if (!same(cards, await box(page.getByTestId('cards')))) throw Error(`Background cards shifted on open: ${JSON.stringify({width,name,cards,after:await box(page.getByTestId('cards'))})}`);
      const selects = await dialog.getByRole('combobox').all();
      let checked = 0;
      for (const select of selects) {
        if (!await select.isEnabled()) continue;
        await select.scrollIntoViewIfNeeded();
        const bounds = await box(dialog);
        await select.click();
        await dialog.getByRole('listbox').waitFor();
        if (!same(bounds, await box(dialog))) throw Error(`${name} dropdown changed dialog size`);
        await page.keyboard.press('Escape');
        if (await dialog.getByRole('listbox').count()) throw Error('Escape did not close dropdown');
        if (await dialog.count() !== 1) throw Error('Dropdown Escape closed dialog');
        await select.click();
        const selected = dialog.getByRole('option', { selected: true });
        const option = await selected.count() ? selected.first() : dialog.getByRole('option').first();
        await option.click();
        if (await dialog.getByRole('listbox').count() || await dialog.count() !== 1) throw Error('Option did not close only dropdown');
        checked++;
      }
      if (!checked) throw Error(`${name} had no usable selectors`);
      if (name === '功率计设置') {
        const footer = dialog.locator('.device-settings-dialog__footer');
        const footerBefore = await footer.evaluate(el => ({ text: el.textContent, box: el.getBoundingClientRect().toJSON(),
          buttons: [...el.querySelectorAll('button')].map(b => ({ text: b.textContent, color: getComputedStyle(b).backgroundColor, box: b.getBoundingClientRect().toJSON() })) }));
        await dialog.getByRole('button', { name: '识别', exact: true }).click();
        const footerDuring = await footer.evaluate(el => ({ text: el.textContent, box: el.getBoundingClientRect().toJSON(),
          buttons: [...el.querySelectorAll('button')].map(b => ({ text: b.textContent, color: getComputedStyle(b).backgroundColor, box: b.getBoundingClientRect().toJSON() })) }));
        if (JSON.stringify(footerBefore) !== JSON.stringify(footerDuring)) throw Error('Detection changed footer');
        await page.waitForTimeout(700);
        const setup = dialog.getByRole('region', { name: '功率本底与判稳' });
        if (await setup.locator('input[type="number"]:visible').count()) throw Error('Advanced numeric inputs exposed by default');
        await setup.scrollIntoViewIfNeeded();
        await page.screenshot({ path: `output/playwright/power-stability-setup-20261023/default-${width}.png` });
        const preset = dialog.getByRole('combobox', { name: '测试容差预设', exact: true });
        await preset.click();
        await dialog.getByRole('option', { name: '已保存的自定义容差', exact: true }).click();
        await dialog.getByText('编辑自定义容差', { exact: true }).click();
        await dialog.getByLabel('绝对漂移容差 (W)').fill('0.4');
        await preset.click();
        await dialog.getByRole('option', { name: '标准 · 相对漂移 0.2%', exact: true }).click();
        if (!(await dialog.getByLabel('当前测试容差').innerText()).includes('0.2%')) throw Error('Standard tolerance missing');
        await preset.click();
        await dialog.getByRole('option', { name: '已保存的自定义容差', exact: true }).click();
        await dialog.getByText('编辑自定义容差', { exact: true }).click();
        if (Number(await dialog.getByLabel('绝对漂移容差 (W)').inputValue()) !== 0.4) throw Error('Preset switching overwrote custom tolerance');
        await preset.click();
        await dialog.getByRole('option', { name: '标准 · 相对漂移 0.2%', exact: true }).click();
        await dialog.getByText('设备响应 · 一次确认', { exact: true }).click();
        const confirmation = dialog.getByLabel('已依据设备规格或实测确认响应参数');
        if (await confirmation.isChecked() || await confirmation.isEnabled()) throw Error('Unknown device response can be confirmed');
        await dialog.getByLabel('电流最短建立时间 (s)').fill('1');
        await dialog.getByLabel('电流回读容差 (A)').fill('0.01');
        await dialog.getByLabel('探头最短响应时间 (s)').fill('2');
        await confirmation.check();
        if (!(await dialog.getByLabel('设备响应状态').innerText()).includes('待保存')) throw Error('Draft confirmation displayed as saved');
        await page.screenshot({ path: `output/playwright/power-stability-setup-20261023/response-${width}.png` });
        await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
        await page.getByRole('button', { name: '功率计设置', exact: true }).click();
        if (!(await dialog.getByLabel('设备响应状态').innerText()).includes('已确认')) throw Error('Saved response not reused');
        await dialog.getByText('维护选项（通常无需修改）', { exact: true }).click();
        if (await dialog.getByLabel('仪器设置备注（可选）').inputValue() !== '') throw Error('Fresh notes not empty');
        if (Number(await dialog.getByLabel('手动覆盖步进 (W，0 为自动学习)').inputValue()) !== 0) throw Error('Automatic step not default');
        await dialog.getByText('维护选项（通常无需修改）', { exact: true }).click();
        await dialog.getByRole('button', { name: '开始采集', exact: true }).click();
        const detected = await dialog.getByLabel('自动获取的功率计设置').innerText();
        if (!detected.includes('3000 W（设备返回，软件增益前）') || !detected.includes('滤波：设备未提供')) throw Error('Automatic settings missing');
        await dialog.getByLabel('已确认 0 A、无光输出且探头已冷却').check();
        await dialog.getByRole('button', { name: '学习本底 (180 s)', exact: true }).click();
        await dialog.getByRole('button', { name: '取消本底任务', exact: true }).waitFor();
        await page.screenshot({ path: `output/playwright/power-stability-setup-20261023/learning-${width}.png` });
        await dialog.getByRole('button', { name: '取消本底任务', exact: true }).click();
        await dialog.getByRole('button', { name: '停止采集', exact: true }).click();
      }
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw Error('Page horizontal overflow');
      const clipping = await dialog.evaluate(el => el.getBoundingClientRect().left < 0 || el.getBoundingClientRect().right > innerWidth || el.getBoundingClientRect().bottom > innerHeight);
      if (clipping) throw Error(`${name} clipped at ${width}`);
      await page.keyboard.press('Escape');
      if (await dialog.count()) throw Error('Escape did not close dialog');
      if (!same(cards, await box(page.getByTestId('cards')))) throw Error('Background cards shifted after close');
      report.push({ width, name, selectors: checked, aligned: true, before });
    }
    await page.goto('http://127.0.0.1:1420/scripts/fixtures/power-background.html?learned=1');
    await page.getByRole('button', { name: '功率计设置', exact: true }).click();
    const learnedDialog = page.getByRole('dialog');
    if (!(await learnedDialog.getByLabel('本底自动学习参数').innerText()).includes('0.001 W（本底观测估计')) throw Error('Learned step not displayed');
    if (!(await learnedDialog.getByLabel('设备响应状态').innerText()).includes('已确认')) throw Error('Saved response not confirmed');
    await learnedDialog.getByRole('combobox', { name: '测试容差预设', exact: true }).click();
    await learnedDialog.getByRole('option', { name: '已保存的自定义容差', exact: true }).click();
    if (!(await learnedDialog.getByLabel('设备响应状态').innerText()).includes('已确认')) throw Error('Tolerance change invalidated device response');
    await learnedDialog.getByRole('region', { name: '功率本底与判稳' }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `output/playwright/power-stability-setup-20261023/learned-${width}.png` });
    await page.keyboard.press('Escape');
  }
  return report;
}
