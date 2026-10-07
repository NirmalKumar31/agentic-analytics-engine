/**
 * The in-flight screen tells the truth about a run, or says nothing.
 *
 * The claim worth testing is not "a spinner appears". It is that every
 * figure on screen moved because the backend reported work, that the
 * elapsed clock is the reader's own wait rather than the server's, and
 * that **no completion fraction exists anywhere**, because the planner
 * decides how many calls a run makes as it goes, so a denominator would be
 * a guess and a bar advancing on a timer is indistinguishable from one
 * advancing because something happened.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RunProgress } from "../components/RunProgress";
import { elapsedLabel, progressOf, workDone } from "../lib/runProgress";
import type { EventType, RunEvent } from "../lib/types";

let seq = 0;
const event = (type: EventType, data: Record<string, unknown> = {}): RunEvent => ({
  event_id: `e${(seq += 1)}`,
  seq,
  type,
  at: 1_700_000_000 + seq,
  data,
});
const reset = () => {
  seq = 0;
};

/** A run that got as far as two completed calls and one verified finding. */
function midRun(): RunEvent[] {
  reset();
  return [
    event("run_started"),
    event("dataset_loaded"),
    event("question_analyzed"),
    event("plan_generated", { task_count: 2 }),
    event("mcp_tool_called", { tool_name: "compute_metric", task_id: "t1" }),
    event("mcp_tool_completed", { tool_name: "compute_metric", task_id: "t1", row_count: 4 }),
    event("mcp_tool_called", { tool_name: "compare_segments", task_id: "t2" }),
    event("mcp_tool_completed", { tool_name: "compare_segments", task_id: "t2", row_count: 5 }),
    event("mcp_tool_called", { tool_name: "statistical_test", task_id: "t3" }),
    event("finding_verified"),
    event("finding_rejected", { reason: "not grounded" }),
    event("chart_created", { chart_kind: "bar", result_id: "res_1" }),
  ];
}

describe("progress is counted from events", () => {
  it("counts only calls the engine reported finishing", () => {
    const progress = progressOf(midRun());
    expect(progress.calls.completed).toBe(2);
    expect(progress.calls.running).toBe(1);
    expect(progress.calls.failed).toBe(0);
  });

  it("counts verified and withheld findings apart", () => {
    const progress = progressOf(midRun());
    expect(progress.findings).toEqual({ verified: 1, withheld: 1 });
  });

  it("reports nothing finished for a run that has only started", () => {
    reset();
    expect(workDone(progressOf([event("run_started")]))).toEqual([]);
  });

  it("names a refused call a refusal, never a failure", () => {
    reset();
    const done = workDone(
      progressOf([
        event("run_started"),
        event("mcp_tool_failed", {
          tool_name: "analyze_timeseries",
          task_id: "t",
          preflight: true,
          error: "does not accept 'dimensions'",
        }),
      ]),
    );
    expect(done).toContain("1 call refused before execution");
    expect(done.join(" ")).not.toMatch(/fail/i);
  });

  it("omits a zero rather than advertising it", () => {
    const done = workDone(progressOf(midRun()));
    expect(done.join(" ")).not.toMatch(/\b0 /);
  });

  it("knows a finished run from a running one", () => {
    reset();
    expect(progressOf([event("run_started")]).finished).toBe(false);
    expect(progressOf([event("run_started"), event("run_completed")]).finished).toBe(true);
    expect(progressOf([event("run_started"), event("run_failed")]).finished).toBe(true);
  });
});

describe("the elapsed figure", () => {
  it("reads in seconds below a minute, so a short run is exact", () => {
    expect(elapsedLabel(0)).toBe("0s");
    expect(elapsedLabel(8.7)).toBe("8s");
    expect(elapsedLabel(59)).toBe("59s");
  });

  it("pads the seconds once it is minutes, so it does not jump width", () => {
    expect(elapsedLabel(64)).toBe("1m 04s");
    expect(elapsedLabel(600)).toBe("10m 00s");
  });

  it("never goes negative, whatever it is handed", () => {
    expect(elapsedLabel(-5)).toBe("0s");
  });
});

describe("what the screen shows while a run is in flight", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("names the stage the engine says is in progress", () => {
    reset();
    render(<RunProgress events={[event("run_started"), event("dataset_loaded")]} replay={null} />);
    expect(screen.getByTestId("run-progress-stage").textContent).toMatch(
      /^Running: understand$/,
    );
  });

  it("says Running, not Starting, between stages", () => {
    /*
     * A real shape, not a contrived one: by the time two calls have
     * returned and a finding is verified, every stage the engine has
     * reported is complete and the next has not begun, so no stage is
     * active. Calling that "Starting" told a reader nothing had happened
     * while the work list beside it said two queries had returned.
     */
    render(<RunProgress events={midRun()} replay={null} />);
    expect(screen.getByTestId("run-progress-stage").textContent).toBe("Running");
  });

  it("says Starting only when there is nothing to report yet", () => {
    render(<RunProgress events={[]} replay={null} />);
    expect(screen.getByTestId("run-progress-stage").textContent).toBe("Starting");
  });

  it("lists the work that has finished", () => {
    render(<RunProgress events={midRun()} replay={null} />);
    const work = screen.getByTestId("run-progress-work").textContent ?? "";
    expect(work).toContain("2 queries returned");
    expect(work).toContain("1 finding verified");
    expect(work).toContain("1 finding withheld");
    expect(work).toContain("1 chart drawn");
  });

  it("shows no work line at all before anything finishes", () => {
    reset();
    render(<RunProgress events={[event("run_started")]} replay={null} />);
    expect(screen.queryByTestId("run-progress-work")).toBeNull();
  });

  it("counts the reader's own wait, and stops when the run ends", () => {
    const { rerender } = render(<RunProgress events={midRun()} replay={null} />);
    expect(screen.getByTestId("run-progress-elapsed").textContent).toBe("0s");
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByTestId("run-progress-elapsed").textContent).toBe("3s");

    // Finished: the clock is measuring nothing now, so it stops.
    rerender(<RunProgress events={[...midRun(), event("run_completed")]} replay={null} />);
    const atEnd = screen.getByTestId("run-progress-elapsed").textContent;
    act(() => vi.advanceTimersByTime(5000));
    expect(screen.getByTestId("run-progress-elapsed").textContent).toBe(atEnd);
  });

  it("does not start the clock before there is a run to time", () => {
    render(<RunProgress events={[]} replay={null} />);
    act(() => vi.advanceTimersByTime(4000));
    expect(screen.getByTestId("run-progress-elapsed").textContent).toBe("0s");
  });

  it("says why an AI run is waiting, without estimating how long", () => {
    render(<RunProgress events={midRun()} replay={null} mode="ai" />);
    const note = screen.getByTestId("run-progress-note").textContent ?? "";
    expect(note).toMatch(/cloud model/i);
    expect(note).toMatch(/not on a timer/i);
    // No estimate. The provider does not give one, so neither does this.
    expect(note).not.toMatch(/\b\d+\s*(seconds?|minutes?|s|m)\b/);
  });

  it("says nothing about a provider on a deterministic run", () => {
    render(<RunProgress events={midRun()} replay={null} mode="deterministic" />);
    expect(screen.queryByTestId("run-progress-note")).toBeNull();
  });

  it("drops the provider note once the run has finished", () => {
    render(
      <RunProgress events={[...midRun(), event("run_completed")]} replay={null} mode="ai" />,
    );
    expect(screen.queryByTestId("run-progress-note")).toBeNull();
  });

  it("says a run stopped, rather than leaving it reading as in progress", () => {
    reset();
    render(
      <RunProgress
        events={[event("run_started"), event("question_analyzed"), event("run_failed", { reason: "boom" })]}
        replay={null}
      />,
    );
    expect(screen.getByTestId("run-progress-stage").textContent).toMatch(/^Stopped at /);
  });
});

describe("nothing on screen is a simulated progress fraction", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("renders no progressbar role and no percentage", () => {
    const { container } = render(<RunProgress events={midRun()} replay={null} mode="ai" />);
    expect(container.querySelector('[role="progressbar"]')).toBeNull();
    expect(container.querySelector("progress")).toBeNull();
    // No "3 of 7", no "40%". A denominator does not exist: the planner
    // decides how many calls a run makes as it goes.
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\d+\s*%/);
    expect(text).not.toMatch(/\d+\s+of\s+\d+/);
  });

  it("leaves every figure unchanged when only time passes", () => {
    const events = midRun();
    const { container } = render(<RunProgress events={events} replay={null} mode="ai" />);
    const before = screen.getByTestId("run-progress-work").textContent;
    const stageBefore = container.querySelectorAll('[data-state="complete"]').length;

    act(() => vi.advanceTimersByTime(30_000));

    expect(screen.getByTestId("run-progress-work").textContent).toBe(before);
    expect(container.querySelectorAll('[data-state="complete"]').length).toBe(stageBefore);
  });
});
