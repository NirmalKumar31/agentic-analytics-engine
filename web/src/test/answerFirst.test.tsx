/**
 * The report opened with the question, the governed contract and an
 * executive summary, and only then the finding that answered it. A reader
 * had to scroll past three blocks of metadata to reach the number they
 * asked for. These pin the answer first, with the population it covers and
 * the rows it was computed over beside it.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReportView } from "../components/ReportView";
import {
  answerResult,
  directAnswer,
  populationClauses,
  rowsInScope,
} from "../lib/answer";
import type { Finding, QueryContract, ResultSnapshot } from "../lib/types";

const snapshot = (over: Partial<ResultSnapshot> = {}): ResultSnapshot =>
  ({
    result_id: "res_1",
    tool_name: "aggregate_for_question",
    task_id: null,
    sql: "select 1",
    columns: ["region", "average_annual_revenue", "row_count"],
    rows: [
      ["west", 511.24, 100],
      ["south", 504.42, 100],
    ],
    row_count: 2,
    truncated: false,
    dataset_fingerprint: "sha256:x",
    duration_ms: 3,
    parameters: {},
    warnings: [],
    statistical_result: null,
    ...over,
  }) as ResultSnapshot;

const finding = (over: Partial<Finding> = {}): Finding =>
  ({
    finding_id: "f1",
    text: "Average annual revenue by region: west: 511.24; south: 504.42.",
    kind: "calculated_fact",
    task_id: null,
    result_ids: ["res_1"],
    evidence_cells: [],
    metric_ids: [],
    verification_status: "supported",
    verifier_reason: "checked",
    numeric_check: null,
    claimed_change: null,
    ...over,
  }) as Finding;

const contract = (over: Partial<QueryContract> = {}): QueryContract =>
  ({
    operation: "average",
    table: "uploaded_data",
    measure: "annual_revenue",
    dimension: "region",
    time_field: null,
    period: null,
    period_field: null,
    filters: [],
    ascending: false,
    confident: true,
    explanation: "ok",
    interpretation: "rule-based",
    contract_hash: "h",
    ...over,
  }) as QueryContract;

describe("directAnswer", () => {
  it("picks the finding citing the contract aggregate, not the first one", () => {
    const context = finding({ finding_id: "f0", result_ids: ["res_profile"] });
    const answer = finding();
    const results = {
      res_profile: snapshot({
        result_id: "res_profile",
        tool_name: "profile_table",
      }),
      res_1: snapshot(),
    };
    expect(directAnswer([context, answer], results)?.finding_id).toBe("f1");
    expect(
      answerResult(directAnswer([context, answer], results), results)
        ?.result_id,
    ).toBe("res_1");
  });

  it("accepts a metric-registry answer too", () => {
    const results = { res_1: snapshot({ tool_name: "compute_metric" }) };
    expect(directAnswer([finding()], results)?.finding_id).toBe("f1");
  });

  it("is null when nothing published cites an executed contract", () => {
    const results = { res_1: snapshot({ tool_name: "profile_table" }) };
    expect(directAnswer([finding()], results)).toBeNull();
    expect(answerResult(null, results)).toBeNull();
  });
});

describe("rowsInScope", () => {
  it("sums the row counts across groups, not the number of groups", () => {
    // The population is 200 rows in two groups. `snapshot.row_count` is 2.
    expect(rowsInScope(snapshot())).toBe(200);
  });

  it("is null when the result does not report a row count", () => {
    expect(
      rowsInScope(snapshot({ columns: ["region", "average_annual_revenue"] })),
    ).toBeNull();
    expect(rowsInScope(null)).toBeNull();
  });

  it("is null rather than wrong when a count is not a number", () => {
    expect(rowsInScope(snapshot({ rows: [["west", 1, "many"]] }))).toBeNull();
  });
});

describe("populationClauses", () => {
  it("says nothing when the question restricted nothing", () => {
    expect(populationClauses(contract())).toEqual([]);
  });

  it("reads filters and the period in the reader’s words", () => {
    expect(
      populationClauses(
        contract({
          filters: [{ column: "age", operator: ">=", value: 30 }],
          period: ["2024-01-01", "2024-12-31"],
          period_field: "signup_date",
        }),
      ),
    ).toEqual(["age >= 30", "signup date 2024-01-01 to 2024-12-31"]);
  });
});

describe("ReportView order", () => {
  const results = { res_1: snapshot() };
  const render_ = (over: Partial<QueryContract> = {}) =>
    render(
      <ReportView
        question="What is the average annual revenue by region?"
        report={{
          question: "q",
          executive_summary: "Summary sentence.",
          key_findings: [],
          sections: [],
          limitations: ["A limitation."],
          next_questions: [],
        }}
        findings={[finding()]}
        rejected={[]}
        charts={[]}
        results={results}
        queryContract={contract(over)}
        onShowWork={() => {}}
      />,
    );

  it("puts the answer before the applied analysis and the summary", () => {
    const { container } = render_();
    const text = container.textContent ?? "";
    const answerAt = text.indexOf("Average annual revenue by region");
    expect(answerAt).toBeGreaterThan(-1);
    expect(answerAt).toBeLessThan(text.indexOf("Applied analysis"));
    expect(answerAt).toBeLessThan(text.indexOf("Summary sentence."));
    expect(answerAt).toBeLessThan(text.indexOf("A limitation."));
  });

  it("states the population and the rows counted", () => {
    render_({
      filters: [{ column: "age", operator: ">=", value: 30 }],
    });
    expect(screen.getByTestId("answer-population")).toHaveTextContent(
      "age >= 30",
    );
    expect(screen.getByTestId("answer-rows")).toHaveTextContent("200");
  });

  it("says every row rather than staying silent when nothing was restricted", () => {
    render_();
    expect(screen.getByTestId("answer-population")).toHaveTextContent(
      "every row in the dataset",
    );
  });

  it("shows the grouped result beside the answer", () => {
    const { container } = render_();
    const table = container.querySelector(".answer-result table");
    expect(table).not.toBeNull();
    expect(table?.textContent).toContain("west");
    expect(table?.textContent).toContain("511.24");
  });

  it("does not repeat the answer in the findings list below", () => {
    const { container } = render_();
    // The answer is itself an `article.finding`, so the rest of the app and
    // the browser suite still select it; it must not also appear twice.
    expect(container.querySelectorAll("article.finding")).toHaveLength(1);
    expect(container.textContent).toContain(
      "The answer above is the only published finding.",
    );
  });

  it("says so plainly when nothing answered the question", () => {
    render(
      <ReportView
        question="q"
        report={null}
        findings={[]}
        rejected={[]}
        charts={[]}
        results={{}}
        queryContract={contract()}
        onShowWork={() => {}}
      />,
    );
    expect(screen.getByTestId("no-direct-answer")).toBeInTheDocument();
  });
});

describe("ReportView empty states", () => {
  const base = {
    question: "q",
    report: null,
    rejected: [],
    charts: [],
    queryContract: contract(),
    onShowWork: () => {},
  };

  it("reads as a completion, not a crash, and says it once", () => {
    const { container } = render(
      <ReportView {...base} findings={[]} results={{}} />,
    );
    expect(
      screen.getByText(/no verified finding answered the requested analysis/i),
    ).toBeVisible();
    // Said twice is how the refusal wording went wrong before. A reader
    // reads a repeated caveat as two separate problems.
    const said = (container.textContent ?? "").match(
      /no verified finding answered the requested analysis/gi,
    );
    expect(said).toHaveLength(1);
  });

  it("distinguishes supporting context from a missing direct answer", () => {
    // Published, but citing a profile rather than the executed contract.
    const results = { res_1: snapshot({ tool_name: "profile_table" }) };
    render(<ReportView {...base} findings={[finding()]} results={results} />);
    expect(screen.getByTestId("no-direct-answer")).toHaveTextContent(
      /supporting context/i,
    );
    expect(screen.getByText("Key findings")).toBeVisible();
  });
});
