// Run with Playwright CLI run-code while the manual power settings dialog is open in ?demo=1.
async (page) => {
  const dialog = page.getByRole('dialog');
  const results = [];
  for (const controller of ['CH341 I²C', 'TDK RS232']) {
    await dialog.getByRole('combobox', { name: '控制器', exact: true }).click();
    await page.getByRole('option', { name: controller, exact: true }).click();
    for (const [width, height] of [[1440, 900], [1180, 720], [760, 600]]) {
      await page.setViewportSize({ width, height });
      const boxes = await page.getByText('硬件配置与读取', { exact: true }).evaluate(el => {
        const card = el.parentElement.parentElement;
        const container = card.parentElement;
        const dialog = card.closest('[role="dialog"]');
        const rect = dialog.getBoundingClientRect();
        return { card: card.getBoundingClientRect().width, container: container.getBoundingClientRect().width,
          sections: container.children.length, dialogWidth: rect.width, x: rect.x, right: rect.right,
          overflow: dialog.scrollWidth > dialog.clientWidth };
      });
      if (boxes.sections !== 1 || boxes.card / boxes.container < 0.99) throw new Error('Single settings section leaves an empty second column');
      if (boxes.dialogWidth > 706 || boxes.x < 0 || boxes.right > width || boxes.overflow) throw new Error('Dialog sizing regression');
      results.push({ controller, width, ...boxes });
      await page.screenshot({ path: `output/playwright/manual-power-${controller.startsWith('CH') ? 'ch341' : 'tdk'}-${width}.png` });
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  const before = await dialog.boundingBox();
  const resource = dialog.getByRole('combobox', { name: 'TDK 串口', exact: true });
  await resource.click();
  if (!await dialog.getByRole('listbox').isVisible()) throw new Error('Resource dropdown did not open');
  if (JSON.stringify(before) !== JSON.stringify(await dialog.boundingBox())) throw new Error('Dropdown moved dialog');
  await page.keyboard.press('Escape');
  if (!await dialog.isVisible() || await dialog.getByRole('listbox').count()) throw new Error('Escape did not close dropdown first');
  const save = dialog.getByRole('button', { name: '保存设置', exact: true });
  const saveBefore = await save.boundingBox();
  await dialog.getByRole('button', { name: '识别', exact: true }).click();
  if (JSON.stringify(saveBefore) !== JSON.stringify(await save.boundingBox())) throw new Error('Identify moved footer');
  await page.keyboard.press('Escape');
  if (await dialog.count()) throw new Error('Escape did not close dialog');
  await page.getByRole('button', { name: '自动测试', exact: true }).click();
  await page.getByRole('button', { name: '电源信息，点击打开设置', exact: true }).click();
  const automatic = await dialog.locator('.power-settings-layout').evaluate(el => ({ count: el.children.length, columns: getComputedStyle(el).gridTemplateColumns.split(' ').length }));
  if (automatic.count !== 2 || automatic.columns !== 2) throw new Error('Automatic power layout lost its two sections');
  await page.screenshot({ path: 'output/playwright/manual-power-auto-regression.png' });
  console.log(JSON.stringify({ results, automatic, interactions: 'PASS' }, null, 2));
}
