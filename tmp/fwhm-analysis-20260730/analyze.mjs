import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPaths = process.argv.slice(2);

function crossing(xs, ys, start, step, target) {
  let previous = start;
  for (let index = start + step; index >= 0 && index < ys.length; index += step) {
    const y0 = ys[previous];
    const y1 = ys[index];
    if ((y0 >= target && y1 <= target) || (y0 <= target && y1 >= target)) {
      if (y1 === y0) return xs[index];
      return xs[previous] + ((target - y0) / (y1 - y0)) * (xs[index] - xs[previous]);
    }
    previous = index;
  }
  return null;
}

function fwhm(xs, ys, baseline) {
  const peak = Math.max(...ys);
  const peakIndex = ys.indexOf(peak);
  const half = baseline + (peak - baseline) / 2;
  const left = crossing(xs, ys, peakIndex, -1, half);
  const right = crossing(xs, ys, peakIndex, 1, half);
  return { baseline, peak, peakNm: xs[peakIndex], half, left, right, width: left == null || right == null ? null : right - left };
}

function percentile(values, q) {
  const sorted = [...values].sort((a, b) => a - b);
  const position = (sorted.length - 1) * q;
  const lower = Math.floor(position);
  const fraction = position - lower;
  return sorted[lower] + fraction * (sorted[Math.min(lower + 1, sorted.length - 1)] - sorted[lower]);
}

function localPeaks(xs, ys, lowerNm, upperNm) {
  const peaks = [];
  for (let i = 1; i < ys.length - 1; i += 1) {
    if (xs[i] >= lowerNm && xs[i] <= upperNm && ys[i] >= ys[i - 1] && ys[i] > ys[i + 1]) {
      peaks.push({ wavelengthNm: xs[i], intensity: ys[i], index: i });
    }
  }
  return peaks.sort((a, b) => b.intensity - a.intensity).slice(0, 12);
}

for (const workbookPath of workbookPaths) {
  const input = await FileBlob.load(workbookPath);
  const workbook = await SpreadsheetFile.importXlsx(input);
  const summary = await workbook.inspect({
    kind: "workbook,sheet,table",
    maxChars: 12000,
    tableMaxRows: 12,
    tableMaxCols: 20,
    tableMaxCellChars: 120,
  });
  const sheet = workbook.worksheets.getItem("测试数据");
  const used = sheet.getUsedRange(true).values;
  const spectra = [];
  for (let col = 9; col + 1 < used[1].length; col += 2) {
    const current = used[1][col];
    const xs = [];
    const ys = [];
    for (let row = 2; row < used.length; row += 1) {
      const x = Number(used[row][col]);
      const y = Number(used[row][col + 1]);
      if (Number.isFinite(x) && Number.isFinite(y)) {
        xs.push(x);
        ys.push(y);
      }
    }
    const globalMinimum = Math.min(...ys);
    const p01 = percentile(ys, 0.01);
    const p10 = percentile(ys, 0.10);
    const rawZero = fwhm(xs, ys, 0);
    const global = fwhm(xs, ys, globalMinimum);
    const percentile01 = fwhm(xs, ys, p01);
    const percentile10 = fwhm(xs, ys, p10);
    spectra.push({ current, points: xs.length, global, rawZero, percentile01, percentile10, localPeaks: localPeaks(xs, ys, global.peakNm - 2, global.peakNm + 2) });
  }
  console.log(JSON.stringify({ workbookPath, summary: summary.ndjson, spectra }));
}
