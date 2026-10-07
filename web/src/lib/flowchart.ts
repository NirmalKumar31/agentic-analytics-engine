/**
 * The run, as a flowchart, for the report canvas.
 *
 * There was a picture of the run on the canvas and it was six dots and a
 * sentence. It was honest and it was unreadable as a diagram: nothing was
 * labelled, nothing was connected, and a reader could not tell from it
 * what the engine had done or in what order. The diagram that *is*
 * labelled and connected lived behind "Inspect evidence", where a reader
 * looking at their answer never saw it.
 *
 * So this derives a flowchart: the stage spine with its labels, the arrows
 * between them, and the tool calls that `compute` fanned out into. Same
 * two derivations the evidence sheet's graph uses, `timelineOf` and
 * `toolCallsOf`, so the canvas and the sheet cannot disagree about what
 * happened.
 *
 * **Two rules carried forward unchanged, because they are why the picture
 * is worth anything.**
 *
 * 1. A node exists because an event carried it. There is no timer, no
 *    optimistic progression, and no stage drawn because a run "usually"
 *    reaches it. A run that made four calls draws four.
 *
 * 2. Nothing here is the engine's unedited text. That was the explicit
 *    reason the labelled graph was kept off the canvas in the first place:
 *    a stopped stage's detail is `stopped: <whatever the engine said>`,
 *    and a failed call's outcome is `failed: <whatever the engine said>`.
 *    An auditor wants those words exactly; a reader is owed a sentence
 *    someone wrote for them. The engine's own words stay one control away,
 *    and `sanitised` below is the whole of the difference.
 */

import { CALL_STATE_LABEL, toolCallsOf, type CallState, type ToolCall } from "./executionGraph";
import { timelineOf, type Stage, type StageId, type StageState } from "./timeline";
import type { RunEvent } from "./types";

export interface FlowStage {
  id: StageId;
  label: string;
  state: StageState;
  /** The state in words, so colour carries none of it on its own. */
  stateLabel: string;
  /** Reader-safe, and `null` where there is nothing true to add. */
  note: string | null;
}

export interface FlowCall {
  key: string;
  label: string;
  state: CallState;
  /** Reader-safe. Never the engine's reason. */
  note: string;
}

export interface Flowchart {
  stages: FlowStage[];
  calls: FlowCall[];
  /** The stage the calls hang off, or `null` when there are none to hang. */
  branchAt: StageId | null;
  /** The diagram in prose, for a reader who cannot see the arrangement. */
  alternative: string;
}

/** The words a reader sees for each stage state. */
export const STAGE_STATE_LABEL: Record<StageState, string> = {
  waiting: "not started",
  active: "running",
  complete: "completed",
  withheld: "published nothing",
  stopped: "stopped here",
  skipped: "not reached",
};

/**
 * What a stage's detail says on the canvas, or nothing.
 *
 * `stage.unedited` is set by `timeline.ts` for exactly the one case that
 * quotes the engine, so this does not match on a string prefix. A prefix
 * match is not a contract, and the first thing to change the engine's
 * wording would have put its text back on the canvas silently.
 *
 * An unedited detail is dropped rather than replaced with a pointer. A
 * first version substituted "the reason is in the evidence", and the
 * refusal screenshot showed why that is noise: the box already said
 * "stopped here" in its state line, the report's own headline *is* the
 * engine's reason, because a refusal's answer is why it refused, and the
 * section carries one control to the full record. Four ways of saying the
 * same thing, three of them in the same box.
 */
function sanitised(stage: Stage): string | null {
  if (!stage.detail || stage.unedited) return null;
  return stage.detail;
}

/**
 * What a call says on the canvas.
 *
 * A completed call's outcome is authored from the event's own row count --
 * "returned 45 rows", and is a fact, so it is kept: the row count is the
 * single most useful thing on the node. Every other state's outcome may
 * carry `data.error` or `data.reason`, so those take the authored state
 * word instead and the engine's text stays in the sheet.
 */
function callNote(call: ToolCall): string {
  return call.state === "completed" ? call.outcome : CALL_STATE_LABEL[call.state];
}

/** The stage the calls belong to: the one that makes them. */
function branchStage(stages: FlowStage[]): StageId | null {
  const compute = stages.find((stage) => stage.id === "compute");
  if (compute) return compute.id;
  return stages.length > 0 ? stages[stages.length - 1]!.id : null;
}

export function flowchartOf(events: RunEvent[]): Flowchart {
  const stages: FlowStage[] = timelineOf(events).map((stage) => ({
    id: stage.id,
    label: stage.label,
    state: stage.state,
    stateLabel: STAGE_STATE_LABEL[stage.state],
    note: sanitised(stage),
  }));
  const calls: FlowCall[] = toolCallsOf(events).map((call) => ({
    key: call.key,
    label: call.label,
    state: call.state,
    note: callNote(call),
  }));

  const branchAt = calls.length > 0 ? branchStage(stages) : null;
  return { stages, calls, branchAt, alternative: describeFlow(stages, calls) };
}

/**
 * The diagram in a sentence.
 *
 * The boxes, the arrows and the order they sit in carry meaning that a
 * list of labels does not, so the arrangement is restated in prose rather
 * than leaving a screen reader to reconstruct it from positions. Built
 * from the same sanitised model the picture is built from, so it cannot
 * describe a run the picture does not show, including not carrying
 * engine text the picture declined to carry.
 */
export function describeFlow(stages: FlowStage[], calls: FlowCall[]): string {
  if (stages.length === 0) return "No run has been recorded.";

  const sequence = stages
    .map((stage) =>
      stage.note
        ? `${stage.label} — ${stage.stateLabel}, ${stage.note}`
        : `${stage.label} — ${stage.stateLabel}`,
    )
    .join("; then ");
  const parts = [`This run ran ${stages.length} stages, in order: ${sequence}.`];

  if (calls.length === 0) {
    parts.push("It made no tool calls.");
    return parts.join(" ");
  }

  const spoken = calls.map((call) => `${call.label}, ${call.note}`).join("; ");
  parts.push(
    `Compute made ${calls.length} ${calls.length === 1 ? "call" : "calls"}: ${spoken}.`,
  );
  return parts.join(" ");
}
