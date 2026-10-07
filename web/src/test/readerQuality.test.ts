/**
 * The gate that would have caught the PDF.
 *
 * A live run printed, as the answer a reader reads first:
 *
 *   "The new segment shows a sustained month-over-month increase from
 *    9.170305676855895 in 2024-01-01T00:00:00 to 10.763569457221712 in
 *    2024-04-01T00:00:00."
 *
 * Correct numbers. Three separate things no reader should be shown.
 */

import { describe, expect, it } from "vitest";

import { describeDefects, readerDefects } from "../lib/readerQuality";

const THE_LIVE_HEADLINE =
  "The new segment shows a sustained month-over-month increase from " +
  "9.170305676855895 in 2024-01-01T00:00:00 to 10.763569457221712 in " +
  "2024-04-01T00:00:00.";

describe("the reader-facing gate", () => {
  it("catches every defect in the headline that was actually published", () => {
    const defects = readerDefects(THE_LIVE_HEADLINE).map((d) => d.defect);
    expect(defects).toContain("excess_precision");
    expect(defects).toContain("iso_timestamp");
  });

  it("names what it found, so a failure says where to look", () => {
    const message = describeDefects("the headline", readerDefects(THE_LIVE_HEADLINE));
    expect(message).toContain("excess_precision");
    expect(message).toContain("9.170305676855895");
  });

  it("passes the headline the engine now produces", () => {
    expect(
      readerDefects("Return rate is highest for new, at 10.97%, and lowest for vip, at 6.93%."),
    ).toEqual([]);
    expect(
      readerDefects("Revenue peaked at $35,564.98 in Dec 2025 and was lowest at $813.24 in Jan 2024."),
    ).toEqual([]);
  });

  it("catches an engine identifier standing in for a name", () => {
    const found = readerDefects("Across customer_segment, new has the highest return_rate.");
    expect(found.map((f) => f.defect)).toContain("snake_case");
  });

  it("catches a value that never arrived", () => {
    for (const text of ["Revenue is undefined for West.", "Return rate: null", "at NaN%"]) {
      expect(readerDefects(text).map((f) => f.defect)).toContain("missing_value");
    }
  });

  it("catches the planner's vocabulary", () => {
    expect(
      readerDefects("Aggregate revenue by analysis type").map((f) => f.defect),
    ).toContain("planner_vocabulary");
  });

  it("catches a label that ran out before its value", () => {
    expect(readerDefects("Return rate:").map((f) => f.defect)).toContain(
      "malformed_punctuation",
    );
    expect(readerDefects("Revenue rose ,, then fell").map((f) => f.defect)).toContain(
      "malformed_punctuation",
    );
  });

  it("leaves precision a reader needs alone", () => {
    // A p-value and an effect size are precision, not an artefact. A gate
    // that failed these would be turned off within a week.
    expect(readerDefects("p = 2.62e-12 with Cramer's V = 0.039")).toEqual([]);
    expect(readerDefects("a change of 0.71 percentage points")).toEqual([]);
  });

  it("leaves a plain date alone", () => {
    expect(readerDefects("from Oct 2025 to Dec 2025")).toEqual([]);
    expect(readerDefects("on Jan 5, 2024")).toEqual([]);
  });

  it("reports every rule a sentence breaks, not the first", () => {
    const found = readerDefects("return_rate was undefined at 2024-01-01T00:00:00");
    expect(found.length).toBeGreaterThanOrEqual(3);
  });
});

describe("the two forms a filter published", () => {
  it("catches a Python None in prose", () => {
    // The scope line, verbatim, as it shipped.
    const found = readerDefects("Order date None ['2025-01-01', '2025-12-31']");
    expect(found.map((f) => f.defect)).toContain("missing_value");
  });

  it("catches the serialised list beside it", () => {
    const found = readerDefects("Order date None ['2025-01-01', '2025-12-31']");
    expect(found.map((f) => f.defect)).toContain("serialised_collection");
  });

  it("passes the sentence that replaced it", () => {
    expect(readerDefects("Order date: Jan 1 - Dec 31, 2025")).toEqual([]);
  });

  it("leaves the em dash alone, which is the product's own mark", () => {
    expect(readerDefects("Previous period —")).toEqual([]);
  });

  it("does not fire on a prose sentence that happens to contain a bracket", () => {
    // A single bracketed aside is not a serialisation: the rule wants a
    // delimiter inside the brackets, which is what a collection has.
    expect(readerDefects("Revenue rose [see note] in Q3.")).toEqual([]);
  });

  it("catches percentage points written as a percentage", () => {
    // Not a reader-quality rule. This is here to record that it is not
    // one. The two readings differ by two orders of magnitude and no
    // pattern over the rendered string can tell them apart; the unit has
    // to be right at the presentation layer, which is where
    // `test_presentation_semantics.py` holds it.
    expect(readerDefects("Difference from the highest -2.30%")).toEqual([]);
  });
});
