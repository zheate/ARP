async (page) => {
  await page.getByRole('button', { name: '系统设置', exact: true }).click();
  const toggle = page.getByRole('switch', { name: '数值变化提示' });
  if (await toggle.getAttribute('aria-checked') !== 'true') await toggle.click();
  await page.locator('label.appearance-palette__choice').filter({ hasText: '玫瑰' }).click();
  const results = [];
  for (const mode of [{ theme: 'dark', label: '深色', motion: 'no-preference' }, { theme: 'light', label: '浅色', motion: 'no-preference' }, { theme: 'dark', label: '深色', motion: 'reduce' }]) {
    await page.getByRole('button', { name: mode.label, exact: true }).click();
    await page.emulateMedia({ reducedMotion: mode.motion });
    const active = await page.evaluate(async () => {
      const readout = document.querySelector('.appearance-readout-example .numeric-readout');
      const digits = [...readout.children];
      const positions = digits.map(digit => ({ x: digit.getBoundingClientRect().x, width: digit.getBoundingClientRect().width }));
      const value = readout.textContent;
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => { observer.disconnect(); reject(new Error('Preview did not update')); }, 4000);
        const observer = new MutationObserver(() => {
          if (readout.textContent !== value) { clearTimeout(timer); observer.disconnect(); resolve(); }
        });
        observer.observe(readout, { childList: true, characterData: true, subtree: true });
      });
      const changedIndexes = digits.flatMap((digit, index) => digit.getAnimations().length ? [index] : []);
      const animation = digits.at(-1).getAnimations()[0];
      if (!animation) throw new Error('Changed digit has no highlight');
      animation.pause();
      animation.currentTime = 200;
      const style = getComputedStyle(digits.at(-1));
      return {
        value: readout.textContent,
        changedIndexes,
        duration: animation.effect.getTiming().duration,
        color: style.color,
        background: style.backgroundColor,
        textShadow: style.textShadow,
        positionsStable: digits.every((digit, index) => digit.getBoundingClientRect().x === positions[index].x && digit.getBoundingClientRect().width === positions[index].width),
        keyframes: animation.effect.getKeyframes(),
      };
    });
    if (JSON.stringify(active.changedIndexes) !== '[4]' || active.duration !== 700 || !active.positionsStable || active.background !== 'rgba(0, 0, 0, 0)' || active.textShadow === 'none') throw new Error(JSON.stringify(active));
    await page.locator('.appearance-readout-example').screenshot({ path: `output/playwright/digit-highlight-${mode.theme}-${mode.motion}.png` });
    const settled = await page.evaluate(async () => {
      const digit = document.querySelector('.appearance-readout-example .numeric-readout').lastElementChild;
      const animations = digit.getAnimations();
      animations.forEach(animation => animation.play());
      await Promise.all(animations.map(animation => animation.finished));
      return { background: getComputedStyle(digit).backgroundColor, animations: digit.getAnimations().length };
    });
    if (settled.background !== 'rgba(0, 0, 0, 0)' || settled.animations !== 0) throw new Error(JSON.stringify(settled));
    results.push({ ...mode, active, settled });
  }
  await toggle.click();
  const disabled = await page.locator('.appearance-readout-example .numeric-readout').evaluate(element => ({ value: element.textContent, animations: element.getAnimations({ subtree: true }).length, backgrounds: [...element.children].map(digit => getComputedStyle(digit).backgroundColor) }));
  if (disabled.value !== '0.860' || disabled.animations !== 0 || disabled.backgrounds.some(color => color !== 'rgba(0, 0, 0, 0)')) throw new Error(JSON.stringify(disabled));
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await toggle.click();
  return { results, disabled };
}
