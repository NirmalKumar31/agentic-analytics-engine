/**
 * The run timeline, derived from backend events and nothing else.
 *
 * This replaces a static agent DAG. That diagram drew the same five boxes
 * and the same arrows for every run, lit a few of them, and was on screen
 * before anything had happened -- a picture of the architecture presented
 * where a reader was looking for a picture of *their run*.
 *
 * Two rules make the difference, and both are the point of this file:
 *
 * 1. **A stage advances only because an event says so.** There is no timer,
 *    no optimistic progression and no "probably finished by now". If the
 *    backend emits nothing, the timeline shows nothing moving, which is the
 *    truth.
 *
 * 2. **The AI interpretation stage exists only when a model was actually
 *    called.** It is read from `contract_resolved.model_calls`, which the
 *    engine sets from `resolved.planning_calls`. A deterministic run never
 *    renders it. An interface that showed "interpreting with AI" on a run
 *    that contacted nothing would be making the single claim this product
 *    exists to be trustworthy about.
 *
 * Kept as a pure function over events so it can be tested against event
 * sequences directly, including the sequences a scripted provider can
 * produce but a browser test cannot easily reach.
 */

import type { EventType, RunEvent } from "./types";

export type StageState =
  | "waiting"
  | "active"
  | "complete"
  /** Ran, produced nothing publishable. Not a failure. */
  | "withheld"
  /** The run ended here: refused, failed, cancelled or out of budget. */
  | "stopped"
  /** The run ended before this stage. It did not run, and is not pending. */
  | "skipped";

export type StageId =
  | "understand"
  | "interpret"
  | "plan"
  | "compute"
  | "verify"
  | "publish";

export interface Stage {
  id: StageId;
  label: string;
  state: StageState;
  /** What happened, when there is something true to say. */
  detail?: string;
  /**
   * The detail quotes the engine verbatim and has not been edited for a
   * reader.
   *
   * Only `stoppedDetail` sets this, and only because it passes
   * `data.reason` straight through: "stopped: the question could not be
   * mapped safely: the question asks about 'gross margin', which is not a
   * column of this table". That belongs in the evidence sheet, where an
   * auditor wants the engine's exact words, and not on the report
   * canvas, which is the one surface that carries no unedited engine
   * text. A surface that cannot take it has to be able to *tell*, which
   * is what this flag is for; the alternative was matching on the
   * `"stopped: "` prefix, and a prefix match is not a contract.
   */
  unedited?: boolean;
}

const LABELS: Record<StageId, string> = {
  understand: "understand",
  interpret: "interpret",
  plan: "plan",
  compute: "compute",
  verify: "verify",
  publish: "publish",
};

/**
 * Events that end a run before it finishes.
 *
 * `analysis_task_failed` is deliberately not in this list, because on its
 * own it is not terminal: a run with several tasks can lose one and publish
 * the rest. It is handled below, where it can be weighed against whether
 * any task succeeded.
 */
const TERMINAL_STOPS: EventType[] = [
  "run_failed",
  "run_cancelled",
  "budget_exceeded",
];

function has(events: RunEvent[], ...types: EventType[]): boolean {
  return events.some((event) => types.includes(event.type));
}

function count(events: RunEvent[], type: EventType): number {
  return events.filter((event) => event.type === type).length;
}

/**
 * Did a model actually plan this run?
 *
 * `model_calls` on `contract_resolved`, which is the engine's own count of
 * planning requests. Absent or zero means no. Anything other than a number
 * means the event did not say, which is also no -- an unparseable field is
 * not evidence that a call happened.
 */
export function modelPlanned(events: RunEvent[]): boolean {
  for (const event of events) {
    if (event.type !== "contract_resolved") continue;
    const calls = (event.data ?? {}).model_calls;
    if (typeof calls === "number" && calls > 0) return true;
  }
  return false;
}

/**
 * `events` is the only input.
 *
 * There is deliberately no `finished` flag. Whether the run is over is
 * itself an event -- `run_completed`, so taking it as a parameter would
 * let a caller tell the timeline something the engine had not said, which
 * is the one thing this derivation exists to prevent.
 */
export function timelineOf(events: RunEvent[]): Stage[] {
  const ordered = [...events].sort((a, b) => a.seq - b.seq);

  /*
   * The two shapes a real run takes, both observed against a local server
   * rather than inferred from the graph:
   *
   *   uploaded fast path
   *     run_started -> dataset_loaded -> contract_resolved x2 ->
   *     question_analyzed -> mcp_tool_called -> mcp_tool_completed ->
   *     finding_verified -> chart_created -> report_completed ->
   *     run_completed
   *
   *   refusal
   *     ... question_analyzed -> analysis_task_failed -> report_started ->
   *     report_completed{finding_count: 0} -> run_completed
   *
   * Neither emits `plan_generated`, `analysis_task_started` or
   * `finding_proposed`. A derivation keyed only on those names reported a
   * successful upload as "plan in progress, compute not reached, verify 1
   * verified" -- three stages disagreeing with each other about one run.
   *
   * So each stage accepts every event that genuinely evidences it.
   */
  const started = has(ordered, "run_started", "dataset_loaded");
  const understood = has(ordered, "question_analyzed");
  const resolved = has(ordered, "contract_resolved");
  // The accepted contract *is* the plan on the fast path, which emits no
  // `plan_generated` at all.
  const planned = has(ordered, "plan_generated", "contract_resolved");
  const computing = has(ordered, "analysis_task_started", "mcp_tool_called");
  const computed = has(
    ordered,
    "analysis_task_completed",
    "mcp_tool_completed",
  );
  const taskFailed = ordered.find((e) => e.type === "analysis_task_failed");
  const proposed = count(ordered, "finding_proposed");
  const verified = count(ordered, "finding_verified");
  const rejected = count(ordered, "finding_rejected");
  const publishing = has(ordered, "report_started");
  const published = has(ordered, "report_completed");
  const stopped = ordered.find((event) => TERMINAL_STOPS.includes(event.type));

  const withAi = modelPlanned(ordered);

  const stages: Stage[] = [];
  const add = (
    id: StageId,
    state: StageState,
    detail?: string,
    unedited = false,
  ) => stages.push({ id, label: LABELS[id], state, detail, unedited });

  add(
    "understand",
    understood ? "complete" : started ? "active" : "waiting",
  );

  // Present only when a model was actually consulted.
  if (withAi) {
    add(
      "interpret",
      resolved ? "complete" : understood ? "active" : "waiting",
      "a model was consulted to plan",
    );
  }

  add("plan", planned ? "complete" : understood ? "active" : "waiting");
  // A refusal reaches the browser as `analysis_task_failed` with no
  // successful task, followed by `report_started`/`report_completed` and
  // `run_completed`. There is no `run_failed`, so reading only the terminal
  // list showed a refused run as having computed, verified and published
  // normally -- observed against a real refusal, not assumed.
  const computeStopped = Boolean(taskFailed) && !computed;
  add(
    "compute",
    computeStopped
      ? "stopped"
      : computed
        ? "complete"
        : computing
          ? "active"
          : "waiting",
    computeStopped ? stoppedDetail(taskFailed!).detail : undefined,
    computeStopped ? stoppedDetail(taskFailed!).unedited : false,
  );

  // Verified, or ran and withheld everything. "Withheld" is not a failure
  // and must not be coloured as one: a claim that could not be checked is
  // withheld by design.
  // `finding_verified` can arrive without a preceding `finding_proposed`.
  let verifyState: StageState = "waiting";
  let verifyDetail: string | undefined;
  if (verified > 0) {
    verifyState = "complete";
    verifyDetail =
      rejected > 0
        ? `${verified} verified, ${rejected} withheld`
        : `${verified} verified`;
  } else if (rejected > 0) {
    verifyState = "withheld";
    verifyDetail = `${rejected} withheld, none published`;
  } else if (proposed > 0) {
    verifyState = "active";
  }
  add("verify", verifyState, verifyDetail);

  // `report_completed` fires whether or not anything was published, so
  // "complete" alone would mark a refused or empty run as published. The
  // event carries its own count; nothing published is `withheld`.
  const publishedCount = publishedFindings(ordered);
  add(
    "publish",
    published
      ? publishedCount === 0
        ? "withheld"
        : "complete"
      : publishing
        ? "active"
        : "waiting",
    published && publishedCount === 0 ? "nothing published" : undefined,
  );

  // Why the run ended, if it ended badly. `analysis_task_failed` counts
  // only when no task succeeded: a run with several tasks can lose one and
  // still publish the rest.
  const reason =
    stopped ?? (taskFailed && !computed ? taskFailed : undefined);

  if (reason) {
    // The earliest stage that did not complete is where it stopped.
    // Everything after it did not run, and must not read as pending:
    // "waiting" after the fact is a lie about a run that is over, and that
    // is exactly how the old five-step index made a refusal look like a run
    // still in progress.
    const at = stages.findIndex((stage) => stage.state !== "complete");
    if (at >= 0) {
      const stop = stoppedDetail(reason);
      stages[at] = {
        ...stages[at]!,
        state: "stopped",
        detail: stop.detail,
        unedited: stop.unedited,
      };
      for (let i = at + 1; i < stages.length; i += 1) {
        if (stages[i]!.state === "complete") continue;
        stages[i] = { ...stages[i]!, state: "skipped", detail: "not reached" };
      }
    }
    return stages;
  }

  // The run finished cleanly. Anything still pending never ran, and
  // "waiting" after the fact describes a run that is over as though it
  // were still going.
  if (has(ordered, "run_completed")) {
    return stages.map((stage) =>
      stage.state === "waiting"
        ? { ...stage, state: "skipped" as StageState, detail: "not reached" }
        : stage,
    );
  }

  return stages;
}

/** What `report_completed` says it published, or null when it did not say. */
function publishedFindings(events: RunEvent[]): number | null {
  for (const event of events) {
    if (event.type !== "report_completed") continue;
    const n = (event.data ?? {}).finding_count;
    if (typeof n === "number") return n;
  }
  return null;
}

/**
 * What stopped the run, and whether the words are the engine's own.
 *
 * Two of the three are authored here and safe anywhere. The third passes
 * `data.reason` through verbatim, which is right for an audit and wrong
 * for the report canvas, so it says which it is rather than leaving every
 * caller to guess from the string.
 */
function stoppedDetail(event: RunEvent): { detail: string; unedited: boolean } {
  if (event.type === "budget_exceeded") {
    return { detail: "stopped: budget reached", unedited: false };
  }
  if (event.type === "run_cancelled") return { detail: "cancelled", unedited: false };
  const reason = (event.data ?? {}).reason;
  return typeof reason === "string" && reason
    ? { detail: `stopped: ${reason}`, unedited: true }
    : { detail: "stopped", unedited: false };
}
