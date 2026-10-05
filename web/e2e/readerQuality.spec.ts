import { describeDefects, readerDefects } from "../src/lib/readerQuality";
import { expect, freshComposer, reportFor, test } from "./fixtures";
import { onCanvas, setTheme } from "./helpers";

/**
 * The report canvas, read as a reader reads it.
 *
 * A live run published this as the first thing on the page:
 *
 *   "a sustained month-over-month increase from 9.170305676855895 in
 *    2024-01-01T00:00:00 to 10.763569457221712 in 2024-04-01T00:00:00"
 *
 * Every number correct. A binary floating-point tail, two stored
 * instants, and `return_rate` in the prose beside them -- three
 * serialisation details standing where a number, a date and a name belong.
 * Nothing failed, because nothing was looking.
 *
 * `src/test/readerQuality.test.ts` pins the rules against fixtures. This
 * runs them over the text the browser actually paints, which is the only
 * place the whole pipeline -- engine, presentation, component, stylesheet
 * -- is assembled.
 *
 * Scoped to primary surfaces. The evidence drawer and the print appendix
 * carry `result_id`s, tool names and hashes on purpose: that is what makes
 * a run auditable, and a reader who opened them asked for them.
 */

/*
 * The shipped rules, imported rather than restated.
 *
 * `readerQuality.ts` is pure string work with no browser dependency, so
 * the spec can use the same module the application does. A second copy of
 * a regular expression is a second thing to keep in step, and the one that
 * drifts is the one in the test nobody reads.
 */

/**
 * The prose of one surface, with the audit trail and the identifiers out.
 *
 * Taken from a clone so nothing is removed from the page a reader sees;
 * this only decides what counts as prose for the rules below.
 */
async function proseOf(
  page: import("@playwright/test").Page,
  selector: string,
): Promise<string> {
  return page.evaluate((target) => {
    const canvas = document.querySelector(target);
    if (!canvas) return "";
    const clone = canvas.cloneNode(true) as HTMLElement;
    // The drawer and the print appendix are audit surfaces and may name
    // anything. Removed from the copy, not from the page.
    for (const audit of clone.querySelectorAll(
      '[data-print-appendix], [data-testid="evidence-drawer"], .evidence-row, .sr-only, .activity-row',
    )) {
      audit.remove();
    }
    /*
     * And anything the interface marks as an identifier.
     *
     * `.mono` is the product's own signal that a string is a name rather
     * than prose -- the dataset strip names the clock column as
     * `order_date` on purpose, because that is what a reader would type
     * into a question and what the schema inspector shows. The rule this
     * spec enforces is that *prose* must not contain engine identifiers,
     * not that identifiers may never be shown; a report that hid the name
     * of the column its dates come from would be less useful and no more
     * honest.
     */
    for (const identifier of clone.querySelectorAll(".mono")) {
      identifier.remove();
    }
    return clone.textContent ?? "";
  }, selector);
}

function assertFitForAReader(where: string, text: string): void {
  expect(text.trim().length, `${where} rendered nothing to check`).toBeGreaterThan(0);
  /*
   * Planner vocabulary and punctuation are excluded here, not because they
   * are acceptable but because the canvas legitimately contains prose the
   * engine wrote ("Aggregate" no longer appears, but a finding may say
   * "snapshot" of a dataset). The four rules asserted are the ones with no
   * honest reading on a primary surface: a float tail, a stored timestamp,
   * an engine identifier, and a value that never arrived.
   */
  const defects = readerDefects(text).filter(
    (found) =>
      found.defect === "excess_precision" ||
      found.defect === "iso_timestamp" ||
      found.defect === "snake_case" ||
      found.defect === "missing_value",
  );
  expect(defects, describeDefects(where, defects)).toEqual([]);
}

test.describe("the report canvas is fit for a reader", () => {
  test("a completed report names no identifiers and no stored values", async ({
    profiled: page,
  }) => {
    await reportFor(page, "What is the total revenue by region?");
    assertFitForAReader("the report", await proseOf(page, '[data-testid="report-panel"]'));
  });

  test("and the headline specifically, which is read first and largest", async ({
    profiled: page,
  }) => {
    await reportFor(page, "What is the total revenue by region?");
    const headline = (await onCanvas(page, '[data-testid="direct-answer"]').textContent()) ?? "";
    assertFitForAReader("the headline", headline);
  });

  test("a governed metric is named in prose, not by its column", async ({
    demo: page,
  }) => {
    /*
     * The surface the published defect was on, and the one the uploaded
     * sample cannot reproduce.
     *
     * `profiled`'s columns are single words -- `revenue`, `region` -- so a
     * finding about them contains no identifier and this gate passes over
     * the fallback path without noticing. The governed warehouse has a
     * metric called `return_rate` and a dimension called
     * `customer_segment`, and the engine's own finding prose names both:
     * "Across customer_segment, new has the highest return_rate at
     * 10.97%". That is the sentence a reader was shown.
     *
     * With the presentation built, the headline is "Return rate is highest
     * for new, at 10.97%" -- the metric as a name, the unit from the
     * registry. Remove the presentation and this test fails, which is the
     * point of it.
     */
    await reportFor(
      page,
      "Which customer segment has the highest return rate, and are the " +
        "differences statistically significant?",
    );
    const headline =
      (await onCanvas(page, '[data-testid="direct-answer"]').textContent()) ?? "";
    assertFitForAReader("the governed-metric headline", headline);
    // And it says what it is: a rate, with its unit, about the group asked
    // about. An assertion that only forbade things would pass over an
    // empty headline.
    expect(headline).toMatch(/return rate/i);
    expect(headline).toMatch(/%/);
  });

  test("a trend names its periods as months, not as stored instants", async ({
    profiled: page,
  }) => {
    /*
     * The surface the published defect appeared on. A trend headline reads
     * the period straight out of the result, and the engine stores a
     * monthly bucket as its first midnight -- so the answer said "peaked
     * in 2025-12-01T00:00:00", which is a serialisation format shown to a
     * reader.
     *
     * Kept as its own case because no other question reaches the
     * time-series headline, and a gate that never visits the surface it
     * guards is a gate nobody notices has stopped working.
     */
    await reportFor(page, "Show the monthly trend of revenue");
    const headline =
      (await onCanvas(page, '[data-testid="direct-answer"]').textContent()) ?? "";
    assertFitForAReader("the trend headline", headline);
    // And it does name a month, so the assertion above is not passing over
    // a headline that mentions no period at all.
    expect(headline).toMatch(/\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}\b/);
  });

  test("a refusal states a number no reader has to decode", async ({ profiled: page }) => {
    /*
     * The same refusal `accessibility.spec.ts` drives, so the job admits
     * it once and this replays the capture.
     *
     * `snake_case` is not asserted here, and the reason is the question: a
     * refusal quotes the words the reader used, and this reader asked
     * about `gross_margin`. Objecting that the answer contains
     * `gross_margin` would be objecting to their own typing. What a
     * refusal still may not do is show a float tail, a stored timestamp,
     * or a value that never arrived -- so those are what is checked.
     */
    await reportFor(page, "What is the average gross_margin by region?");
    const headline =
      (await onCanvas(page, '[data-testid="direct-answer"]').textContent()) ?? "";
    expect(headline.trim().length, "the refusal rendered no headline").toBeGreaterThan(0);
    const defects = readerDefects(headline).filter(
      (found) => found.defect !== "snake_case",
    );
    expect(defects, describeDefects("the refusal headline", defects)).toEqual([]);
  });

  test("in dark, where a different stylesheet assembles the same text", async ({
    profiled: page,
  }) => {
    await reportFor(page, "What is the total revenue by region?", { theme: "dark" });
    assertFitForAReader("the report in dark", await proseOf(page, '[data-testid="report-panel"]'));
    await setTheme(page, "light");
  });

  test("and in print, which reuses the same values rather than its own", async ({
    profiled: page,
  }) => {
    await reportFor(page, "What is the total revenue by region?");
    await page.emulateMedia({ media: "print" });
    try {
      const headline =
        (await onCanvas(page, '[data-testid="direct-answer"]').textContent()) ?? "";
      assertFitForAReader("the printed headline", headline);
    } finally {
      await page.emulateMedia({ media: "screen" });
    }
  });

  test("the composer, before anything has run", async ({ profiled: page }) => {
    // A dataset strip that named its columns in snake_case would fail
    // here, and the strip is the first thing a reader sees after upload.
    await freshComposer(page);
    assertFitForAReader(
      "the dataset strip",
      await proseOf(page, '[data-testid="dataset-context"]'),
    );
  });
});
