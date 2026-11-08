async (page) => {
  if(!page.url().startsWith('http://127.0.0.1:1432/?demo=1')||await page.evaluate(()=>!!window.__TAURI_INTERNALS__))throw Error('Requires isolated browser demo');
  await page.emulateMedia({reducedMotion:'reduce'});
  const checks=[];
  const check=(name,pass,detail)=>{checks.push({name,pass,detail});if(!pass)throw Error(name+': '+JSON.stringify(detail));};
  if(await page.getByRole('dialog').count())await page.getByRole('button',{name:'关闭设置弹窗',exact:true}).click();
  for(const [width,height] of [[1920,1080],[1366,768]]){
    await page.setViewportSize({width,height});
    for(const [device,resource] of [['电源','TDK 串口'],['功率计','串口资源'],['光谱仪','设备资源']]){
      await page.getByRole('button',{name:device+'信息，点击打开设置',exact:true}).click();
      const dialog=page.getByRole('dialog');const trigger=dialog.getByRole('combobox',{name:resource,exact:true});
      if(await trigger.isDisabled()){
        await dialog.getByRole('button',{name:device==='电源'?'安全断开':'停止采集',exact:true}).click();
        await page.waitForFunction(name=>[...document.querySelectorAll('[role=dialog] [role=combobox]')].find(e=>e.getAttribute('aria-label')===name||document.getElementById(e.getAttribute('aria-labelledby'))?.textContent===name)?.disabled===false,resource);
      }
      const before=await dialog.boundingBox();const footer=await dialog.locator('.device-settings-dialog__footer').boundingBox();
      await trigger.click();await dialog.getByRole('listbox').waitFor();
      const menu=await dialog.getByRole('listbox').boundingBox();
      check(`${width}-${device}-anchored-menu`,menu.x>=before.x&&menu.y>=before.y&&menu.y+menu.height<=before.y+before.height+1,menu);
      check(`${width}-${device}-stable-dialog`,JSON.stringify(before)===JSON.stringify(await dialog.boundingBox()));
      await page.screenshot({path:`output/github-publish-all-20261016/${device}-dark-${width}.png`});
      await page.keyboard.press('Escape');await dialog.getByRole('listbox').waitFor({state:'hidden'});
      check(`${width}-${device}-escape-dropdown-first`,await dialog.isVisible());
      await trigger.click();await dialog.getByRole('option').last().click();await dialog.getByRole('listbox').waitFor({state:'hidden'});
      check(`${width}-${device}-selection-keeps-dialog`,await dialog.isVisible());
      if(width===1366){
        await dialog.getByRole('button',{name:'识别',exact:true}).click();
        await page.waitForFunction(()=>![...document.querySelectorAll('[role=dialog] button')].some(e=>e.textContent.includes('识别中')));
        check(`${width}-${device}-refresh-footer-stable`,JSON.stringify(footer)===JSON.stringify(await dialog.locator('.device-settings-dialog__footer').boundingBox()));
        check(`${width}-${device}-save-label-stable`,(await dialog.getByRole('button',{name:'保存设置',exact:true}).count())===1);
      }
      await trigger.click();await page.getByRole('option').first().focus();await page.keyboard.press('Escape');await dialog.getByRole('listbox').waitFor({state:'hidden'});await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
      check(`${width}-${device}-escape-dialog-second`,await page.getByRole('dialog').count()===0);
    }
  }
  return checks;
}
