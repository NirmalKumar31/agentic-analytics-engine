/**
 * The timeline derives from events, and only from events.
 *
 * The claim worth testing is not "five stages render". It is that a stage
 * advances **because the backend said so**, that an AI interpretation stage
 * appears **only when a model was actually called**, and that a run which
 * stopped is shown as stopped at the point it stopped rather than as still
 * in progress.
 *
 * That last one is a debt. The five-step pipeline index used to assert it
 * and was deleted in step B; three places in the suite record it as owed by
 * step D. This is where it is paid, and it is paid against real event
 * sequences rather than against a derived phase string.
 */

import { describe, expect, it } from "vitest";

import { modelPlanned, timelineOf, type StageId } from "../lib/timeline";
import type { EventType, RunEvent } from "../lib/types";

let seq = 0;
const event = (
  type: EventType,
  data: Record<string, unknown> = {},
): RunEvent => ({
  event_id: `e${(seq += 1)}`,
  seq,
  type,
  at: seq,
  data,
});

/** A run that got as far as `through`, deterministically. */
function sequence(through: StageId | "none"): RunEvent[] {
  seq = 0;
  const events: RunEvent[] = [event("run_started"), event("dataset_loaded")];
  if (through === "none") return events;
  events.push(event("question_analyzed"));
  if (through === "understand") return events;
  events.push(event("contract_resolved", { model_calls: 0, route: "rules" }));
  events.push(event("plan_generated", { task_count: 1 }));
  if (through === "plan") return events;
  events.push(event("analysis_task_started"), event("analysis_task_completed"));
  if (through === "compute") return events;
  events.push(event("finding_proposed"), event("finding_verified"));
  if (through === "verify") return events;
  events.push(event("report_started"), event("report_completed"));
  return events;
}

const stateOf = (events: RunEvent[], id: StageId) =>
  timelineOf(events).find((stage) => stage.id === id)?.state;

const ids = (events: RunEvent[]) => timelineOf(events).map((stage) => stage.id);

describe("stages advance only on events", () => {
  it("shows nothing started before a run starts", () => {
    expect(timelineOf([]).every((stage) => stage.state === "waiting")).toBe(true);
  });

  it("marks the first stage active once the run has started", () => {
    expect(stateOf(sequence("none"), "understand")).toBe("active");
    expect(stateOf(sequence("none"), "plan")).toBe("waiting");
  });

  it("completes a stage only when its own event has arrived", () => {
    const upToPlan = sequence("plan");
    expect(stateOf(upToPlan, "understand")).toBe("complete");
    expect(stateOf(upToPlan, "plan")).toBe("complete");
    // Nothing has been computed, and the timeline must not imply it has.
    expect(stateOf(upToPlan, "compute")).toBe("waiting");
    expect(stateOf(upToPlan, "verify")).toBe("waiting");
    expect(stateOf(upToPlan, "publish")).toBe("waiting");
  });

  it("completes every stage for a run that finished", () => {
    const done = sequence("publish");
    for (const id of ["understand", "plan", "compute", "verify", "publish"] as StageId[]) {
      expect(stateOf(done, id), `${id} did not complete`).toBe("complete");
    }
  });

  it("reads the sequence rather than the arrival order", () => {
    // Events can arrive out of order over a stream; `seq` is the engine's
    // own ordering and is what the derivation sorts on.
    const ordered = sequence("publish");
    const shuffled = [...ordered].reverse();
    expect(timelineOf(shuffled)).toEqual(timelineOf(ordered));
  });
});

describe("the AI interpretation stage is conditional on a real call", () => {
  it("is absent from a run that called no model", () => {
    expect(ids(sequence("publish"))).not.toContain("interpret");
    expect(modelPlanned(sequence("publish"))).toBe(false);
  });

  it("appears when contract_resolved records a model call", () => {
    seq = 0;
    const events = [
      event("run_started"),
      event("question_analyzed"),
      event("contract_resolved", { model_calls: 1, route: "ai" }),
    ];
    expect(ids(events)).toContain("interpret");
    expect(stateOf(events, "interpret")).toBe("complete");
  });

  it("is absent when the field is missing, zero, or not a number", () => {
    // An unparseable field is not evidence that a call happened. Showing
    // "interpreting with AI" on a run that contacted nothing is the single
    // claim this product most needs to get right.
    for (const data of [
      {},
      { model_calls: 0 },
      { model_calls: "1" },
      { model_calls: null },
    ]) {
      seq = 0;
      const events = [event("run_started"), event("contract_resolved", data)];
      expect(ids(events), JSON.stringify(data)).not.toContain("interpret");
    }
  });
});

describe("a run that stopped is shown stopped, not still running", () => {
  // The claim the deleted stepper used to carry.
  it("marks the stage it stopped at, and no later one as active", () => {
    seq = 0;
    const events = [
      event("run_started"),
      event("question_analyzed"),
      event("contract_resolved", { model_calls: 0 }),
      event("run_failed", { reason: "the executed result could not be read" }),
    ];
    const stages = timelineOf(events);
    const stopped = stages.filter((stage) => stage.state === "stopped");
    expect(stopped).toHaveLength(1);
    // The contract resolved, so planning is done; the run died at compute.
    expect(stopped[0]!.id).toBe("compute");
    expect(stages.some((stage) => stage.state === "active")).toBe(false);
  });

  it("names the reason the engine gave, when it gave one", () => {
    seq = 0;
    const events = [
      event("run_started"),
      event("run_failed", { reason: "no executable task for this dataset" }),
    ];
    const stopped = timelineOf(events).find((s) => s.state === "stopped");
    expect(stopped?.detail).toContain("no executable task");
  });

  it("distinguishes a budget stop from a cancellation", () => {
    seq = 0;
    const budget = timelineOf([event("run_started"), event("budget_exceeded")]);
    expect(budget.find((s) => s.state === "stopped")?.detail).toMatch(/budget/i);

    seq = 0;
    const cancelled = timelineOf([event("run_started"), event("run_cancelled")]);
    expect(cancelled.find((s) => s.state === "stopped")?.detail).toMatch(
      /cancelled/i,
    );
  });
});

describe("a refusal, as the engine actually emits it", () => {
  /*
   * Captured from a real refused run against a local server in fake mode,
   * not reconstructed from the code:
   *
   *   run_started -> dataset_loaded -> contract_resolved x2 ->
   *   question_analyzed -> analysis_task_failed -> report_started ->
   *   report_completed{finding_count: 0} -> run_completed
   *
   * There is no `run_failed`. Reading only the terminal-event list showed
   * this run as having computed, verified and published normally, which is
   * the opposite of what happened.
   */
  const refusal = (): RunEvent[] => {
    seq = 0;
    return [
      event("run_started"),
      event("dataset_loaded"),
      event("contract_resolved", { model_calls: 0 }),
      event("question_analyzed"),
      event("analysis_task_failed", {
        reason:
          "the question could not be mapped safely: the question asks about 'gross margin', which is not a column of this table",
      }),
      event("report_started"),
      event("report_completed", { finding_count: 0, rejected_count: 0 }),
      event("run_completed", {}),
    ];
  };

  it("stops at compute, where the task actually failed", () => {
    const stages = timelineOf(refusal());
    const stopped = stages.filter((stage) => stage.state === "stopped");
    expect(stopped).toHaveLength(1);
    // Compute. The contract resolved, so `contract_resolved` fired, and
    // the failure came when the engine tried to turn that contract into an
    // executable task, which is what `analysis_task_failed` reports.
    expect(stopped[0]!.id).toBe("compute");
    expect(stopped[0]!.detail).toMatch(/not a column of this table/);
  });

  it("does not report a refused run as published", () => {
    // `report_completed` fires on a refusal too, with `finding_count: 0`.
    // Nothing was published and nothing was computed, so publish reads as
    // not reached rather than as a completed publication.
    const publish = timelineOf(refusal()).find((s) => s.id === "publish");
    expect(publish?.state).toBe("skipped");
    expect(publish?.detail).toBe("not reached");
  });

  it("marks every stage after the stop as not reached", () => {
    const stages = timelineOf(refusal());
    const stopIndex = stages.findIndex((s) => s.state === "stopped");
    for (const stage of stages.slice(stopIndex + 1)) {
      expect(stage.state, `${stage.id} is ${stage.state}`).toBe("skipped");
    }
  });

  it("leaves no stage claiming to be in progress", () => {
    expect(timelineOf(refusal()).some((s) => s.state === "active")).toBe(false);
  });
});

describe("the uploaded fast path, as the engine actually emits it", () => {
  /*
   * Also captured from a real run. It emits no `plan_generated`, no
   * `analysis_task_started` and no `finding_proposed`: the contract is the
   * plan, and the work is an MCP call.
   */
  const fastPath = (): RunEvent[] => {
    seq = 0;
    return [
      event("run_started"),
      event("dataset_loaded"),
      event("contract_resolved", { model_calls: 0 }),
      event("question_analyzed"),
      event("mcp_tool_called"),
      event("mcp_tool_completed"),
      event("finding_verified"),
      event("chart_created"),
      event("report_completed", { finding_count: 1 }),
      event("run_completed", {}),
    ];
  };

  it("reaches publish with every stage complete", () => {
    const stages = timelineOf(fastPath());
    for (const stage of stages) {
      expect(stage.state, `${stage.id} is ${stage.state}`).toBe("complete");
    }
  });

  it("does not report a successful run as having skipped anything", () => {
    const stages = timelineOf(fastPath());
    expect(stages.some((s) => s.state === "skipped")).toBe(false);
    expect(stages.some((s) => s.state === "stopped")).toBe(false);
    expect(stages.some((s) => s.state === "active")).toBe(false);
  });

  it("still shows no AI stage, because no model was called", () => {
    expect(timelineOf(fastPath()).map((s) => s.id)).not.toContain("interpret");
  });
});

describe("verification is reported honestly", () => {
  it("says withheld, not failed, when everything was rejected", () => {
    seq = 0;
    const events = [
      ...sequence("compute"),
      event("finding_proposed"),
      event("finding_rejected", { rule: "no_evidence" }),
      event("report_started"),
      event("report_completed"),
    ];
    const verify = timelineOf(events).find((stage) => stage.id === "verify");
    expect(verify?.state).toBe("withheld");
    expect(verify?.detail).toMatch(/withheld/);
    // A completed run that published nothing is not a stopped run.
    expect(timelineOf(events).some((s) => s.state === "stopped")).toBe(false);
  });

  it("counts both when some were published and some withheld", () => {
    seq = 0;
    const events = [
      ...sequence("compute"),
      event("finding_proposed"),
      event("finding_proposed"),
      event("finding_verified"),
      event("finding_rejected"),
    ];
    const verify = timelineOf(events).find((stage) => stage.id === "verify");
    expect(verify?.state).toBe("complete");
    expect(verify?.detail).toBe("1 verified, 1 withheld");
  });
});
