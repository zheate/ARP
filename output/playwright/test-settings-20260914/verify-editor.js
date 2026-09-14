async (page) => {
  if (await page.getByRole('button', { name: '停止', exact: true }).isEnabled()) {
    await page.getByRole('button', { name: '停止', exact: true }).click();
    await page.getByRole('button', { name: '测试下一台', exact: true }).click();
  }
  const dialog = page.getByRole('dialog', { name: '编辑测试设置' });
  if (!(await dialog.count())) await page.getByRole('button', { name: '编辑', exact: true }).click();
  await dialog.getByRole('combobox').click();
  if (await page.getByRole('listbox').count()) {
    await page.keyboard.press('Escape');
    if (!(await dialog.isVisible()) || await page.getByRole('listbox').count()) throw new Error('Dropdown Escape failed');
  }
  const initial = await dialog.getByRole('textbox').evaluateAll(nodes => nodes.map(n => n.value));
  await dialog.getByRole('textbox').nth(0).fill('4');
  await dialog.getByRole('textbox').nth(6).fill('977');
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  const baseline = await page.locator('.sample-nameplate').boundingBox();
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  const restored = await dialog.getByRole('textbox').evaluateAll(nodes => nodes.map(n => n.value));
  if (JSON.stringify(initial) !== JSON.stringify(restored)) throw new Error('Cancel changed configuration');
  const opened = await page.locator('.sample-nameplate').boundingBox();
  if (JSON.stringify(baseline) !== JSON.stringify(opened)) throw new Error('Dialog shifted page');
  await dialog.getByRole('textbox').nth(0).fill('4');
  await dialog.getByRole('textbox').nth(6).fill('977');
  await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
  await dialog.waitFor({ state: 'detached' });
  if (!(await page.getByRole('button', { name: '保存设置', exact: true }).isDisabled())) throw new Error('Save did not clear dirty');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  if (await dialog.getByRole('textbox').nth(0).inputValue() !== '4' || await dialog.getByRole('textbox').nth(6).inputValue() !== '977') throw new Error('Both groups did not save');
  await dialog.getByRole('combobox').click();
  const closedBox = await dialog.boundingBox();
  await page.getByRole('option', { name: '增加停留点', exact: true }).click();
  if (!(await dialog.isVisible())) throw new Error('Script selection closed dialog');
  await dialog.getByRole('button', { name: '保存设置', exact: true }).click();
  await dialog.waitFor({ state: 'detached' });
  if (!(await page.getByRole('combobox').textContent()).includes('增加停留点')) throw new Error('Script did not reach main selector');
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'detached' });
  const after = await page.locator('.sample-nameplate').boundingBox();
  if (JSON.stringify(baseline) !== JSON.stringify(after)) throw new Error('Closing shifted page');
  console.log(JSON.stringify({ cancelPreservesConfiguration: true, bothGroupsSaved: true, pagePositionStable: true, dropdownEscape: true, scriptSelectionSaved: true, dialogEscape: true, dialogBox: closedBox }));
}
