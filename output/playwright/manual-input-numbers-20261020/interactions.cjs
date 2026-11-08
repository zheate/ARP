async (page) => {
  const checks = [];
  const check = (name, passed, detail) => {
    checks.push({ name, passed: !!passed, detail });
    if (!passed) throw new Error(name + ': ' + JSON.stringify(detail));
  };
  await page.setViewportSize({ width: 1180, height: 960 });
  await page.goto('http://localhost:1420/?demo=1&run=1&page=manual');
  await page.evaluate(() => localStorage.setItem('power-test.theme', 'light'));
  await page.reload();
  await page.getByRole('button', { name: '开启输出', exact: true }).click();
  const current = page.locator('#manual-psu-current');
  const voltage = page.locator('#manual-psu-voltage');
  await current.fill('2');
  await current.press('ArrowUp');
  check('current-up-arrow', await current.inputValue() === '3');
  await current.press('ArrowRight');
  check('current-tenth-step', await current.inputValue() === '3.1');
  await current.press('Control+Enter');
  check('confirm-keeps-focus', await current.evaluate(el => el === document.activeElement));
  await voltage.fill('22.5');
  await voltage.press('ArrowUp');
  check('voltage-tenth-step', await voltage.inputValue() === '22.6');
  await voltage.locator('..').getByRole('button', { name: '减少数值', exact: true }).click();
  check('voltage-step-button', await voltage.inputValue() === '22.5');
  for (const locator of [current, voltage]) {
    for (const value of ['0', '12.345678']) {
      await locator.fill(value);
      await locator.press('Tab');
      check(`${await locator.getAttribute('id')}-${value}-preserved`, await locator.inputValue() === value);
      const fit = await locator.evaluate(el => {
        const css = getComputedStyle(el);
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        ctx.font = `${css.fontWeight} ${css.fontSize} ${css.fontFamily}`;
        const available = el.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight);
        const textWidth = ctx.measureText(el.value).width;
        return { available, textWidth, fits: textWidth <= available };
      });
      check(`${await locator.getAttribute('id')}-${value}-fits`, fit.fits, fit);
    }
  }
  await voltage.fill('22');
  await current.fill('2');
  await current.press('End');
  await page.locator('.manual-device-card--power').screenshot({ path: '/Users/zh/Documents/test/ARP/output/playwright/manual-input-numbers-20261020/after-focused-light-1180.png' });
  return { checks, browserOnly: true, hardwareAccess: false };
}
