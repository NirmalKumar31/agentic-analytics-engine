/**
 * One report, two data sources.
 *
 * Real runs come back in two shapes, and this was discovered by capturing
 * payloads from a running server rather than by reading the graph:
 *
 *   an uploaded file  -> `presentation` is populated. The server has
 *                        already decided the headline, the scope line, the
 *                        highlights, the chart and the table.
 *   the demo warehouse -> `presentation` is **null**. It is answered through
 *                        the metric registry, which produces no
 *                        presentation snapshot, so the report has to be
 *                        derived from findings, results and the contract.
 *
 * Redesigning only the presentation path would have left the demo warehouse
 * -- the first thing most visitors open -- on the old seven-panel report,
 * which is the half-migrated composition this work exists to remove.
 *
 * So the view takes a `ReportModel` and neither branch knows which source
 * produced it. Nothing here invents a value: every field is read from the
 * payload or left null, and the view omits what is null rather than
 * printing a placeholder.
 */

import {
  answerResult,
  coverageScope,
  directAnswer,
  isComplete,
  observationsUsed,
  populationClauses,
  rowsInScope,
} from "./answer";
import type {
  AnalysisPresentation,
  ChartSpec,
  DisplayField,
  Finding,
  QueryContract,
  Report,
  ResultSnapshot,
  Verdict,
} from "./types";

export interface ReportHighlight {
  id: string;
  label: string;
  value: string | null;
  compare: string | null;
}

export interface ReportNote {
  code: string;
  severity: string;
  message: string;
}

export interface ReportModel {
  /** Set only when the shape is not an answer: a refusal or a failure. */
  eyebrow: string | null;
  answer: string;
  /** Population, observations, period, coverage -- whatever was recorded. */
  context: string | null;
  summary: string | null;
  chart: ChartSpec | null;
  chartSnapshot: ResultSnapshot | null;
  noChartReason: string | null;
  highlights: ReportHighlight[];
  notes: ReportNote[];
  tableSnapshot: ResultSnapshot | null;
  previewRows: number;
  displayFields: DisplayField[] | null;
  /** True when the breakdown shown is not the whole answer. */
  partial: boolean;
  compatibilityDerived: boolean;
}

const EYEBROW: Record<string, string> = {
  refusal: "Not answered",
  failure: "Not completed",
};

function presentationScope(presentation: AnalysisPresentation): string | null {
  const scope = presentation.scope;
  const parts: string[] = [];
  if (scope.filters.length) parts.push(scope.filters.join("; "));
  if (scope.period) parts.push(scope.period);
  if (scope.rows_represented != null && scope.rows_matching != null) {
    parts.push(
      `${scope.rows_represented.toLocaleString()} of ${scope.rows_matching.toLocaleString()} matching rows`,
    );
  }
  if (
    scope.observations_represented != null &&
    scope.observations_matching != null &&
    (scope.observations_represented !== scope.rows_represented ||
      scope.observations_matching !== scope.rows_matching)
  ) {
    parts.push(
      `${scope.observations_represented.toLocaleString()} of ${scope.observations_matching.toLocaleString()} non-null values used`,
    );
  }
  if (scope.groups_returned != null) {
    const total = scope.groups_total ?? scope.groups_returned;
    parts.push(
      scope.complete === false
        ? `${scope.groups_returned.toLocaleString()} of ${total.toLocaleString()} groups`
        : `${total.toLocaleString()} groups`,
    );
  }
  return parts.length ? parts.join(" · ") : null;
}

function fromPresentation(
  presentation: AnalysisPresentation,
  results: Record<string, ResultSnapshot>,
): ReportModel {
  const table = presentation.table;
  const snapshot = table ? (results[table.result_id] ?? null) : null;
  const chart = presentation.chart;
  const chartSnapshot = chart?.result_id
    ? (results[chart.result_id] ?? null)
    : null;

  const chartSpec: ChartSpec | null =
    chart?.spec && chart.result_id && chart.kind !== "kpi"
      ? {
          chart_id: chart.chart_id ?? `presentation-${chart.result_id}`,
          title: chart.title ?? presentation.headline,
          result_id: chart.result_id,
          finding_ids: [],
          spec: chart.spec,
        }
      : null;

  const headline = presentation.headline.trim();
  return {
    eyebrow: EYEBROW[presentation.shape] ?? null,
    answer: presentation.headline,
    context: presentationScope(presentation),
    summary: presentation.secondary_summary || null,
    chart: chartSpec,
    chartSnapshot,
    noChartReason: chartSpec ? null : (chart?.no_chart_reason ?? null),
    highlights: presentation.highlights.map((highlight) => ({
      id: highlight.highlight_id,
      label: highlight.label,
      value: highlight.value.formatted_value,
      compare: highlight.comparison_value?.formatted_value ?? null,
    })),
    // A caveat that repeats the headline is an echo, not a note: the
    // builder sets both from the same sentence for a refusal.
    notes: presentation.caveats
      .filter((caveat) => caveat.message.trim() !== headline)
      .map((caveat) => ({
        code: caveat.code,
        severity: caveat.severity,
        message: caveat.message,
      })),
    tableSnapshot: snapshot,
    previewRows: table?.preview_limit ?? 12,
    displayFields: table?.display_fields ?? presentation.display_fields ?? null,
    partial: presentation.scope.complete === false,
    compatibilityDerived: Boolean(presentation.compatibility_derived),
  };
}

function fromFindings({
  report,
  findings,
  charts,
  results,
  queryContract,
}: {
  report: Report | null;
  findings: Finding[];
  rejected: Verdict[];
  charts: ChartSpec[];
  results: Record<string, ResultSnapshot>;
  queryContract: QueryContract | null;
}): ReportModel {
  /*
   * `directAnswer` is strict: it wants a finding that answers the question
   * as asked. On a real trend question over the demo warehouse it returned
   * nothing while three supported findings existed, so the display headline
   * read "No published finding answered this question directly" above three
   * verified statements. That is a worse lie than the one it is avoiding.
   *
   * When it declines, the first published finding leads. It is the engine's
   * own first key finding, in its own words -- not a synthesis, and not a
   * claim about whether it answers the question.
   */
  const answer = directAnswer(findings, results) ?? findings[0] ?? null;
  const answerSnapshot = answerResult(answer, results);
  const rows = rowsInScope(answerSnapshot);
  const observations = observationsUsed(answerSnapshot);
  const population = populationClauses(queryContract);
  const scope = coverageScope(answerSnapshot);
  const complete = isComplete(answerSnapshot);

  const context = [
    population.length > 0 ? population.join("; ") : "no row filters requested",
    rows !== null ? `${rows.toLocaleString()} rows` : null,
    observations !== null && observations !== rows
      ? `${observations.toLocaleString()} observations used`
      : null,
    scope,
  ]
    .filter(Boolean)
    .join(" · ");

  // The chart the engine built, paired with the result it was built from.
  const chart = charts[0] ?? null;
  const chartSnapshot = chart ? (results[chart.result_id] ?? null) : null;

  // Supporting findings, in the order the engine ranked them, minus the one
  // already stated as the answer.
  const supporting = findings.filter(
    (finding) => finding.finding_id !== answer?.finding_id,
  );

  return {
    eyebrow: answer ? null : "Not answered",
    answer:
      answer?.text ?? "No verified finding answered the requested analysis.",
    context: context || null,
    /*
     * `executive_summary` is deliberately not used here. On a real run it
     * reads "Each finding below passed the publication checks and is listed
     * once, with the results it was checked against" -- a description of
     * how the report was assembled, not a summary of the answer. Under a
     * display-scale headline that is a line the reader has to skip.
     */
    summary: null,
    chart: chart && chartSnapshot ? chart : null,
    chartSnapshot,
    noChartReason: null,
    highlights: supporting.map((finding) => ({
      id: finding.finding_id,
      label: finding.text,
      value: null,
      compare: null,
    })),
    notes: (report?.limitations ?? []).map((limitation, index) => ({
      code: `limitation-${index}`,
      severity: "info",
      message: limitation,
    })),
    tableSnapshot: answerSnapshot,
    previewRows: 12,
    displayFields: null,
    partial: complete === false,
    compatibilityDerived: false,
  };
}

export function reportModel(input: {
  presentation: AnalysisPresentation | null | undefined;
  report: Report | null;
  findings: Finding[];
  rejected: Verdict[];
  charts: ChartSpec[];
  results: Record<string, ResultSnapshot>;
  queryContract: QueryContract | null;
}): ReportModel {
  if (input.presentation) {
    return fromPresentation(input.presentation, input.results);
  }
  return fromFindings({
    report: input.report,
    findings: input.findings,
    rejected: input.rejected,
    charts: input.charts,
    results: input.results,
    queryContract: input.queryContract,
  });
}
