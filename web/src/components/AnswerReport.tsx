/**
 * The report, answer first.
 *
 * What was here opened with a panel headed **REPORT**, then a line labelled
 * "Question", then a bordered card labelled "Verified answer" holding the
 * sentence a reader came for, then **CHARTS**, then **ANALYSIS**, then
 * **LIMITATIONS AND NEXT QUESTIONS**, then **ANALYSIS** again (a duplicate
 * heading, this time an agent diagram), then **STAGES**, then **ACTIVITY**.
 * Seven panels, in which the answer was the third thing and was wearing a
 * label describing its own epistemic status before stating itself.
 *
 * The order here is the order a reader needs:
 *
 *   question          what was asked, quietly, so the answer has a subject
 *   ANSWER            display scale, one per page, never truncated
 *   context line      population, observations, period, coverage
 *   chart             full content width
 *   findings          ranked: headline, driver, exception, caveat
 *   result table      every row, in its own scroll frame
 *   [ Show work → ]   one control, everything technical behind it
 *
 * Nothing technical is resident. Route, contract, build SHA, coverage,
 * verification outcomes, cited cells, timings, planner fallback, the
 * activity trace and the limitations all live in the evidence drawer --
 * **relocated, not deleted**, which is the difference between a disclosure
 * and a loss.
 */

import { Chart } from "./Chart";
import { EvidenceBody } from "./EvidenceDrawer";
import { opensSheet } from "./SideSheet";
import { ResultPanel } from "./ResultPanel";
import type { ReportModel } from "../lib/reportModel";
import type { RunPayload } from "../lib/types";

export function AnswerReport({
  question,
  model,
  publishedCount,
  withheldCount,
  onShowEvidence,
  compact,
  run,
}: {
  question: string;
  model: ReportModel;
  publishedCount: number;
  withheldCount: number;
  /** The run, for the print appendix. Absent in a Compare pane. */
  run?: RunPayload | null;
  /** Opens the evidence drawer. One trigger, for the whole report. */
  onShowEvidence?: () => void;
  /**
   * Inside a Compare pane.
   *
   * The question is stated once at the top of the comparison, and the
   * comparison has one evidence control for both strategies -- so a pane
   * repeats neither. Without this the screen carried the question three
   * times and three "Show work" buttons, which is the duplication Compare
   * exists to remove.
   */
  compact?: boolean;
}) {
  return (
    <article
      // Keyed on the tone, not on the eyebrow. The eyebrow is suppressed
      // when the headline already states the outcome ("No findings" above
      // "No findings to publish"), and gating the rule bar on it left a
      // terminal state with no severity mark at all.
      className={`report${compact ? " report--compact" : ""}${
        model.terminalTone ? " report--terminal" : ""
      }`}
      data-tone={model.terminalTone}
      data-state={model.terminalState}
      data-testid={compact ? "compare-report" : "report-panel"}
    >
      {!compact && (
        <p className="report-question" data-testid="report-question">
          {question}
        </p>
      )}

      {/* Only when the shape is not an answer. A "Verified answer" label
          above every answer is a badge the reader learns to skip, and it
          delays the sentence they came for by one line. */}
      {model.eyebrow && (
        <p
          className="report-eyebrow"
          data-testid="terminal-state"
          data-tone={model.terminalTone ?? "warn"}
        >
          {model.eyebrow}
        </p>
      )}

      {compact ? (
        <p className="compare-answer" data-testid="direct-answer">
          {model.answer}
        </p>
      ) : (
        <h1 className="display" data-testid="direct-answer">
          {model.answer}
        </h1>
      )}

      {model.context && (
        <p className="context-line" data-testid="answer-coverage">
          {model.context} · {publishedCount} verified, {withheldCount} withheld
        </p>
      )}

      {model.partial && (
        <p className="notice warn" role="status" data-testid="partial-answer">
          This is a partial breakdown.
          {model.partialDetail ? ` ${model.partialDetail}.` : ""} It is not the
          complete answer to the question as asked.
        </p>
      )}

      {model.summary && <p className="report-summary">{model.summary}</p>}

      {model.chart && model.chartSnapshot ? (
        <section className="report-visual" aria-label="Analysis visualisation">
          <Chart chart={model.chart} snapshot={model.chartSnapshot} />
        </section>
      ) : model.noChartReason ? (
        <p className="report-no-chart">{model.noChartReason}</p>
      ) : null}

      {model.highlights.length > 0 && (
        <section className="findings" aria-label="What the numbers show">
          <h2 className="section-heading">What the numbers show</h2>
          <ol className="finding-list">
            {model.highlights.map((highlight, index) => (
              <li key={highlight.id} className="finding-item">
                <span className="finding-rank" aria-hidden="true">
                  {index + 1}
                </span>
                <span className="finding-body">
                  <span className="finding-label">{highlight.label}</span>
                  {highlight.value && (
                    <span className="finding-value figure">
                      {highlight.value}
                    </span>
                  )}
                  {highlight.compare && (
                    <span className="finding-compare">
                      vs {highlight.compare}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {model.notes.length > 0 && (
        <section className="report-notes" aria-label="Analysis notes">
          <h2 className="section-heading">What to be careful about</h2>
          {model.notes.map((note) => (
            <p className={`notice ${note.severity}`} key={note.code}>
              {note.message}
            </p>
          ))}
        </section>
      )}

      {model.tableSnapshot && (
        <section className="report-table" aria-label="Result table">
          <h2 className="section-heading">Result</h2>
          <ResultPanel
            snapshot={model.tableSnapshot}
            question={question}
            previewRows={model.previewRows}
            displayFields={model.displayFields ?? undefined}
          />
        </section>
      )}

      {/* One control. Everything technical is behind it. */}
      {!compact && onShowEvidence && (
      <div className="report-actions">
        <button
          type="button"
          className="btn"
          {...opensSheet(onShowEvidence)}
          data-testid="show-work"
        >
          Show work <span aria-hidden="true">→</span>
        </button>
        <button
          type="button"
          className="btn ghost small no-print"
          onClick={() => window.print()}
        >
          Print / Save PDF
        </button>
      </div>
      )}

      {/*
        The evidence appendix.

        Hidden on screen -- the drawer is where a reader opens it -- and
        visible in print, because technical truth cannot vanish from a
        document merely because the screen hid it behind a control. The
        `hidden` attribute rather than a class: it keeps the appendix out of
        the accessibility tree and out of the tab order on screen, and a
        print rule overrides it. Rendering it only while printing was the
        alternative and it is not reliable -- `page.pdf()` does not fire
        `beforeprint`, so the appendix would be missing from exactly the
        artefact it exists for.
      */}
      {!compact && run && (
        <section
          className="print-appendix"
          data-print-appendix=""
          data-testid="print-appendix"
          hidden
        >
          <h2 className="section-heading">Appendix: evidence</h2>
          <EvidenceBody run={run} expanded />
        </section>
      )}

      {model.compatibilityDerived && (
        <p className="report-compat">
          This archived run predates the presentation contract; its layout is
          derived from the preserved evidence.
        </p>
      )}
    </article>
  );
}
