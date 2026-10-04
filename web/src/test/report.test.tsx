import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReportUnderTest } from "./renderReport";
import type { AnalysisPresentation, QueryContract, ResultSnapshot } from "../lib/types";

const contract: QueryContract = {
  operation: "average",
  table: "uploaded_data",
  measure: "annual_revenue",
  dimension: "region",
  dimensions: ["region"],
  filters: [
    { column: "age", operator: ">=", value: 30 },
    { column: "age", operator: "<=", value: 40 },
  ],
  ascending: false,
  confident: true,
  explanation: "validated",
  interpretation: "ai-grounded",
  contract_hash: "contract",
};

function report() {
  return render(<ReportUnderTest
      question="Average annual revenue for people aged 30 to 40 by region"
      report={null}
      findings={[]}
      rejected={[]}
      charts={[]}
      results={{}}
      queryContract={contract}
      onShowWork={() => {}}
    />,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("ReportView", () => {
  it("renders a supplied presentation contract instead of parsing finding prose", () => {
    const presentation: AnalysisPresentation = {
      schema_version: "1.0",
      shape: "boolean_comparison",
      headline: "Average sleep is 6.33 when the filter is On.",
      secondary_summary: "A descriptive difference of 0.12.",
      highlights: [{
        highlight_id: "on",
        label: "Blue light filter active",
        value: { raw_value: 6.33, formatted_value: "6.33", unit: null },
        comparison_value: { raw_value: 6.21, formatted_value: "6.21", unit: null },
        delta: { raw_value: 0.12, formatted_value: "0.12", unit: null },
        evidence_cells: [{ result_id: "r1", row: 1, column: "average_sleep", value: 6.33, label: null }],
        interpretation_level: "derived",
      }],
      scope: { rows_total: 20, rows_matching: 20, rows_represented: 20, groups_returned: 2, groups_total: 2, complete: true, filters: [], period: null, ordering: "dimension" },
      display_fields: [],
      table: { result_id: "r1", visible_columns: ["flag", "average_sleep"], display_fields: [], default_sort: null, preview_limit: null, complete: true },
      chart: { chart_id: null, result_id: null, kind: "none", title: null, subtitle: null, x_field: null, y_field: null, series_field: null, no_chart_reason: "A chart is not needed for two values.", spec: null },
      caveats: [],
      provenance_refs: [],
      compatibility_derived: false,
    };
    const result = {
      result_id: "r1", tool_name: "aggregate", task_id: null, sql: null,
      columns: ["flag", "average_sleep"], rows: [[0, 6.21], [1, 6.33]], row_count: 2,
      truncated: false, dataset_fingerprint: "x", duration_ms: 1, parameters: {}, warnings: [], statistical_result: null,
    } as ResultSnapshot;
    render(<ReportUnderTest question="q" report={null} findings={[]} rejected={[]} charts={[]} results={{ r1: result }} presentation={presentation} onShowWork={() => {}} />);
    expect(screen.getByTestId("direct-answer")).toHaveTextContent("Average sleep is 6.33 when the filter is On.");
    expect(screen.getByText("A chart is not needed for two values.")).toBeVisible();
    expect(screen.queryByText(/and further groups/i)).toBeNull();
  });

  it("shows the operation, population filters and grouping actually executed", () => {
    report();
    const applied = screen.getByTestId("applied-analysis");
    expect(applied).toHaveTextContent("average");
    expect(applied).toHaveTextContent("annual revenue");
    expect(applied).toHaveTextContent("region");
    expect(applied).toHaveTextContent("age >= 30");
    expect(applied).toHaveTextContent("age <= 40");
  });

  it("describes a zero-finding completion without implying a crash", () => {
    report();
    expect(
      screen.getByText(/no verified finding answered the requested analysis/i),
    ).toBeVisible();
  });

  it("offers the browser print path for saving a PDF", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    report();
    await userEvent.click(
      screen.getByRole("button", { name: /print \/ save pdf/i }),
    );
    expect(print).toHaveBeenCalledOnce();
  });
});
