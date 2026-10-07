/**
 * The site suggested "Which team_size contributes most to age?".
 *
 * It is reproducible arithmetic and not a question anyone wants answered.
 * It happened because the builder took `measures[0]` and proposed totalling
 * it, and on that dataset the only classified measure was `age`, since the
 * real measure is near-unique and reads as an identifier.
 */

import { describe, expect, it } from "vitest";
import { suggestions } from "../components/DatasetSummary";
import type { DatasetSummary, InferredField } from "../lib/types";

const field = (
  name: string,
  role: InferredField["role"],
  additive: InferredField["additive"] = "unknown",
): InferredField =>
  ({
    name,
    data_type: role === "measure" ? "DOUBLE" : "VARCHAR",
    role,
    null_pct: 0,
    distinct_count: 10,
    reason: "",
    additive,
    min_value: null,
    max_value: null,
  }) as InferredField;

const summary = (over: Partial<DatasetSummary> = {}): DatasetSummary =>
  ({
    table: "uploaded_data",
    row_count: 1200,
    status: "inferred",
    fields: [],
    time_fields: [],
    dimensions: [],
    measures: [],
    identifiers: [],
    ...over,
  }) as DatasetSummary;

describe("suggestions", () => {
  it("never proposes summing a column whose sum is meaningless", () => {
    // The production dataset: `age` is the only classified measure.
    const data = summary({
      measures: ["age"],
      dimensions: ["team_size"],
      fields: [
        field("age", "measure", "weak"),
        field("team_size", "dimension"),
      ],
    });

    const questions = suggestions(data);

    expect(questions.join(" ")).not.toMatch(/total age/i);
    expect(questions.join(" ")).not.toMatch(/contributes most/i);
    // An average of ages is a real question, so it is offered instead.
    expect(questions).toContain("What is the average age by team_size?");
  });

  it("proposes totals for a column that reads as a quantity", () => {
    const data = summary({
      measures: ["annual_revenue"],
      dimensions: ["region"],
      time_fields: ["signup_date"],
      fields: [
        field("annual_revenue", "measure", "strong"),
        field("region", "dimension"),
        field("signup_date", "time"),
      ],
    });

    const questions = suggestions(data);
    expect(questions).toContain("What is total annual_revenue by region?");
    expect(questions.join(" ")).toMatch(/highest total annual_revenue/);
  });

  it("never names a generated column", () => {
    const data = summary({
      measures: ["noise_metric", "annual_revenue"],
      dimensions: ["noise_group", "region"],
      fields: [
        field("noise_metric", "measure", "strong"),
        field("annual_revenue", "measure", "strong"),
        field("noise_group", "dimension"),
        field("region", "dimension"),
      ],
    });

    const joined = suggestions(data).join(" ");
    expect(joined).not.toMatch(/noise_/);
    expect(joined).toMatch(/annual_revenue/);
  });

  it("falls back to counting when nothing is safe to total", () => {
    const data = summary({
      measures: [],
      dimensions: ["region"],
      fields: [field("region", "dimension")],
    });
    expect(suggestions(data)).toEqual(["How many rows by region?"]);
  });

  it("offers nothing rather than something meaningless", () => {
    expect(suggestions(summary())).toEqual([]);
  });
});
