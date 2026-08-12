import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const outputDir = path.dirname(fileURLToPath(import.meta.url));
const workbookPath = path.join(outputDir, "sample_three_tables.xlsx");
const input = await FileBlob.load(workbookPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const sheets = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 4000 });
console.log(sheets.ndjson);
for (const [sheetName, range, fileName] of [
  ["测试数据", "A1:L8", "01_test_data.png"],
  ["3A逐秒数据", "A1:G6", "02_second_data.png"],
  ["3A光谱数据", "A1:F5", "03_spectrum_data.png"],
  ["5A逐秒数据", "A1:G6", "04_5a_second_data.png"],
  ["5A光谱数据", "A1:F5", "05_5a_spectrum_data.png"],
]) {
  const table = await workbook.inspect({
    kind: "table",
    range: `${sheetName}!${range}`,
    include: "values,formulas",
    tableMaxRows: 8,
    tableMaxCols: 12,
    maxChars: 5000,
  });
  console.log(table.ndjson);
  const image = await workbook.render({ sheetName, range, scale: 2, format: "png" });
  await fs.writeFile(path.join(outputDir, fileName), new Uint8Array(await image.arrayBuffer()));
}
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errors.ndjson);
