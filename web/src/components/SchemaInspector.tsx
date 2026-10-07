/**
 * What the engine worked out about an uploaded file, available but not in
 * the way.
 *
 * This was a pass-through that rendered the whole column table as a
 * permanent panel between the upload and the question box. A reader who
 * wanted to ask something had to scroll past twenty rows of inferred
 * metadata to reach the field they came for, every time, and most
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
import type {
  DatasetSummary as DatasetSummaryPayload,
  RoleChange,
} from "../lib/types";

export function SchemaInspector({
  summary,
  onConfirmRoles,
  open,
}: {
  summary: DatasetSummaryPayload;
  /**
   * Start expanded. True inside the schema side sheet: a reader who pressed
   * "Inspect schema" has already asked the question, and meeting a
   * collapsed disclosure there is being asked it twice.
   */
  open?: boolean;
  /**
   * Applies a batch and resolves once the server has accepted it. Absent
   * for a dataset whose roles are not confirmable, such as a demo warehouse or a
   * replayed recording, which is what hides the control entirely rather
   * than showing a disabled one.
   */
  onConfirmRoles?: (changes: RoleChange[]) => Promise<void>;
}) {
  const fields = summary.fields ?? [];
  // Close calls nobody has settled. A confirmed field is still ambiguous,
  // so counting `ambiguous` alone would never reach zero.
  const ambiguous =
    summary.unresolved_ambiguity_count ??
    fields.filter((field) => field.ambiguous && field.role_source !== "user_confirmed")
      .length;

  return (
    <details className="technical-audit" data-testid="schema-inspector" open={open}>
      <summary>
        Dataset understanding
        {/*
          The `inferred` marker stays on the summary line rather than inside
          the body. It was a tag in the old panel head, and collapsing the
          panel would have hidden the single most important caveat about
          everything in it: these roles come from types and cardinality, not
          from a definition anyone wrote down. A reader who never opens the
          disclosure still has to be told that.
        */}
        <span className="tag">inferred</span>
        <span className="small dim">
          {fields.length} {fields.length === 1 ? "column" : "columns"} read
          {ambiguous > 0
            ? `, ${ambiguous} role${ambiguous === 1 ? "" : "s"} the data cannot settle`
            : ""}
        </span>
      </summary>
      <div className="technical-audit-body">
        <DatasetSummary
          summary={summary}
          showHead={false}
          onConfirmRoles={onConfirmRoles}
        />
      </div>
    </details>
  );
}
