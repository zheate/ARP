async (page) => {
  await page.evaluate(() => {
    document.documentElement.dataset.theme = 'light';
    document.documentElement.dataset.accent = 'rose';
    document.documentElement.classList.remove('dark');
  });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const chart = page.getByRole('img', {name:'光谱曲线',exact:true});
  const checkbox = page.getByRole('checkbox', {name:'光谱曲线平滑',exact:true});
  const before = await chart.evaluate(canvas => canvas.toDataURL());
  if (!await checkbox.isChecked()) throw new Error('Smoothing must be enabled by default');
  await checkbox.uncheck();
  await page.waitForFunction(before => document.querySelector('canvas[aria-label="光谱曲线"]').toDataURL() !== before, before);
  const linear = await chart.evaluate(canvas => canvas.toDataURL());
  await checkbox.check();
  await page.waitForFunction(before => document.querySelector('canvas[aria-label="光谱曲线"]').toDataURL() === before, before);
  await page.getByRole('dialog').screenshot({path:'output/playwright/spectrum-smoothing/settings-dialog.png',animations:'disabled'});
  return {defaultEnabled:true, disablingChangesCurve:before !== linear, reEnablingRestoresCurve:true};
}
