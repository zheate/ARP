import { performance } from 'node:perf_hooks';
import { createHash } from 'node:crypto';
const root = new URL('../../../../', import.meta.url);
const before = process.argv.includes('--before');
const {traceMonotoneCurve} = await import(before ? './monotone-before.ts' : new URL('tauri-ui/src/lib/monotone-curve.ts', root));
const {comparisonRows, metricComparisonRows} = await import(before ? './data-browser-before.ts' : new URL('tauri-ui/src/lib/data-browser.ts', root));
const median = a => a.sort((x,y)=>x-y)[Math.floor(a.length/2)];
function time(fn, iterations=10) { for(let i=0;i<10;i++)fn(); const times=[]; for(let b=0;b<15;b++){const t=performance.now();for(let i=0;i<iterations;i++)fn();times.push((performance.now()-t)/iterations);} return {medianMs:median([...times]),p95BatchMs:times.sort((a,b)=>a-b)[14]}; }
const curves=[];
for(const length of [1024,3648,8192]) {
 const points=Array.from({length},(_,i)=>({x:972+i*8/(length-1), y:100+12000*Math.exp(-(((972+i*8/(length-1)-976.2)/.4)**2))}));
 let count=0; const sink={moveTo(){count++},lineTo(){count++},bezierCurveTo(){count++}};
 const run=()=>traceMonotoneCurve(sink,points);
 const timing=time(run,100); const bytes=[];
 for(let i=0;i<7;i++){global.gc();const start=process.memoryUsage().heapUsed;run();bytes.push(process.memoryUsage().heapUsed-start);}
 const hash=createHash('sha256');const path=Object.fromEntries(['moveTo','lineTo','bezierCurveTo'].map(key=>[key,(...args)=>hash.update(JSON.stringify([key,...args]))]));traceMonotoneCurve(path,points);
 curves.push({length,...timing,heapIncreaseBytes:median(bytes),commands:count,hash:hash.digest('hex')});
}
const comparisons=[];
for(const length of [601,4000,16000]) for(const duplicate of [false,true]) {
 const points=Array.from({length},(_,i)=>({currentA:duplicate?2:i/10,powerW:i/100,voltageV:1,rowId:i}));
 const others=points.map(p=>({...p,powerW:p.powerW+1}));const details=[{points},{points:others}];
 const result=[comparisonRows(points,others),metricComparisonRows(details,'powerW')];
 comparisons.push({length,duplicate,...time(()=>{comparisonRows(points,others);metricComparisonRows(details,'powerW')},length>4000?1:3),hash:createHash('sha256').update(JSON.stringify(result)).digest('hex')});
}
console.log(JSON.stringify({curves,comparisons},null,2));
