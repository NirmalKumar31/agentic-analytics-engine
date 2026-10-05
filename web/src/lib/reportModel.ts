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

import { terminalPresentation } from "./terminalReport";
import {
  answerResult,
  coverageScope,
  rankedAnswer,
  isComplete,
  observationsUsed,
  populationClauses,
  rowsInScope,
} from "./answer";
import type { RunState } from "./runState";
import type { RunPayload } from "./types";
import type {
  PlannerInterpretation,
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
  /** Severity for the rule bar beside a terminal state. */
  terminalTone?: "supported" | "warn" | "error" | "neutral";
  /**
   * The state identifier, for anything reading the outcome rather than the
   * prose. It is what the stylesheet and the suite key on, so a report
   * cannot look like one outcome while reporting another.
   */
  terminalState?: string;
  /** Deprecated alias of `answer`, kept so both names read naturally. */
  headline?: string;
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
  /**
   * The exact coverage, for the partial notice.
   *
   * "This is a partial breakdown" without numbers is a hedge. The numbers
   * are what make it actionable -- 25 of 45 groups, 3,575 of 6,435 matching
   * rows -- and dropping them was a regression this file's first draft
   * introduced and `answerFirst.test.tsx` caught.
   */
  partialDetail: string | null;
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
    partialDetail: presentationScope(presentation),
    compatibilityDerived: Boolean(presentation.compatibility_derived),
  };
}

function fromFindings({
  report,
  findings,
  charts,
  results,
  queryContract,
  plannerInterpretation,
}: {
  report: Report | null;
  findings: Finding[];
  rejected: Verdict[];
  charts: ChartSpec[];
  results: Record<string, ResultSnapshot>;
  queryContract: QueryContract | null;
  plannerInterpretation?: PlannerInterpretation | null;
}): ReportModel {
  /*
   * Ranked against what was asked, not taken from the top of the list.
   *
   * `directAnswer` only recognises a finding citing a result produced by a
   * tool that executed an accepted contract, and a contract is only
   * accepted for uploaded data -- so on the governed warehouse it always
   * declines and `findings[0]` used to lead. That makes the planner's task
   * ordering into editorial ranking, and a live run showed the cost: a
   * report answering "which customer segments are driving the increase in
   * return rate?" led with a finding about refund amounts while the
   * verified segment comparison sat second.
   *
   * `rankedAnswer` scores published findings against the metrics and
   * groupings the planner recorded. It is not the strict rule that was
   * tried before -- that one printed "no published finding answered this
   * question directly" above three verified statements, because it
   * declined whenever the contract tool was absent. This one declines only
   * when the measure the question named appears in no published finding.
   */
  const ranked = rankedAnswer(findings, results, plannerInterpretation);
  const answer = ranked.finding;
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
    /*
     * The eyebrow says when the headline is not an answer.
     *
     * `Not answered` is for a report with no published finding at all.
     * `Not a direct answer` is the weaker, more common case: findings were
     * published and verified, but none of them is about the measure the
     * question named. Promoting one of those silently is what put a
     * refund trend at the top of a report about customer segments.
     *
     * The finding is still shown. Withholding a verified fact because it
     * is off-topic would be a second mistake; labelling it is the point.
     */
    eyebrow: answer ? (ranked.onTopic ? null : "Not a direct answer") : "Not answered",
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
    partialDetail: scope,
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
  /** The run's terminal state, when it did not end in a verified answer. */
  state?: RunState | null;
  run?: RunPayload | null;
}): ReportModel {
  const base = input.presentation
    ? fromPresentation(input.presentation, input.results)
    : fromFindings({
        report: input.report,
        findings: input.findings,
        rejected: input.rejected,
        charts: input.charts,
        results: input.results,
        queryContract: input.queryContract,
        // The planner's own record of what was asked. Present on the
        // governed-warehouse path, where no contract is accepted.
        plannerInterpretation: input.run?.planner_interpretation ?? null,
      });

  /*
   * A terminal state owns the top of the report.
   *
   * It used to be a separate card beside the report, which meant a refusal
   * stated its reason twice -- once in the card and once as the headline,
   * because the presentation builder sets the headline from the same stop
   * reason. One of them has to win, and it is the one written for a reader.
   */
  const terminal = input.state
    ? terminalPresentation(input.state, input.run ?? null)
    : null;
  if (!terminal) return base;

  // An eyebrow the headline already contains is an echo: "No findings"
  // above "No findings to publish" is the state said twice, one line apart.
  const echoes =
    terminal.eyebrow != null &&
    terminal.headline.toLowerCase().startsWith(terminal.eyebrow.toLowerCase());

  return {
    ...base,
    eyebrow: echoes ? null : terminal.eyebrow,
    terminalState: input.state?.state,
    headline: terminal.headline,
    answer: terminal.headline,
    terminalTone: terminal.tone,
    // The engine's own sentence, under the headline. The *raw*
    // `stopped_reason` is not here: it is in the evidence drawer.
    summary: terminal.explanation,
    // A caveat that repeats what the state already said is an echo.
    notes: base.notes.filter(
      (note) =>
        note.message.trim() !== terminal.headline.trim() &&
        note.message.trim() !== (terminal.explanation ?? "").trim(),
    ),
  };
}
