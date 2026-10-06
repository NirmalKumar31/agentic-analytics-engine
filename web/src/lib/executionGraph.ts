/**
 * The tool calls a run actually made, derived from its events and nothing
 * else.
 *
 * `timeline.ts` derives the six coarse stages. This derives the layer below
 * them: every call the engine proposed, and what became of it. Together
 * they are the execution graph, and the whole claim of both files is that
 * a node exists because an event said so -- never because a stage usually
 * happens, never because enough time has passed.
 *
 * **Four outcomes, and the distinction between them is the point.**
 *
 *   completed  the tool ran and returned
 *   failed     the tool ran and errored
 *   refused    `preflight` declined the call; it never crossed MCP
 *   suppressed the same failing call was proposed again and not re-sent
 *
 * A refused call is a guardrail working. Drawing it as a failed tool tells
 * a reader the analysis broke when what happened is the engine checking a
 * proposed call against the tool's real signature and declining it --
 * exactly the misreading `ActivityLog` was corrected for, and it must not
 * come back in a second rendering of the same events.
 *
 * **Pairing is by `task_id` and tool name, not by order.** Four calls
 * dispatched together complete out of order -- observed in the committed
 * `returns-segments` recording, where `task_03` returns before `task_02` --
 * so an index-based pairing attributes one call's row count to another.
 *
 * A refused or suppressed failure has no preceding `mcp_tool_called` at
 * all: the worker emits it before the client is reached. So those open a
 * node of their own rather than closing one.
 */

import { agentLabel, toolLabel } from "./format";
import type { Stage } from "./timeline";
import type { RunEvent } from "./types";

export type CallState =
  /** Ran and returned. */
  | "completed"
  /** Ran and errored. */
  | "failed"
  /** Declined by preflight. Never dispatched. */
  | "refused"
  /** Proposed again unchanged after failing, and not sent a second time. */
  | "suppressed"
  /** Dispatched, nothing back yet. Only reachable mid-run. */
  | "running";

export interface ToolCall {
  key: string;
  /** Raw tool name. Kept for provenance; never painted on its own. */
  tool: string;
  /** Reader label for the tool. */
  label: string;
  /** Which agent reached for it. */
  agent: string;
  state: CallState;
  /** What became of it, in words. Never a duration, never an argument. */
  outcome: string;
}

/** How long a sanitised engine message may run inside a node. */
const REASON = 140;

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function reasonOf(data: Record<string, unknown>): string {
  const raw = text(data.error) || text(data.reason);
  if (!raw) return "";
  return raw.length > REASON ? `${raw.slice(0, REASON - 1)}…` : raw;
}

/**
 * Every tool call this run made, in the order it proposed them.
 *
 * Arguments and durations are deliberately absent. They are the auditor's
 * view and they already have a home in the activity trace; a graph node
 * carrying a `{ metric=…, grain=… }` blob is a second, worse trace.
 */
export function toolCallsOf(events: RunEvent[]): ToolCall[] {
  const ordered = [...events].sort((a, b) => a.seq - b.seq);
  const calls: ToolCall[] = [];
  /** Open nodes, keyed by task and tool, so a reply closes its own call. */
  const open = new Map<string, number>();

  const node = (
    event: RunEvent,
    state: CallState,
    outcome: string,
  ): ToolCall => {
    const data = event.data ?? {};
    const tool = text(data.tool_name);
    return {
      key: event.event_id,
      tool,
      label: tool ? toolLabel(tool) : "a tool",
      agent: agentLabel(text(data.agent) || "analysis_worker"),
      state,
      outcome,
    };
  };

  for (const event of ordered) {
    const data = event.data ?? {};
    const pair = `${text(data.task_id)}\u0000${text(data.tool_name)}`;

    if (event.type === "mcp_tool_called") {
      open.set(pair, calls.length);
      calls.push(node(event, "running", "running"));
      continue;
    }

    if (event.type === "mcp_tool_completed") {
      const rows = data.row_count;
      const outcome =
        typeof rows === "number"
          ? `returned ${rows} ${rows === 1 ? "row" : "rows"}`
          : "returned a result";
      const at = open.get(pair);
      open.delete(pair);
      if (at === undefined) {
        calls.push(node(event, "completed", outcome));
      } else {
        calls[at] = { ...calls[at]!, state: "completed", outcome };
      }
      continue;
    }

    if (event.type !== "mcp_tool_failed") continue;

    const why = reasonOf(data);

    // Refused before it was ever sent. Not a failure of the tool, and not
    // of the analysis: the engine checked the proposed call against the
    // tool's signature and declined it.
    if (data.preflight === true) {
      calls.push(
        node(
          event,
          "refused",
          why ? `refused before execution: ${why}` : "refused before execution",
        ),
      );
      continue;
    }

    // The same call, proposed again unchanged after it had already failed.
    // Also never dispatched.
    if (data.suppressed === true) {
      calls.push(
        node(
          event,
          "suppressed",
          why
            ? `not sent a second time: ${why}`
            : "not sent a second time after failing",
        ),
      );
      continue;
    }

    const outcome = why ? `failed: ${why}` : "failed";
    const at = open.get(pair);
    open.delete(pair);
    if (at === undefined) {
      calls.push(node(event, "failed", outcome));
    } else {
      calls[at] = { ...calls[at]!, state: "failed", outcome };
    }
  }

  return calls;
}

/** The four words a reader sees for the four outcomes. */
export const CALL_STATE_LABEL: Record<CallState, string> = {
  completed: "completed",
  failed: "failed",
  refused: "refused before execution",
  suppressed: "not re-sent",
  running: "running",
};

/**
 * The graph in a sentence, for a reader who cannot see the arrangement.
 *
 * The marks, the connectors and the order they sit in carry meaning that a
 * list of labels alone does not, so the alternative restates the sequence
 * in prose rather than leaving a screen reader to reconstruct it from
 * positions.
 *
 * It is built from the same two derivations the picture is built from, so
 * it cannot describe a run the picture does not show.
 */
export function describeGraph(stages: Stage[], calls: ToolCall[]): string {
  if (stages.length === 0) return "No run has been recorded.";

  const parts: string[] = [];
  const sequence = stages
    .map((stage) => {
      const state = stage.state === "complete" ? "completed" : stage.state;
      return stage.detail
        ? `${stage.label} — ${state}, ${stage.detail}`
        : `${stage.label} — ${state}`;
    })
    .join("; ");
  parts.push(`This run ran ${stages.length} stages, in order: ${sequence}.`);

  if (calls.length === 0) {
    parts.push("No tool calls were recorded.");
    return parts.join(" ");
  }

  const spoken = calls
    .map((call) => `${call.label}, ${call.outcome}`)
    .join("; ");
  parts.push(
    `${calls.length} tool ${calls.length === 1 ? "call was" : "calls were"} proposed: ${spoken}.`,
  );

  const refused = calls.filter((call) => call.state === "refused").length;
  if (refused > 0) {
    parts.push(
      `${refused} of them ${refused === 1 ? "was" : "were"} refused before execution and never reached a tool.`,
    );
  }

  return parts.join(" ");
}
