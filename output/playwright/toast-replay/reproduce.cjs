async (page) => {
  const toast = page.getByRole('status').filter({ hasText: '输出电压：12.00 V' });
  await page.getByRole('button', { name: '读取输出电压', exact: true }).click();
  await toast.waitFor({ state: 'visible' });
  await toast.waitFor({ state: 'hidden', timeout: 6000 });
  await page.getByRole('button', { name: '自动测试', exact: true }).click();
  await toast.waitFor({ state: 'visible', timeout: 2000 });
  console.log('CONFIRMED: original cleanup replays an expired voltage toast after navigation');
  await page.goto('http://localhost:1420/?demo=1&page=manual');
}
