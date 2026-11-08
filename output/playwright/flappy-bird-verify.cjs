async (page) => {
  const failures = [];
  page.on('pageerror', (error) => failures.push(error.message));
  const settlePage = async (name) => page.waitForFunction((name) => {
    const panel = document.querySelector(`[data-page="${name}"]`);
    return panel && panel.getAnimations({ subtree: true }).every((animation) =>
      animation.effect.getComputedTiming().iterations === Infinity || animation.playState === 'finished');
  }, name);
  await page.reload();
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  await settlePage('settings');
  const marker = page.getByRole('combobox', { name: '自动测试功率实时曲线末端样式', exact: true });
  const preview = page.getByRole('img', { name: '外观示例功率曲线', exact: true });
  const results = [];
  await preview.screenshot({ path: 'output/playwright/flappy-bird-before-selection.png' });
  const before = await preview.boundingBox();
  for (const style of ['point', 'paper-plane', 'paw', 'comet', 'ecg', 'flappy-bird']) {
    await marker.selectOption(style);
    if (await marker.inputValue() !== style) throw new Error(`Cannot select ${style}`);
  }
  const after = await preview.boundingBox();
  if (JSON.stringify(before) !== JSON.stringify(after)) throw new Error(`Marker selection moved the chart: ${JSON.stringify({ before, after })}`);

  const birdBounds = async (canvas) => canvas.evaluate((element) => {
    const { width, height } = element;
    const pixels = element.getContext('2d').getImageData(0, 0, width, height).data;
    const counts = { yellow: 0, white: 0, orange: 0 };
    let minX = width, minY = height, maxX = 0, maxY = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      const [r, g, b, a] = pixels.subarray(index, index + 4);
      const color = a === 255 && r === 249 && g === 212 && b === 59 ? 'yellow'
        : a === 255 && r === 255 && g === 253 && b === 242 ? 'white'
        : a === 255 && r === 255 && g === 155 && b === 61 ? 'orange' : null;
      if (!color) continue;
      counts[color]++;
      const x = index / 4 % width, y = Math.floor(index / 4 / width);
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
    return { width, height, counts, minX, minY, maxX, maxY };
  });
  const waitForBird = async (label) => page.waitForFunction((label) => {
    const canvas = [...document.querySelectorAll(`canvas[aria-label="${label}"]`)].at(-1);
    if (!canvas || !canvas.width || !canvas.height) return false;
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    for (let index = 0; index < pixels.length; index += 4) {
      if (pixels[index] === 249 && pixels[index + 1] === 212 && pixels[index + 2] === 59 && pixels[index + 3] === 255) return true;
    }
    return false;
  }, label);
  for (const width of [1440, 1180]) {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 720 });
    for (const theme of [{ id: 'dark', label: '深色' }, { id: 'light', label: '浅色' }]) {
      await page.getByRole('button', { name: theme.label, exact: true }).click();
      await settlePage('settings');
      await waitForBird('外观示例功率曲线');
      await preview.screenshot({ path: `output/playwright/flappy-bird-preview-${theme.id}-${width}.png` });
      const previewBird = await birdBounds(preview);
      await page.getByRole('button', { name: '自动测试', exact: true }).click();
      const chart = page.getByRole('img', { name: '功率实时曲线', exact: true });
      await settlePage('automatic');
      await waitForBird('功率实时曲线');
      await chart.screenshot({ path: `output/playwright/flappy-bird-live-${theme.id}-${width}.png` });
      const liveBird = await birdBounds(chart);
      for (const bird of [previewBird, liveBird]) {
        if (bird.counts.yellow < 10 || bird.counts.white < 5 || bird.counts.orange < 3) throw new Error(`Bird not rendered: ${JSON.stringify(bird)}`);
        if (bird.maxX >= bird.width - 1 || bird.minY <= 0 || bird.maxY >= bird.height - 1) throw new Error(`Clipped bird: ${JSON.stringify(bird)}`);
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      if (overflow) throw new Error(`Horizontal overflow at ${width}`);
      await page.screenshot({ path: `output/playwright/flappy-bird-dashboard-${theme.id}-${width}.png` });
      results.push({ width, theme: theme.id, previewBird, liveBird, overflow });
      await page.getByRole('button', { name: '系统设置', exact: true }).click();
      await settlePage('settings');
    }
  }
  await page.reload();
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  const restored = await marker.inputValue();
  const saved = await page.evaluate(() => localStorage.getItem('power-test.latest-point-marker'));
  if (restored !== 'flappy-bird' || saved !== 'flappy-bird') throw new Error(`Preference not restored: ${restored}/${saved}`);
  if (failures.length) throw new Error(JSON.stringify(failures));
  await page.getByRole('button', { name: '深色', exact: true }).click();
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('button', { name: '自动测试', exact: true }).click();
  return { markerStyles: 6, layoutStable: true, restored, saved, failures, results };
}
