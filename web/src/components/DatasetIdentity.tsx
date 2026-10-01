/**
 * Which dataset this answer is about, stated compactly and kept on screen.
 *
 * This used to be a pass-through to `RightRail`, rendered only while
 * `!hasRun`. So the dataset disappeared at exactly the moment it mattered
 * most: once a report existed, nothing on the screen said which data
 * produced it. A reader who uploaded a file, asked a question, then
 * scrolled through a report had no way to confirm they were reading an
 * answer about their file rather than the demo warehouse.
 *
 * It is a strip rather than a panel because it is context, not content. It
 * should be legible in one glance and should not compete with the answer.
 *
 * The dataset fingerprint is deliberately absent. It is in the technical
 * inspector, where a hash has an audience; in the main report it is an
 * unexplained identifier, which is precisely what this interface is not
 * supposed to put in front of a reader.
 */

import type { DatasetCatalog } from "../lib/types";

/** What kind of dataset this is, in the reader's terms. */
function kindLabel(kind: string): string {
  if (kind === "upload") return "Your file";
  if (kind === "demo") return "Demo warehouse";
  // An unrecognised kind is reported as itself rather than guessed at.
  return kind ? kind.replaceAll("_", " ") : "Dataset";
}

export function DatasetIdentity({ catalog }: { catalog: DatasetCatalog | null }) {
  if (!catalog) return null;

  const tables = catalog.tables ?? [];
  const rows = tables.reduce((total, table) => total + (table.row_count ?? 0), 0);
  // One table is named; several are counted. Naming one of five tells a
  // reader less than saying there are five.
  const shape =
    tables.length === 1
      ? `${rows.toLocaleString()} rows`
      : `${tables.length} tables, ${rows.toLocaleString()} rows`;

  return (
    <div className="dataset-strip" data-testid="dataset-identity">
      <span className="dataset-strip-kind">{kindLabel(catalog.dataset_kind)}</span>
      {catalog.source && (
        <span className="dataset-strip-source mono" title={catalog.source}>
          {catalog.source}
        </span>
      )}
      <span className="dataset-strip-shape small dim">{shape}</span>
    </div>
  );
}
