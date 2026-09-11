async (page) => {
  const sn = page.getByRole('textbox', { name: '产品 SN', exact: true });
  const directory = page.getByRole('textbox', { name: '输出目录', exact: true });
  const start = page.getByRole('button', { name: '开始', exact: true });
  if (await start.isEnabled()) throw new Error('Empty SN should block start');
  await sn.fill('555');
  if (!(await start.isEnabled())) throw new Error('Typed SN still blocked by saved snapshot');
  if (await page.getByText('未就绪：请填写产品 SN', { exact: true }).count()) throw new Error('Stale SN message is still visible');
  await sn.fill('   ');
  if (await start.isEnabled()) throw new Error('Whitespace SN should block start');
  await sn.fill('555');
  const outputDir = await directory.inputValue();
  await directory.fill('');
  if (await start.isEnabled()) throw new Error('Cleared output directory should block start');
  await directory.fill(outputDir);
  if (!(await start.isEnabled())) throw new Error('Completed form should be startable');
  await start.click();
  const submitted = await page.evaluate(() => window.__snStartPayload);
  if (submitted?.sn !== '555' || submitted?.outputDir !== outputDir) throw new Error('Start did not send current form');
  return { staleSnBlockerResolved: true, requiredFieldsRechecked: true, startedWithoutSeparateSave: true, submittedSn: submitted.sn };
}
