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
        await dialog.getByText('高级判稳参数（首次旁路测试可跳过）', { exact: true }).click();
        if (await dialog.getByLabel('仪器设置备注（可选）').inputValue() !== '') throw Error('Fresh notes not empty');
        if (Number(await dialog.getByLabel('手动步进 (W，0 为自动估计)').inputValue()) !== 0) throw Error('Automatic step not default');
        await dialog.getByLabel('已用响应实测和测试规范确认上述参数').scrollIntoViewIfNeeded();
        if (await dialog.getByLabel('已用响应实测和测试规范确认上述参数').isChecked()) throw Error('Unknown parameters were confirmed');
        await page.screenshot({ path: `output/playwright/power-background-20261022/parameters-${width}.png` });
        await dialog.getByText('高级判稳参数（首次旁路测试可跳过）', { exact: true }).click();
        await dialog.getByRole('button', { name: '开始采集', exact: true }).click();
        const detected = await dialog.getByLabel('自动获取的功率计设置').innerText();
        if (!detected.includes('3000 W（设备返回，软件增益前）') || !detected.includes('滤波：设备未提供')) throw Error('Automatic settings missing');
        await dialog.getByLabel('已确认 0 A、无光输出且探头已冷却').check();
        await dialog.getByRole('button', { name: '学习本底 (180 s)', exact: true }).click();
        await dialog.getByRole('button', { name: '取消本底任务', exact: true }).waitFor();
        await page.screenshot({ path: `output/playwright/power-background-20261022/learning-${width}.png` });
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
  }
  return report;
}
