/**
 * Compare Both was answering three different questions with one hash
 * comparison. These pin them apart.
 */

import { describe, expect, it } from "vitest";
import { laneStages } from "../components/ExecutionLanes";
import { compareRuns } from "../lib/comparison";
import type {
  CanonicalContract,
  Finding,
  QuestionCoverage,
  QueryContract,
  RunPayload,
} from "../lib/types";

const canonical: CanonicalContract = {
  operation: "average",
  table: "uploaded_data",
  measure: "annual_revenue",
  dimensions: ["region"],
  dimension: "region",
  time_field: null,
  time_grain: null,
  period: null,
  period_field: null,
  filters: [],
  ascending: false,
};

const complete: QuestionCoverage = {
  complete: true,
  required_components: ["operation", "measure", "dimensions"],
  applied_components: ["operation", "measure", "dimensions"],
  missing_components: [],
  rejection_codes: [],
  details: [],
};

const incomplete: QuestionCoverage = {
  complete: false,
  required_components: ["operation", "measure", "dimensions"],
  applied_components: ["operation", "measure"],
  missing_components: ["dimensions"],
  rejection_codes: ["missing_requested_grouping"],
  details: ["the question asks for a breakdown by region"],
};

const contract = (over: Partial<QueryContract> = {}): QueryContract =>
  ({
    ...canonical,
    confident: true,
    explanation: "ok",
    interpretation: "rule-based",
    contract_hash: "h",
    canonical_contract: canonical,
    ...over,
  }) as QueryContract;

const run = (over: Partial<RunPayload> = {}): RunPayload =>
  ({
    question: "q",
    findings: [
      {
        finding_id: "f",
        text: "Average revenue by region: north: 10.",
      } as Finding,
    ],
    rejected: [],
    charts: [],
    tasks: [],
    results: {},
    mcp_trace: [],
    events: [],
    stopped_reason: "",
    status: "completed",
    query_contract: contract(),
    question_coverage: complete,
    ...over,
  }) as RunPayload;

describe("compareRuns", () => {
  it("says both agreed only when the contract matches, covers the question and the values match", () => {
    const state = compareRuns(run(), run());
    expect(state.verdict).toBe("both_agree");
    expect(state.shareOneResult).toBe(true);
    expect(state.tone).toBe("supported");
  });

  it("does not call agreement on an incomplete contract a success", () => {
    // Two planners agreeing about the wrong thing is the failure mode
    // that made "same governed interpretation" misleading.
    const state = compareRuns(
      run({ question_coverage: incomplete }),
      run({ question_coverage: incomplete }),
    );
    expect(state.verdict).toBe("agree_but_incomplete");
    expect(state.tone).toBe("warn");
    expect(state.detail).toMatch(/agreement is not correctness/i);
    expect(state.detail).toMatch(/dimensions/);
  });

  it("does not present a fallback as the model independently agreeing", () => {
    const state = compareRuns(run(), run({ planner_fallback: true }));
    expect(state.verdict).toBe("agree_via_fallback");
    expect(state.detail).toMatch(/not because the model independently agreed/i);
  });

  it("names the differing fields when the contracts differ", () => {
    const other = { ...canonical, dimensions: ["business_type"] };
    const state = compareRuns(
      run(),
      run({
        query_contract: contract({
          dimensions: ["business_type"],
          canonical_contract: other,
        }),
      }),
    );
    expect(state.verdict).toBe("contracts_differ");
    expect(state.differences.map((d) => d.label)).toContain("Grouping");
    expect(state.shareOneResult).toBe(false);
  });

  it("treats the same contract with different numbers as an engine defect", () => {
    // There is no honest "the modes disagree" here: one governed request
    // over one dataset fingerprint has one answer.
    const state = compareRuns(
      run(),
      run({
        findings: [
          {
            finding_id: "f",
            text: "Average revenue by region: north: 99.",
          } as Finding,
        ],
      }),
    );
    expect(state.verdict).toBe("inconsistent_results");
    expect(state.tone).toBe("error");
    expect(state.detail).toMatch(/internal inconsistency/i);
    expect(state.shareOneResult).toBe(false);
  });

  it.each([
    ["refused", "one_refused"],
    ["failed", "one_failed"],
    ["timeout", "one_failed"],
  ])(
    "reports a %s side as %s rather than as a comparison",
    (status, verdict) => {
      expect(compareRuns(run(), run({ status })).verdict).toBe(verdict);
    },
  );

  it("reports both refusing as its own state", () => {
    const state = compareRuns(
      run({ status: "refused" }),
      run({ status: "refused" }),
    );
    expect(state.verdict).toBe("both_refused");
    expect(state.shareOneResult).toBe(false);
  });

  it("keeps two panes when the modes withheld different findings", () => {
    // Verification runs per run: the same published figure does not mean
    // the same findings survived. Sharing one result would hide the
    // withheld finding along with the pane that held it.
    const state = compareRuns(
      run(),
      run({ rejected: [{ finding_id: "r", text: "withheld" }] } as never),
    );
    expect(state.shareOneResult).toBe(false);
    expect(state.headline).toMatch(/withheld different findings/i);
  });

  it("says nothing before both sides finish", () => {
    expect(compareRuns(run(), null).verdict).toBe("not_comparable");
    expect(compareRuns(null, null).shareOneResult).toBe(false);
  });
});

describe("execution lanes", () => {
  it("shows the planner each mode used, and the shared computation", () => {
    const withPlanning = run({
      events: [
        {
          type: "contract_resolved",
          data: { model_calls: 1, planner: "ai-grounded" },
        },
        { type: "mcp_tool_called", data: {} },
      ],
      timings: { planning_ms: 820, execution_ms: 44, verification_ms: 2 },
    } as never);

    const ai = laneStages(withPlanning, "ai");
    const deterministicStages = laneStages(withPlanning, "deterministic");
    expect(ai).toHaveLength(5);
    expect(deterministicStages).toHaveLength(5);
    const aiPlanner = ai[0]!;
    const detPlanner = deterministicStages[0]!;
    expect(aiPlanner.label).toBe("Cloud semantic planner");
    expect(aiPlanner.detail).toMatch(/1 model call/);
    expect(aiPlanner.detail).toMatch(/820ms/);

    expect(detPlanner.label).toBe("Rule resolver");
    expect(detPlanner.detail).toMatch(/no model calls/);

    // Everything after the planner is the same lane.
    expect(ai.slice(1).map((stage) => stage.label)).toEqual(
      deterministicStages.slice(1).map((stage) => stage.label),
    );
    expect(ai[2]!.label).toBe("DuckDB via MCP");
  });

  it("always returns five stages, which the lane alignment depends on", () => {
    // `.lane-grid` declares `grid-template-rows: auto repeat(5, auto) auto`
    // and the lanes take those rows through `subgrid` so the two columns
    // line up stage for stage. A sixth stage would fall outside the
    // declared rows.
    const cases: RunPayload[] = [
      run(),
      run({ query_contract: null, question_coverage: null, events: [] } as never),
      run({
        query_contract: null,
        question_coverage: null,
        events: [
          { type: "question_analyzed", data: {} },
          { type: "plan_generated", data: { task_count: 4 } },
        ],
      } as never),
      run({ status: "refused", findings: [], events: [] } as never),
    ];
    for (const payload of cases) {
      for (const mode of ["ai", "deterministic"] as const) {
        expect(laneStages(payload, mode)).toHaveLength(5);
      }
    }
    expect(laneStages(null, "ai")).toHaveLength(5);
  });

  it("describes the registry path by what it did, not by a contract it never built", () => {
    // The bundled demo dataset is answered from the metric registry by
    // planning agents. It emits no contract_resolved and carries no upload
    // contract, and the lane used to report "Rule resolver: not reached"
    // and "Contract validation: not accepted" for a run that succeeded.
    const demo = run({
      query_contract: null,
      question_coverage: null,
      events: [
        {
          type: "question_analyzed",
          data: {
            analysis_type: "correlation",
            target_metrics: ["repeat_purchase_rate"],
            dimensions: ["shipping_delay_days"],
          },
        },
        { type: "plan_generated", data: { task_count: 3 } },
        { type: "followup_round_started", data: {} },
        { type: "mcp_tool_called", data: {} },
      ],
      timings: { planning_ms: 410, execution_ms: 52, verification_ms: 3 },
    } as never);

    for (const mode of ["ai", "deterministic"] as const) {
      const stages = laneStages(demo, mode);
      expect(stages).toHaveLength(5);
      for (const stage of stages.slice(0, 2)) {
        expect(stage.detail).not.toMatch(/not reached|not accepted/i);
        expect(stage.state).not.toBe("idle");
      }
      expect(stages[0]!.label).not.toMatch(/resolver|semantic planner/i);
      expect(stages[1]!.label).toBe("Analysis plan");
      expect(stages[1]!.detail).toMatch(/3 tasks/);
      expect(stages[1]!.detail).toMatch(/1 follow-up round/);
      expect(stages[2]!.label).toBe("DuckDB via MCP");
    }

    // The planner is still named per mode, as it is on the contract path.
    expect(laneStages(demo, "ai")[0]!.label).toBe("Question analyst");
    expect(laneStages(demo, "deterministic")[0]!.label).toBe(
      "Scripted analyst",
    );
    expect(laneStages(demo, "ai")[0]!.detail).toMatch(/correlation/);
  });

  it("still keeps contract stages for a run that was on the contract path", () => {
    // Detection must key on evidence a contract existed, not on the mere
    // presence of planning events, or an upload run that failed early would
    // be described as if it had been a registry run.
    const stages = laneStages(
      run({
        events: [{ type: "question_analyzed", data: {} }],
      } as never),
      "deterministic",
    );
    expect(stages[0]!.label).toBe("Rule resolver");
    expect(stages[1]!.label).toBe("Contract validation");
  });

  it("says the planner fell back rather than implying it agreed", () => {
    const stages = laneStages(
      run({
        planner_fallback: true,
        events: [{ type: "contract_resolved", data: { model_calls: 1 } }],
      } as never),
      "ai",
    );
    expect(stages[0]!.detail).toMatch(/fell back to the rules contract/i);
    expect(stages[0]!.state).toBe("failed");
  });

  it("marks computation skipped when the request was refused first", () => {
    const stages = laneStages(
      run({ status: "refused", findings: [], events: [] } as never),
      "deterministic",
    );
    const compute = stages.find((stage) => stage.key === "compute")!;
    expect(compute.state).toBe("skipped");
    expect(compute.detail).toMatch(/refused first/i);
  });
});
