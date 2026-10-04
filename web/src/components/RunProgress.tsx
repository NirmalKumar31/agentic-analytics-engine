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
 * One timeline now, derived from backend events only. The activity log
 * stays -- it is the per-call record, which is different information -- and
 * moves into the evidence drawer in step E.
 */

import { ActivityLog } from "./ActivityLog";
import { RunTimeline } from "./RunTimeline";
import type { RecordingSummary, RunEvent, RunPayload } from "../lib/types";

export function RunProgress({
  run,
  events,
  replay,
  showTrace,
  onToggleTrace,
  running,
}: {
  run: RunPayload | null;
  events: RunEvent[];
  replay: RecordingSummary | null;
  showTrace: boolean;
  onToggleTrace: () => void;
  running: boolean;
}) {
  return (
    <>
      <RunTimeline events={events} />
      {replay && (
        <p className="timeline-provenance">
          recorded run · <span className="mono">{replay.recording_id}</span>
        </p>
      )}
      <ActivityLog
        events={events}
        trace={run?.mcp_trace ?? []}
        showTrace={showTrace}
        onToggleTrace={onToggleTrace}
        running={running}
      />
    </>
  );
}
