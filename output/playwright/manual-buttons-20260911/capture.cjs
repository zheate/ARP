async (page) => {
  if (page.url() !== 'http://127.0.0.1:1435/?demo=1') throw new Error('Demo preview required');
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.getByRole('button', { name: '急停', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).last().click();
  await page.getByRole('button', { name: '手动配置', exact: true }).click();
  const actions = page.locator('.manual-tdk-actions');
  for (const theme of ['dark', 'light']) {
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value;
      document.documentElement.classList.toggle('dark', value === 'dark');
    }, theme);
    await page.waitForTimeout(500);
    await actions.screenshot({ path: `output/playwright/manual-buttons-20260911/${theme}.png`, animations: 'disabled' });
    console.log(theme, await actions.locator('button').evaluateAll((buttons) => buttons.map((button) => {
      const style = getComputedStyle(button);
      return { text: button.textContent, color: style.color, background: style.backgroundColor, border: style.borderColor, shadow: style.boxShadow };
    })));
  }
  await page.evaluate(() => {
    document.documentElement.dataset.theme = 'dark';
    document.documentElement.classList.add('dark');
  });
}
