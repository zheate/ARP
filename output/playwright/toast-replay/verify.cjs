async (page) => {
  const toast = page.getByRole('status').filter({ hasText: '输出电压：12.00 V' });
  const read = page.getByRole('button', { name: '读取输出电压', exact: true });
  const navigate = async () => {
    await page.getByRole('button', { name: '自动测试', exact: true }).click();
    await page.waitForTimeout(400);
    if (await toast.count()) throw new Error('Dismissed toast replayed on automatic page');
    await page.getByRole('button', { name: '手动配置', exact: true }).click();
    await read.waitFor({ state: 'visible' });
    await page.waitForTimeout(400);
    if (await toast.count()) throw new Error('Dismissed toast replayed on manual page');
  };
  await read.click();
  await toast.waitFor({ state: 'visible' });
  await toast.waitFor({ state: 'hidden', timeout: 6000 });
  await navigate();
  await navigate();
  await read.click();
  await toast.waitFor({ state: 'visible' });
  if (await toast.count() !== 1) throw new Error('New read replayed older notices');
  await toast.getByRole('button', { name: '关闭提示' }).click();
  await toast.waitFor({ state: 'hidden' });
  await navigate();
  return { autoDismissedToastStaysHidden: true, repeatedNavigationDoesNotReplay: true, newReadStillShowsOneToast: true, manuallyDismissedToastStaysHidden: true };
}
