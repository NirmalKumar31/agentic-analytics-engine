/**
 * The three numbers a reader could not previously tell apart: groups the
 * analysis covered, rows the table is showing, and rows behind the groups.
 * The table said "25 rows · showing first 12" while the answer said "every
 * row in the dataset", and a UI preview was indistinguishable from an
 * analytical truncation.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ResultPanel, scopeSentence } from "../components/ResultPanel";
import { csvFilename, resultToCsv } from "../lib/resultCsv";
import type { GroupCoverage, ResultSnapshot } from "../lib/types";

const coverage = (over: Partial<GroupCoverage> = {}): GroupCoverage => ({
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
});

const snapshot = (
  rows: number,
  over: Partial<ResultSnapshot> = {},
): ResultSnapshot =>
  ({
    result_id: "res_1",
    tool_name: "aggregate_for_question",
    task_id: null,
    sql: "select 1",
    columns: ["store", "total_sales", "row_count"],
    rows: Array.from({ length: rows }, (_, index) => [
      `s${index}`,
      1000 - index,
      143,
    ]),
    row_count: rows,
    truncated: false,
    dataset_fingerprint: "sha256:x",
    duration_ms: 1,
    parameters: {},
    warnings: [],
    statistical_result: null,
    group_coverage: coverage({ groups_returned: rows, groups_total: rows }),
    ...over,
  }) as ResultSnapshot;

describe("scopeSentence", () => {
  it("separates the preview from the analysis", () => {
    const text = scopeSentence(snapshot(45), 12);
    expect(text).toContain("Showing 12 of 45 result rows");
    expect(text).toContain("covers all 45 groups");
    expect(text).toContain("6,435 of 6,435 matching rows");
  });

  it("says a partial analysis is partial, whatever the preview shows", () => {
    const text = scopeSentence(
      snapshot(25, {
        group_coverage: coverage({
          complete: false,
          groups_returned: 25,
          groups_total: 45,
          rows_represented: 3575,
        }),
      }),
      25,
    );
    expect(text).toContain("Showing all 25 result rows");
    expect(text).toContain("covers 25 of 45 groups");
    expect(text).toContain("3,575 of 6,435 matching rows");
    expect(text).not.toMatch(/all 45 groups/);
  });
});

describe("ResultPanel", () => {
  it("previews rows without hiding how many the analysis holds", () => {
    render(<ResultPanel snapshot={snapshot(40)} question="q" />);
    expect(screen.getAllByRole("row")).toHaveLength(13); // header + 12
    expect(screen.getByTestId("result-scope")).toHaveTextContent(
      "Showing 12 of 40 result rows",
    );
    expect(
      screen.getByRole("button", { name: /show all 40 rows/i }),
    ).toBeVisible();
  });

  it("shows every row on request", async () => {
    render(<ResultPanel snapshot={snapshot(40)} question="q" />);
    await userEvent.click(screen.getByRole("button", { name: /show all/i }));
    expect(screen.getAllByRole("row")).toHaveLength(41);
    expect(screen.queryByRole("button", { name: /show all/i })).toBeNull();
  });

  it("sorts a complete result, because reordering it changes nothing", async () => {
    render(<ResultPanel snapshot={snapshot(5)} question="q" />);
    await userEvent.click(
      screen.getByRole("button", { name: /sort by total_sales/i }),
    );
    const first = screen.getAllByRole("row")[1]!;
    expect(first).toHaveTextContent("996");
  });

  it("does not offer sorting on a partial result", () => {
    // Sorting a visible subset would imply a ranking the result cannot
    // support: the missing groups might all belong at the top.
    render(
      <ResultPanel
        snapshot={snapshot(25, {
          group_coverage: coverage({
            complete: false,
            groups_returned: 25,
            groups_total: 45,
          }),
        })}
        question="q"
      />,
    );
    expect(screen.queryByRole("button", { name: /sort by/i })).toBeNull();
  });

  it("downloads the analytical result, not the preview", async () => {
    const created: string[] = [];
    const url = {
      createObjectURL: vi.fn(() => "blob:x"),
      revokeObjectURL: vi.fn(),
    };
    vi.stubGlobal("URL", { ...URL, ...url });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        created.push(this.download);
      });

    render(
      <ResultPanel snapshot={snapshot(40)} question="Total sales by store?" />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: /download csv/i }),
    );

    expect(url.createObjectURL).toHaveBeenCalledTimes(1);
    expect(created).toEqual(["total-sales-by-store.csv"]);
    click.mockRestore();
    vi.unstubAllGlobals();
  });
});

describe("resultToCsv", () => {
  it("exports every analytical row, not the previewed subset", () => {
    const csv = resultToCsv(snapshot(40));
    expect(csv.split("\n")).toHaveLength(41);
    expect(csv.split("\n")[0]).toBe("store,total_sales,row_count");
  });

  it("quotes a value containing a comma or a quote", () => {
    const csv = resultToCsv(
      snapshot(1, {
        rows: [["a,b", 'say "hi"', 1]],
      } as Partial<ResultSnapshot>),
    );
    expect(csv).toContain('"a,b"');
    expect(csv).toContain('"say ""hi"""');
  });

  it("builds a filename a reader can find again", () => {
    expect(csvFilename("What is the total Weekly_Sales by Store?")).toBe(
      "what-is-the-total-weekly-sales-by-store.csv",
    );
    expect(csvFilename("???")).toBe("analysis.csv");
  });
});
