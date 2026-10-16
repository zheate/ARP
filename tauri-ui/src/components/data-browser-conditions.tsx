import { useMemo } from "react"
import { compareMetadata, comparisonLabels, type DataDetail } from "@/lib/data-browser"

export function TestConditionDiff({
  details,
  baselineRecordId,
}: {
  details: DataDetail[]
  baselineRecordId: string | null
}) {
  const diff = useMemo(
    () => compareMetadata(details, baselineRecordId),
    [details, baselineRecordId],
  )
  const labels = useMemo(() => comparisonLabels(details.map((d) => d.record)), [details])
  const changed = diff.rows.filter((r) => r.different)
  const incomplete = diff.rows.filter((r) => r.missing && !r.different)
  const same = diff.rows.filter((r) => !r.different && !r.missing)
  const table = (rows: typeof diff.rows) => (
    <div className="db-table-scroll">
      <table className="db-data-table db-condition-table">
        <thead>
          <tr>
            <th>参数</th>
            {details.map((d, index) => (
              <th key={d.record.id} data-record-id={d.record.id}>
                {labels.get(d.record.id)}
                {index === diff.baselineIndex && <small>基准</small>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td>
                {row.label}
                {row.identity && <small>记录标识</small>}
              </td>
              {row.cells.map((cell) => (
                <td key={cell.recordId} data-diff={cell.state}>
                  {cell.value ?? "未记录"}
                  {cell.state === "different" && <small>与基准不同</small>}
                  {cell.state === "no-baseline" && <small>基准未记录，无法比较</small>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
  return (
    <section className="db-condition-diff" aria-label="测试条件差异">
      <div className="db-chart-title">
        <strong>测试条件差异</strong>
        <span>按已记录字段比较</span>
      </div>
      <p role="status">
        {diff.differentCount
          ? `发现 ${diff.differentCount} 项测试条件不同`
          : "已记录测试条件未发现差异"}
        {` · ${diff.missingCount} 项字段存在未记录 · ${diff.identityCount} 项记录标识不同`}
      </p>
      {changed.length > 0 && table(changed)}
      {incomplete.length > 0 && (
        <details open>
          <summary>未记录字段（{incomplete.length}）</summary>
          {table(incomplete)}
        </details>
      )}
      {same.length > 0 && (
        <details>
          <summary>相同字段（{same.length}）</summary>
          {table(same)}
        </details>
      )}
      <p className="db-help">
        未记录不等于不同。会话 ID
        和记录版本用于追溯，不代表物理测试条件；记录版本不是软件版本。这里只比较实际保存的配置，不推断差异原因。
      </p>
    </section>
  )
}
