/**
 * The parts of a report a reader needs first.
 *
 * The report opened with the question, then the governed contract, then an
 * executive summary, and only then the finding that actually answered it.
 * A reader had to scroll past three blocks of metadata to reach the number
 * they asked for. So the answer is found here and rendered first, with the
 * population it covers and the rows it was computed over beside it, and the
 * provenance after it rather than before.
 */

import type { Finding, QueryContract, ResultSnapshot } from "./types";

/** The tools that execute an accepted query contract. */
const CONTRACT_TOOLS = new Set(["aggregate_for_question", "compute_metric"]);

/**
 * The published finding that answers the question.
 *
 * Identified by the result it cites, not by its position: the finding that
 * answers the contract is the one citing the aggregate the contract was
 * executed as. Position happened to agree, and would have stopped agreeing
 * the moment a supporting finding was published first.
 */
export function directAnswer(
  findings: Finding[],
  results: Record<string, ResultSnapshot>,
): Finding | null {
  for (const finding of findings) {
    for (const id of finding.result_ids) {
      const snapshot = results[id];
      if (snapshot && CONTRACT_TOOLS.has(snapshot.tool_name)) return finding;
    }
  }
  return null;
}

/** The result a finding was computed from, if this run still holds it. */
export function answerResult(
  finding: Finding | null,
  results: Record<string, ResultSnapshot>,
): ResultSnapshot | null {
  if (!finding) return null;
  for (const id of finding.result_ids) {
    const snapshot = results[id];
    if (snapshot && CONTRACT_TOOLS.has(snapshot.tool_name)) return snapshot;
  }
  return null;
}

/**
 * The rows the answer was computed over, or null when the result does not
 * say. Taken from the engine's own `row_count` column and summed across
 * groups, because a grouped result reports one count per group.
 *
 * Deliberately not `snapshot.row_count`: that is the number of rows in the
 * *result*, which for a grouped answer is the number of groups. Reporting
 * four where the answer covers four hundred rows would misstate the
 * population by two orders of magnitude.
 */
/**
 * The rows the answer was computed over, or null when the result does not
 * say.
 *
 * Read from the engine's measured coverage when present. The fallback sums
 * the result's own `row_count` column, which is right only when every group
 * is present -- and that is exactly what coverage records, so the fallback
 * is used only for a result that carries no coverage block at all.
 *
 * Deliberately not `snapshot.row_count`: that is the number of rows in the
 * *result*, which for a grouped answer is the number of groups.
 */
export function rowsInScope(snapshot: ResultSnapshot | null): number | null {
  if (!snapshot) return null;
  const coverage = snapshot.group_coverage;
  if (coverage) {
    if (coverage.complete && coverage.rows_matching != null) {
      return coverage.rows_matching;
    }
    if (coverage.rows_represented != null) return coverage.rows_represented;
    return null;
  }
  const index = snapshot.columns.findIndex(
    (column) => column.toLowerCase() === "row_count",
  );
  if (index < 0) return null;
  let total = 0;
  for (const row of snapshot.rows) {
    const value = row[index];
    if (typeof value === "number") total += value;
    else if (
      typeof value === "string" &&
      value.trim() !== "" &&
      !Number.isNaN(Number(value))
    ) {
      total += Number(value);
    } else return null;
  }
  return total;
}

/** Non-null measure values that actually contributed to the aggregate. */
export function observationsUsed(snapshot: ResultSnapshot | null): number | null {
  if (!snapshot) return null;
  const coverage = snapshot.group_coverage;
  if (coverage) {
    if (coverage.complete && coverage.observations_matching != null) {
      return coverage.observations_matching;
    }
    if (coverage.observations_represented != null) {
      return coverage.observations_represented;
    }
  }
  const index = snapshot.columns.findIndex(
    (column) => column.toLowerCase() === "value_count",
  );
  if (index < 0) return null;
  let total = 0;
  for (const row of snapshot.rows) {
    const value = row[index];
    if (typeof value === "number") total += value;
    else if (
      typeof value === "string" &&
      value.trim() !== "" &&
      !Number.isNaN(Number(value))
    ) {
      total += Number(value);
    } else return null;
  }
  return total;
}

/**
 * What the answer covers, in the reader's terms, or null when the result is
 * not a grouped answer.
 *
 * Every phrase here is built from counted values. The report used to say
 * "Population: every row in the dataset" whenever the question stated no
 * filters, and "Rows counted 3,575" from the rows that came back -- for a
 * result holding 25 of 45 groups and 55% of the rows.
 */
export function coverageScope(snapshot: ResultSnapshot | null): string | null {
  const coverage = snapshot?.group_coverage;
  if (!coverage) return null;
  const { groups_returned: returned, groups_total: total } = coverage;
  const groups =
    total == null
      ? `${returned.toLocaleString()} group${returned === 1 ? "" : "s"}`
      : coverage.complete
        ? `all ${total.toLocaleString()} group${total === 1 ? "" : "s"}`
        : `${returned.toLocaleString()} of ${total.toLocaleString()} groups`;
  const rows =
    coverage.rows_represented != null && coverage.rows_matching != null
      ? `${coverage.rows_represented.toLocaleString()} of ${coverage.rows_matching.toLocaleString()} matching rows`
      : null;
  const observations =
    coverage.observations_represented != null &&
    coverage.observations_matching != null &&
    (coverage.observations_represented !== coverage.rows_represented ||
      coverage.observations_matching !== coverage.rows_matching)
      ? `${coverage.observations_represented.toLocaleString()} of ${coverage.observations_matching.toLocaleString()} non-null values used`
      : null;
  const ordered = coverage.complete
    ? null
    : coverage.ranked_by_request
      ? "ranked as requested"
      : `ordered by ${coverage.ordering}`;
  return [groups, rows, observations, ordered].filter(Boolean).join(" · ");
}

/** Whether this answer covers every group the question asked for. */
export function isComplete(snapshot: ResultSnapshot | null): boolean | null {
  const coverage = snapshot?.group_coverage;
  return coverage ? coverage.complete : null;
}

/**
 * How the population was restricted, in the reader's terms.
 *
 * Empty means no restriction was asked for -- which the caller states as
 * "every row", never as silence, because a reader cannot tell an
 * unrestricted answer from an unreported restriction.
 */
export function populationClauses(
  contract: QueryContract | null | undefined,
): string[] {
  if (!contract) return [];
  const clauses: string[] = [];
  for (const filter of contract.filters ?? []) {
    const value =
      filter.value === null || filter.value === undefined || filter.value === ""
        ? ""
        : ` ${filter.value}`;
    clauses.push(
      `${filter.column.replaceAll("_", " ")} ${filter.operator}${value}`,
    );
  }
  if (contract.period) {
    const column = contract.period_field
      ? `${contract.period_field.replaceAll("_", " ")} `
      : "";
    clauses.push(`${column}${contract.period[0]} to ${contract.period[1]}`);
  }
  return clauses;
}
