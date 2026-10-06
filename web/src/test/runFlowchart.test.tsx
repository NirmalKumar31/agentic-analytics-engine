/**
 * The run's shape is on the canvas, labelled, and connected.
 *
 * What was there before was six unlabelled dots, a line of counts and a
 * control. Every part of it was honestly derived from events and together
 * it was not a diagram: nothing said which stage was which, nothing
 * connected one to the next, and the only labelled picture of the run
 * lived behind "Inspect evidence" where a reader looking at their answer
 * never opened it.
 *
 * So there is a flowchart: a stage spine with arrows, each box carrying
 * its state in words, and the tool calls `compute` fanned out into. These
 * pin what it must keep being and the two things it must not become --
 * both inherited from the attempt that embedded the evidence sheet's
 * graph here and was refused.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RunFlowchart } from "../components/RunFlowchart";
import { ReportWorkspace } from "../components/ReportWorkspace";
import { flowchartOf } from "../lib/flowchart";
import type { RunEvent, RunPayload } from "../lib/types";

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

describe("the flowchart on the canvas", () => {
  it("is present for a run that emitted events", () => {
    workspace(trend);
    expect(screen.getByTestId("run-flowchart")).toBeInTheDocument();
  });

  it("draws one labelled box per stage the engine reported", () => {
    render(<RunFlowchart events={trend.events ?? []} />);
    const nodes = document.querySelectorAll(".run-flow-node");
    expect(nodes.length).toBeGreaterThan(0);
    for (const node of nodes) {
      expect(node.getAttribute("data-state")).toBeTruthy();
      expect(node.getAttribute("data-stage")).toBeTruthy();
      // The label is the part the dots did not have.
      expect(node.querySelector(".run-flow-label")?.textContent).toBeTruthy();
      // And the state in words, so colour carries none of it alone.
      expect(node.querySelector(".run-flow-state")?.textContent).toBeTruthy();
    }
  });

  it("names every stage the derivation names, in order", () => {
    const { stages } = flowchartOf(trend.events ?? []);
    render(<RunFlowchart events={trend.events ?? []} />);
    const drawn = [...document.querySelectorAll(".run-flow-node")].map((node) =>
      node.getAttribute("data-stage"),
    );
    expect(drawn).toEqual(stages.map((stage) => stage.id));
  });

  it("fans the tool calls out of the stage that made them", () => {
    render(<RunFlowchart events={trend.events ?? []} />);
    const branch = screen.getByTestId("run-flow-branch");
    expect(branch).toBeInTheDocument();
    const calls = document.querySelectorAll('[data-testid="flow-call"]');
    expect(calls.length).toBe(flowchartOf(trend.events ?? []).calls.length);
    expect(calls.length).toBeGreaterThan(0);
    for (const call of calls) {
      expect(call.getAttribute("data-call-state")).toBeTruthy();
    }
  });

  it("draws no fan at all when the engine reported no calls", () => {
    render(
      <RunFlowchart
        events={[{ event_id: "e1", seq: 1, type: "run_started", at: 1, data: {} }]}
      />,
    );
    expect(document.querySelector('[data-testid="run-flow-branch"]')).toBeNull();
  });

  it("keeps the counts, because a diagram does not replace them", () => {
    render(<RunFlowchart events={trend.events ?? []} />);
    expect(screen.getByTestId("run-flow-counts").textContent).toMatch(
      /quer(y|ies) returned/,
    );
  });

  it("says so plainly when the engine reported no completed work", () => {
    render(
      <RunFlowchart
        events={[{ event_id: "e1", seq: 1, type: "run_started", at: 1, data: {} }]}
      />,
    );
    expect(screen.getByTestId("run-flow-counts").textContent).toMatch(/no completed work/i);
  });

  it("restates the arrangement in prose", () => {
    render(<RunFlowchart events={trend.events ?? []} />);
    const narrative = screen.getByTestId("run-flow-narrative").textContent ?? "";
    expect(narrative).toMatch(/stages, in order/);
    expect(narrative).toMatch(/Compute made \d+ (call|calls)/);
  });

  it("opens the evidence sheet rather than expanding in place", () => {
    const onShowEvidence = vi.fn();
    render(<RunFlowchart events={trend.events ?? []} onShowEvidence={onShowEvidence} />);
    screen.getByTestId("run-flow-open").click();
    expect(onShowEvidence).toHaveBeenCalledOnce();
  });
});

describe("what it must never become", () => {
  it("is not the evidence sheet's graph: no audit rows, no second timeline", () => {
    /*
     * The inherited constraint. A first attempt embedded `ExecutionGraph`
     * on the canvas and two tests refused it: the graph carries the
     * engine's *unedited* stop reason, and it put `run-timeline` back on
     * the canvas the redesign removed it from. Both still hold -- the
     * flowchart is its own markup over the same derivations, not that
     * component rendered twice.
     */
    render(<RunFlowchart events={trend.events ?? []} />);
    expect(document.querySelector('[data-testid="run-timeline"]')).toBeNull();
    expect(document.querySelector('[data-testid="graph-call"]')).toBeNull();
    expect(document.querySelector('[data-testid="execution-graph"]')).toBeNull();
    expect(document.querySelector('[data-testid="graph-text-alternative"]')).toBeNull();
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
    const flow = canvas.querySelector('[data-testid="run-flowchart"]')!;
    // 4 === DOCUMENT_POSITION_FOLLOWING
    expect(answer.compareDocumentPosition(flow) & 4).toBe(4);
  });

  it("is absent in a Compare pane, which has one control for both runs", () => {
    render(<RunFlowchart events={trend.events ?? []} />);
    expect(document.querySelector('[data-testid="run-flow-open"]')).toBeNull();
  });
});

describe("the engine's own words stay in the evidence", () => {
  /** A refusal, as the engine actually emits one. */
  const REASON =
    "the question could not be mapped safely: the question asks about " +
    "'gross margin', which is not a column of this table";

  const refused: RunEvent[] = [
    { event_id: "e1", seq: 1, type: "run_started", at: 1, data: {} },
    { event_id: "e2", seq: 2, type: "question_analyzed", at: 2, data: {} },
    { event_id: "e3", seq: 3, type: "contract_resolved", at: 3, data: {} },
    {
      event_id: "e4",
      seq: 4,
      type: "mcp_tool_failed",
      at: 4,
      data: { tool_name: "aggregate_for_question", preflight: true, reason: REASON },
    },
    { event_id: "e5", seq: 5, type: "run_failed", at: 5, data: { reason: REASON } },
  ];

  it("does not print a stopped stage's reason on the canvas", () => {
    render(<RunFlowchart events={refused} />);
    expect(document.body.textContent).not.toContain("gross margin");
  });

  it("says which stage stopped, and nothing it cannot say in its own words", () => {
    /*
     * "stopped here" and no more. A first version added "the reason is in
     * the evidence" as the stage's note, and the refusal screenshot showed
     * that for noise: the state line already said it stopped, the report's
     * headline *is* the engine's reason -- a refusal's answer is why it
     * refused -- and the section carries one control to the full record.
     */
    render(<RunFlowchart events={refused} onShowEvidence={() => undefined} />);
    const stopped = document.querySelector('.run-flow-node[data-state="stopped"]');
    expect(stopped).not.toBeNull();
    expect(stopped!.textContent).toMatch(/stopped here/);
    expect(stopped!.querySelector(".run-flow-note")).toBeNull();
    // And the way to the engine's own words is still on offer.
    expect(screen.getByTestId("run-flow-open")).toBeInTheDocument();
  });

  it("does not print a refused call's reason either", () => {
    render(<RunFlowchart events={refused} />);
    const call = document.querySelector('[data-testid="flow-call"]')!;
    expect(call.textContent).toContain("refused before execution");
    expect(call.textContent).not.toContain("gross margin");
  });

  it("keeps the reason out of the text alternative as well", () => {
    /*
     * The alternative is prose, so it is the easiest place for engine text
     * to come back in unnoticed -- `describeGraph` in the evidence sheet
     * builds the same sentence *with* the reason, by design, and copying
     * it would have undone this quietly.
     */
    render(<RunFlowchart events={refused} />);
    const narrative = screen.getByTestId("run-flow-narrative").textContent ?? "";
    expect(narrative).not.toContain("gross margin");
    expect(narrative).toMatch(/compute — stopped here/);
  });

  it("still reports an authored stop reason, which is not engine text", () => {
    /*
     * The distinction this depends on: "stopped: budget reached" is
     * written here, so the canvas may say it. Suppressing every stop
     * detail would have been the easy version and would have told a
     * reader less than the truth allows.
     */
    const outOfBudget: RunEvent[] = [
      { event_id: "e1", seq: 1, type: "run_started", at: 1, data: {} },
      { event_id: "e2", seq: 2, type: "question_analyzed", at: 2, data: {} },
      { event_id: "e3", seq: 3, type: "budget_exceeded", at: 3, data: {} },
    ];
    render(<RunFlowchart events={outOfBudget} />);
    expect(document.body.textContent).toContain("budget reached");
  });
});

describe("the motion is bound to an event and to nothing else", () => {
  const motion = readFileSync(join(__dirname, "..", "styles", "motion.css"), "utf8");
  const withoutComments = motion.replace(/\/\*[\s\S]*?\*\//g, "");

  it("transitions the marks on colour, which only data-state changes", () => {
    expect(withoutComments).toMatch(
      /\.timeline-mark,\s*\.graph-call-mark,\s*\.run-flow-mark\s*\{[^}]*transition:/,
    );
  });

  it("transitions the flowchart's box and connector on the same basis", () => {
    expect(withoutComments).toMatch(/\.run-flow-box\s*\{[^}]*transition:/);
    expect(withoutComments).toMatch(
      /\.run-flow-node::before,\s*\.run-flow-node::after\s*\{[^}]*transition:/,
    );
  });

  it("gives a call chip one pass on arrival, because an event created it", () => {
    expect(withoutComments).toMatch(/\.run-flow-call\s*\{\s*animation:\s*node-arrive/);
    expect(withoutComments).toMatch(/@keyframes node-arrive/);
  });

  it("loops nothing: an animation that repeats is a claim about elapsed time", () => {
    expect(withoutComments).not.toMatch(/infinite/);
  });

  it("still withdraws every animation under reduced motion", () => {
    expect(withoutComments).toMatch(/prefers-reduced-motion/);
    expect(withoutComments).toMatch(/animation-iteration-count:\s*1\s*!important/);
  });
});

describe("the diagram has no glyphs a screen reader would read out", () => {
  const css = readFileSync(join(__dirname, "..", "styles", "timeline.css"), "utf8");

  it("draws its connectors as geometry rather than as arrow characters", () => {
    /*
     * `content: "→"` in a pseudo-element is announced by some screen
     * readers, which would narrate an arrow between every pair of stages
     * on top of the list semantics and the text alternative. The
     * connectors are an empty box with two borders turned 45 degrees.
     */
    const block = css.slice(css.indexOf(".run-flow-node::before"));
    const rules = block.slice(0, block.indexOf(".run-flow-node:last-child"));
    expect(rules).toMatch(/content:\s*""/);
    expect(rules).not.toMatch(/content:\s*"[^"]+"/);
  });

  it("marks the decorative mark itself as hidden", () => {
    render(<RunFlowchart events={trend.events ?? []} />);
    for (const mark of document.querySelectorAll(".run-flow-mark")) {
      expect(mark.getAttribute("aria-hidden")).toBe("true");
    }
  });
});

describe("the spine turns horizontal only where it fits as prose", () => {
  const css = readFileSync(join(__dirname, "..", "styles", "timeline.css"), "utf8");

  it("switches at 1440px, not at the usual 768", () => {
    /*
     * Measured, not chosen. A stage box holding a note -- "a model was
     * consulted to plan", 29 characters -- has to clear the sweep's 180px
     * floor for a box with a sentence in it. Six of those need 1080px of
     * track; the track measures 704 at 768px, 960 at 1024 and 1304 from
     * 1440 up, where the reading column caps. 768 and 1024 cannot hold
     * the row.
     *
     * Pinned because the 768px breakpoint is the house default -- it is
     * what `.timeline-track` uses -- and copying it here is the mistake
     * this number exists to prevent. It failed two sweep cells doing
     * exactly that.
     */
    const row = css.indexOf(".run-flow-track {\n    grid-auto-flow: column");
    expect(row, "the horizontal spine rule is gone").toBeGreaterThan(-1);
    const query = css.lastIndexOf("@media", row);
    expect(css.slice(query, css.indexOf("{", query)).trim()).toBe(
      "@media screen and (min-width: 1440px)",
    );
  });

  it("stays a column on paper, geometry and all", () => {
    /*
     * `screen and` is one word and it is load-bearing. A printed sheet is
     * about 816px of paper laid out from whatever viewport the reader
     * printed from, so without it this query still matched on paper --
     * and a first fix that overrode `grid-auto-flow` in the print block
     * left the row's *connectors* applied to a column of full-width
     * boxes, printing four arrowheads against the right margin. Found by
     * opening the artefact, not by a failing assertion.
     */
    const query = "@media screen and (min-width: 1440px)";
    const block = css.slice(css.indexOf(query) + query.length);
    const scoped = block.slice(0, block.indexOf("\n}\n"));
    // Both halves of the row treatment are inside the screen-only query.
    expect(scoped).toMatch(/grid-auto-flow: column/);
    expect(scoped).toMatch(/\.run-flow-node::after \{[^}]*rotate\(-45deg\)/);
  });

  it("is a width the acceptance matrix actually tests", () => {
    /*
     * A breakpoint at 1280 would put the behaviour on both sides of it
     * outside every cell of the sweep, so neither side would ever be
     * measured.
     */
    const matrix = readFileSync(join(__dirname, "..", "..", "hosted", "matrix.ts"), "utf8");
    const widths = [...matrix.matchAll(/\b(\d{3,4})\b/g)].map((m) => Number(m[1]));
    expect(widths).toContain(1440);
  });
});
