/**
 * The run's shape is visible without opening anything.
 *
 * The full graph lives in the evidence sheet and stays there -- the
 * stages, the per-call outcomes and the text alternative are an audit, and
 * an audit does not belong between a reader and their answer. But behind
 * one control it was invisible: a reader who wanted to know whether
 * anything actually ran had no sign there was anything to open.
 *
 * So there is a summary on the canvas: the marks, the counts that came
 * from events, and a control that opens the rest. What these pin is the
 * two things it must not become.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ExecutionGraphSummary } from "../components/ExecutionGraphSummary";
import { ReportWorkspace } from "../components/ReportWorkspace";
import type { RunPayload } from "../lib/types";

function load(name: string): RunPayload {
  return JSON.parse(readFileSync(join(__dirname, "runs", name), "utf8"));
}

const trend = load("trend.json");

function workspace(run: RunPayload, onShowEvidence = () => undefined) {
  return render(
    <ReportWorkspace
      comparison={null}
      run={run}
      aiRun={null}
      aiError={null}
      config={null}
      deterministicPending={false}
      onShowEvidence={onShowEvidence}
    />,
  );
}

describe("the summary on the canvas", () => {
  it("is present for a run that emitted events", () => {
    workspace(trend);
    expect(screen.getByTestId("execution-graph-summary")).toBeInTheDocument();
  });

  it("draws one mark per stage the engine reported", () => {
    render(<ExecutionGraphSummary events={trend.events ?? []} />);
    const marks = document.querySelectorAll(".graph-summary-mark");
    expect(marks.length).toBeGreaterThan(0);
    for (const mark of marks) {
      expect(mark.getAttribute("data-state")).toBeTruthy();
    }
  });

  it("states the work that finished, from events", () => {
    render(<ExecutionGraphSummary events={trend.events ?? []} />);
    expect(screen.getByTestId("execution-graph-summary-line").textContent).toMatch(
      /quer(y|ies) returned/,
    );
  });

  it("says so plainly when the engine reported no completed work", () => {
    render(
      <ExecutionGraphSummary
        events={[
          {
            event_id: "e1",
            seq: 1,
            type: "run_started",
            at: 1,
            data: {},
          },
        ]}
      />,
    );
    expect(screen.getByTestId("execution-graph-summary-line").textContent).toMatch(
      /no completed work/i,
    );
  });

  it("opens the evidence sheet rather than expanding in place", () => {
    const onShowEvidence = vi.fn();
    render(
      <ExecutionGraphSummary events={trend.events ?? []} onShowEvidence={onShowEvidence} />,
    );
    screen.getByTestId("execution-graph-open").click();
    expect(onShowEvidence).toHaveBeenCalledOnce();
  });
});

describe("what the summary must never become", () => {
  it("is not a second graph: no stage labels, no call rows", () => {
    /*
     * A first version embedded `ExecutionGraph` in a `<details>` here, and
     * two tests refused it. The graph carries the engine's *unedited* stop
     * reason -- "the question could not be mapped safely: the question
     * asks about 'gross margin', which is not a column of this table" --
     * and the canvas does not take unedited engine text. It also put
     * `run-timeline` back on the canvas the redesign removed it from.
     */
    render(<ExecutionGraphSummary events={trend.events ?? []} />);
    expect(document.querySelector('[data-testid="run-timeline"]')).toBeNull();
    expect(document.querySelector('[data-testid="graph-call"]')).toBeNull();
    expect(document.querySelector('[data-testid="execution-graph"]')).toBeNull();
  });

  it("keeps the full graph off the canvas in a whole report", () => {
    workspace(trend);
    const canvas = screen.getByTestId("report-panel");
    for (const testId of ["run-timeline", "graph-call", "planning-audit", "activity"]) {
      expect(
        canvas.querySelector(`[data-testid="${testId}"]:not([data-print-appendix] *)`),
        `${testId} is resident on the canvas`,
      ).toBeNull();
    }
  });

  it("sits under the answer, never above it", () => {
    workspace(trend);
    const canvas = screen.getByTestId("report-panel");
    const answer = canvas.querySelector('[data-testid="direct-answer"]')!;
    const summary = canvas.querySelector('[data-testid="execution-graph-summary"]')!;
    // 4 === DOCUMENT_POSITION_FOLLOWING
    expect(answer.compareDocumentPosition(summary) & 4).toBe(4);
  });

  it("is absent in a Compare pane, which has one control for both runs", () => {
    render(<ExecutionGraphSummary events={trend.events ?? []} />);
    expect(document.querySelector('[data-testid="execution-graph-open"]')).toBeNull();
  });
});

describe("the motion is bound to an event and to nothing else", () => {
  const motion = readFileSync(join(__dirname, "..", "styles", "motion.css"), "utf8");
  const withoutComments = motion.replace(/\/\*[\s\S]*?\*\//g, "");

  it("transitions the marks on colour, which only data-state changes", () => {
    expect(withoutComments).toMatch(
      /\.timeline-mark,\s*\.graph-call-mark,\s*\.graph-summary-mark\s*\{[^}]*transition:/,
    );
  });

  it("gives a call node one pass on arrival, because an event created it", () => {
    expect(withoutComments).toMatch(/\.graph-call\s*\{\s*animation:\s*node-arrive/);
    expect(withoutComments).toMatch(/@keyframes node-arrive/);
  });

  it("loops nothing: an animation that repeats is a claim about elapsed time", () => {
    expect(withoutComments).not.toMatch(/infinite/);
    expect(withoutComments).not.toMatch(/node-arrive[^;]*infinite/);
  });

  it("still withdraws every animation under reduced motion", () => {
    expect(withoutComments).toMatch(/prefers-reduced-motion/);
    expect(withoutComments).toMatch(/animation-iteration-count:\s*1\s*!important/);
  });
});
