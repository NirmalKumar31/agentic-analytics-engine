import { expect, test, type Page } from "@playwright/test";

import { ask, canvasTestId, openApp, uploadFile, waitForReport } from "./helpers";

/**
 * The answer-first report, measured.
 *
 * Each test here corresponds to a defect that was found by measuring
 * something a screenshot showed as fine, and each one stays narrow enough
 * to name the defect it guards.
 */

/** 36 categories over 400 rows: a long table and a high-cardinality chart. */
function wideCsv(rows = 400, groups = 36): string {
  const lines = ["id,order_date,category,revenue"];
  for (let i = 0; i < rows; i += 1) {
    const month = String((i % 12) + 1).padStart(2, "0");
    lines.push(
      `${i},2025-${month}-15,cat_${String(i % groups).padStart(2, "0")},${(10 + ((i * 13) % 900)).toFixed(2)}`,
    );
  }
  return lines.join("\n");
}

async function wideReport(page: Page) {
  await openApp(page);
  await uploadFile(page, "wide.csv", wideCsv());
  await ask(page, "What is the total revenue by category?");
  await waitForReport(page);
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
      page,
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
      await page.evaluate(
        (value) => document.documentElement.setAttribute("data-theme", value),
        theme,
      );
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

/** Two dimensions, so the chart carries a colour encoding. */
function twoDimensionCsv(rows = 240): string {
  const regions = ["North", "South", "East", "West"];
  const channels = ["web", "retail", "partner"];
  const lines = ["order_id,order_date,region,channel,revenue"];
  for (let i = 0; i < rows; i += 1) {
    const month = String((i % 12) + 1).padStart(2, "0");
    lines.push(
      `${i},2025-${month}-15,${regions[i % 4]},${channels[i % 3]},${(10 + ((i * 7) % 490)).toFixed(2)}`,
    );
  }
  return lines.join("\n");
}

test.describe("a multi-series chart draws in the measured palette", () => {
  for (const theme of ["light", "dark"] as const) {
    test(`every series comes from the ramp in ${theme}`, async ({ page }) => {
      /*
       * A single-series chart exercises `config.mark.color`; this exercises
       * `config.range.category`, which is a different code path and the only
       * one the original token work covered. Both had to be checked: the
       * first was broken precisely because the second looked right.
       */
      await openApp(page);
      await uploadFile(page, "multi.csv", twoDimensionCsv());
      await ask(page, "What is the total revenue by region and channel?");
      await waitForReport(page);
      await page.evaluate(
        (value) => document.documentElement.setAttribute("data-theme", value),
        theme,
      );
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
    page,
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
    page,
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
      page,
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
    page,
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
    page,
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
