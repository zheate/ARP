async (page) => {
  const cdp=await page.context().newCDPSession(page);
  await cdp.send('Network.enable');await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
  await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
  const results=[];
  for(let round=0;round<5;round++) for(const [phase,port] of [['before',1431],['after',1432]]){
    await page.goto(`http://127.0.0.1:${port}/?demo=1&run=1`,{waitUntil:'load'});
    await page.getByRole('textbox',{name:'产品 SN',exact:true}).waitFor();
    const result=await page.evaluate(()=>({readyMs:performance.now(),scripts:performance.getEntriesByType('resource').filter(r=>r.name.endsWith('.js')).map(r=>({name:r.name.split('/').at(-1),bytes:r.decodedBodySize})),css:performance.getEntriesByType('resource').filter(r=>r.name.endsWith('.css')).map(r=>({name:r.name.split('/').at(-1),bytes:r.decodedBodySize}))}));
    if(phase==='after' && result.scripts.some(s=>/^report-|^data-browser-page-/.test(s.name))) throw new Error('Deferred pages loaded on startup');
    results.push({phase,round,...result});
  }
  await cdp.send('Emulation.setCPUThrottlingRate',{rate:1});await cdp.detach();
  await page.getByRole('button',{name:'数据浏览',exact:true}).click();
  await page.getByPlaceholder('搜索产品 SN…').waitFor();
  return {status:'PASS',results};
}
