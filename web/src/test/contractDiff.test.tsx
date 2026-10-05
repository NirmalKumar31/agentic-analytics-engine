/**
 * Compare Both could say *whether* two interpretations agreed. This pins
 * that it also says *where* they differ, because a reader told only
 * "different governed interpretations" cannot tell a swapped measure from
 * a dropped row restriction, and those are very different things.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CompareWorkspace } from "../components/CompareWorkspace";
import { COMPARED_FIELD_COUNT } from "../lib/contractDiff";
import { canonicalOf, contractDifferences } from "../lib/contractDiff";
import type {
  CanonicalContract,
  QueryContract,
  RunPayload,
} from "../lib/types";

const canonical: CanonicalContract = {
  operation: "average",
  table: "uploaded_data",
  measure: "annual_revenue",
  dimension: "region",
  dimensions: ["region"],
  time_field: null,
  period: null,
  period_field: null,
  filters: [
    { column: "age", operator: ">=", value: 30 },
    { column: "age", operator: "<=", value: 40 },
  ],
  ascending: false,
};

const contract = (over: Partial<QueryContract> = {}): QueryContract => ({
  ...canonical,
  confident: true,
  explanation: "validated",
  interpretation: "rule-based",
  contract_hash: "hash-a",
  canonical_contract: canonical,
  ...over,
});

const withCanonical = (
  over: Partial<CanonicalContract>,
  hash = "hash-b",
): QueryContract =>
  contract({
    ...over,
    contract_hash: hash,
    canonical_contract: { ...canonical, ...over },
  });

describe("contractDifferences", () => {
  it("reports nothing for two identical canonical contracts", () => {
    expect(contractDifferences(contract(), contract())).toEqual([]);
  });

  it("ignores who read the wording", () => {
    const ai = contract({
      interpretation: "ai-grounded",
      explanation: "AI plan",
    });
    expect(contractDifferences(contract(), ai)).toEqual([]);
  });

  it("names a swapped measure", () => {
    const diff = contractDifferences(
      contract(),
      withCanonical({ measure: "headcount" }),
    );
    expect(diff).toEqual([
      { label: "Measure", deterministic: "annual_revenue", ai: "headcount" },
    ]);
  });

  it("names a dropped row restriction and says what is missing", () => {
    const [only, ...rest] = contractDifferences(
      contract(),
      withCanonical({ filters: [] }),
    );
    expect(rest).toEqual([]);
    expect(only?.label).toBe("Row filters");
    expect(only?.deterministic).toContain("age >= 30");
    expect(only?.deterministic).toContain("age <= 40");
    expect(only?.ai).toBe("none");
  });

  it("does not report a difference for filters stated in another order", () => {
    const reordered = withCanonical({
      filters: [...canonical.filters].reverse(),
    });
    expect(contractDifferences(contract(), reordered)).toEqual([]);
  });

  it("names an added time period", () => {
    const diff = contractDifferences(
      contract(),
      withCanonical({
        period: ["2024-01-01", "2024-12-31"],
        period_field: "signup_date",
      }),
    );
    expect(diff.map((d) => d.label)).toEqual(["Time period", "Period column"]);
    expect(diff.at(0)).toEqual({
      label: "Time period",
      deterministic: "none",
      ai: "2024-01-01 to 2024-12-31",
    });
  });

  it("names a reversed sort order", () => {
    const diff = contractDifferences(
      contract(),
      withCanonical({ ascending: true }),
    );
    expect(diff).toEqual([
      { label: "Sort order", deterministic: "descending", ai: "ascending" },
    ]);
  });

  it("reports nothing when either side has no contract", () => {
    expect(contractDifferences(contract(), null)).toEqual([]);
    expect(contractDifferences(null, contract())).toEqual([]);
  });

  it("falls back to the full contract when no canonical block is present", () => {
    const older = contract({ canonical_contract: undefined });
    expect(canonicalOf(older)?.measure).toBe("annual_revenue");
    expect(contractDifferences(older, older)).toEqual([]);
  });
});

describe("CompareWorkspace contract diff", () => {
  const side = (run: RunPayload | null) => ({
    title: "T",
    subtitle: "S",
    run,
    error: null,
    pending: false,
    children: null,
  });
  const runWith = (queryContract: QueryContract): RunPayload =>
    ({ query_contract: queryContract }) as RunPayload;

  it("shows the differing rows when the two panes disagree", () => {
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side(runWith(contract()))}
        ai={side(runWith(withCanonical({ dimensions: ["store_id"] })))}
      />,
    );
    const table = screen.getByTestId("contract-diff");
    expect(table).toHaveTextContent("Grouping");
    expect(table).toHaveTextContent("region");
    expect(table).toHaveTextContent("store_id");
    expect(table).not.toHaveTextContent("Measure");
  });

  it("captions the diff with the table's own counts", () => {
    /*
     * Requirement 17: the caption's agree/differ counts must equal the
     * counts in the table it labels. The failure it guards against is a
     * caption written beside a table rather than derived from it -- "two
     * fields differ" over three rows, which a reader has no way to
     * resolve and will usually believe.
     *
     * So the assertion reads both numbers out of the caption and compares
     * them with the rendered rows, rather than against a literal.
     */
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side(runWith(contract()))}
        ai={side(
          runWith(
            withCanonical({ dimensions: ["store_id"], operation: "avg" }),
          ),
        )}
      />,
    );

    const table = screen.getByTestId("contract-diff");
    const rows = table.querySelectorAll("tbody tr").length;
    expect(rows, "the scenario produces no differing rows").toBeGreaterThan(1);

    const caption = (
      screen.getByTestId("contract-diff-caption").textContent ?? ""
    ).replace(/\s+/g, " ");
    const [differing, compared] = [...caption.matchAll(/\d+/g)].map((m) =>
      Number(m[0]),
    );
    expect(differing, `caption says "${caption}"`).toBe(rows);
    expect(compared, "the caption's total is not the number compared").toBe(
      COMPARED_FIELD_COUNT,
    );
    expect(compared).toBeGreaterThan(rows);
  });

  it("says 'field differs' for one, 'fields differ' for several", () => {
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side(runWith(contract()))}
        ai={side(runWith(withCanonical({ dimensions: ["store_id"] })))}
      />,
    );
    expect(screen.getByTestId("contract-diff-caption")).toHaveTextContent(
      /1 of \d+ compared contract field differs/,
    );
  });

  it("shows no diff table when the panes agree", () => {
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side(runWith(contract()))}
        ai={side(runWith(contract({ interpretation: "ai-grounded" })))}
      />,
    );
    expect(screen.queryByTestId("contract-diff")).toBeNull();
    // The verdict, not its wording: `data-verdict` is the stable signal
    // and the prose is written for a reader.
    expect(screen.getByTestId("contract-comparison")).toHaveAttribute(
      "data-verdict",
      "both_agree",
    );
  });

  it("reads identical canonical fields as agreement, whatever the hash says", () => {
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side(runWith(contract()))}
        ai={side(runWith(contract({ contract_hash: "hash-z" })))}
      />,
    );
    // Equality is decided on the canonical fields now, not the hash. The
    // hash is derived from those fields, so a differing hash over
    // identical fields cannot arise from the engine -- and treating it as
    // a divergence told the reader two identical interpretations differed.
    expect(screen.queryByTestId("contract-diff")).toBeNull();
    expect(screen.getByTestId("contract-comparison")).toHaveAttribute(
      "data-verdict",
      "both_agree",
    );
  });
});

describe("groupings and grain in the diff", () => {
  it("names a dropped second cut", () => {
    // The shape that made two-dimensional questions unrepresentable: one
    // pane grouped by two columns, the other by one, and comparing the
    // singular projection showed them as identical.
    const diff = contractDifferences(
      contract({
        dimensions: ["region", "business_type"],
        canonical_contract: {
          ...canonical,
          dimensions: ["region", "business_type"],
        },
      }),
      withCanonical({ dimensions: ["region"] }),
    );

    expect(diff).toHaveLength(1);
    expect(diff[0]?.label).toBe("Grouping");
    expect(diff[0]?.deterministic).toBe("region then business_type");
    expect(diff[0]?.ai).toBe("region");
  });

  it("treats a reordered grouping as a difference", () => {
    // "by region then channel" is not "by channel then region": the
    // result's row order and its chart differ.
    const diff = contractDifferences(
      contract({
        dimensions: ["region", "business_type"],
        canonical_contract: {
          ...canonical,
          dimensions: ["region", "business_type"],
        },
      }),
      withCanonical({ dimensions: ["business_type", "region"] }),
    );
    expect(diff.map((d) => d.label)).toEqual(["Grouping"]);
  });

  it("names a differing time grain", () => {
    const diff = contractDifferences(
      contract(),
      withCanonical({ time_grain: "month" }),
    );
    expect(diff).toEqual([
      { label: "Time grain", deterministic: "none", ai: "month" },
    ]);
  });
});
