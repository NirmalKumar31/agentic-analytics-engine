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

import type {
  Finding,
  PlannerInterpretation,
  QueryContract,
  ResultSnapshot,
} from "./types";

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

/**
 * How well a published finding answers what was actually asked.
 *
 * `directAnswer` only recognises a finding that cites a result produced by
 * a tool executing an accepted contract, and a contract is only accepted
 * for uploaded data. On the governed warehouse it therefore always
 * declines, and the headline fell back to `findings[0]`, the planner's
 * own first finding, which makes task ordering into editorial ranking.
 *
 * A live run showed what that costs. Asked "Which customer segments are
 * driving the increase in return rate?", the report led with a finding
 * about `refund_amount` over two months, while the finding that compared
 * `return_rate` across `customer_segment`, the question answered,
 * verified and published, sat second.
 *
 * So findings are scored against the components the planner says the
 * question fixed. The metric is weighted above the grouping because a
 * finding about the wrong measure cannot answer the question at all,
 * whereas one about the right measure without the grouping is at least
 * about the right thing.
 */
function scoreAgainstInterpretation(
  finding: Finding,
  results: Record<string, ResultSnapshot>,
  interpretation: PlannerInterpretation,
): number {
  const haystack = new Set<string>();
  for (const id of finding.result_ids) {
    const snapshot = results[id];
    if (!snapshot) continue;
    for (const column of snapshot.columns) haystack.add(column.toLowerCase());
    // The parameters a task was dispatched with name the metric even when
    // the result's columns rename it.
    for (const value of Object.values(snapshot.parameters ?? {})) {
      if (typeof value === "string") haystack.add(value.toLowerCase());
      else if (Array.isArray(value)) {
        for (const item of value) {
          if (typeof item === "string") haystack.add(item.toLowerCase());
        }
      }
    }
  }

  let score = 0;
  const metrics = interpretation.metrics ?? [];
  const dimensions = interpretation.dimensions ?? [];
  if (metrics.length > 0 && metrics.some((m) => haystack.has(m.toLowerCase()))) {
    score += 4;
  }
  if (dimensions.length > 0 && dimensions.some((d) => haystack.has(d.toLowerCase()))) {
    score += 2;
  }
  return score;
}

/** Whether a finding is about the measure the question named. */
function matchesRequestedMetric(
  finding: Finding,
  results: Record<string, ResultSnapshot>,
  interpretation: PlannerInterpretation,
): boolean {
  const metrics = interpretation.metrics ?? [];
  if (metrics.length === 0) return true;
  return scoreAgainstInterpretation(finding, results, interpretation) >= 4;
}

/**
 * The finding that leads the report.
 *
 * In order of authority: a finding citing the accepted contract's own
 * result; then the best match against what the planner says was asked;
 * then nothing. The last case is deliberate, because `onTopic: false` means no
 * published finding was about the measure the question named, and saying
 * so is better than promoting an unrelated fact to the headline.
 *
 * It does not reproduce the earlier failure where a strict rule printed
 * "no published finding answered this question" above three verified
 * statements: that rule declined whenever the contract tool was absent,
 * which on the warehouse is always. This one declines only when the
 * requested measure appears in no published finding at all.
 */
export function rankedAnswer(
  findings: Finding[],
  results: Record<string, ResultSnapshot>,
  interpretation: PlannerInterpretation | null | undefined,
): { finding: Finding | null; onTopic: boolean } {
  const contractAnswer = directAnswer(findings, results);
  if (contractAnswer) return { finding: contractAnswer, onTopic: true };
  if (findings.length === 0) return { finding: null, onTopic: false };

  if (!interpretation || (interpretation.metrics ?? []).length === 0) {
    // Nothing to rank against. The engine's own first finding leads, as
    // before. This is not worse than it was, it is just not better.
    return { finding: findings[0] ?? null, onTopic: true };
  }

  const ranked = [...findings]
    .map((finding) => ({
      finding,
      score: scoreAgainstInterpretation(finding, results, interpretation),
    }))
    // Stable: equal scores keep the engine's order.
    .sort((a, b) => b.score - a.score);

  const best = ranked[0]?.finding ?? null;
  return {
    finding: best,
    onTopic: best ? matchesRequestedMetric(best, results, interpretation) : false,
  };
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
 * is present, and that is exactly what coverage records, so the fallback
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
 * filters, and "Rows counted 3,575" from the rows that came back, so for a
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
 * Empty means no restriction was asked for, which the caller states as
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
