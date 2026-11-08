// Isolated production dialogs: never sends real device commands.
async (page) => {
  const report = [];
  const base = 'http://127.0.0.1:1420/scripts/fixtures/power-background.html';
  const same = (a, b) => a && b && ['x','y','width','height'].every(k => Math.abs(a[k] - b[k]) < 0.5);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const width of [1440, 1180, 760]) {
    await page.setViewportSize({width, height: width === 760 ? 600 : 900});
    await page.goto(base);
    await page.evaluate(() => document.fonts.ready);
    const cards = await page.getByTestId('cards').boundingBox();
    for (const name of ['电源设置', '功率计设置', '光谱仪设置']) {
      await page.getByRole('button', {name, exact: true}).click();
      const dialog = page.getByRole('dialog');
      if (!same(cards, await page.getByTestId('cards').boundingBox())) throw Error('Cards moved on open');
      let count = 0;
      for (const select of await dialog.getByRole('combobox').all()) {
        if (!await select.isEnabled()) continue;
        await select.scrollIntoViewIfNeeded();
        const before = await dialog.boundingBox();
        await select.click();
        await dialog.getByRole('listbox').waitFor();
        if (!same(before, await dialog.boundingBox())) throw Error('Dropdown changed dialog size');
        await page.keyboard.press('Escape');
        if (await dialog.getByRole('listbox').count() || await dialog.count() !== 1) throw Error('Dropdown Escape failure');
        await select.click();
        const selected = dialog.getByRole('option', {selected: true});
        await (await selected.count() ? selected.first() : dialog.getByRole('option').first()).click();
        if (await dialog.getByRole('listbox').count() || await dialog.count() !== 1) throw Error('Option close failure');
        count++;
      }
      if (!count) throw Error('No selectors checked');
      if (name === '功率计设置') {
        for (const label of ['自动功率判稳策略', '测试容差预设', '复核本底 (20 s)', '设备响应 · 一次确认', '维护选项（通常无需修改）']) {
          if (await dialog.getByText(label, {exact: true}).count()) throw Error('Removed setting remains: ' + label);
        }
        const footer = dialog.locator('.device-settings-dialog__footer');
        const before = await footer.evaluate(el => ({text:el.textContent,box:el.getBoundingClientRect().toJSON()}));
        await dialog.getByRole('button', {name:'识别',exact:true}).click();
        const during = await footer.evaluate(el => ({text:el.textContent,box:el.getBoundingClientRect().toJSON()}));
        if (JSON.stringify(before) !== JSON.stringify(during)) throw Error('Detection changed footer');
        await page.waitForTimeout(700);
        await dialog.getByRole('button', {name:'开始采集',exact:true}).click();
        await dialog.getByLabel('已确认 0 A、无激光输出', {exact:true}).check();
        await dialog.getByRole('button', {name:'学习跳动量 (20 s)',exact:true}).click();
        await dialog.getByRole('button', {name:'取消学习',exact:true}).waitFor();
        await dialog.getByRole('button', {name:'取消学习',exact:true}).click();
        await dialog.getByRole('region', {name:'功率计跳动噪声'}).scrollIntoViewIfNeeded();
        await page.screenshot({path:`output/playwright/noise-sn-20s-20261023/dialog-${width}.png`});
      }
      const box = await dialog.boundingBox();
      if (!box || box.x < 0 || box.x + box.width > width + 0.5) throw Error('Clipped dialog');
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw Error('Horizontal overflow');
      await page.keyboard.press('Escape');
      if (await dialog.count()) throw Error('Dialog Escape failure');
      if (!same(cards, await page.getByTestId('cards').boundingBox())) throw Error('Cards moved after close');
      report.push({width,name,selectors:count,aligned:true});
    }
    await page.goto(base + '?learned=1');
    await page.getByRole('button',{name:'功率计设置',exact:true}).click();
    const saved = page.getByRole('dialog');
    await saved.getByRole('button',{name:'重新学习 (20 s)',exact:true}).waitFor();
    if (!(await saved.getByRole('region',{name:'功率计跳动噪声'}).innerText()).includes('已记住跳动量')) throw Error('Saved SN jump missing');
    await saved.getByRole('region',{name:'功率计跳动噪声'}).scrollIntoViewIfNeeded();
    await page.screenshot({path:`output/playwright/noise-sn-20s-20261023/saved-${width}.png`});
  }
  return report;
}
