/**
 * The report opened with the question, the governed contract and an
 * executive summary, and only then the finding that answered it. A reader
 * had to scroll past three blocks of metadata to reach the number they
 * asked for. These pin the answer first, with the population it covers and
 * the rows it was computed over beside it.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReportUnderTest } from "./renderReport";
import {
  answerResult,
  directAnswer,
  observationsUsed,
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

describe("observationsUsed", () => {
  it("counts non-null measure values separately from population rows", () => {
    expect(
      observationsUsed(
        snapshot({
          columns: ["region", "average_annual_revenue", "row_count", "value_count"],
          rows: [
            ["west", 511.24, 100, 96],
            ["south", 504.42, 100, 91],
          ],
        }),
      ),
    ).toBe(187);
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

describe("the report order", () => {
  const results = { res_1: snapshot() };
  const render_ = (over: Partial<QueryContract> = {}) =>
    render(<ReportUnderTest
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
    // The limitation is a note *under* the answer, not above it.
    expect(answerAt).toBeLessThan(text.indexOf("A limitation."));
    // "Applied analysis" was a resident contract panel. It is in the
    // evidence drawer now, under "Accepted contract".
    expect(text).not.toContain("Applied analysis");
    // The executive summary is gone beside a canonical answer. It said
    // "Each finding below passed the publication checks", which is true of
    // every report; a real one would restate the answer above it.
    expect(text).not.toContain("Summary sentence.");
  });

  it("states the population and the rows counted", () => {
    render_({
      filters: [{ column: "age", operator: ">=", value: 30 }],
    });
    // One context line now, rather than a `<dl>` of Population / Population
    // rows / Observations used / Coverage. The facts are the same and all
    // still stated; what is gone is four labelled rows between the answer
    // and the chart.
    const context = screen.getByTestId("answer-coverage");
    expect(context).toHaveTextContent("age >= 30");
    expect(context).toHaveTextContent("200");
  });

  it("names the requested population rather than staying silent", () => {
    // A reader cannot tell silence from an unreported restriction. It says
    // what the *question* restricted -- how much of that population the
    // answer covers is the coverage line's job, and conflating the two is
    // how "every row in the dataset" came to sit above a 55% result.
    render_();
    expect(screen.getByTestId("answer-coverage")).toHaveTextContent(
      "no row filters requested",
    );
  });

  it("shows the grouped result beside the answer", () => {
    const { container } = render_();
    const table = container.querySelector(".report-table table");
    expect(table).not.toBeNull();
    expect(table?.textContent).toContain("west");
    expect(table?.textContent).toContain("511.24");
  });

  it("does not repeat the answer in the findings list below", () => {
    const { container } = render_();
    // One published finding, and it is the answer -- so the ranked list
    // beneath has nothing left to rank and does not render at all.
    expect(container.querySelectorAll(".finding-item")).toHaveLength(0);
    // The answer appears once.
    const said = (container.textContent ?? "").match(
      /Average annual revenue by region/g,
    );
    expect(said).toHaveLength(1);
    expect(screen.queryByText("Key findings")).toBeNull();
    expect(screen.getByTestId("report-panel")).toBeInTheDocument();
    // And the sentence that panel used to carry is gone with it.
    expect(container.textContent).not.toContain("only published finding");
  });

  it("says so plainly when nothing answered the question", () => {
    render(<ReportUnderTest
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
    // The report says so in the answer slot itself rather than in a
    // separate "no direct answer" line: there is one place a reader looks
    // for the answer, and this is what it says when there is not one.
    expect(screen.getByTestId("direct-answer")).toHaveTextContent(
      /no verified finding answered/i,
    );
  });
});

describe("the report empty states", () => {
  const base = {
    question: "q",
    report: null,
    rejected: [],
    charts: [],
    queryContract: contract(),
    onShowWork: () => {},
  };

  it("reads as a completion, not a crash, and says it once", () => {
    const { container } = render(<ReportUnderTest {...base} findings={[]} results={{}} />,
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
    render(<ReportUnderTest {...base} findings={[finding()]} results={results} />);
    // A published finding citing a profile rather than the executed
    // contract is not the answer to the question -- but it is the engine's
    // own first published statement, and leading with it in the engine's
    // words beats leading with a sentence about the absence of an answer.
    // The alternative shipped briefly in step E and read "No published
    // finding answered this question directly" above three verified claims.
    expect(screen.getByTestId("direct-answer")).toHaveTextContent(
      /Average annual revenue by region/i,
    );
  });
});

describe("the report anchor the browser suite waits on", () => {
  it("renders the Key findings heading even when nothing was published", () => {
    render(<ReportUnderTest
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
    // The anchor is `report-panel`, and it is present even when nothing
    // was published.
    expect(screen.getByTestId("report-panel")).toBeInTheDocument();
    expect(screen.getByTestId("direct-answer")).toHaveTextContent(
      /no verified finding answered/i,
    );
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
    render(<ReportUnderTest
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
    expect(screen.getByTestId("answer-coverage")).toHaveTextContent("6,435");
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
    expect(screen.getByTestId("answer-coverage")).toHaveTextContent("3,575");
  });
});
