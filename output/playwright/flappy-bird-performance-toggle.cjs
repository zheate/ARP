async (page) => {
  if (!await page.evaluate(() => Boolean(window.__birdProfile))) throw new Error('Run flappy-bird-performance.cjs first to install counters');
  const session = await page.context().newCDPSession(page);
  await session.send('Performance.enable');
  const metrics = async () => Object.fromEntries((await session.send('Performance.getMetrics')).metrics.map(({ name, value }) => [name, value]));
  const results = [];
  await page.bringToFront();
  for (const mode of ['paused', 'running', 'paused', 'running', 'paused', 'running']) {
    await page.goto('http://localhost:1420/?demo=1&run=1');
    await page.bringToFront();
    if (mode === 'paused') await page.addStyleTag({ content: '.chart-flappy-bird__frames { animation-play-state: paused !important; }' });
    await page.getByRole('button', { name: '开始', exact: true }).click();
    await page.waitForTimeout(1000);
    if (await page.evaluate(() => document.hidden)) throw new Error('Benchmark page is hidden');
    await page.evaluate(() => { window.__birdProfile = { commits: 0, raf: 0, clears: {} }; });
    const before = await metrics();
    await page.waitForTimeout(6000);
    const after = await metrics();
    const counts = await page.evaluate(() => window.__birdProfile);
    results.push({ mode, duration: 6000, counts, scriptMs: (after.ScriptDuration - before.ScriptDuration) * 1000, taskMs: (after.TaskDuration - before.TaskDuration) * 1000, layoutMs: (after.LayoutDuration - before.LayoutDuration) * 1000, styleMs: (after.RecalcStyleDuration - before.RecalcStyleDuration) * 1000 });
    await page.getByRole('button', { name: '停止', exact: true }).click();
  }
  await session.detach();
  await page.goto('http://localhost:1420/?demo=1');
  return { results, means: Object.fromEntries(['paused', 'running'].map((mode) => {
    const group = results.filter((result) => result.mode === mode);
    return [mode, Object.fromEntries(['scriptMs', 'taskMs', 'layoutMs', 'styleMs'].map((key) => [key, group.reduce((sum, result) => sum + result[key], 0) / group.length]))];
  })) };
}
