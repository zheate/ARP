async (page) => {
  const results = [];
  for (const width of [1920, 1440, 1366, 1100, 900]) {
    await page.setViewportSize({ width, height: 900 });
    const strip = page.locator('.sample-nameplate');
    const controls = page.locator('.sample-nameplate__script-controls');
    const boxes = await controls.locator(':scope > *').evaluateAll(nodes => nodes.map(n => { const b = n.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; }));
    if (boxes.some(b => Math.abs(b.y - boxes[0].y) > 1 || Math.abs(b.height - boxes[0].height) > 1)) throw new Error('Toolbar baselines differ at ' + width);
    if (await strip.evaluate(n => n.scrollWidth > n.clientWidth)) throw new Error('Toolbar overflow at ' + width);
    const before = await strip.boundingBox();
    await page.screenshot({ path: 'output/playwright/test-settings-20260914/toolbar-' + width + '.png' });
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '编辑测试设置' });
    await dialog.getByRole('combobox').click();
    await page.getByRole('option', { name: '标准测试', exact: true }).click();
    const box = await dialog.boundingBox();
    if (box.x < 0 || box.x + box.width > width + 1 || box.y < 0 || box.y + box.height > 901) throw new Error('Dialog outside viewport at ' + width);
    const cards = await dialog.locator('.power-settings-section').evaluateAll(nodes => nodes.map(n => { const b = n.getBoundingClientRect(); return { y: b.y, width: b.width, height: b.height }; }));
    if (Math.abs(cards[0].y - cards[1].y) > 1 || Math.abs(cards[0].width - cards[1].width) > 1 || Math.abs(cards[0].height - cards[1].height) > 1) throw new Error('Cards misaligned at ' + width);
    const opened = await strip.boundingBox();
    if (JSON.stringify(before) !== JSON.stringify(opened)) throw new Error('Page shifted at ' + width);
    const withoutDropdown = await dialog.boundingBox();
    await dialog.getByRole('combobox').click();
    const withDropdown = await dialog.boundingBox();
    if (JSON.stringify(withoutDropdown) !== JSON.stringify(withDropdown)) throw new Error('Dropdown resized dialog');
    await page.keyboard.press('Escape');
    await page.screenshot({ path: 'output/playwright/test-settings-20260914/editor-' + width + '.png' });
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'detached' });
    results.push({ width, toolbarAligned: true, cardsAligned: true, noOverflow: true, noLayoutShift: true });
  }
  await page.getByRole('button', { name: '电源信息，点击打开设置', exact: true }).click();
  const power = page.getByRole('dialog', { name: '电源设置' });
  const fields = power.locator('.power-settings-section--automatic').getByRole('textbox');
  if (await fields.first().inputValue() !== '4') throw new Error('Power dialog did not share saved script parameters');
  await page.keyboard.press('Escape');
  await page.getByRole('textbox', { name: '产品 SN', exact: true }).fill('UI-CHECK');
  const save = page.getByRole('button', { name: '保存设置', exact: true });
  if (!(await save.isEnabled())) throw new Error('Moved save button did not enable');
  await save.click();
  if (!(await save.isDisabled())) throw new Error('Moved save button did not save');
  return { layouts: results, originalPowerEditorShared: true, movedSaveButton: true };
}
