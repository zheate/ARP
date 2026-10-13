async (page) => {
  const results = [];
  const audit = async (id, theme, width) => {
    const geometry = await page.evaluate(() => {
      const visible = el => el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
      const selectors = '.device-settings-dialog, .power-settings-section, .device-settings-form, .manual-device-card, .shipping-report-path-row, .appearance-preview, .sample-nameplate';
      const overflow = [...document.querySelectorAll(selectors)].filter(visible).filter(el => el.scrollWidth > el.clientWidth + 1).map(el => ({class:el.className, client:el.clientWidth, scroll:el.scrollWidth}));
      const box = el => {const r=el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height};};
      const pairs = [...document.querySelectorAll('.power-settings-parameter-control')].map(el => ({input:box(el.querySelector('input')), button:box(el.querySelector('[data-slot="button"]'))}));
      const paths = [...document.querySelectorAll('.shipping-report-path-row')].map(el => ({input:box(el.querySelector('input')),button:box(el.querySelector('button'))}));
      const cards = [...document.querySelectorAll('.manual-device-card')].map(box);
      const folder = document.querySelector('.sample-nameplate__output');
      return {overflow,pairs,paths,cards,folder:folder?{input:box(folder.querySelector('input')),button:box(folder.querySelector('button'))}:null,documentOverflow:document.documentElement.scrollWidth>innerWidth};
    });
    if (geometry.overflow.length || geometry.documentOverflow) throw new Error(JSON.stringify({id,theme,width,...geometry}));
    for (const pair of geometry.pairs) if (Math.abs(pair.input.x-pair.button.x)>1 || Math.abs(pair.input.w-pair.button.w)>1) throw new Error('Parameter and action column mismatch');
    if (geometry.folder && geometry.folder.input.h !== geometry.folder.button.h) throw new Error('Directory control height mismatch');
    if (geometry.paths.length>1 && Math.abs(geometry.paths[0].input.w-geometry.paths[1].input.w)>1) throw new Error('Path widths differ');
    if (geometry.cards.length===3 && geometry.cards[0].y===geometry.cards[1].y && geometry.cards.some(c=>c.h!==geometry.cards[0].h || c.y!==geometry.cards[0].y)) throw new Error('Device cards misaligned');
    results.push({id,theme,width,...geometry});
    await page.screenshot({path:`output/playwright/ui-coordination/after-${id}-${theme}-${width}.png`});
  };
  for (const [theme,label] of [['dark','深色'],['light','浅色']]) {
    await page.getByRole('button',{name:'系统设置',exact:true}).click();
    await page.getByRole('button',{name:label,exact:true}).click();
    for (const [width,height] of [[1440,900],[1180,720],[760,700]]) {
      await page.setViewportSize({width,height});
      for (const [name,id] of [['自动测试','automatic'],['手动配置','manual'],['PD 采集','pd'],['数据浏览','data'],['出货报告','shipping'],['系统设置','settings']]) {
        await page.getByRole('button',{name,exact:true}).click();
        await page.waitForTimeout(350);
        await audit(id,theme,width);
      }
      await page.getByRole('button',{name:'自动测试',exact:true}).click();
      for (const [name,id] of [['电源','power'],['功率计','meter'],['光谱仪','spectrum']]) {
        await page.getByRole('button',{name:name+'信息，点击打开设置',exact:true}).click();
        await page.waitForTimeout(150);
        await audit(id,theme,width);
        const dialog = page.getByRole('dialog');
        const bounds=await dialog.boundingBox();
        if(bounds.x<0 || bounds.y<0 || bounds.x+bounds.width>width+1 || bounds.y+bounds.height>height+1) throw new Error('Dialog out of viewport');
        await page.keyboard.press('Escape');
      }
    }
  }
  return {status:'PASS',cases:results.length,results};
}
