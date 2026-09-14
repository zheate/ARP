async (page) => {
  const results = [];
  if (await page.getByRole('dialog').count()) await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => {
    const root = document.documentElement;
    root.classList.remove('dark');
    root.dataset.theme = 'light';
    root.style.colorScheme = 'light';
  });
  for (const name of ['电源', '功率计', '光谱仪']) {
    await page.getByRole('button', { name: name + '信息，点击打开设置', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: name + '设置', exact: true });
    const fonts = await dialog.evaluate(n => ({
      title: getComputedStyle(n.querySelector('h2')).fontSize,
      label: getComputedStyle(n.querySelector('[data-slot="label"]')).fontSize,
      input: n.querySelector('[data-slot="input"]') ? getComputedStyle(n.querySelector('[data-slot="input"]')).fontSize : null,
      button: getComputedStyle(n.querySelector('[data-slot="button"]')).fontSize,
      horizontalOverflow: n.scrollWidth > n.clientWidth,
    }));
    if (fonts.title !== '20px' || fonts.label !== '16px' || (fonts.input !== null && fonts.input !== '16px') || fonts.button !== '16px' || fonts.horizontalOverflow) throw new Error(JSON.stringify({name, fonts}));
    await page.screenshot({ path: 'output/playwright/test-settings-20260914/larger-fonts-' + name + '.png', animations: 'disabled' });
    results.push({name, ...fonts});
    await page.keyboard.press('Escape');
  }
  return results;
}
