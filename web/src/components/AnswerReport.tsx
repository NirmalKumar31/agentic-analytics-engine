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

import { useMediaQuery } from "../lib/useMediaQuery";
import { Chart } from "./Chart";
import { EvidenceBody } from "./EvidenceDrawer";
import { opensSheet } from "./SideSheet";
import { ResultPanel } from "./ResultPanel";
import type { ReportModel } from "../lib/reportModel";
import type { RunPayload } from "../lib/types";

/** The phone widths the brief names: 360 and 390 are both below this. */
const FOLD_BELOW = 640;
/** Below this many, folding hides a line to save a line. */
const FOLD_ABOVE = 3;

function Finding({
  highlight,
  rank,
}: {
  highlight: ReportModel["highlights"][number];
  rank: number;
}) {
  return (
    <li className="finding-item">
      <span className="finding-rank" aria-hidden="true">
        {rank}
      </span>
      <span className="finding-body">
        <span className="finding-label">{highlight.label}</span>
        {highlight.value && (
          <span className="finding-value figure">{highlight.value}</span>
        )}
        {highlight.compare && (
          <span className="finding-compare">vs {highlight.compare}</span>
        )}
      </span>
    </li>
  );
}

/**
 * `contract a7c31a · sha 5913f6e · 21 ms`, beside the one control.
 *
 * The approved mockup carries this line and the implementation dropped it,
 * on the reading that requirement 6 forbids technical material on the
 * default canvas. That requirement names the panels it is about -- the
 * activity log, the stage list, the planning audit, the DAG -- and this is
 * none of them: it is the identity of what produced the numbers above it,
 * in the same register as the dataset strip that names the file.
 *
 * Which is also why it is three facts and not four. It answers "can I
 * refer to this run later" without starting to explain the run; everything
 * that explains it is still behind "Show work".
 */
function ReportStamp({ run }: { run: RunPayload | null }) {
  if (!run) return null;
  const contract = run.query_contract?.contract_hash?.slice(0, 6) ?? null;
  const build =
    run.build_sha && run.build_sha !== "unknown"
      ? run.build_sha.slice(0, 7)
      : null;
  const total = run.timings?.total_ms ?? null;

  const parts = [
    contract ? `contract ${contract}` : null,
    build ? `sha ${build}` : null,
    total != null ? `${Math.round(total)} ms` : null,
  ].filter((part): part is string => part !== null);

  // Nothing rather than a line of placeholders: a stamp that says
  // "contract — · sha — · — ms" is worse than no stamp, because it looks
  // like a record and holds none.
  if (parts.length === 0) return null;

  return (
    <span className="report-stamp mono" data-testid="report-stamp">
      {parts.join(" · ")}
    </span>
  );
}

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
  /*
   * Requirement: exactly one finding expanded on arrival at phone widths.
   *
   * Only where there is something to fold, and only on the report itself
   * -- a Compare pane is already two columns of summary and folding inside
   * one would bury the comparison. `useMediaQuery` rather than a CSS rule
   * because the fold is a change of markup, not of layout: a `<details>`
   * forced open by a media query still carries a summary nobody wants on a
   * desktop, and a reader tabbing past it finds a control that does
   * nothing.
   */
  const narrow = useMediaQuery(`(max-width: ${FOLD_BELOW}px)`);
  const folds = !compact && narrow && model.highlights.length > FOLD_ABOVE;
  const shown = folds ? model.highlights.slice(0, 1) : model.highlights;
  const folded = folds ? model.highlights.slice(1) : [];

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
            {shown.map((highlight, index) => (
              <Finding key={highlight.id} highlight={highlight} rank={index + 1} />
            ))}
          </ol>
          {folded.length > 0 && (
            /*
             * One finding on arrival at phone widths, which is the brief's
             * requirement and only makes sense where there is something to
             * fold: an engine that published two one-line rows has nothing
             * to hide, and hiding one to save a line would be worse than
             * showing it.
             *
             * A `<details>`, so it is open to the keyboard and to a reader
             * who prints -- the print cascade expands every disclosure, so
             * the paper copy is never the folded one.
             */
            <details className="findings-more">
              <summary>
                {folded.length} more {folded.length === 1 ? "finding" : "findings"}
              </summary>
              <ol className="finding-list" start={shown.length + 1}>
                {folded.map((highlight, index) => (
                  <Finding
                    key={highlight.id}
                    highlight={highlight}
                    rank={shown.length + index + 1}
                  />
                ))}
              </ol>
            </details>
          )}
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
        <ReportStamp run={run ?? null} />
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
