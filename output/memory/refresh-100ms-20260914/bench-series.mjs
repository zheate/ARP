import { performance, PerformanceObserver } from 'node:perf_hooks';
import { Session } from 'node:inspector';
const mode = process.argv[2];
const { mergeSnapshotSeries } = await import(`./${mode}-series.mjs`);
const count = Number(process.argv[3] || 1200);
const iterations = 20000;
const getX = point => point.elapsedS;
let history = Array.from({ length: count }, (_, i) => ({ elapsedS: i / 10, powerW: Math.sin(i) }));
let minorGc = 0;
const observer = new PerformanceObserver(list => { for (const entry of list.getEntries()) if (entry.detail.kind === 1) minorGc++; });
observer.observe({ entryTypes: ['gc'] });
const inspector = new Session();
inspector.connect();
const post = (method, params = {}) => new Promise((resolve, reject) => inspector.post(method, params, (error, result) => error ? reject(error) : resolve(result)));
global.gc();
await post('HeapProfiler.startSampling', { samplingInterval: 32768, includeObjectsCollectedByMajorGC: true, includeObjectsCollectedByMinorGC: true });
const start = performance.now();
for (let i = 0; i < iterations; i++) {
  const elapsedS = (count + i) / 10;
  history = mergeSnapshotSeries(history, undefined, { startX: (i + 1) / 10, points: [{ elapsedS, powerW: Math.sin(i) }] }, getX, i, i + 1);
  if (i % 1000 === 0) await new Promise(resolve => setImmediate(resolve));
}
const durationMs = performance.now() - start;
const { profile } = await post('HeapProfiler.stopSampling');
const total = node => node.selfSize + node.children.reduce((sum, child) => sum + total(child), 0);
await new Promise(resolve => setImmediate(resolve));
observer.disconnect();
inspector.disconnect();
console.log(JSON.stringify({ mode, count, iterations, durationMs, allocatedSampleMB: total(profile.head) / 1048576, minorGc, finalPoints: history.length, first: history[0].elapsedS, last: history.at(-1).elapsedS }));
