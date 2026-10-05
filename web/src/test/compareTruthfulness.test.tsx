/**
 * The Compare strip may not claim agreement over data that is absent.
 *
 * A live run of "Which customer segments are driving the increase in return
 * rate?" summarised itself as `contracts: identical · coverage: identical`
 * while each pane's own evidence appendix, on the same printed page, read
 * "no contract was accepted" and "coverage not recorded for this run". Two
 * absences had been read as an agreement.
 *
 * The cause was a difference *list*: `contractDifferences` returns `[]`
 * both when two contracts agree and when there is nothing to compare, and
 * the strip rendered the first meaning for both. So the state is now
 * computed separately from the list, with four values rather than two.
 *
 * These are assertions about truthfulness, not about layout. A report whose
 * summary contradicts its own appendix is worse than one that says it does
 * not know.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CompareWorkspace } from "../components/CompareWorkspace";
import {
  COMPARISON_LABEL,
  contractComparison,
} from "../lib/contractDiff";
import type { QueryContract, QuestionCoverage, RunPayload } from "../lib/types";

const CONTRACT: QueryContract = {
  operation: "aggregate",
  table: "sales",
  measure: "return_rate",
  dimensions: ["customer_segment"],
  filters: [],
  time_grain: "month",
  period: null,
  period_field: null,
  time_field: "order_date",
  ascending: false,
  confident: true,
  explanation: "validated",
  interpretation: "rule-based",
  contract_hash: "same-hash",
} as unknown as QueryContract;

const COVERAGE: QuestionCoverage = {
  complete: true,
  required_components: [],
  applied_components: [],
  missing_components: [],
  rejection_codes: [],
  details: [],
};

function runWith(overrides: Partial<RunPayload> = {}): RunPayload {
  return { findings: [], ...overrides } as RunPayload;
}

function side(overrides: Record<string, unknown> = {}) {
  return {
    title: "AI Analytics",
    subtitle: "A cloud model plans and interprets.",
    run: null,
    error: null,
    pending: false,
    children: null,
    ...overrides,
  } as Parameters<typeof CompareWorkspace>[0]["ai"];
}

/** The strip's value for one term, read from the rendered page. */
function fact(term: string): string {
  const dt = screen.getByText(term, { selector: "dt" });
  const dd = dt.nextElementSibling;
  return (dd?.textContent ?? "").trim();
}

describe("the comparison state", () => {
  it("is not_recorded when neither pane accepted a contract", () => {
    // The exact shape of the live run: both panes completed, neither
    // produced a contract.
    expect(
      contractComparison(undefined, undefined, { bothRan: true }),
    ).toBe("not_recorded");
  });

  it("is not_recorded when only one pane accepted a contract", () => {
    expect(contractComparison(CONTRACT, undefined, { bothRan: true })).toBe(
      "not_recorded",
    );
    expect(contractComparison(undefined, CONTRACT, { bothRan: true })).toBe(
      "not_recorded",
    );
  });

  it("is identical only when both contracts exist and agree", () => {
    expect(
      contractComparison(CONTRACT, { ...CONTRACT, interpretation: "ai" }, {
        bothRan: true,
      }),
    ).toBe("identical");
  });

  it("is different when both exist and disagree", () => {
    expect(
      contractComparison(
        CONTRACT,
        { ...CONTRACT, dimensions: ["region"] } as QueryContract,
        { bothRan: true },
      ),
    ).toBe("different");
  });

  it("is not_comparable when a run is missing entirely", () => {
    expect(contractComparison(CONTRACT, CONTRACT, { bothRan: false })).toBe(
      "not_comparable",
    );
  });

  it("has a distinct word for every state", () => {
    // Four states collapsing to three words would put the defect back.
    const words = Object.values(COMPARISON_LABEL);
    expect(new Set(words).size).toBe(words.length);
  });
});

describe("the Compare strip", () => {
  it("says contracts were not recorded rather than identical", () => {
    render(
      <CompareWorkspace
        question="Which customer segments are driving the increase in return rate?"
        deterministic={side({
          title: "Deterministic Analytics",
          run: runWith(),
          children: <p>left</p>,
        })}
        ai={side({ run: runWith(), children: <p>right</p> })}
      />,
    );
    expect(fact("contracts")).toBe("not recorded");
    expect(fact("contracts")).not.toBe("identical");
  });

  it("says coverage was not recorded rather than identical", () => {
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side({
          title: "Deterministic Analytics",
          run: runWith(),
          children: <p>left</p>,
        })}
        ai={side({ run: runWith(), children: <p>right</p> })}
      />,
    );
    expect(fact("coverage")).toBe("not recorded");
  });

  it("still says identical when both panes really did record the same thing", () => {
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side({
          title: "Deterministic Analytics",
          run: runWith({ query_contract: CONTRACT, question_coverage: COVERAGE }),
          children: <p>left</p>,
        })}
        ai={side({
          run: runWith({
            query_contract: { ...CONTRACT, interpretation: "ai" } as QueryContract,
            question_coverage: COVERAGE,
          }),
          children: <p>right</p>,
        })}
      />,
    );
    expect(fact("contracts")).toBe("identical");
    expect(fact("coverage")).toBe("identical");
  });

  it("flags a missing comparison rather than passing it off as a dash", () => {
    // `not recorded` is a fact the reader needs, so it is marked like a
    // difference is. Silence would read as "nothing to see".
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side({
          title: "Deterministic Analytics",
          run: runWith(),
          children: <p>left</p>,
        })}
        ai={side({ run: runWith(), children: <p>right</p> })}
      />,
    );
    const dt = screen.getByText("contracts", { selector: "dt" });
    expect(dt.nextElementSibling?.className).toContain("compare-fact--warn");
  });
});

describe("a refused call is not a failed tool", () => {
  it("says a tool was not called when the engine refused it before dispatch", async () => {
    // `preflight` checks a proposed call against the tool's signature and
    // refuses it before anything crosses MCP. The trace read "Analyze
    // Timeseries failed: analyze_timeseries does not accept 'dimensions'",
    // which describes the analysis breaking rather than a guardrail
    // working. The event always carried `preflight`; the wording ignored it.
    const { ActivityLog } = await import("../components/ActivityLog");
    render(
      <ActivityLog
        events={[
          {
            event_id: "e1",
            type: "mcp_tool_failed",
            at: 0,
            data: {
              agent: "analysis_worker",
              tool_name: "analyze_timeseries",
              error: "analyze_timeseries does not accept 'dimensions'.",
              preflight: true,
            },
          },
        ] as never}
        trace={[]}
        showTrace={false}
        onToggleTrace={() => {}}
        running={false}
      />,
    );
    expect(screen.getByText(/was not called/)).toBeInTheDocument();
    expect(screen.queryByText(/Timeseries failed/)).not.toBeInTheDocument();
  });

  it("still says failed when the tool itself failed", async () => {
    const { ActivityLog } = await import("../components/ActivityLog");
    render(
      <ActivityLog
        events={[
          {
            event_id: "e2",
            type: "mcp_tool_failed",
            at: 0,
            data: {
              agent: "analysis_worker",
              tool_name: "compute_metric",
              error: "the warehouse connection was lost",
            },
          },
        ] as never}
        trace={[]}
        showTrace={false}
        onToggleTrace={() => {}}
        running={false}
      />,
    );
    expect(screen.getByText(/failed/)).toBeInTheDocument();
  });
});
