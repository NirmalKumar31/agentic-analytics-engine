/**
 * The execution graph, rendered.
 *
 * `executionGraph.test.ts` pins the derivation. This pins what a reader
 * gets: that the graph is inside **How this analysis ran** rather than
 * above the answer, that each outcome is stated in words and not only in a
 * colour, that a refused call never reads as a failed tool, and that the
 * text alternative is open on paper.
 *
 * The refused case is here rather than in the browser suite on purpose: a
 * preflight rejection needs a planner proposing an argument the tool does
 * not accept, which is not a state a browser test can reach without making
 * the engine do something wrong on command.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExecutionGraph } from "../components/ExecutionGraph";
import { EvidenceBody } from "../components/EvidenceDrawer";
import type { RunEvent, RunPayload } from "../lib/types";

function load(...parts: string[]): RunPayload {
  return JSON.parse(readFileSync(join(__dirname, "runs", ...parts), "utf8"));
}

const trend = load("trend.json");

let seq = 0;
const event = (
  type: RunEvent["type"],
  data: Record<string, unknown> = {},
): RunEvent => ({
  event_id: `e${(seq += 1)}`,
  seq,
  type,
  at: seq,
  data,
});

/** A run whose first proposed call was declined before it was sent. */
function refusedRun(): RunEvent[] {
  seq = 0;
  return [
    event("run_started"),
    event("dataset_loaded"),
    event("question_analyzed"),
    event("mcp_tool_failed", {
      tool_name: "analyze_timeseries",
      task_id: "task_01",
      agent: "analysis_worker",
      preflight: true,
      error: "analyze_timeseries does not accept 'dimensions'",
    }),
    event("report_started"),
    event("report_completed", { finding_count: 0, rejected_count: 0 }),
    event("run_completed"),
  ];
}

describe("the graph is a record of this run", () => {
  it("draws one node per call the engine actually made", () => {
    render(<ExecutionGraph events={trend.events ?? []} />);
    // Three `mcp_tool_called`/`mcp_tool_completed` pairs in the fixture.
    expect(screen.getAllByTestId("graph-call")).toHaveLength(3);
  });

  it("draws nothing at all for a run with no events", () => {
    const { container } = render(<ExecutionGraph events={[]} />);
    expect(container.querySelector(".graph-calls")).toBeNull();
  });

  it("states each outcome in words, not in a colour alone", () => {
    render(<ExecutionGraph events={trend.events ?? []} />);
    for (const node of screen.getAllByTestId("graph-call")) {
      expect(node.textContent ?? "").toMatch(/returned \d+ rows?|returned a result/);
    }
  });

  it("carries no argument blob and no duration into a node", () => {
    render(<ExecutionGraph events={trend.events ?? []} />);
    const graph = screen.getByTestId("graph-calls").textContent ?? "";
    expect(graph).not.toMatch(/\bms\b/);
    expect(graph).not.toContain("{");
  });
});

describe("a refused call is drawn as a refusal", () => {
  it("says it was refused before execution", () => {
    render(<ExecutionGraph events={refusedRun()} />);
    const node = screen.getByTestId("graph-call");
    expect(node.getAttribute("data-call-state")).toBe("refused");
    expect(node.textContent).toContain("refused before execution");
  });

  it("does not say the tool failed", () => {
    render(<ExecutionGraph events={refusedRun()} />);
    expect(screen.getByTestId("graph-call").textContent).not.toContain(
      "failed",
    );
  });

  it("repeats the distinction in the text alternative", () => {
    render(<ExecutionGraph events={refusedRun()} />);
    expect(screen.getByTestId("graph-narrative").textContent).toContain(
      "refused before execution and never reached a tool",
    );
  });
});

describe("where it sits, and what paper gets", () => {
  it("is inside the section named How this analysis ran", () => {
    render(<EvidenceBody run={trend} />);
    const section = screen.getByRole("region", {
      name: "How this analysis ran",
    });
    expect(
      within(section).getByTestId("execution-graph"),
    ).toBeInTheDocument();
  });

  it("keeps the text alternative closed on screen", () => {
    render(<ExecutionGraph events={trend.events ?? []} />);
    expect(
      screen.getByTestId("graph-text-alternative").hasAttribute("open"),
    ).toBe(false);
  });

  it("opens it in the markup for the appendix, where CSS cannot", () => {
    render(<ExecutionGraph events={trend.events ?? []} expanded />);
    expect(
      screen.getByTestId("graph-text-alternative").hasAttribute("open"),
    ).toBe(true);
  });

  it("opens it through the evidence body the appendix renders", () => {
    render(<EvidenceBody run={trend} expanded />);
    expect(
      screen.getByTestId("graph-text-alternative").hasAttribute("open"),
    ).toBe(true);
  });
});
