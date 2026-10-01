/**
 * What the engine worked out about an uploaded file, available but not in
 * the way.
 *
 * This was a pass-through that rendered the whole column table as a
 * permanent panel between the upload and the question box. A reader who
 * wanted to ask something had to scroll past twenty rows of inferred
 * metadata to reach the field they came for, every time -- and most
 * readers do not need it at all, because the inference is usually right
 * and the report cites its columns anyway.
 *
 * So it is a disclosure, closed by default, and its summary line carries
 * the two facts worth knowing without opening it: how many columns were
 * read, and how many of their roles the data could not settle. A reader
 * with no close calls never needs to look; a reader with three is told
 * so in the one place they will see it.
 *
 * Opening it is still the honest, complete record. The suggested questions
 * moved to the composer, which is where a reader is choosing what to ask,
 * so there is now one place examples come from rather than two.
 */

import { DatasetSummary } from "./DatasetSummary";
import type { DatasetSummary as DatasetSummaryPayload } from "../lib/types";

export function SchemaInspector({
  summary,
}: {
  summary: DatasetSummaryPayload;
}) {
  const fields = summary.fields ?? [];
  const ambiguous = fields.filter((field) => field.ambiguous).length;

  return (
    <details className="technical-audit" data-testid="schema-inspector">
      <summary>
        Dataset understanding
        <span className="small dim">
          {" — "}
          {fields.length} {fields.length === 1 ? "column" : "columns"} read
          {ambiguous > 0
            ? `, ${ambiguous} role${ambiguous === 1 ? "" : "s"} the data cannot settle`
            : ""}
        </span>
      </summary>
      <div className="technical-audit-body">
        <DatasetSummary summary={summary} showHead={false} />
      </div>
    </details>
  );
}
