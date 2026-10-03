/**
 * The complete analytical result, with its scope stated and a way out.
 *
 * Three things a reader could not previously tell apart: how many groups
 * the analysis covered, how many the table was showing, and how many rows
 * were behind them. The table said "25 rows · showing first 12" while the
 * answer said "every row in the dataset", and the difference between a UI
 * preview and an analytical truncation was invisible.
 *
 * Sorting is offered only where it stays truthful: reordering rows of a
 * complete result changes nothing about it. It is withheld on a partial
 * result, where sorting the visible subset would imply a ranking the
 * result cannot support.
 */

import { useMemo, useState } from "react";
import { formatCell } from "../lib/format";
import { csvFilename, resultToCsv } from "../lib/resultCsv";
import type { Cell, DisplayField, EvidenceCell, ResultSnapshot } from "../lib/types";

interface Props {
  snapshot: ResultSnapshot;
  question: string;
  highlight?: EvidenceCell[];
  /** Rows shown before "Show all". Never changes the analytical result. */
  previewRows?: number;
  /** Backend-owned labels and semantic formatting. */
  displayFields?: DisplayField[];
}

type SortState = { column: number; direction: "asc" | "desc" } | null;

/* `value_count` is execution evidence, not another analytical dimension.
 * It remains in the snapshot (and therefore in the full CSV export) so the
 * non-null denominator is auditable, while the report states it in the scope
 * sentence and caveat. Repeating it as a column for every group made the
 * business table harder to scan and, at phone widths, widened the entire
 * document instead of adding information. */
const INTERNAL_EVIDENCE_COLUMNS = new Set(["value_count"]);

export function ResultPanel({
  snapshot,
  question,
  highlight = [],
  previewRows = 12,
  displayFields = [],
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [sort, setSort] = useState<SortState>(null);

  const coverage = snapshot.group_coverage;
  const complete = coverage ? coverage.complete : null;
  const sortable = complete !== false;

  const cited = new Set(highlight.map((cell) => `${cell.row}:${cell.column}`));
  const fields = new Map(displayFields.map((field) => [field.source_name, field]));
  const tableColumns = snapshot.columns
    .map((column, index) => ({ column, index }))
    .filter(({ column }) => !INTERNAL_EVIDENCE_COLUMNS.has(column));
  const labelFor = (column: string) => fields.get(column)?.display_label ?? column.replaceAll("_", " ");
  const valueFor = (column: string, value: Cell) => {
    const labels = fields.get(column)?.boolean_labels;
    return labels?.[String(value)] ?? formatCell(value);
  };

  const ordered = useMemo(() => {
    const indexed = snapshot.rows.map((row, index) => ({ row, index }));
    if (!sort || !sortable) return indexed;
    const { column, direction } = sort;
    return [...indexed].sort((left, right) => {
      const a = left.row[column];
      const b = right.row[column];
      const numeric = typeof a === "number" && typeof b === "number";
      const comparison = numeric
        ? (a as number) - (b as number)
        : String(a ?? "").localeCompare(String(b ?? ""));
      return direction === "asc" ? comparison : -comparison;
    });
  }, [snapshot.rows, sort, sortable]);

  const visible = expanded ? ordered : ordered.slice(0, previewRows);
  const hidden = ordered.length - visible.length;

  const download = () => {
    const blob = new Blob([resultToCsv(snapshot)], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = csvFilename(question);
    link.click();
    URL.revokeObjectURL(url);
  };

  const toggleSort = (column: number) => {
    if (!sortable) return;
    setSort((current) =>
      current && current.column === column
        ? { column, direction: current.direction === "asc" ? "desc" : "asc" }
        : { column, direction: "asc" },
    );
  };

  return (
    <div className="stack result-panel" data-testid="result-panel">
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col" aria-label="row number">
                #
              </th>
              {tableColumns.map(({ column, index }) => (
                <th
                  key={column}
                  scope="col"
                  aria-sort={
                    sort?.column === index
                      ? sort.direction === "asc"
                        ? "ascending"
                        : "descending"
                      : "none"
                  }
                >
                  {sortable ? (
                    <button
                      type="button"
                      className="th-sort"
                      onClick={() => toggleSort(index)}
                      aria-label={`Sort by ${labelFor(column)}`}
                    >
                      {labelFor(column)}
                    </button>
                  ) : (
                    labelFor(column)
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map(({ row, index }) => (
              <tr key={index}>
                <td className="dim">{index}</td>
                {tableColumns.map(({ column, index: columnIndex }) => (
                  <td
                    key={column}
                    className={
                      cited.has(`${index}:${column}`) ? "cited" : undefined
                    }
                  >
                    {valueFor(column, (row as Cell[])[columnIndex] ?? null)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="result-foot small">
        <span data-testid="result-scope">
          {scopeSentence(snapshot, visible.length)}
        </span>
        <span className="spacer" style={{ flex: 1 }} />
        {hidden > 0 ? (
          <button
            className="btn ghost small no-print"
            onClick={() => setExpanded(true)}
          >
            Show all {ordered.length.toLocaleString()} rows
          </button>
        ) : null}
        <button className="btn ghost small no-print" onClick={download}>
          Download CSV
        </button>
      </div>
    </div>
  );
}

/**
 * One sentence distinguishing the analysis from the preview.
 *
 * Every number here is counted. "Showing 12 of 45 groups" and "45 of 45
 * groups in the analysis" are different facts and both belong on screen.
 */
export function scopeSentence(
  snapshot: ResultSnapshot,
  showing: number,
): string {
  const coverage = snapshot.group_coverage;
  const total = snapshot.rows.length;
  const preview =
    showing < total
      ? `Showing ${showing.toLocaleString()} of ${total.toLocaleString()} result rows.`
      : `Showing all ${total.toLocaleString()} result row${total === 1 ? "" : "s"}.`;
  if (!coverage) return preview;
  const groups =
    coverage.groups_total != null
      ? coverage.complete
        ? `The analysis covers all ${coverage.groups_total.toLocaleString()} groups`
        : `The analysis covers ${coverage.groups_returned.toLocaleString()} of ${coverage.groups_total.toLocaleString()} groups`
      : `The analysis covers ${coverage.groups_returned.toLocaleString()} groups`;
  const rows =
    coverage.rows_represented != null && coverage.rows_matching != null
      ? `, over ${coverage.rows_represented.toLocaleString()} of ${coverage.rows_matching.toLocaleString()} matching rows`
      : "";
  const observations =
    coverage.observations_represented != null &&
    coverage.observations_matching != null &&
    (coverage.observations_represented !== coverage.rows_represented ||
      coverage.observations_matching !== coverage.rows_matching)
      ? `; ${coverage.observations_represented.toLocaleString()} of ${coverage.observations_matching.toLocaleString()} non-null values contributed`
      : "";
  return `${preview} ${groups}${rows}${observations}.`;
}
