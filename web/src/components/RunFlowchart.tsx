/**
 * How this analysis ran — on the report, under the answer.
 *
 * This replaces the canvas summary, which was six unlabelled dots, a line
 * of counts and a control. The counts were true and the dots were honestly
 * derived, and together they were not a diagram: nothing said which stage
 * was which, nothing connected one to the next, and the only labelled
 * picture of the run lived behind "Inspect evidence" where a reader
 * looking at their answer never opened it.
 *
 * So the labelled picture is here: the stage spine with arrows between the
 * boxes, each box carrying its own state in words, and the tool calls that
 * `compute` fanned out into. The counts stay, under it, because "2 queries
 * returned, 1 finding verified" is the shortest true summary of a run and
 * a diagram does not replace it.
 *
 * **What is deliberately still not here.** The evidence sheet's graph
 * carries the engine's unedited text -- a stopped stage's reason, a failed
 * call's error -- and that was the stated reason an earlier attempt to put
 * the graph on the canvas was refused by two tests. It is still the rule.
 * `lib/flowchart.ts` sanitises both, the canvas says "stopped here" and
 * points at the evidence, and the engine's exact words stay one control
 * away where an auditor wants them. The per-call *audit* rows -- arguments,
 * durations, the agent that reached for the tool -- stay there too.
 *
 * **Motion.** Nothing advances on its own. A stage box transitions on
 * colour, which only `data-state` changes, and `data-state` changes only
 * when an event arrives. A call chip animates once, on mount, because a
 * call chip exists only because an event created it. No loop, no
 * `infinite`, nothing on a timer: if the backend goes quiet the diagram is
 * still, which is the truth.
 */

import { flowchartOf } from "../lib/flowchart";
import { progressOf, workDone } from "../lib/runProgress";
import type { RunEvent } from "../lib/types";

export function RunFlowchart({
  events,
  onShowEvidence,
}: {
  events: RunEvent[];
  /** Absent inside a Compare pane, which has one control for both runs. */
  onShowEvidence?: () => void;
}) {
  const { stages, calls, branchAt, alternative } = flowchartOf(events);
  if (stages.length === 0) return null;

  const done = workDone(progressOf(events));

  return (
    <section
      className="run-flow"
      aria-label="How this analysis ran"
      data-testid="run-flowchart"
    >
      <h2 className="section-heading">How this analysis ran</h2>

      {/*
        An ordered list, because the order is the content. The connectors
        are drawn by the stylesheet as pseudo-elements with no text, so
        nothing announces an arrow -- the sequence is already carried by
        the list and restated in the text alternative below.
      */}
      <ol className="run-flow-track" data-testid="run-flow-track">
        {stages.map((stage) => (
          <li
            key={stage.id}
            className="run-flow-node"
            data-stage={stage.id}
            data-state={stage.state}
            data-testid={`flow-stage-${stage.id}`}
          >
            <span className="run-flow-box">
              <span className="run-flow-mark" aria-hidden="true" />
              <span className="run-flow-label">{stage.label}</span>
              {/* The state in words as well as in fill. A reader who
                  cannot separate the hues still has to be able to tell a
                  finished stage from a stopped one. */}
              <span className="run-flow-state">{stage.stateLabel}</span>
              {stage.note && <span className="run-flow-note">{stage.note}</span>}
            </span>
          </li>
        ))}
      </ol>

      {branchAt && (
        /*
          The fan-out, as its own block rather than nested inside one
          column of the spine.

          Nested, it had to fit a sixth of the track's width on a wide
          screen and the chips were unreadable; positioned absolutely
          under its own column, it broke at every width the spine reflows
          at. As a block it reads at 360px and at 1920px, prints, and the
          connection to the stage is stated in words -- which is also what
          a screen reader gets, rather than a drawn line it cannot see.
        */
        <div className="run-flow-branch" data-testid="run-flow-branch">
          <p className="run-flow-branch-label">
            {branchAt} ran {calls.length} {calls.length === 1 ? "query" : "queries"}
          </p>
          <ul className="run-flow-calls">
            {calls.map((call) => (
              <li
                key={call.key}
                className="run-flow-call"
                data-call-state={call.state}
                data-testid="flow-call"
              >
                <span className="run-flow-call-label">{call.label}</span>
                <span className="run-flow-call-note">{call.note}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="run-flow-counts" data-testid="run-flow-counts">
        {/* Counts, not a status. "Running" would be a claim about now; a
            count is a record of what the engine reported. */}
        {done.length > 0
          ? done.join(" · ")
          : "The engine reported no completed work for this run."}
      </p>

      {/* The arrangement restated, for a reader who cannot trace it and
          for one who would rather read it than trace it. A `<details>`
          rather than an `sr-only` block: keyboard operable, available to
          everyone, and it prints. */}
      <details className="run-flow-alternative" data-testid="run-flow-alternative">
        <summary>Text alternative</summary>
        <p data-testid="run-flow-narrative">{alternative}</p>
      </details>

      {onShowEvidence ? (
        /*
          One control, and it still opens the sheet rather than expanding
          in place. What is behind it is the audit: the route, the accepted
          contract, the coverage, the timings, the per-call rows with their
          arguments and the engine's own words for anything that stopped.
          None of that belongs between a reader and their answer.
        */
        <button
          type="button"
          className="btn ghost small"
          data-testid="run-flow-open"
          onClick={onShowEvidence}
        >
          Every stage, call and timing <span aria-hidden="true">→</span>
        </button>
      ) : null}
    </section>
  );
}
