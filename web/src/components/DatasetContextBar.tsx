/**
 * What the dataset in front of you actually is, in one line.
 *
 * This replaces an always-open schema table. A full field list resident on
 * the canvas is a reference document that most readers never need and that
 * every reader has to scroll past; the facts that *do* change how you phrase
 * a question are few, and they fit on one line: how many rows, how many
 * fields, which column carries the clock, and whether anything is ambiguous.
 *
 * The ambiguity count is the reason this strip exists rather than being
 * decoration. "Two date columns" is the difference between a question that
 * gets answered and one that gets refused, and a reader who learns it from
 * the refusal has learned it too late.
 *
 * The full schema is one control away, as a side sheet.
 */

import { opensSheet } from "./SideSheet";
import type { DatasetCatalog, DatasetSummary } from "../lib/types";

/**
 * What kind of dataset this is, in the reader's terms.
 *
 * Carried over from `DatasetIdentity`, which this strip replaces. It is not
 * decoration: "Your file" and "Demo warehouse" are the difference between a
 * number about the reader's business and a number about a generated
 * fixture, and a reader who confuses the two has been misled by the
 * interface rather than by the engine.
 */
function kindLabel(kind: string): string {
  if (kind === "upload") return "Your file";
  if (kind === "demo") return "Demo warehouse";
  // An unrecognised kind is reported as itself rather than guessed at.
  return kind ? kind.replaceAll("_", " ") : "Dataset";
}

export function DatasetContextBar({
  catalog,
  summary,
  onInspect,
}: {
  catalog: DatasetCatalog | null;
  summary: DatasetSummary | null;
  /** Absent when there is no schema to inspect, as in a replayed recording. */
  onInspect?: () => void;
}) {
  if (!catalog) return null;

  const tables = catalog.tables ?? [];
  const rows = tables.reduce((total, table) => total + (table.row_count ?? 0), 0);
  const fieldCount =
    summary?.fields?.length ??
    tables.reduce((total, table) => total + (table.columns?.length ?? 0), 0);

  // Close calls nobody has settled. A confirmed field is still flagged
  // `ambiguous`, so counting that alone would never reach zero.
  const unresolved =
    summary?.unresolved_ambiguity_count ??
    (summary?.fields ?? []).filter(
      (field) => field.ambiguous && field.role_source !== "user_confirmed",
    ).length;

  const clocks = summary?.time_fields ?? [];

  const kind = kindLabel(catalog.dataset_kind);
  const sourceSaysKind = (catalog.source ?? "")
    .toLowerCase()
    .includes(kind.toLowerCase());

  const facts: string[] = [];
  if (tables.length > 1) facts.push(`${tables.length} tables`);
  if (rows > 0) facts.push(`${rows.toLocaleString()} rows`);
  if (fieldCount > 0) facts.push(`${fieldCount} fields`);

  return (
    <div className="context-bar" data-testid="dataset-context">
      {/* Suppressed when the source already says it. The demo warehouse's
          source is "Commerce demo warehouse", so printing the kind beside
          it read "Demo warehouse  Commerce demo warehouse" -- the same fact
          twice, which is what this strip was built to stop doing in the
          header. An upload keeps it, because "Your file" says something the
          filename does not. */}
      {!sourceSaysKind && (
        <span className="context-bar-kind">{kind}</span>
      )}
      {catalog.source && (
        <span className="context-bar-source mono" title={catalog.source}>
          {catalog.source}
        </span>
      )}

      {facts.length > 0 && (
        <span className="context-bar-facts">{facts.join(" · ")}</span>
      )}

      {/* Which column the engine will read as the clock. Named, not
          counted: "1 time field" tells a reader nothing they can act on,
          and `order_date` tells them exactly what "in 2024" will mean. */}
      {clocks.length > 0 && (
        <span className="context-bar-clock">
          {clocks.length === 1 ? "time: " : "time: "}
          <span className="mono">{clocks.join(", ")}</span>
        </span>
      )}

      {/* These roles came from types and cardinality, not from a governed
          definition anyone wrote down.
          It used to be a tag on the inspector's collapsed summary, legible
          without opening it. The inspector is behind a control now, so the
          caveat has to live out here or a reader who never presses that
          control would take inferred roles for defined ones. */}
      {summary?.status === "inferred" && (
        <span className="context-bar-inferred" data-testid="roles-inferred">
          roles inferred
        </span>
      )}

      {unresolved > 0 && (
        <span className="context-bar-warn" data-testid="context-ambiguity">
          <span aria-hidden="true">⚠</span>{" "}
          {unresolved === 1
            ? "1 ambiguous field"
            : `${unresolved} ambiguous fields`}
        </span>
      )}

      {/* `dataset_fingerprint` is deliberately absent. It belongs in the
          evidence drawer, where a hash has an audience; here it is an
          unexplained identifier. */}
      {onInspect && (
        <button
          type="button"
          className="context-bar-inspect"
          {...opensSheet(onInspect)}
          data-testid="inspect-schema"
        >
          Inspect schema <span aria-hidden="true">→</span>
        </button>
      )}
    </div>
  );
}
