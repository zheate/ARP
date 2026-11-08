// Hand-drawn 24 × 24 sprites. Integer pixels only, no smoothing or gradients.
function drawPixelSprite(ctx, id, frame) {
  const r = (x,y,w,h,c) => {ctx.fillStyle=c;ctx.fillRect(x,y,w,h)};
  const ink='#503c35', cream='#fff8de', pink='#f59391';
  const art=(x,y,rows,colors)=>rows.forEach((row,j)=>[...row].forEach((v,i)=>{if(colors[v])r(x+i,y+j,1,1,colors[v])}));
  if(id==='bee') {
    const wy=[0,2,4,2][frame];
    art(5,1+wy,['..XXX..','XXwwwX.','XwhhhwX','XwhhhwX','.XwwwX.','..XXX..'],{X:ink,w:'#e9faff',h:'#b8e0eb'});
    art(1,4-wy/2,['.XXX.','XwwwX','XwhwX','.XwwX','..XX.'],{X:ink,w:'#effaff',h:'#c1e5ed'});
    r(13,3,1,4,ink);r(12,2,2,2,ink);r(18,2,1,5,ink);r(18,1,2,2,ink);
    art(2,7,[
      '.....XXXXXXXX.....',
      '...XXhhhhhhhhXX...',
      '..XhhbbXXbbbhhhX..',
      '.XhbbbXXbbbbbbbhX.',
      'XhbbbXXbbbbwwbbbhX',
      'XbbbXXXbbbbwwwbbhX',
      'XbbbXXXbbbbwEEbbbX',
      'XbbbXXXbbbbwEEbbbX',
      'XbbbXXXbbbbwwbbbbX',
      '.XbbbXXbbccbbccbbX',
      '.XssbbXXbbccbbbbbX',
      '..XsssXXbbbbEbbsX.',
      '...XXsssssssssXX..',
      '.....XXXXXXXXX....'
    ],{X:ink,h:'#ffe788',b:'#ffd04d',s:'#e8a22e',w:cream,E:'#3c3031',c:pink});
    r(1,13,2,2,ink);r(0,14,1,1,ink);
  } else if(id==='fish') {
    // A round pufferfish: the belly is the silhouette, fins stay tiny.
    const p={X:'#68503e',h:'#ffe7a3',b:'#edc16b',s:'#d49f56',W:'#fff8de',E:'#443b38',c:'#eea09a',f:'#e5ad63'};
    const fin=[0,1,2,1][frame];
    art(0,9+fin,['XX...','XhX..','XhhXX','.Xhhh','XhhXX','XhX..','XX...'],p);
    r(9,2,1,3,p.X);r(16,2,1,3,p.X);r(5,5,1,3,p.X);r(21,6,1,2,p.X);
    art(3,3,[
      '.......XXXXXX.......',
      '.....XXhhhhhhXX.....',
      '....XhhhhhhhhhhX....',
      '...XhhbhhhhhbhhhX...',
      '..XhhhhhhhhhhhhhhX..',
      '..XhbhhWWWhhWWWhhX..',
      '.XhhhhhWEEhhWEEhhhX.',
      '.XhhhhhWEEhhWEEhhhX.',
      '.XhhhhhWWWbbWWWhhhX.',
      '.XhhccchhhhhhhccchX.',
      '.XWWcccWWWXXWWcccWX.',
      '.XWWWWWWWWXWXWWWWWX.',
      '..XWWWWWWWXXWWWWWX..',
      '..XWWWWWWWWWWWWWWX..',
      '...XWWWWWWWWWWWWX...',
      '....XsWWWWWWWWsX....',
      '.....XXssssssXX.....',
      '.......XXXXXX.......'
    ],p);
    // Two-pixel fin strokes change shape without moving the fish off its data anchor.
    r(5,13,2,2,p.X);r(5,13,1,1,p.f);r(4-fin%2,14,2,1,p.f);
    if(frame===1||frame===2){r(22,4-frame,1,2,'#b2d4da');r(21,3-frame,1,1,'#d5e9e7')}
  } else if(id==='pacman') {
    const rows=[
      '......XXXXXX......',
      '....XXhhhhhhXX....',
      '...XhhhhhhhhhhX...',
      '..XhhhhbbbbbbbhX..',
      '.XhhbbbbbbbbbbbhX.',
      '.XhbbbbbbbbbbbbhX.',
      'XhhbbbbbWWbbbbbbhX',
      'XhbbbbbbWWbbbbbbhX',
      'XbbbbbbbEEbbbbbbbX',
      'XbbbbbbbEEbbbbbbbX',
      'XbbbbbbbbbbbbbbbbX',
      'XbbbbbbcccbbbbbbbX',
      '.XsbbbbcccbbbbbbX.',
      '.XssbbbbbbbbbbbsX.',
      '..XssbbbbbbbbssX..',
      '...XsssssssssXX...',
      '....XXssssssXX....',
      '......XXXXXX......'
    ];
    art(3,3,rows,{X:ink,h:'#ffe995',b:'#ffd44d',s:'#e8ac37',W:cream,E:'#493337',c:pink});
    const opening=[1,3,5,3][frame];
    for(let x=13;x<24;x++){
      const h=Math.floor((x-12)*opening/9);
      if(h>0)ctx.clearRect(x,12-h,1,h*2+1);
    }
    // The stepped mouth is deliberately open to the background.
  } else if(id==='dino') {
    // Upright baby dinosaur: oversized head, cream belly, tiny arms, clear tail.
    const p={X:'#3d6559',h:'#c1e9bf',b:'#8ccda7',s:'#65a98b',W:'#fff5d5',E:'#314740',c:'#f2aa9e',o:'#eab28b'};
    const step=[0,1,0,-1][frame];
    art(0,12,[
      'XX.......',
      'XhX......',
      'XhhXX....',
      '.XhhhXX..',
      '.XbbhhhXX',
      '..Xbbbhhh',
      '...Xbbbbb',
      '....XXsss',
      '......XXX'
    ],p);
    r(7,10,2,3,p.X);r(7,10,1,2,p.o);r(5,13,2,2,p.X);r(5,13,1,1,p.o);
    art(7,9,[
      '...XXXXXX....',
      '..XhhbbbbX...',
      '.XhhbbbbbbX..',
      '.XhbbbWWbbbX.',
      'XhhbbWWWWbbX.',
      'XhbbbWWWWWbX.',
      'XbbbbWWWWWbX.',
      'XbbbbWWWWWbX.',
      '.XbbbWWWWWbX.',
      '.XssbbWWWbsX.',
      '..XsssssssX..',
      '...XXXXXXX...'
    ],p);
    art(9,1,[
      '....XXXXXXXX...',
      '..XXhhhhhhhhXX.',
      '.XhhhhhhhhhhhhX',
      '.XhhbbbbbbbbbhX',
      'XhhbbbbWWbbbbhX',
      'XhbbbbbWEEbbbhX',
      'XbbbbbbWEEbbbhX',
      'XbbbbbbWWWbbbhX',
      'XbbbbbbbbbbEbbX',
      '.XbbbccbbbbbbbX',
      '.XbbbccbbbXXXXX',
      '..XbbbbbbbWbbX.',
      '...XssbbbbbbX..',
      '....XXXXXXXX...'
    ],p);
    // Short arms and alternating feet. No vertical sprite bounce.
    r(17,14,2,3,p.X);r(18,15,3,2,p.X);r(18,15,2,1,p.b);
    r(9,20+Math.max(0,step),4,2,p.X);r(10,20+Math.max(0,step),2,1,p.s);
    r(15,20+Math.max(0,-step),5,2,p.X);r(16,20+Math.max(0,-step),3,1,p.b);
  } else if(id==='gecko') {
    // Horizontal baby gecko with a large head and a single clean spiral tail.
    const p={X:'#53643e',h:'#e0eca1',b:'#abd26d',s:'#84ad58',W:'#fff8df',E:'#374237',c:'#eea391'};
    const step=[0,1,0,-1][frame];
    art(0,9,[
      '..XXXXX..',
      '.XhhhhhX.',
      'XhhXXXhhX',
      'XhX...XhX',
      'XhX.X.XhX',
      'XhX.XhhX.',
      'XhX..XX..',
      '.XhhXXX..',
      '..XhhhhXX',
      '...XXhhhh',
      '.....XXXX'
    ],p);
    art(7,10,[
      '...XXXXX...',
      '..XhhhhhX..',
      '.XhhbbbbhX.',
      'XhhbbbbbbhX',
      'XhbbbbbbbhX',
      'XbbbWWWWWbX',
      '.XbbWWWWWX.',
      '..XsssssX..',
      '...XXXXX...'
    ],p);
    // Far legs sit behind the near legs, keeping only two broad toes per foot.
    r(8,17,2,3,p.s);r(7,19,3,1,p.X);r(15,16,2,3,p.s);
    r(10,18+Math.max(0,step),2,3,p.X);r(9,20+Math.max(0,step),4,1,p.X);r(10,19+Math.max(0,step),1,1,p.h);
    r(18,17+Math.max(0,-step),2,3,p.X);r(17,19+Math.max(0,-step),4,1,p.X);r(18,18+Math.max(0,-step),1,1,p.h);
    art(10,3,[
      '...XXX..XXX...',
      '..XWWWXXWWWX..',
      '.XhWEEhhWEEhX.',
      '.XhWEEhhWEEhX.',
      'XhhWWWhhWWWhhX',
      'XhhhhhhhhhhhhX',
      'XhbbbbbbbbbbhX',
      'XbbccbbbbccbbX',
      '.XbccbbbbccbX.',
      '.XbbbbXXbbbbX.',
      '..XbbbbbbbbX..',
      '...XssssssX...',
      '....XXXXXX....'
    ],p);
  }
}
