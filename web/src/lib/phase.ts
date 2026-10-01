/**
 * What the application is doing, derived rather than stored.
 *
 * `App` already holds fifteen pieces of mutable state. A sixteenth saying
 * which phase we are in would be one that can disagree with the other
 * fifteen -- and a phase that says `completed` while the run payload says
 * `refused` is worse than no phase at all, because the interface would
 * then confidently render the wrong thing.
 *
 * So this is a pure function of the state that already exists. There is
 * nothing to keep in step, and a phase that looks wrong is a bug in one
 * input rather than a missing `setPhase` call on some branch nobody
 * remembered.
 *
 * The phases exist because the stylesheet needs them. Motion is bound to
 * what the application is actually doing: the ambient grid may drift while
 * there is nothing to read, and must hold still and recede once a report
 * is on screen. Without a phase on the body, that rule has nothing to key
 * on, and the previous stylesheet simply animated everything all the time.
 */

import type { RunPayload, ServerConfig, SessionPayload } from "./types";

export type Phase =
  /** Before the server has said what it can do. */
  | "booting"
  /** No dataset yet. The onboarding state, and one of the two idle ones. */
  | "choose_dataset"
  /** A file is uploading or being profiled. */
  | "profiling"
  /** A dataset is open and nothing has been asked. The other idle state. */
  | "ready_to_ask"
  /** Submitted, and the engine has not yet said what it will compute. */
  | "routing"
  /** A contract exists and DuckDB is running it. */
  | "executing"
  /** Results are in and the publication checks are running. */
  | "verifying"
  /** A verified result is being assembled into a report. */
  | "presenting"
  /** A report is on screen. */
  | "completed"
  /** The question could not be mapped safely. Not a failure. */
  | "refused"
  /** Something broke. */
  | "failed"
  /** The dataset went away: deleted, replaced or timed out. */
  | "expired";

export interface PhaseInputs {
  config: ServerConfig | null;
  configError: string | null;
  session: SessionPayload | null;
  replay: unknown | null;
  runId: string | null;
  run: RunPayload | null;
  busy: boolean;
  error: string | null;
  /**
   * The live event stream, which is where mid-run progress actually is.
   *
   * A separate input from `run` because the payload does not exist until
   * the run finishes. Reading stages out of `run.events` made
   * `executing`, `verifying` and `presenting` unreachable: the function
   * returned a terminal phase the moment a payload appeared, and before
   * that there were no events to read.
   */
  events?: readonly { type: string }[];
}

/** Event types that mark a run having got past planning. */
const EXECUTING = new Set([
  "mcp_tool_called",
  "mcp_tool_completed",
  "analysis_task_started",
]);
const VERIFYING = new Set([
  "finding_proposed",
  "finding_verified",
  "finding_rejected",
]);
const PRESENTING = new Set(["report_started"]);

function latestPhaseFromEvents(events: readonly { type: string }[]): Phase {
  // The furthest stage reached is the one to report. A trailing MCP call
  // after verification has begun must not pull the phase backwards, so
  // each match only ever advances it.
  let phase: Phase = "routing";
  for (const event of events) {
    const type = String(event.type);
    if (PRESENTING.has(type)) phase = "presenting";
    else if (VERIFYING.has(type) && phase !== "presenting") phase = "verifying";
    else if (EXECUTING.has(type) && phase === "routing") phase = "executing";
  }
  return phase;
}

/**
 * The phase, from the state that already exists.
 *
 * Terminal states are read from the run payload rather than inferred from
 * the absence of findings: a run with no findings may have been refused,
 * may have had everything withheld by verification, or may have broken,
 * and those are three different things to show a reader.
 */
export function phaseOf(inputs: PhaseInputs): Phase {
  const {
    config,
    configError,
    session,
    replay,
    runId,
    run,
    busy,
    error,
    events,
  } = inputs;

  if (configError) return "failed";
  if (!config) return "booting";

  // A finished run outranks everything: once there is a payload, what the
  // payload says happened is what happened.
  if (run) {
    const outcome = String(run.outcome ?? "");
    if (outcome === "refused") return "refused";
    if (outcome === "cancelled") return "expired";
    if (outcome && outcome !== "completed") return "failed";
    if (run.status === "failed") return "failed";
    return "completed";
  }

  if (runId) return latestPhaseFromEvents(events ?? []);

  if (session || replay) return busy ? "profiling" : "ready_to_ask";

  if (busy) return "profiling";
  // An error with no dataset is a failed upload, and the visitor is back
  // at the choice. Saying `failed` here would replace the thing they need
  // to see -- the upload control -- with a dead end.
  if (error) return "choose_dataset";
  return "choose_dataset";
}

/** Whether this phase has a report on screen. */
export function showsReport(phase: Phase): boolean {
  return phase === "completed" || phase === "presenting";
}

/** Whether this phase is idle: nothing running and nothing to read. */
export function isIdle(phase: Phase): boolean {
  return phase === "choose_dataset" || phase === "ready_to_ask";
}
