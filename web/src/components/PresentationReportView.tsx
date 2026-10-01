import type {
  AnalysisPresentation,
  ChartSpec,
  ResultSnapshot,
} from "../lib/types";
import { Chart } from "./Chart";
import { ResultPanel } from "./ResultPanel";

interface Props {
  question: string;
  presentation: AnalysisPresentation;
  results: Record<string, ResultSnapshot>;
  onShowWork: (findingId: string) => void;
}

function scopeText(presentation: AnalysisPresentation): string | null {
  const scope = presentation.scope;
  const parts: string[] = [];
  if (scope.filters.length) parts.push(scope.filters.join("; "));
  if (scope.period) parts.push(scope.period);
  if (scope.rows_represented != null && scope.rows_matching != null) {
    parts.push(`${scope.rows_represented.toLocaleString()} of ${scope.rows_matching.toLocaleString()} matching rows`);
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

/**
 * Renders the deterministic presentation contract. No values, labels, shape
 * or chart semantics are inferred here; the API has already established them.
 */
export function PresentationReportView({ question, presentation, results, onShowWork }: Props) {
  const table = presentation.table;
  const snapshot = table ? results[table.result_id] : undefined;
  const chart = presentation.chart;
  const chartSpec: ChartSpec | null = chart?.spec && chart.result_id
    ? {
        chart_id: chart.chart_id ?? `presentation-${chart.result_id}`,
        title: chart.title ?? presentation.headline,
        result_id: chart.result_id,
        finding_ids: presentation.highlights.flatMap(() =>
          presentation.provenance_refs
            ?.filter((ref) => ref.evidence_cells.some((cell) => cell.result_id === chart.result_id))
            .map((ref) => ref.finding_id) ?? [],
        ),
        spec: chart.spec,
      }
    : null;
  const scope = scopeText(presentation);
  const primaryFinding = presentation.provenance_refs?.[0]?.finding_id;

  return (
    <section className="panel presentation-report" data-testid="report-panel">
      <div className="panel-head">
        <h2>Report</h2>
        <span className="spacer" />
        <button className="btn ghost small no-print" onClick={() => window.print()}>
          Print / Save PDF
        </button>
      </div>
      <div className="panel-body stack">
        <p className="question-label">Question</p>
        <p className="question-text">{question}</p>

        <article className={`finding answer-card shape-${presentation.shape}`} data-testid="direct-answer">
          <p className="eyebrow">Verified answer</p>
          <h3>{presentation.headline}</h3>
          {presentation.secondary_summary ? <p>{presentation.secondary_summary}</p> : null}
          {presentation.highlights.length > 0 ? (
            <dl className="highlight-grid">
              {presentation.highlights.map((highlight) => (
                <div key={highlight.highlight_id}>
                  <dt>{highlight.label}</dt>
                  <dd>{highlight.value.formatted_value}</dd>
                  {highlight.comparison_value ? <small>vs {highlight.comparison_value.formatted_value}</small> : null}
                </div>
              ))}
            </dl>
          ) : null}
          {scope ? <p className="answer-scope" data-testid="answer-coverage">{scope}</p> : null}
          {primaryFinding ? (
            <button className="btn ghost small" onClick={() => onShowWork(primaryFinding)}>Show work →</button>
          ) : null}
        </article>

        {chart?.kind === "kpi" ? null : chartSpec && snapshot ? (
          <section className="report-visual" aria-label="Analysis visualisation">
            <Chart chart={chartSpec} snapshot={snapshot} onOpenProvenance={onShowWork} />
          </section>
        ) : chart?.no_chart_reason ? (
          <p className="notice info small">{chart.no_chart_reason}</p>
        ) : null}

        {snapshot ? (
          <section className="report-table">
            <h3>Result table</h3>
            <ResultPanel
              snapshot={snapshot}
              question={question}
              previewRows={table?.preview_limit ?? 12}
              displayFields={table?.display_fields ?? presentation.display_fields}
            />
          </section>
        ) : null}

        {presentation.caveats.length ? (
          <section className="caveat-list" aria-label="Analysis notes">
            <h3>Notes</h3>
            {presentation.caveats.map((caveat) => <p className={`notice ${caveat.severity}`} key={caveat.code}>{caveat.message}</p>)}
          </section>
        ) : null}

        {presentation.compatibility_derived ? <p className="small dim">This archived run predates the presentation contract; its layout is derived from the preserved evidence.</p> : null}
      </div>
    </section>
  );
}
