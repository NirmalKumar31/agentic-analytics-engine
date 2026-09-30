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
    expect(answerAt).toBeLessThan(text.indexOf("A limitation."));
    // The executive summary is gone beside a canonical answer. It said
    // "Each finding below passed the publication checks", which is true of
    // every report; a real one would restate the answer above it.
    expect(text).not.toContain("Summary sentence.");
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

  it("names the requested population rather than staying silent", () => {
    // A reader cannot tell silence from an unreported restriction. It says
    // what the *question* restricted -- how much of that population the
    // answer covers is the coverage line's job, and conflating the two is
    // how "every row in the dataset" came to sit above a 55% result.
    render_();
    expect(screen.getByTestId("answer-population")).toHaveTextContent(
      "no row filters requested",
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
    // The findings panel is gone: with the answer above it, its whole
    // content was "the answer above is the only published finding", a
    // heading explaining its own emptiness. The browser suite waits on
    // `report-panel` instead of on this heading.
    expect(screen.queryByText("Key findings")).toBeNull();
    expect(screen.getByTestId("report-panel")).toBeInTheDocument();
    // And the sentence that panel used to carry is gone with it.
    expect(container.textContent).not.toContain("only published finding");
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

describe("the report anchor the browser suite waits on", () => {
  it("renders the Key findings heading even when nothing was published", () => {
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
    // Hiding this panel on a refusal broke `waitForReport` for every
    // browser test that asserts a refusal, because the helper had no
    // other signal that the report had rendered at all.
    // Nothing published at all: the panel still appears, because now it
    // is saying something a reader needs rather than restating the answer.
    expect(screen.getByText("Key findings")).toBeVisible();
    expect(
      screen.getByText("None were published for this question."),
    ).toBeVisible();
  });
});

describe("coverage wording", () => {
  const withCoverage = (over: Record<string, unknown>) =>
    snapshot({
      group_coverage: {
        complete: true,
        groups_returned: 45,
        groups_total: 45,
        rows_total: 6435,
        rows_matching: 6435,
        rows_represented: 6435,
        query_limit: null,
        ordering: "dimension",
        ranked_by_request: false,
        ...over,
      },
    } as never);

  const report = (snap: ReturnType<typeof snapshot>) =>
    render(
      <ReportView
        question="q"
        report={null}
        findings={[finding()]}
        rejected={[]}
        charts={[]}
        results={{ res_1: snap }}
        queryContract={contract()}
        onShowWork={() => {}}
      />,
    );

  it("states complete coverage from counted values", () => {
    report(withCoverage({}));
    expect(screen.getByTestId("answer-coverage")).toHaveTextContent(
      "all 45 groups",
    );
    expect(screen.getByTestId("answer-coverage")).toHaveTextContent(
      "6,435 of 6,435 matching rows",
    );
    expect(screen.getByTestId("answer-rows")).toHaveTextContent("6,435");
    expect(screen.queryByTestId("partial-answer")).toBeNull();
  });

  it("says a partial breakdown is partial, with exact numbers", () => {
    // The production failure: 25 of 45 groups, 3,575 of 6,435 rows.
    report(
      withCoverage({
        complete: false,
        groups_returned: 25,
        groups_total: 45,
        rows_represented: 3575,
        query_limit: 25,
      }),
    );
    const notice = screen.getByTestId("partial-answer");
    expect(notice).toHaveTextContent(/partial breakdown/i);
    expect(notice).toHaveTextContent("25 of 45 groups");
    expect(notice).toHaveTextContent("3,575 of 6,435 matching rows");
    expect(notice).toHaveTextContent(/not the complete answer/i);
  });

  it("never claims every row for a partial result", () => {
    const { container } = report(
      withCoverage({ complete: false, groups_returned: 25, groups_total: 45 }),
    );
    expect(container.textContent).not.toMatch(/every row in the dataset/i);
    expect(container.textContent).not.toMatch(/complete breakdown/i);
  });

  it("reports rows represented, not rows matching, when partial", () => {
    report(
      withCoverage({
        complete: false,
        groups_returned: 25,
        groups_total: 45,
        rows_represented: 3575,
      }),
    );
    expect(screen.getByTestId("answer-rows")).toHaveTextContent("3,575");
  });
});
