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
import { RunFlowchart } from "./RunFlowchart";
import { opensSheet } from "./SideSheet";
import { ResultPanel } from "./ResultPanel";
import type { ReportModel } from "../lib/reportModel";
import type { RunPayload } from "../lib/types";

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
   * The phone-width fold is gone, and it is worth saying why rather than
   * leaving a gap where it was.
   *
   * It showed one highlight on arrival below 640px and hid the rest behind
   * a `<details>`, above a threshold of three. **The presentation contract
   * emits at most two highlights for every shape it builds** -- highest
   * and lowest -- so the threshold was never met and the branch never ran
   * in product output. It appeared to work only because the published
   * recordings carried no presentation snapshot and the report fell back
   * to listing the engine's own findings, which is the defect
   * `presentation/fields.py` and `recordings/record.py` were corrected
   * for.
   *
   * So the fold was kept alive by a bug, and keeping it for a constructed
   * test model would have left dead markup, a dead constant, a dead media
   * query and a disclosure control a keyboard user could reach.
   *
   * If the contract later emits more than three reader-facing highlights,
   * a fold is the right answer again -- and the order is: raise the cap,
   * produce a recording through the normal pipeline that reaches it, then
   * build the fold against that.
   */

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
          {/* Every highlight the run published. No fold: see above. */}
          <ol className="finding-list">
            {model.highlights.map((highlight, index) => (
              <Finding key={highlight.id} highlight={highlight} rank={index + 1} />
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

      {/*
        The run's shape, under the answer.

        The full graph is in the evidence sheet and stays there; behind one
        control it was invisible, so a reader who wanted to know whether
        anything ran had no sign that there was anything to open.

        Absent in a Compare *pane*, and the reason has changed. It used to
        be that Compare showed no diagram at all. Compare now draws one
        per strategy itself, inside "How each strategy got there", because
        that is where the two runs are being compared and a pane is not:
        when the strategies agree, Compare renders one pane's report as
        the shared answer, so a diagram drawn from inside the pane would
        show one run's stages under a heading that speaks for both.
      */}
      {!compact && run?.events && run.events.length > 0 && (
        <RunFlowchart events={run.events} onShowEvidence={onShowEvidence} />
      )}

      {/*
        One control. Everything technical is behind it.

        It said "Show work", which reads as an offer to explain the answer
        -- and what is behind it is the audit: the route, the accepted
        contract, the coverage, the timings, the execution graph and the
        activity trace. "Inspect evidence" says what it is, and says the
        same thing Compare's control says ("Inspect both traces"), so the
        two surfaces no longer name one idea two ways.
      */}
      {!compact && onShowEvidence && (
      <div className="report-actions">
        <button
          type="button"
          className="btn"
          {...opensSheet(onShowEvidence)}
          data-testid="inspect-evidence"
        >
          Inspect evidence <span aria-hidden="true">→</span>
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
