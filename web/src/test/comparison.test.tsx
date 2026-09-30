/**
 * Compare Both was answering three different questions with one hash
 * comparison. These pin them apart.
 */

import { describe, expect, it } from "vitest";
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

  it("says nothing before both sides finish", () => {
    expect(compareRuns(run(), null).verdict).toBe("not_comparable");
    expect(compareRuns(null, null).shareOneResult).toBe(false);
  });
});
