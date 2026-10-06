/**
 * The run's shape, on the canvas, in one line.
 *
 * The full execution graph lives in the evidence sheet, under "How this
 * analysis ran", and that is still the right home for it: the stages, the
 * per-call outcomes and the text alternative are an audit, and an audit
 * does not belong between a reader and their answer.
 *
 * But behind one control it was invisible. A reader who wanted to know
 * whether anything actually ran -- which is the question this product
 * exists to answer -- had no sign that there was anything to open. So this
 * is the sign: the same marks the graph draws, the counts that came from
 * events, and a control that opens the rest.
 *
 * It is **under the answer**, never above it, and it is a summary rather
 * than a second graph: no per-call rows, no stage labels, no engine text.
 * Everything here is derived from the same two functions the full graph
 * uses, so the two cannot disagree about what happened.
 *
 * It does not embed the graph. A first version did, in a `<details>`, and
 * two tests refused it: the graph carries the engine's unedited stop
 * reason, which the canvas does not take, and it put `run-timeline` back
 * on the canvas that the redesign removed it from.
 */

import { progressOf, workDone } from "../lib/runProgress";
import { timelineOf } from "../lib/timeline";
import type { RunEvent } from "../lib/types";

export function ExecutionGraphSummary({
  events,
  onShowEvidence,
}: {
  events: RunEvent[];
  /** Absent inside a Compare pane, which has one control for both runs. */
  onShowEvidence?: () => void;
}) {
  const stages = timelineOf(events);
  if (stages.length === 0) return null;

  const progress = progressOf(events);
  const done = workDone(progress);

  return (
    <section
      className="graph-summary"
      aria-label="How this analysis ran"
      data-testid="execution-graph-summary"
    >
      <h2 className="section-heading">How this analysis ran</h2>

      {/* The marks only, no labels: the labels are in the full graph, and
          five words under five dots is a second timeline competing with
          the first. Hidden from the accessibility tree because the counts
          and the full graph both say this in words. */}
      <ol className="graph-summary-track" aria-hidden="true">
        {stages.map((stage) => (
          <li
            key={stage.id}
            className="graph-summary-mark"
            data-stage={stage.id}
            data-state={stage.state}
          />
        ))}
      </ol>

      <p className="graph-summary-line" data-testid="execution-graph-summary-line">
        {/* Counts, not a status. "Running" would be a claim about now; a
            count is a record of what the engine reported. */}
        {done.length > 0
          ? done.join(" · ")
          : "The engine reported no completed work for this run."}
      </p>

      {onShowEvidence ? (
        /*
          A control that opens the sheet, not an inline copy of the graph.

          The first version embedded `ExecutionGraph` in a `<details>` here,
          and two tests refused it for the same reason: the full graph
          carries the engine's *unedited* stop reason -- "the question could
          not be mapped safely: the question asks about 'gross margin',
          which is not a column of this table" -- and the canvas is not
          where unedited engine text goes. The refused report already says
          so in its own words and points at the evidence. It also made
          `run-timeline` resident on the canvas, which the information
          architecture forbids.

          So this stays a summary: marks, counts, and a way through.
        */
        <button
          type="button"
          className="btn ghost small"
          data-testid="execution-graph-open"
          onClick={onShowEvidence}
        >
          Every stage and call <span aria-hidden="true">→</span>
        </button>
      ) : null}
    </section>
  );
}
