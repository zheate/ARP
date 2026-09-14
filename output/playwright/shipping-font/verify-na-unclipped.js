async (page) => {
  const input = page.getByRole('spinbutton', {name:'Encircled Energy NA', exact:true});
  await input.fill('0.175');
  await input.press('Tab');
  const results=[];
  for (const width of [1280,1440]) {
    await page.setViewportSize({width,height:1000});
    await input.scrollIntoViewIfNeeded();
    results.push(await input.evaluate(el => {
      const style=getComputedStyle(el);
      const canvas=document.createElement('canvas');
      const ctx=canvas.getContext('2d'); ctx.font=style.font;
      const textWidth=ctx.measureText(el.value).width;
      const available=el.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight);
      if(textWidth>available || el.value!=='0.175') throw new Error('NA text does not fit');
      return {width:innerWidth,value:el.value,fieldWidth:el.getBoundingClientRect().width,textWidth,available,spinner:getComputedStyle(el,'::-webkit-inner-spin-button').webkitAppearance};
    }));
    await input.locator('..').screenshot({path:`output/playwright/shipping-font/na-unclipped-${width}.png`});
  }
  return results;
}
