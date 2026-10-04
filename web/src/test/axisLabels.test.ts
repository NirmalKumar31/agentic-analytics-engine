/**
 * The angle is a function of the labels, not of which code path drew them.
 */

import { describe, expect, it } from "vitest";

import { ANGLED, FLAT, categoryLabelAngle } from "../lib/axisLabels";

function spec(labels: string[], over: Record<string, unknown> = {}) {
  return {
    mark: "bar",
    data: { values: labels.map((region) => ({ region, revenue: 1 })) },
    encoding: {
      x: { field: "region", type: "nominal" },
      y: { field: "revenue", type: "quantitative" },
    },
    ...over,
  };
}

describe("category labels", () => {
  it("lie flat when there are few and they are short", () => {
    expect(categoryLabelAngle(spec(["East", "North", "South", "West"]))).toBe(FLAT);
  });

  it("angle when there are many", () => {
    const many = Array.from({ length: 33 }, (_, i) => `g${i}`);
    expect(categoryLabelAngle(spec(many))).toBe(ANGLED);
  });

  it("angle when there are few but they are long", () => {
    // Four labels, but "Home & Kitchen Appliances" flat would overlap its
    // neighbours and Vega resolves that by dropping one.
    expect(
      categoryLabelAngle(
        spec([
          "Home & Kitchen Appliances",
          "Sports & Outdoors",
          "Toys & Games",
          "Beauty",
        ]),
      ),
    ).toBe(ANGLED);
  });

  it("counts distinct labels, not rows", () => {
    // A grouped bar repeats each category once per series. Counting rows
    // would angle a four-category chart because it has twelve bars.
    const repeated = ["East", "North", "South", "West"].flatMap((r) => [r, r, r]);
    expect(categoryLabelAngle(spec(repeated))).toBe(FLAT);
  });

  it("leaves a specification that already chose alone", () => {
    expect(
      categoryLabelAngle(
        spec(["East", "West"], {
          encoding: {
            x: { field: "region", type: "nominal", axis: { labelAngle: -45 } },
            y: { field: "revenue", type: "quantitative" },
          },
        }),
      ),
    ).toBeNull();
  });

  it("leaves a quantitative or temporal axis alone", () => {
    for (const type of ["quantitative", "temporal"]) {
      expect(
        categoryLabelAngle(
          spec(["a"], {
            encoding: {
              x: { field: "region", type },
              y: { field: "revenue", type: "quantitative" },
            },
          }),
        ),
        `${type} x axis`,
      ).toBeNull();
    }
  });

  it("decides nothing without rows to measure", () => {
    expect(categoryLabelAngle(spec([]))).toBeNull();
    expect(categoryLabelAngle(null)).toBeNull();
    expect(categoryLabelAngle({ mark: "bar" })).toBeNull();
  });
});
