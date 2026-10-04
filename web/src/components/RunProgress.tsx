/**
 * What a run is doing while it runs.
 *
 * Three things used to be here, inside a `<section class="panel">` headed
 * **ANALYSIS**: a static agent DAG, a STAGES card list, and the activity
 * log. The DAG drew the same boxes and arrows for every run and was on
 * screen before anything had happened; the stage cards restated the same
 * five steps a third time, after the stepper and the DAG had each already
 * said them.
 *
 * One timeline now, derived from backend events only. The activity log is
 * the per-call record -- different information, and technical -- so it
 * moved into the evidence drawer with everything else the canvas gave up.
 */

import { RunTimeline } from "./RunTimeline";
import type { RecordingSummary, RunEvent } from "../lib/types";

export function RunProgress({
  events,
  replay,
}: {
  events: RunEvent[];
  replay: RecordingSummary | null;
}) {
  return (
    <>
      <RunTimeline events={events} />
      {replay && (
        <p className="timeline-provenance">
          recorded run · <span className="mono">{replay.recording_id}</span>
        </p>
      )}
    </>
  );
}
