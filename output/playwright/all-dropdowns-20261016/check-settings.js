async (page) => {
  const checks=[];const check=(name,pass,detail)=>{checks.push({name,pass,detail});if(!pass)throw Error(name+': '+JSON.stringify(detail));};
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:'系统设置',exact:true}).click();
  const marker=page.getByRole('combobox',{name:'自动测试功率实时曲线末端样式',exact:true});
  const line=page.getByRole('combobox',{name:'自动测试功率实时曲线线型',exact:true});
  const trail=page.getByRole('combobox',{name:'尾迹时长',exact:true});
  for(const [width,height] of [[1920,1080],[1366,768]]){
    await page.setViewportSize({width,height});
    for(const theme of ['深色','浅色']){
      await page.getByRole('button',{name:theme,exact:true}).click();
      for(const [trigger,count] of [[marker,9],[line,3],[trail,4]]){
        await trigger.scrollIntoViewIfNeeded();const before=await trigger.boundingBox();await trigger.click();
        check(`${width}-${theme}-${await trigger.getAttribute('aria-label')}-options`,await page.getByRole('option').count()===count);
        const menu=await page.getByRole('listbox').boundingBox();
        check(`${width}-${theme}-menu-in-viewport`,menu.y>=0&&menu.x>=0&&menu.x+menu.width<=width+1&&menu.y+menu.height<=height+1,menu);
        check(`${width}-${theme}-trigger-stable`,JSON.stringify(before)===JSON.stringify(await trigger.boundingBox()));
        await page.keyboard.press('Escape');await page.getByRole('listbox').waitFor({state:'hidden'});
      }
      await marker.click();await page.waitForFunction(()=>document.activeElement?.getAttribute('role')==='option');await page.keyboard.press('End');await page.keyboard.press('Enter');
      await page.waitForFunction(()=>document.activeElement?.getAttribute('aria-label')==='自动测试功率实时曲线末端样式');
      check(`${width}-${theme}-keyboard-selection`,(await marker.innerText()).includes('小火龙'));
      await marker.click();await page.waitForFunction(()=>document.querySelector('[role=option][aria-selected=true]')===document.activeElement);
      check(`${width}-${theme}-last-option-visible`,await page.getByRole('option',{name:'小火龙',exact:true}).evaluate(e=>{const r=e.getBoundingClientRect(),m=e.parentElement.getBoundingClientRect();return r.top>=m.top&&r.bottom<=m.bottom}));
      await page.screenshot({path:`output/playwright/all-dropdowns-20261016/settings-${theme}-${width}.png`});
      await page.keyboard.press('Escape');await page.getByRole('listbox').waitFor({state:'hidden'});
      await marker.click();await page.getByRole('button',{name:'系统设置',exact:true}).click();await page.getByRole('listbox').waitFor({state:'hidden'});
      check(`${width}-${theme}-outside-click-closes`,await page.getByRole('listbox').count()===0);
      check(`${width}-${theme}-no-page-overflow`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    }
  }
  await page.getByRole('switch',{name:'曲线尾迹',exact:true}).click();check('trail-disabled',await trail.isDisabled());
  await page.getByRole('switch',{name:'曲线尾迹',exact:true}).click();await trail.click();await page.getByRole('option',{name:'15 秒',exact:true}).click();
  await line.click();await page.getByRole('option',{name:'虚线',exact:true}).click();
  check('settings-persisted',await page.evaluate(()=>localStorage.getItem('power-test.latest-point-marker')==='dinosaur'&&localStorage.getItem('power-test.automatic-curve-line-style')==='dashed'),await page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([k])=>k.includes('curve')||k.includes('marker')))));
  await page.getByRole('button',{name:'深色',exact:true}).click();
  for(const accent of ['极光蓝','森林绿','鸢尾紫','玫瑰','石墨','经典橙']){
    await page.getByRole('radio',{name:accent,exact:true}).locator('..').click();await marker.click();
    check('theme-'+accent,await page.getByRole('listbox').evaluate(e=>getComputedStyle(e).backgroundColor!=='rgba(0, 0, 0, 0)'));
    await page.keyboard.press('Escape');await page.getByRole('listbox').waitFor({state:'hidden'});
  }
  return checks;
}
