/**
 * The tool-call layer of the execution graph.
 *
 * Three claims are worth pinning, and all three are places a plausible
 * implementation gets it wrong:
 *
 * 1. **A refused call is not a failed tool.** `preflight` declines a
 *    proposed call before it crosses MCP. Drawing that as a tool failure
 *    tells the reader the analysis broke when the engine was being
 *    careful. `ActivityLog` was corrected for exactly this; a second
 *    rendering of the same events must not reintroduce it.
 * 2. **Calls pair by `task_id`, not by order.** The committed
 *    `returns-segments` recording dispatches four calls and they return
 *    out of order -- `task_03` before `task_02` -- so index pairing
 *    attributes one call's row count to another.
 * 3. **A refused or suppressed failure opens its own node.** The worker
 *    emits it before the client is reached, so there is no preceding
 *    `mcp_tool_called` to close.
 */

import { describe, expect, it } from "vitest";

import { describeGraph, toolCallsOf } from "../lib/executionGraph";
import { timelineOf } from "../lib/timeline";
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

const reset = () => {
  seq = 0;
};

describe("tool calls come from events and nothing else", () => {
  it("records no calls for a run that made none", () => {
    reset();
    expect(toolCallsOf([event("run_started"), event("run_completed")])).toEqual(
      [],
    );
  });

  it("pairs a completion with its own call, not with the next one", () => {
    reset();
    // The shape the `returns-segments` recording actually has: both calls
    // dispatched, then answered in the other order.
    const calls = toolCallsOf([
      event("mcp_tool_called", {
        tool_name: "analyze_timeseries",
        task_id: "task_01",
        agent: "analysis_worker",
      }),
      event("mcp_tool_called", {
        tool_name: "compare_segments",
        task_id: "task_02",
        agent: "analysis_worker",
      }),
      event("mcp_tool_completed", {
        tool_name: "compare_segments",
        task_id: "task_02",
        row_count: 4,
      }),
      event("mcp_tool_completed", {
        tool_name: "analyze_timeseries",
        task_id: "task_01",
        row_count: 24,
      }),
    ]);

    expect(calls).toHaveLength(2);
    expect(calls[0]!.label).toBe("Analyze Timeseries");
    expect(calls[0]!.outcome).toBe("returned 24 rows");
    expect(calls[1]!.label).toBe("Compare Segments");
    expect(calls[1]!.outcome).toBe("returned 4 rows");
    expect(calls.every((call) => call.state === "completed")).toBe(true);
  });

  it("reads events in sequence order, not arrival order", () => {
    reset();
    const ordered = [
      event("mcp_tool_called", { tool_name: "compute_metric", task_id: "t" }),
      event("mcp_tool_completed", {
        tool_name: "compute_metric",
        task_id: "t",
        row_count: 3,
      }),
    ];
    const shuffled = [ordered[1]!, ordered[0]!];
    expect(toolCallsOf(shuffled)).toEqual(toolCallsOf(ordered));
  });

  it("names a dispatched call that errored a failure", () => {
    reset();
    const calls = toolCallsOf([
      event("mcp_tool_called", { tool_name: "statistical_test", task_id: "t" }),
      event("mcp_tool_failed", {
        tool_name: "statistical_test",
        task_id: "t",
        error: "ToolCallFailed: the column has one distinct value",
      }),
    ]);

    expect(calls).toHaveLength(1);
    expect(calls[0]!.state).toBe("failed");
    expect(calls[0]!.outcome).toMatch(/^failed: /);
  });
});

describe("a refused call is not a failed tool", () => {
  const refusal = () => {
    reset();
    return toolCallsOf([
      event("mcp_tool_failed", {
        tool_name: "analyze_timeseries",
        task_id: "task_01",
        preflight: true,
        error: "analyze_timeseries does not accept 'dimensions'",
      }),
    ]);
  };

  it("gives it its own state", () => {
    expect(refusal()[0]!.state).toBe("refused");
  });

  it("says it was refused before execution, in words", () => {
    expect(refusal()[0]!.outcome).toContain("refused before execution");
  });

  it("never says the tool failed", () => {
    expect(refusal()[0]!.outcome).not.toContain("failed");
  });

  it("opens its own node, having no call to close", () => {
    expect(refusal()).toHaveLength(1);
  });

  it("separates a re-proposed call from both", () => {
    reset();
    const calls = toolCallsOf([
      event("mcp_tool_failed", {
        tool_name: "compare_segments",
        task_id: "task_02",
        suppressed: true,
        error: "compare_segments requires a dimension",
      }),
    ]);
    expect(calls[0]!.state).toBe("suppressed");
    expect(calls[0]!.outcome).toContain("not sent a second time");
  });

  it("still marks a plain failure as failed when neither flag is set", () => {
    reset();
    const calls = toolCallsOf([
      event("mcp_tool_failed", { tool_name: "compute_metric", error: "boom" }),
    ]);
    expect(calls[0]!.state).toBe("failed");
  });
});

describe("the graph carries no arguments and no timings", () => {
  it("drops both from the node, however the event carries them", () => {
    reset();
    const calls = toolCallsOf([
      event("mcp_tool_called", {
        tool_name: "compare_segments",
        task_id: "t",
        arguments: { metric: "return_rate", dimension: "customer_segment" },
        duration_ms: 32.56,
      }),
      event("mcp_tool_completed", {
        tool_name: "compare_segments",
        task_id: "t",
        row_count: 4,
        duration_ms: 32.56,
        arguments: { metric: "return_rate" },
      }),
    ]);

    const rendered = JSON.stringify(calls);
    expect(rendered).not.toContain("32.56");
    expect(rendered).not.toContain("customer_segment");
  });

  it("leaves a call with nothing back yet as running, not as finished", () => {
    reset();
    const calls = toolCallsOf([
      event("mcp_tool_called", { tool_name: "compute_metric", task_id: "t" }),
    ]);
    expect(calls[0]!.state).toBe("running");
  });
});

describe("the text alternative describes the same run as the picture", () => {
  it("names every stage and every call", () => {
    reset();
    const events = [
      event("run_started"),
      event("question_analyzed"),
      event("plan_generated", { task_count: 1 }),
      event("mcp_tool_called", {
        tool_name: "compare_segments",
        task_id: "t",
      }),
      event("mcp_tool_completed", {
        tool_name: "compare_segments",
        task_id: "t",
        row_count: 4,
      }),
      event("finding_verified"),
      event("report_completed", { finding_count: 1 }),
      event("run_completed"),
    ];
    const stages = timelineOf(events);
    const calls = toolCallsOf(events);
    const text = describeGraph(stages, calls);

    for (const stage of stages) expect(text).toContain(stage.label);
    for (const call of calls) expect(text).toContain(call.label);
    expect(text).toContain("returned 4 rows");
  });

  it("counts the refused calls separately, so the reason is not buried", () => {
    reset();
    const events = [
      event("run_started"),
      event("question_analyzed"),
      event("mcp_tool_failed", {
        tool_name: "analyze_timeseries",
        task_id: "t",
        preflight: true,
        error: "analyze_timeseries does not accept 'dimensions'",
      }),
    ];
    const text = describeGraph(timelineOf(events), toolCallsOf(events));
    expect(text).toContain("refused before execution and never reached a tool");
  });

  it("says so plainly when no tool call was recorded", () => {
    reset();
    const events = [event("run_started"), event("question_analyzed")];
    expect(describeGraph(timelineOf(events), toolCallsOf(events))).toContain(
      "No tool calls were recorded",
    );
  });
});
