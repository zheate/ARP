async (page) => {
  const failures = [];
  page.on('pageerror', (error) => failures.push(error.message));
  await page.goto('http://localhost:1420/?demo=1');
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  const selector = page.getByRole('combobox', { name: '自动测试功率实时曲线末端样式', exact: true });
  await selector.selectOption('flappy-bird');
  const bird = page.locator('.chart-flappy-bird');
  await bird.waitFor();
  const sprite = page.locator('.chart-flappy-bird__frames');
  const poses = await sprite.evaluate((canvas) => {
    const ctx = canvas.getContext('2d'), width = canvas.width / 4, height = canvas.height;
    const frames = Array.from({ length: 4 }, (_, index) => [...ctx.getImageData(index * width, 0, width, height).data]);
    const equal = (left, right) => left.every((value, index) => value === right[index]);
    if (!equal(frames[0], frames[2]) || equal(frames[0], frames[1]) || equal(frames[1], frames[3])) throw new Error('Wing poses did not change');
    for (let index = 1; index < 4; index++) for (let y = 0; y < height; y++) for (let x = Math.round(width / 3); x < width; x++) {
      const pixel = (y * width + x) * 4;
      if (!equal(frames[0].slice(pixel, pixel + 4), frames[index].slice(pixel, pixel + 4))) throw new Error('Animation changed the body or eye');
    }
    return { frameWidth: width, height, distinctWingPoses: 3 };
  });
  const transformBefore = await sprite.evaluate((el) => getComputedStyle(el).transform);
  await page.waitForFunction((before) => getComputedStyle(document.querySelector('.chart-flappy-bird__frames')).transform !== before, transformBefore);

  const renders = [];
  for (const width of [1440, 1180]) {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 720 });
    for (const theme of [{ id: 'dark', label: '深色' }, { id: 'light', label: '浅色' }]) {
      await page.getByRole('button', { name: theme.label, exact: true }).click();
      await page.waitForFunction(() => document.querySelector('[data-page="settings"]').getAnimations({ subtree: true }).every((animation) => animation.effect.getComputedTiming().iterations === Infinity || animation.playState === 'finished'));
      await page.getByRole('img', { name: '外观示例功率曲线', exact: true }).locator('..').screenshot({ path: `output/playwright/flappy-bird-flapping-preview-${theme.id}-${width}.png` });
      await page.getByRole('button', { name: '自动测试', exact: true }).click();
      await page.waitForFunction(() => document.querySelector('[data-page="automatic"]').getAnimations({ subtree: true }).every((animation) => animation.effect.getComputedTiming().iterations === Infinity || animation.playState === 'finished'));
      const box = await bird.boundingBox();
      const chart = await page.getByRole('img', { name: '功率实时曲线', exact: true }).boundingBox();
      if (box.x < chart.x || box.y < chart.y || box.x + box.width > chart.x + chart.width || box.y + box.height > chart.y + chart.height) throw new Error(`Clipped bird at ${width}/${theme.id}: ${JSON.stringify({ box, chart })}`);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      if (overflow) throw new Error(`Overflow at ${width}`);
      await page.getByLabel('功率实时', { exact: true }).screenshot({ path: `output/playwright/flappy-bird-flapping-live-${theme.id}-${width}.png` });
      renders.push({ width, theme: theme.id, box, chart, overflow });
      await page.getByRole('button', { name: '系统设置', exact: true }).click();
    }
  }
  await page.getByRole('button', { name: '深色', exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 900 });
  for (const [index, time] of [50, 150, 250, 350].entries()) {
    await sprite.evaluate((el, time) => { const animation = el.getAnimations()[0]; animation.pause(); animation.currentTime = time; }, time);
    await page.getByRole('img', { name: '外观示例功率曲线', exact: true }).locator('..').screenshot({ path: `output/playwright/flappy-bird-wing-frame-${index}.png` });
  }
  await sprite.evaluate((el) => el.getAnimations()[0].play());
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const reduced = await sprite.evaluate((el) => ({ name: getComputedStyle(el).animationName, transform: getComputedStyle(el).transform }));
  if (reduced.name !== 'none' || reduced.transform !== 'none') throw new Error(`Reduced motion ignored: ${JSON.stringify(reduced)}`);
  await page.emulateMedia({ reducedMotion: 'no-preference' });

  await bird.evaluate((el) => { el.style.transform = 'translate(-200px, 0)'; });
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.chart-flappy-bird__frames')).animationPlayState === 'paused');
  await page.getByRole('button', { name: '自动测试', exact: true }).click();
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.chart-flappy-bird__frames')).animationPlayState === 'running');
  for (const style of ['point', 'paper-plane', 'paw', 'comet', 'ecg']) {
    await selector.selectOption(style);
    if (await bird.count() !== 0) throw new Error(`Animation retained after selecting ${style}`);
  }
  await selector.selectOption('flappy-bird');
  await page.reload();
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  if (await selector.inputValue() !== 'flappy-bird') throw new Error('Preference not restored');
  if (failures.length) throw new Error(JSON.stringify(failures));
  return { poses, transformChanged: true, reduced, offscreenPaused: true, restored: true, renders, failures };
}
