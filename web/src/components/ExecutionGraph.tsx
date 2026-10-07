/**
 * How this analysis ran.
 *
 * The stage sequence from `timeline.ts`, the tool calls from
 * `executionGraph.ts`, and a prose alternative built from both. Nothing
 * here is on a timer and nothing advances on its own: every node exists
 * because an event carried it, and a node that is not drawn is a stage or a
 * call the engine never reported.
 *
 * It is deliberately not the old static DAG. That diagram drew the same
 * five boxes and the same arrows for every run, before anything had
 * happened: a picture of the architecture standing where a reader was
 * looking for a picture of their run. The nodes below are per-run, and a
 * run that made four tool calls draws four.
 *
 * **It lives in the evidence sheet, below the answer, never above it.** A
 * reader arrives for the answer; the machinery is a disclosure over the
 * argument, offered under its own heading rather than placed in front of
 * the thing they came for.
 *
 * Three states are given their own words rather than their own hue alone:
 * `completed`, `failed`, and `refused before execution`, which is a call
 * `preflight` declined and never sent. Colour carries none of that on its
 * own, because each node says its outcome in text, so the distinction survives a
 * monochrome print and a reader who cannot separate the colours.
 */

import { RunTimeline } from "./RunTimeline";
import { describeGraph, toolCallsOf } from "../lib/executionGraph";
import { timelineOf } from "../lib/timeline";
import type { RunEvent } from "../lib/types";

export function ExecutionGraph({
  events,
  expanded = false,
}: {
  events: RunEvent[];
  /**
   * Open the text alternative from the start, for the print appendix.
   *
   * Chromium lays a closed `<details>` out but does not paint it, so CSS
   * alone cannot open one for paper, the same trap the planning audit
   * hit. Opening it in the markup is engine-independent.
   */
  expanded?: boolean;
}) {
  const stages = timelineOf(events);
  const calls = toolCallsOf(events);
  if (stages.length === 0 && calls.length === 0) return null;

  const narrative = describeGraph(stages, calls);

  return (
    <div className="execution-graph" data-testid="execution-graph">
      <RunTimeline events={events} titled={false} />

      {calls.length > 0 && (
        <ol className="graph-calls" data-testid="graph-calls">
          {calls.map((call) => (
            <li
              key={call.key}
              className="graph-call"
              data-call-state={call.state}
              data-testid="graph-call"
            >
              <span className="graph-call-mark" aria-hidden="true" />
              <span className="graph-call-tool">{call.label}</span>
              <span className="graph-call-outcome">{call.outcome}</span>
              <span className="graph-call-agent">{call.agent}</span>
            </li>
          ))}
        </ol>
      )}

      {/* The arrangement -- order, connectors, filled and hollow marks --
          carries meaning that the labels alone do not, so it is restated in
          prose. Built from the same two derivations the picture is built
          from, so it cannot describe a run the picture does not show.

          A `<details>` rather than an `sr-only` block: it is keyboard
          operable, it is available to a sighted reader who would rather
          read the sequence than trace it, and it prints. */}
      <details
        className="graph-alternative"
        data-testid="graph-text-alternative"
        open={expanded}
      >
        <summary>Text alternative</summary>
        <p className="graph-narrative" data-testid="graph-narrative">
          {narrative}
        </p>
      </details>
    </div>
  );
}
