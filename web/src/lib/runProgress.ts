/**
 * What a run has actually done so far, from its events and nothing else.
 *
 * The in-flight screen used to be the stage sequence alone. On a
 * deterministic run that is over in a second, which is fine; on an AI run
 * it can sit on one stage for half a minute while a provider thinks, and
 * the reader has no way to tell a slow run from a stuck one. "Interpreting
 * with AI", unchanged, for thirty seconds, is the same picture as a hung
 * request.
 *
 * So this counts the work that has *finished*: calls returned, findings
 * verified, findings withheld, charts drawn. Those numbers only move when
 * an event says so, which makes them evidence that something is happening
 * rather than reassurance that something might be.
 *
 * **There is deliberately no completion fraction.** A percentage needs a
 * denominator, and the number of tool calls a run will make is not known
 * until it has made them. The planner decides, and a follow-up round can
 * add more. Any bar would be either a guess or a timer, and a bar that
 * advances on a timer is indistinguishable from one that advances because
 * work was done. That is the single claim this product cannot afford to
 * get wrong, so the figures here are counts of completed work and an
 * elapsed time, both of which are facts.
 */

import { toolCallsOf } from "./executionGraph";
import { timelineOf, type Stage } from "./timeline";
import type { RunEvent } from "./types";

export interface RunProgressSummary {
  /** The stage an event says is in progress, or null when none is. */
  active: Stage | null;
  /** Stages the engine has reported finishing. */
  completedStages: number;
  /** Stages drawn at all. Not a denominator for a percentage; see above. */
  totalStages: number;
  calls: {
    completed: number;
    failed: number;
    refused: number;
    running: number;
  };
  findings: { verified: number; withheld: number };
  charts: number;
  /** The run ended, well or badly. */
  finished: boolean;
  /** It ended badly, and at which stage. */
  stopped: Stage | null;
}

function count(events: RunEvent[], type: RunEvent["type"]): number {
  return events.filter((event) => event.type === type).length;
}

export function progressOf(events: RunEvent[]): RunProgressSummary {
  const stages = timelineOf(events);
  const calls = toolCallsOf(events);

  const tally = (state: string) =>
    calls.filter((call) => call.state === state).length;

  return {
    active: stages.find((stage) => stage.state === "active") ?? null,
    completedStages: stages.filter((stage) => stage.state === "complete").length,
    totalStages: stages.length,
    calls: {
      completed: tally("completed"),
      failed: tally("failed"),
      refused: tally("refused"),
      running: tally("running"),
    },
    findings: {
      verified: count(events, "finding_verified"),
      withheld: count(events, "finding_rejected"),
    },
    charts: count(events, "chart_created"),
    finished: events.some(
      (event) =>
        event.type === "run_completed" ||
        event.type === "run_failed" ||
        event.type === "run_cancelled",
    ),
    stopped: stages.find((stage) => stage.state === "stopped") ?? null,
  };
}

/**
 * The work finished so far, in words, for a reader and a screen reader.
 *
 * Only items with a non-zero count, so a run that has not reached a stage
 * does not advertise "0 charts drawn" as though that were a result. An
 * empty list means nothing has finished yet, which the caller says
 * differently.
 */
export function workDone(progress: RunProgressSummary): string[] {
  const out: string[] = [];
  const plural = (n: number, one: string, many: string) =>
    `${n} ${n === 1 ? one : many}`;

  if (progress.calls.completed > 0) {
    out.push(plural(progress.calls.completed, "query returned", "queries returned"));
  }
  if (progress.calls.refused > 0) {
    // Named as a refusal, never as a failure: `preflight` declining a call
    // it knew was wrong is a guardrail working.
    out.push(
      plural(progress.calls.refused, "call refused before execution", "calls refused before execution"),
    );
  }
  if (progress.calls.failed > 0) {
    out.push(plural(progress.calls.failed, "query failed", "queries failed"));
  }
  if (progress.findings.verified > 0) {
    out.push(plural(progress.findings.verified, "finding verified", "findings verified"));
  }
  if (progress.findings.withheld > 0) {
    out.push(plural(progress.findings.withheld, "finding withheld", "findings withheld"));
  }
  if (progress.charts > 0) {
    out.push(plural(progress.charts, "chart drawn", "charts drawn"));
  }
  return out;
}

/** `8s`, `1m 04s`. Seconds below a minute, so a short run reads exactly. */
export function elapsedLabel(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  if (whole < 60) return `${whole}s`;
  const minutes = Math.floor(whole / 60);
  return `${minutes}m ${String(whole % 60).padStart(2, "0")}s`;
}
