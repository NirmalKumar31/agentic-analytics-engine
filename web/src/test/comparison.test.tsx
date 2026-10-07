/**
 * Compare Both was answering three different questions with one hash
 * comparison. These pin them apart.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

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

/*
 * The "execution lanes" suite stood here and went with `ExecutionLanes`.
 *
 * It tested `laneStages`, the pure function behind the STAGES card panel:
 * that five stages were always returned, that the registry path was
 * described by what it did rather than by a contract it never built, that a
 * planner fallback was not reported as the model agreeing, and that
 * computation was marked skipped when the request was refused first.
 *
 * The panel is gone. It restated the same five steps a third time, after
 * the stepper and the agent diagram had each already said them, and
 * Compare now states how each strategy got there in a single table.
 *
 * Those claims are not lost. They are the run timeline's, derived from the
 * events a run actually emitted rather than from a mode string, and tested
 * in `timeline.test.ts` against captured payloads -- including the two
 * sequences that showed the old derivation was reading fields the engine
 * does not emit. `compareRuns` keeps the fallback claim directly: see
 * "does not present a fallback as the model independently agreeing" above.
 */

describe("a run that has not finished has not disagreed", () => {
  // Seen against a real governed Compare run: the deterministic pane
  // finished, the AI pane still said "running", and the header announced
  // "Different governed interpretations. These panes answered different
  // questions". They had not. The AI run had no contract yet, and a missing
  // contract was read as a different one. Both planners went on to produce
  // the identical figure.
  const contract = {
    operation: "aggregate",
    measure: "revenue",
    dimensions: ["region"],
  } as unknown as RunPayload["query_contract"];

  function payload(overrides: Partial<RunPayload>): RunPayload {
    return {
      run_id: "r",
      session_id: "s",
      question: "What is total revenue by region?",
      status: "completed",
      report: null,
      findings: [],
      rejected: [],
      charts: [],
      tasks: [],
      results: {},
      mcp_trace: [],
      events: [],
      metrics: {} as RunPayload["metrics"],
      stopped_reason: "",
      ...overrides,
    } as RunPayload;
  }

  it("says not comparable yet while one side is still running", () => {
    const state = compareRuns(
      payload({ status: "completed", query_contract: contract }),
      payload({ status: "running" }),
    );
    expect(state.verdict).toBe("not_comparable");
    expect(state.headline).toMatch(/not comparable yet/i);
    expect(state.detail).toMatch(/need to finish/i);
  });

  it("does not claim they answered different questions", () => {
    const state = compareRuns(
      payload({ status: "completed", query_contract: contract }),
      payload({ status: "running" }),
    );
    expect(state.verdict).not.toBe("contracts_differ");
    expect(state.detail).not.toMatch(/different questions/i);
    expect(state.differences).toEqual([]);
  });

  it("still reports a failure that is already known", () => {
    // A pane that failed has finished, so that verdict is not premature.
    const state = compareRuns(
      payload({ status: "failed" }),
      payload({ status: "running" }),
    );
    expect(state.verdict).toBe("one_failed");
  });

  it("compares them once both have finished", () => {
    const state = compareRuns(
      payload({ status: "completed", query_contract: contract }),
      payload({ status: "completed", query_contract: contract }),
    );
    expect(state.verdict).not.toBe("not_comparable");
  });
});

describe("the copy matches the layout it describes", () => {
  /*
   * Compare showed two reports side by side and called them panes. The
   * panes are gone -- a full report in half a laptop's width is not a
   * comparison, and the verdict copy went on saying "These panes
   * answered different questions" above a switcher with no panes in it.
   *
   * Found by reading a production screenshot, which is the only place a
   * stale word like this shows up: every assertion about the verdict
   * matched on the parts that had not changed.
   */
  it("says strategy, not pane, in every verdict sentence", () => {
    const source = readFileSync(join(__dirname, "..", "lib", "comparison.ts"), "utf8");
    // Comments may discuss the old layout; rendered strings may not.
    const strings = source
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
      .match(/"(?:[^"\\]|\\.)*"/g) ?? [];
    const offenders = strings.filter((text) => /\bpanes?\b/i.test(text));
    expect(offenders, "verdict copy still calls a strategy a pane").toEqual([]);
  });

  it("and neither does the mode taxonomy", () => {
    const source = readFileSync(
      join(__dirname, "..", "components", "ModeSelector.tsx"),
      "utf8",
    );
    const rendered = source
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    expect(/\bpanes\b/i.test(rendered)).toBe(false);
  });
});
