import { type Page } from "@playwright/test";

import { expect, reportFor, test } from "./fixtures";

import { canvasTestId, openApp, recordingButtons, setTheme, waitForReport } from "./helpers";

/**
 * The answer-first report, measured.
 *
 * Each test here corresponds to a defect that was found by measuring
 * something a screenshot showed as fine, and each one stays narrow enough
 * to name the defect it guards.
 */

async function wideReport(page: Page) {
  // The `wide` session: 36 categories over 400 rows. Every test here asks
  // the same question of it, so one upload per engine serves them all.
  await reportFor(page, "What is the total revenue by category?");
}

/**
 * Wait for Vega to have drawn, not merely for the report to exist.
 *
 * `waitForReport` resolves on the report panel. The plot is rendered
 * asynchronously after that, and measuring without waiting made the
 * chart-width test read "no chart was measured" on Firefox while passing on
 * Chromium, which simply happened to be quicker.
 */
async function waitForPlot(page: Page) {
  await page.waitForSelector(".chart-host svg", { timeout: 30_000 });
  await expect
    .poll(async () =>
      page.evaluate(() => {
        const plot = document.querySelector(".chart-host svg");
        return plot ? Math.round(plot.getBoundingClientRect().width) : 0;
      }),
    )
    .toBeGreaterThan(0);
}

/** Every `--series-*` value for the theme currently applied. */
async function seriesRamp(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    return [1, 2, 3, 4, 5]
      .map((n) => style.getPropertyValue(`--series-${n}`).trim().toLowerCase())
      .filter(Boolean);
  });
}

/** `#rrggbb` for an `rgb(...)` string, so it can be compared to a token. */
function toHex(rgb: string): string {
  const parts = rgb.match(/\d+/g);
  if (!parts || parts.length < 3) return rgb;
  return (
    "#" +
    parts
      .slice(0, 3)
      .map((n) => Number(n).toString(16).padStart(2, "0"))
      .join("")
  );
}

test.describe("the chart draws in the measured palette", () => {
  for (const theme of ["light", "dark"] as const) {
    test(`a single-series chart uses a --series token in ${theme}`, async ({
      wide: page,
    }) => {
      /*
       * The defect: `config.range.category` only applies where a *colour
       * encoding* exists. A single-series bar or line has none, so every
       * such chart was drawn in Vega's own default `#4c78a8` -- a blue from
       * neither palette, identical in both themes, and never measured
       * against the plot surface. The token range was right and was simply
       * never reached.
       */
      await wideReport(page);
      // The toggle, not `setAttribute`: writing the attribute directly
      // leaves React's own theme state saying "light" while the document
      // says "dark", and on a shared session the next reset then waits
      // forever for a control that reads the other way round.
      await setTheme(page, theme);
      // The chart re-renders from tokens on a theme change.
      await waitForPlot(page);

      const fills = await page.evaluate(() =>
        [...document.querySelectorAll(".chart-host svg path")]
          .map((node) => getComputedStyle(node).fill)
          .filter((fill) => fill && fill !== "none" && !fill.includes("0, 0, 0, 0")),
      );
      expect(fills.length, "no filled marks were drawn").toBeGreaterThan(0);

      const ramp = await seriesRamp(page);
      expect(ramp.length).toBe(5);

      const used = [...new Set(fills.map(toHex))];
      for (const fill of used) {
        expect(
          ramp,
          `${fill} is not in the measured series ramp (${ramp.join(", ")})`,
        ).toContain(fill);
      }
      // And explicitly not Vega's default.
      expect(used).not.toContain("#4c78a8");
    });
  }
});

test.describe("a multi-series chart draws in the measured palette", () => {
  for (const theme of ["light", "dark"] as const) {
    test(`every series comes from the ramp in ${theme}`, async ({ twoSeries: page }) => {
      /*
       * A single-series chart exercises `config.mark.color`; this exercises
       * `config.range.category`, which is a different code path and the only
       * one the original token work covered. Both had to be checked: the
       * first was broken precisely because the second looked right.
       */
      await reportFor(page, "What is the total revenue by region and channel?");
      await setTheme(page, theme);
      await waitForPlot(page);

      const fills = await page.evaluate(() =>
        [...document.querySelectorAll(".chart-host svg path")]
          .map((node) => getComputedStyle(node).fill)
          .filter((fill) => fill && fill !== "none" && !fill.includes("0, 0, 0, 0")),
      );
      const used = [...new Set(fills.map(toHex))];
      expect(
        used.length,
        "this is not a multi-series chart; the test asserts nothing",
      ).toBeGreaterThan(1);

      const ramp = await seriesRamp(page);
      for (const fill of used) {
        expect(
          ramp,
          `${fill} is not in the measured series ramp (${ramp.join(", ")})`,
        ).toContain(fill);
      }
      // Distinct series must be distinct colours: the ramp climbs a
      // contrast ladder so a reader who cannot separate the hues still has
      // lightness, and reusing one entry twice throws that away.
      expect(new Set(used).size).toBe(used.length);
    });
  }
});

test.describe("the report keeps the properties the old suite proved", () => {
  test("the table scrolls inside its own frame, not the page", async ({
    wide: page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await wideReport(page);

    const pageOverflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    );
    expect(pageOverflow, "the page scrolls sideways").toBeLessThanOrEqual(1);

    const wrap = page.locator(".table-wrap").first();
    await expect(wrap).toBeVisible();
    expect(
      await wrap.evaluate((node) => getComputedStyle(node).overflowX),
    ).toBe("auto");
  });

  test("the chart fills the column it is given, at 36 groups", async ({
    wide: page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await wideReport(page);
    await waitForPlot(page);

    const fraction = await page.evaluate(() => {
      const host = document.querySelector(".chart-host");
      const plot = host?.querySelector("svg, canvas");
      if (!host || !plot) return null;
      const h = host.getBoundingClientRect();
      const p = plot.getBoundingClientRect();
      return h.width === 0 ? null : p.width / h.width;
    });
    expect(fraction, "no chart was measured").not.toBeNull();
    // The defect this replaced: a chart's width was decided by how many
    // categories it had, so a two-category plot was a narrow strip in a
    // wide empty card.
    expect(fraction!).toBeGreaterThan(0.9);
  });
});

test.describe("the answer leads, at phone widths", () => {
  for (const width of [360, 390]) {
    test(`the answer and its context are above the first break at ${width}`, async ({
      wide: page,
    }) => {
      await page.setViewportSize({ width, height: 740 });
      await wideReport(page);

      const viewportHeight = 740;
      const answer = await page.getByTestId("direct-answer").boundingBox();
      const context = await page.getByTestId("answer-coverage").boundingBox();
      expect(answer).not.toBeNull();
      expect(context).not.toBeNull();

      // "Above the first viewport break" is the acceptance criterion the
      // brief replaced a height ceiling with: a height ceiling rewards
      // truncating real content, which is the opposite of the point.
      expect(
        answer!.y + answer!.height,
        "the answer is below the fold",
      ).toBeLessThan(viewportHeight);
      expect(
        context!.y + context!.height,
        "the context line is below the fold",
      ).toBeLessThan(viewportHeight);
    });
  }
});

test.describe("the report canvas is only what the brief allows", () => {
  test("the run timeline is not resident once the report exists", async ({
    wide: page,
  }) => {
    await wideReport(page);
    // It narrates a run in flight. Once the report exists the answer is the
    // thing on screen, and the timeline is in the evidence drawer.
    await expect(canvasTestId(page, "run-timeline")).toHaveCount(0);

    await page.getByTestId("show-work").click();
    await expect(
      page.getByTestId("evidence-drawer").getByTestId("run-timeline"),
    ).toBeVisible();
  });

  test("the drawer's timeline reports the run's own record, not the stream", async ({
    wide: page,
  }) => {
    /*
     * The live stream is what arrived; `run.events` is the engine's
     * complete record. On a finished run they were not the same -- a
     * streamed `contract_resolved` that never landed left the plan stage
     * reading "active" underneath a published report.
     */
    await wideReport(page);
    await page.getByTestId("show-work").click();

    const drawer = page.getByTestId("evidence-drawer");
    await expect(drawer.getByTestId("run-timeline")).toBeVisible();
    expect(
      await drawer.locator('.timeline-stage[data-state="active"]').count(),
      "a stage is still active under a finished report",
    ).toBe(0);
  });
});

test.describe("a recorded report on a phone", () => {
  test("shows every highlight it has, and the fold is not reached", async ({
    page,
  }) => {
    /*
     * This test used to assert the fold itself, against a recording that
     * published six full-sentence findings at 390px.
     *
     * It could only ever do that because the recording carried **no
     * presentation snapshot**, so the report fell back to the engine's own
     * finding prose -- which is the defect `presentation/fields.py` and
     * `recordings/record.py` were corrected for. With the snapshot carried,
     * a recorded run renders the way a live one does: a headline, at most
     * two highlights, and the table.
     *
     * The presentation layer emits two highlights for every shape it
     * builds -- highest and lowest -- and the fold's threshold was three,
     * so the branch never ran in product output. It has been **removed**
     * rather than kept for a constructed test model, which would have left
     * dead markup and a disclosure a keyboard user could reach.
     * `src/test/highlightsAreNotFolded.test.tsx` asserts the cap and the
     * absence of the control over every committed payload.
     *
     * So this asserts what the phone report actually does, which is the
     * thing that was never checked: every highlight is present, none is
     * hidden behind a disclosure, and the table is still reachable below
     * them.
     */
    await page.setViewportSize({ width: 390, height: 844 });
    await openApp(page);
    await recordingButtons(page).first().click();
    await waitForReport(page);

    const highlights = page.locator(".finding-item");
    const total = await highlights.count();
    expect(
      total,
      "a recorded report published no highlights at all",
    ).toBeGreaterThan(0);

    // Nothing folded, and nothing lost: the count on screen is the count
    // in the report.
    await expect(page.locator(".findings-more")).toHaveCount(0);
    await expect(
      page.locator(".findings > .finding-list > .finding-item"),
    ).toHaveCount(total);
    for (const highlight of await highlights.all()) {
      await expect(highlight).toBeVisible();
    }

    // The table is below the answer, not pushed off the end of it.
    await expect(page.getByTestId("result-panel")).toBeVisible();
  });

  test("renders the period column as months, not as stored instants", async ({
    page,
  }) => {
    /*
     * The parity defect, at the surface it was visible on.
     *
     * The headline resolved a period through its grain and said
     * "Oct 2025"; the table beside it formatted the same cell with no
     * field metadata and said "2025-01-01T00:00:00". Both are in this
     * report, so one test can hold them to each other.
     */
    await page.setViewportSize({ width: 390, height: 844 });
    await openApp(page);
    await recordingButtons(page).first().click();
    await waitForReport(page);

    const table = await page.getByTestId("result-panel").innerText();
    expect(table, "the result table published a stored instant").not.toMatch(
      /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/,
    );
  });

  test("and shows all of them on a desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openApp(page);
    await recordingButtons(page).first().click();
    await waitForReport(page);
    await expect(page.locator(".findings-more")).toHaveCount(0);
  });
});
