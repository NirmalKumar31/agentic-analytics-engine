/**
 * The headline must answer the question, not come first in the list.
 *
 * A live run of "Which customer segments are driving the increase in return
 * rate?" led with:
 *
 *   "refund_amount rose from 49,863 in 2025-10-01 to 92,372 in 2025-11-01"
 *
 * while the finding that compared `return_rate` across `customer_segment` --
 * the question, answered, verified and published -- was second. Nothing was
 * wrong with either finding. The report simply promoted whichever one the
 * planner happened to emit first.
 *
 * The cause: `directAnswer` only recognises a finding citing a result from a
 * tool that executed an accepted contract, and a contract is only accepted
 * for uploaded data. On the governed warehouse -- which is what the public
 * deployment serves -- it always declines, and the fallback was
 * `findings[0]`. Task ordering had become editorial ranking.
 *
 * These fixtures are the shape of that run: a refund trend first, a segment
 * comparison second, and the planner's own record of what was asked.
 */

import { describe, expect, it } from "vitest";

import { rankedAnswer } from "../lib/answer";
import type {
  Finding,
  PlannerInterpretation,
  ResultSnapshot,
} from "../lib/types";

function finding(id: string, text: string, resultIds: string[]): Finding {
  return {
    finding_id: id,
    text,
    kind: "observation",
    task_id: null,
    result_ids: resultIds,
    evidence_cells: [],
    metric_ids: [],
    verification_status: "supported",
    verifier_reason: "",
    numeric_check: null,
    claimed_change: null,
  } as unknown as Finding;
}

function snapshot(
  id: string,
  tool: string,
  columns: string[],
  parameters: Record<string, unknown> = {},
): ResultSnapshot {
  return {
    result_id: id,
    tool_name: tool,
    task_id: null,
    sql: null,
    columns,
    rows: [],
    row_count: 0,
    truncated: false,
    dataset_fingerprint: "fp",
    duration_ms: 1,
    parameters,
    warnings: [],
    statistical_result: null,
  } as unknown as ResultSnapshot;
}

/** What the planner recorded for the question that was actually asked. */
const ASKED: PlannerInterpretation = {
  analysis_type: "segmentation",
  metrics: ["return_rate"],
  dimensions: ["customer_segment"],
  period: null,
  ambiguous: true,
  ambiguities: ["No baseline or comparison period was named"],
};

// The run's own results, in the order the planner dispatched them.
const RESULTS: Record<string, ResultSnapshot> = {
  res_refund: snapshot("res_refund", "analyze_timeseries", ["period", "refund_amount"], {
    metric: "refund_amount",
    grain: "month",
  }),
  res_segment: snapshot(
    "res_segment",
    "compare_segments",
    ["customer_segment", "return_rate"],
    { metric: "return_rate", dimension: "customer_segment" },
  ),
  res_chisq: snapshot("res_chisq", "statistical_test", ["statistic", "p_value"], {
    test_type: "chi_square",
  }),
};

const REFUND = finding("f1", "refund_amount rose from 49,863 to 92,372.", ["res_refund"]);
const SEGMENT = finding(
  "f2",
  "Across customer_segment, new has the highest return_rate at 9.96%.",
  ["res_segment"],
);
const CHISQ = finding("f3", "A chi-square test returned p = 2.62e-12.", ["res_chisq"]);

describe("the headline", () => {
  it("answers the question instead of leading with the first finding", () => {
    const { finding: answer, onTopic } = rankedAnswer(
      [REFUND, SEGMENT, CHISQ],
      RESULTS,
      ASKED,
    );
    expect(answer?.finding_id).toBe("f2");
    expect(onTopic).toBe(true);
  });

  it("prefers the requested measure over the requested grouping", () => {
    // A finding about the right metric without the grouping still beats one
    // about the wrong metric: a wrong measure cannot answer the question,
    // while a right one without the cut is at least about the right thing.
    const rateOnly = finding("f4", "return_rate fell to 7.80%.", ["res_rate_only"]);
    const results = {
      ...RESULTS,
      res_rate_only: snapshot("res_rate_only", "analyze_timeseries", ["period", "return_rate"], {
        metric: "return_rate",
      }),
    };
    const { finding: answer } = rankedAnswer([REFUND, rateOnly], results, ASKED);
    expect(answer?.finding_id).toBe("f4");
  });

  it("says so when no published finding is about the measure asked for", () => {
    const { finding: answer, onTopic } = rankedAnswer([REFUND, CHISQ], RESULTS, ASKED);
    // The finding is still surfaced -- withholding a verified fact would be
    // a second mistake -- but it is not presented as the answer.
    expect(answer).not.toBeNull();
    expect(onTopic).toBe(false);
  });

  it("keeps the engine's order when nothing was recorded to rank against", () => {
    // No interpretation means no basis for reordering, and inventing one
    // would be worse than the ordering the planner chose.
    const { finding: answer, onTopic } = rankedAnswer(
      [REFUND, SEGMENT],
      RESULTS,
      null,
    );
    expect(answer?.finding_id).toBe("f1");
    expect(onTopic).toBe(true);
  });

  it("returns nothing when nothing was published", () => {
    expect(rankedAnswer([], RESULTS, ASKED).finding).toBeNull();
  });
});
