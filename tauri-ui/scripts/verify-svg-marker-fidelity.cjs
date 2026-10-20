// Compare all approved source poses with their vector contours at 4x size.
async (page) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto('http://localhost:1420/svg-gallery-fixture');
  await page.evaluate(() => document.body.style.zoom = '1');
  const metrics = await page.evaluate(async () => {
    const load = async path => { const image = new Image(); image.src = path; await image.decode(); return image; };
    const root = document.createElement('main');
    root.style.cssText = 'padding:24px;font:16px sans-serif;background:#f7f5ef;color:#222';
    root.innerHTML = '<h1>原图 / SVG · 每帧 4 倍显示</h1>';
    document.body.replaceChildren(root);
    const sources = [
      ['bee', 'bee-flap-four-frames-97043142efa1.png', [[32,72,600,600],[614,80,600,600],[25,671,600,600],[614,672,600,600]]],
      ['green-dinosaur', 'dino-walk-four-frames-9f95d27db3c1.png', [[8,496,310,310],[320,496,310,310],[636,496,310,310],[947,496,310,310]]],
      ['dinosaur', 'dino-red-tail-flame-aligned-four-frames-b7fb24b7b24a.png', [[8,496,310,310],[320,496,310,310],[636,496,310,310],[947,496,310,310]]],
    ];
    const metrics = [];
    for (const [style, file, crops] of sources) {
      const original = await load(`/chart-markers/${file}`), vector = await load(`/chart-markers/${style}-vector.svg`);
      const flame = style === 'dinosaur' ? await load('/chart-markers/dinosaur-flame-vector.svg') : null;
      const title = document.createElement('h2'); title.textContent = style; root.append(title);
      const row = document.createElement('div'); row.style.cssText = 'display:flex;gap:28px'; root.append(row);
      for (let frame = 0; frame < 4; frame++) {
        const [x,y,w,h] = crops[frame];
        const a = document.createElement('canvas'), b = document.createElement('canvas');
        a.width = a.height = b.width = b.height = 128;
        const ac = a.getContext('2d'), bc = b.getContext('2d');
        ac.scale(128 / w,128 / h); ac.translate(-x,-y);
        if (style === 'bee') { ac.beginPath(); ac.rect((frame % 2)*627,Math.floor(frame/2)*627,627,627); ac.clip(); }
        ac.drawImage(original,0,0,1254,1254);
        bc.drawImage(vector,frame*w,0,w,h,0,0,128,128);
        if (flame) bc.drawImage(flame,frame*w,0,w,h,0,0,128,128);
        const ap = ac.getImageData(0,0,128,128).data, bp = bc.getImageData(0,0,128,128).data;
        let union=0, intersect=0, error=0;
        for(let pixel=0;pixel<ap.length;pixel+=4) {
          const aa=ap[pixel+3]/255, ba=bp[pixel+3]/255;
          if(aa>.5 || ba>.5) union++;
          if(aa>.5 && ba>.5) intersect++;
          for(let channel=0;channel<3;channel++) error+=Math.abs(ap[pixel+channel]*aa-bp[pixel+channel]*ba);
        }
        const result={style,frame,silhouetteIoU:intersect/union,meanChannelError:error/(128*128*3)};
        if(result.silhouetteIoU<.96 || result.meanChannelError>6) throw Error(JSON.stringify(result));
        metrics.push(result);
        const pair=document.createElement('div');pair.style.cssText='display:grid;gap:8px';
        pair.append(`帧 ${frame+1} · 原图 / SVG`,a,b); row.append(pair);
      }
    }
    return metrics;
  });
  await page.screenshot({path:'output/playwright/svg-markers-20261020/original-vs-svg.png',fullPage:true});
  return metrics;
}
