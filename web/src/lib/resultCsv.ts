/**
 * The aggregate result as CSV, built in the browser from the cited result.
 *
 * Exports the *analytical* result, not the rows the table happens to be
 * previewing: a download that silently carried 12 of 45 groups would be
 * the truncation defect again, in a file the reader takes away and cannot
 * check against the page.
 */

import type { ResultSnapshot } from "./types";

function escapeCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function resultToCsv(snapshot: ResultSnapshot): string {
  const header = snapshot.columns.map(escapeCell).join(",");
  const rows = snapshot.rows.map((row) => row.map(escapeCell).join(","));
  return [header, ...rows].join("\n");
}

/** A filename a reader can find again, without exposing internal ids. */
export function csvFilename(question: string): string {
  const slug =
    question
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 60) || "analysis";
  return `${slug}.csv`;
}
