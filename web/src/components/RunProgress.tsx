import { ActivityLog } from "./ActivityLog";
import { ExecutionFlow } from "./ExecutionFlow";
import { ExecutionLane } from "./ExecutionLanes";
import type { RecordingSummary, RunEvent, RunPayload, UiMode } from "../lib/types";

export function RunProgress({
  run,
  events,
  replay,
  uiMode,
  showTrace,
  onToggleTrace,
  running,
}: {
  run: RunPayload | null;
  events: RunEvent[];
  replay: RecordingSummary | null;
  uiMode: UiMode;
  showTrace: boolean;
  onToggleTrace: () => void;
  running: boolean;
}) {
  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <h2>Analysis</h2>
          <span className="spacer" />
          {replay && (
            <span className="small dim">recorded run · {replay.recording_id}</span>
          )}
        </div>
        <ExecutionFlow events={events} />
        {run && uiMode !== "compare" && (
          <div className="lane-grid single">
            <ExecutionLane
              run={run}
              mode={uiMode === "ai" ? "ai" : "deterministic"}
              title="Stages"
              compared={false}
            />
          </div>
        )}
      </section>
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
