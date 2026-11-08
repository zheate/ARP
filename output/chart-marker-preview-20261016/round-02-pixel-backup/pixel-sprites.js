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
    const ty=[0,1,2,1][frame];
    art(0,7+ty,['XX....','XbX...','XhbX..','.XbbXX','..Xbbb','.XbbXX','XhbX..','XbX...','XX....'],{X:'#334f77',b:'#5e9fe0',h:'#bce8f7'});
    art(6,5,[
      '....XXXXXX....',
      '..XXhhhhhXX...',
      '.XhhhhhhbbXX..',
      'XhhhbbbbbbbhX.',
      'XhhbbbbwwbbhhX',
      'XhbbbbwWWWbbhX',
      'XbbbbbwWEEbbhX',
      'XbbbbbwWEEbbhX',
      'XbbbbbbwwwbbbX',
      'XbbbffbbccbbXX',
      '.XsbffbbbccbbX',
      '.XssssbbbbbsX.',
      '..XXssssssXX..',
      '....XXXXXX....'
    ],{X:'#334f77',h:'#9edcf2',b:'#70b9e8',s:'#4d8dcc',w:'#f8fcf7',W:'#fff',E:'#29394d',c:'#f4a5ab',f:'#4282c5'});
    if(frame===1||frame===3){r(9,14,1,3,'#334f77');r(10,15,2,2,'#4282c5')}
    r(11,3,3,2,'#334f77');r(12,4,4,2,'#72b9e8');
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
  } else if(id==='laser') {
    const glow=['#f4b9b1','#efaaa6','#f7c8b6','#efaaa6'][frame];
    r(8,1,8,1,glow);r(4,3,2,1,glow);r(18,3,2,1,glow);r(1,8,1,7,glow);r(22,8,1,7,glow);r(4,20,2,1,glow);r(18,20,2,1,glow);r(8,22,8,1,glow);
    art(4,4,[
      '.....XXXXXX.....',
      '...XXhhhhhhXX...',
      '..XhhhWWWhhhhX..',
      '.XhhWWWhhhhhhhX.',
      '.XhhWbbbbbbbbhX.',
      'XhhWbbbbbbbbbbhX',
      'XhbbbWWbbWWbbbhX',
      'XhbbbWEbbWEbbbbX',
      'XbbbbEEbbEEbbbbX',
      'XbbccbbbbbbccbbX',
      '.XbbccbbbbccbbX.',
      '.XsbbbbbEEbbbbsX',
      '..XsssbbbbsssX..',
      '...XXssssssXX...',
      '.....XXXXXX.....'
    ],{X:'#934953',h:'#ffc1ac',W:'#fff7d9',b:'#ff8b88',s:'#ec646f',E:'#6c354a',c:'#ef6282'});
    const sx=frame%2?21:2,sy=frame%2?3:18;
    r(sx,sy-1,1,3,'#ffda9d');r(sx-1,sy,3,1,'#ffda9d');
  } else if(id==='gecko') {
    // A short body, broad baby head and big eyes distinguish it from a lizard icon.
    const step=frame%2;
    const green='#8bc666',dark='#416244',light='#d9ec9e';
    r(0,14,2,2,dark);r(1,16,4,2,dark);r(4,14,3,3,dark);r(5,12,4,3,dark);
    r(1,14,1,2,green);r(2,16,2,1,green);r(4,14,2,2,green);r(6,12,2,2,green);
    for(const [x,y,offset] of [[7,5,step],[14,4,1-step],[7,17,1-step],[14,18,step]]){
      const dy=y<12?-offset:offset;
      r(x,y+dy,3,3,dark);r(x-1,y+dy-1,1,2,dark);r(x+3,y+dy-1,1,2,dark);r(x+1,y+dy-2,1,3,dark);r(x,y+dy,2,2,light);
      r(x+1,y<12?y+3:y-2,2,3,green);
    }
    art(6,7,[
      '...XXXXXX........',
      '..XhhhhhXX.XXX...',
      '.XhbbbbbbbXXWwX..',
      'XhbbbbbbbXWWEEwX.',
      'XhbbbbbbbhWWWEEwX',
      'XbbbbbbbWWhWWwwX.',
      '.XbbbbbbWEEbbbbhX',
      '..XssbbbWEEbbccX.',
      '...XXssssbbccbbX.',
      '.....XXXXXXXbbX..',
      '............XX...'
    ],{X:dark,h:'#c5e68c',b:green,s:'#66a75b',W:'#fff8dd',w:'#e0efc3',E:'#304837',c:'#f2a4a0'});
    // Tiny tail alternates between two stepped silhouettes.
    if(frame===2||frame===3){ctx.clearRect(0,14,4,4);r(1,13,2,2,dark);r(2,14,3,2,dark);r(2,14,2,1,green)}
  }
}
