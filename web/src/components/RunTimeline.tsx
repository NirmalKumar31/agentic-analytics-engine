/**
 * What is happening to this run, right now.
 *
 * A horizontal sequence on a wide screen, a vertical one on a phone. Each
 * stage carries its own state, and every state comes from an event the
 * backend emitted -- see `lib/timeline.ts` for the derivation and for why
 * the AI interpretation stage is conditional.
 *
 * It does not animate a travelling trace. The storyboard allows one, and it
 * is deliberately not here yet: an animation that advances on its own is
 * indistinguishable from an animation that advances because something
 * happened, and this component's whole claim is that it only moves when the
 * engine moves. Motion arrives in step I with its reduced-motion tests.
 */

import { timelineOf } from "../lib/timeline";
import type { RunEvent } from "../lib/types";

export function RunTimeline({
  events,
  titled = true,
}: {
  events: RunEvent[];
  /** False where the caller already names this region. */
  titled?: boolean;
}) {
  const stages = timelineOf(events);
  if (stages.length === 0) return null;

  const active = stages.find((stage) => stage.state === "active");
  const stoppedAt = stages.find((stage) => stage.state === "stopped");

  return (
    <section className="timeline" data-testid="run-timeline">
      {/* Only when nothing else names it.

          On the report canvas the timeline stands alone and needs its own
          heading. Inside the evidence drawer it sits in a section that is
          already labelled "Run progress" and carries a visible heading
          saying the same thing, so rendering this made a screen reader
          announce the name three times -- and, once the print appendix
          rendered the drawer's body unconditionally, put two identical
          headings in the document for every report. */}
      {titled && <h2 className="sr-only">Run progress</h2>}

      {/* One live region for the whole timeline. Announcing every stage
          separately would narrate five changes for one step forward. */}
      <p className="sr-only" role="status">
        {stoppedAt
          ? `Run stopped at ${stoppedAt.label}.`
          : active
            ? `${active.label} in progress.`
            : "Run complete."}
      </p>

      <ol className="timeline-track">
        {stages.map((stage) => (
          <li
            key={stage.id}
            className="timeline-stage"
            data-stage={stage.id}
            data-state={stage.state}
            data-testid={`stage-${stage.id}`}
          >
            <span className="timeline-mark" aria-hidden="true" />
            <span className="timeline-label">{stage.label}</span>
            {stage.detail && (
              <span className="timeline-detail">{stage.detail}</span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
