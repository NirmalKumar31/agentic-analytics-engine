import { formatCell } from '../lib/format'
import type { Cell, EvidenceCell, ResultSnapshot } from '../lib/types'

interface Props {
  snapshot: ResultSnapshot
  highlight?: EvidenceCell[]
  maxRows?: number
}

/**
 * The result rows behind a finding, with the cited cells marked.
 *
 * Values are rendered as text children, never as markup, so a dataset value
 * containing HTML is displayed rather than interpreted.
 */
export function ResultTable({ snapshot, highlight = [], maxRows = 60 }: Props) {
  const cited = new Set(highlight.map((c) => `${c.row}:${c.column}`))
  const citedRows = new Set(highlight.map((c) => c.row))
  const rows = snapshot.rows.slice(0, maxRows)

  return (
    <div className="stack">
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col" aria-label="row number">#</th>
              {snapshot.columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row: Cell[], rowIndex: number) => (
              <tr key={rowIndex} className={citedRows.has(rowIndex) ? 'cited-row' : undefined}>
                <td className="dim">{rowIndex}</td>
                {snapshot.columns.map((column, columnIndex) => (
                  <td
                    key={column}
                    className={cited.has(`${rowIndex}:${column}`) ? 'cited' : undefined}
                    title={cited.has(`${rowIndex}:${column}`) ? 'cited by this finding' : undefined}
                  >
                    {formatCell(row[columnIndex])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small dim" style={{ margin: 0 }}>
        {snapshot.row_count.toLocaleString()} row{snapshot.row_count === 1 ? '' : 's'}
        {snapshot.rows.length > rows.length ? ` · showing first ${rows.length}` : ''}
        {snapshot.truncated ? ' · truncated by the result limit' : ''}
      </p>
    </div>
  )
}
