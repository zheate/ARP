async (page) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const toggle = page.getByRole('switch', { name: '数值变化提示' });
  if (await toggle.getAttribute('aria-checked') !== 'true') await toggle.click();
  const result = await page.evaluate(async () => {
    const digit = document.querySelector('.appearance-readout-example .numeric-readout').lastElementChild;
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const initial = getComputedStyle(digit).color;
    const value = digit.textContent;
    await new Promise(resolve => {
      const observer = new MutationObserver(() => {
        if (digit.textContent !== value) { observer.disconnect(); resolve(); }
      });
      observer.observe(digit, { childList: true, characterData: true, subtree: true });
    });
    const animations = digit.getAnimations();
    animations.forEach(animation => { animation.pause(); animation.currentTime = 0; });
    return { reducedMotion, initial, highlighted: getComputedStyle(digit).color, animations: animations.map(animation => ({ timing: animation.effect.getTiming(), keyframes: animation.effect.getKeyframes() })) };
  });
  return result;
}
