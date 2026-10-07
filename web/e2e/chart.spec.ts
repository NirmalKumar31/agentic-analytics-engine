import { advertiseAi, compareWith, SAMPLE_CSV_DATASET, startCompare } from "./compareHelpers";
import { expect, reportFor, test } from "./fixtures";

import { endSession, inDrawer, openApp, sampleCsv, uploadFile, waitForCompare } from "./helpers";

/**
 * That the chart actually draws.
 *
 * There was no browser-level check on charts at all, which is how a
 * released build came to render the literal words "chart data must be
 * inline values" in place of both chart panels. Every layer passed its own
 * tests: the engine produced a valid specification citing the result it
 * drew, the safety check correctly rejected a specification with no inline
 * data, and nothing in between resolved one into the other. The seam had no
 * test because each side of it did.
 *
 * So these assert on the rendered plot, not on the specification: a chart
 * element with marks in it, and none of the strings the failure paths
 * produce.
 */

/** Every message a chart card can show instead of a chart. */
const FAILURE_TEXT = [
  /chart data must be inline values/i,
  /the chart could not be rendered/i,
  /the chart specification was rejected/i,
  /specification contains/i,
  /not a column of the/i,
];

async function expectNoChartFailure(page: import("@playwright/test").Page) {
  const body = (await page.locator("body").textContent()) ?? "";
  for (const pattern of FAILURE_TEXT) {
    expect(body, `the page shows a chart failure: ${pattern}`).not.toMatch(
      pattern,
    );
  }
}

/**
 * A chart card holding a plot with marks.
 *
 * An empty <svg> satisfies "a chart element is present", so the count of
 * drawn marks is what is asserted. A bar chart of four regions has four
 * rects; requiring at least one keeps this independent of the encoding.
 */
async function expectDrawnChart(page: import("@playwright/test").Page) {
  const card = page.locator(".chart-card").first();
  await expect(card).toBeVisible({ timeout: 30_000 });
  const plot = card.locator(".chart-host svg, .chart-host canvas").first();
  await expect(plot).toBeVisible({ timeout: 30_000 });

  const marks = await card
    .locator(".chart-host svg path, .chart-host svg rect, .chart-host svg line")
    .count();
  expect(marks, "the chart rendered no marks").toBeGreaterThan(0);

  const box = await plot.boundingBox();
  expect(box?.width ?? 0).toBeGreaterThan(50);
  expect(box?.height ?? 0).toBeGreaterThan(50);
}

test.describe("a chart on an uploaded dataset", () => {
  test("draws the plot rather than reporting its own specification", async ({
    profiled: page,
  }) => {
    await reportFor(page, "What is the total revenue by region?");

    await expectDrawnChart(page);
    await expectNoChartFailure(page);
  });

  test("the chart survives being printed", async ({ profiled: page }) => {
    // The report offers "Print / Save PDF" as a first-class path, and the
    // PDF is the artefact a reader keeps. A chart that renders on screen
    // and vanishes on paper is the same loss.
    await reportFor(page, "What is the total revenue by region?");
    await expectDrawnChart(page);

    await page.emulateMedia({ media: "print" });
    await expectDrawnChart(page);
    await expectNoChartFailure(page);

    // Nothing clipped to nothing by the print height limits.
    const box = await page
      .locator(".chart-card .chart-host svg, .chart-card .chart-host canvas")
      .first()
      .boundingBox();
    expect(box?.height ?? 0).toBeGreaterThan(50);
    await page.emulateMedia({ media: "screen" });
  });

  test("draws a trend as a line, not just a bar chart", async ({ profiled: page }) => {
    // The hydration walks encoding channels, so it is kind-agnostic, but
    // a trend plots the engine's synthesised `period` column rather than a
    // column of the uploaded file, which is the one field name that could
    // fail to resolve against the result.
    await reportFor(page, "Show the monthly trend of revenue");

    await expectDrawnChart(page);
    await expectNoChartFailure(page);
    const card = page.locator(".chart-card").first();
    // A line chart draws its series as a path; a bar chart would not.
    expect(
      await card.locator(".chart-host svg path.line, .chart-host svg path").count(),
    ).toBeGreaterThan(0);
    // `figcaption`, not `h4`: a figure's name is a caption, and an `<h4>`
    // in a document whose headings run h1, h2 was both a skipped level and
    // a competitor in the heading outline.
    await expect(card.locator("figcaption")).toContainText(/over time/i);
  });

  test("a revenue axis reads at the same precision as the table", async ({
    profiled: page,
  }) => {
    // A total shown as 83,373,290.48 in the table and 83M on the axis
    // beside it reads as two different figures.
    await reportFor(page, "What is the total revenue by region?");
    await expectDrawnChart(page);

    const labels = await page
      .locator(".chart-card .chart-host svg text")
      .allTextContents();
    const numeric = labels.filter((text) => /^[\d,]+(\.\d+)?$/.test(text));
    expect(numeric.length, "the axis has no numeric labels").toBeGreaterThan(0);
    for (const label of numeric) {
      // Thousands separated, and never abbreviated to k/M/G.
      expect(label).not.toMatch(/[kMGBT]/);
      if (label.includes(".")) {
        expect(label.split(".")[1]).toHaveLength(2);
      }
    }
  });
});

test.describe("the run timeline, on real runs", () => {
  /*
   * These two tests were "the stage lane" and asserted `ExecutionLane`, the
   * STAGES card panel. The panel is gone; its claims are not, and they are
   * the timeline's now.
   *
   * The claim worth keeping is the one that caught a real defect: the lane
   * described every run with the *upload contract* path's stages, so a
   * working demo run reported "Rule resolver: not reached" and "Contract
   * validation: not accepted": two false statements about a run that
   * succeeded. The timeline derives from the events a run actually emitted,
   * so the equivalent assertion is that a successful run has no stage
   * reading as stopped or not-reached.
   */
  test("a successful demo run shows no stage as stopped or unreached", async ({
    demo: page,
  }) => {
    /*
     * The stages come from the engine's own event stream either way.
     *
     * `reportFor` admits the question once per job and replays the settled
     * payload afterwards, and that payload *is* a real run's, events
     * included. What a replay cannot assert is the progression while it
     * happens, which is `accessibility.spec.ts`'s "a run in flight" and
     * `app.spec.ts`'s live demo run. This asserts the finished record.
     */
    await reportFor(page, "What is the total revenue by region?");

    await page.getByTestId("inspect-evidence").click();
    const timeline = inDrawer(page, "run-timeline");
    await expect(timeline).toBeVisible();
    expect(await timeline.locator('[data-state="stopped"]').count()).toBe(0);
    expect(await timeline.locator('[data-state="skipped"]').count()).toBe(0);
    await expect(timeline).not.toContainText(/not reached/i);
  });

  test("a successful uploaded run reaches publish", async ({ profiled: page }) => {
    await reportFor(page, "What is the total revenue by region?");

    await page.getByTestId("inspect-evidence").click();
    const timeline = inDrawer(page, "run-timeline");
    await expect(timeline).toBeVisible();
    // Five stages for a deterministic run, every one of them reached.
    await expect(timeline.locator(".timeline-stage")).toHaveCount(5);
    await expect(timeline.getByTestId("stage-publish")).toHaveAttribute(
      "data-state",
      "complete",
    );
    await expect(timeline).not.toContainText(/not reached/i);
  });
});

test.describe("Compare Both, in a browser", () => {
  // This describe opens its own session (see the test below), so it
  // hands it back rather than leaving it for the container to time out.
  test.afterEach(async ({ page }) => {
    await endSession(page);
  });

  /*
   * The Compare setup lives in `compareHelpers.ts`.
   *
   * This file kept its own copy: an `/api/config` override and an
   * `/api/comparisons` handler that issued the real deterministic run with
   * `route.fetch`. That request is invisible to the traffic recorder --
   * `route.fetch` produces no page event, so the analysis happened and
   * nothing counted it. `compareWith` records it where it is made and
   * remembers the settled payload, so the first comparison of a question
   * in the whole job is real and every later one replays it.
   */

  test("shows one result, with both planning lanes above it", async ({
    page,
  }) => {
    /*
     * Its own page, not the shared session.
     *
     * `compareOverOneRun` answers `/api/config` so the selector offers
     * Compare, and the application requests that config **as it mounts**.
     * The shared `profiled` session was created at the start of the worker,
     * long before any such route existed, so on it the Compare strategy is
     * never offered and the click waits out the whole test timeout,
     * which is how this failed on Firefox while passing on Chromium.
     *
     * `compareSetup.spec.ts` pins the ordering rule this follows.
     */
    await advertiseAi(page);
    /*
     * The same dataset label `compare.spec.ts` uses.
     *
     * Both files are `sampleCsv()`, byte-identical data under two
     * filenames, and the label exists to stop a comparison being
     * replayed across *different* data, not across two names for the
     * same data. Sharing it means the job admits one comparison over
     * this dataset instead of two.
     */
    await compareWith(page, undefined, SAMPLE_CSV_DATASET);
    await openApp(page);
    await uploadFile(page, "compare-lanes.csv", sampleCsv());
    await startCompare(page, "What is the total revenue by region?");
    // The comparison's anchor, not the single-run report's: Compare renders
    // compact panes and, under agreement, one shared result.
    await waitForCompare(page);

    // One shared result, not two copies of it.
    await expect(page.getByTestId("shared-result")).toBeVisible({
      timeout: 60_000,
    });
    // One report, not the same report twice.
    await expect(page.getByTestId("compare-report")).toHaveCount(1);
    await expect(page.locator(".compare-pane")).toHaveCount(0);

    // The planning comparison stays: that is what actually differed. It was
    // two `.lane` stage stacks restating the same five steps twice; it is
    // one table with a row per strategy, and each row carries that
    // strategy's own status.
    await expect(page.getByTestId("compare-routes")).toBeVisible();
    await expect(page.locator('[data-testid="pane-status"]')).toHaveCount(2);

    // And the one chart it shows is drawn, not described.
    await expectDrawnChart(page);
    await expectNoChartFailure(page);
  });
});

test.describe("every colour in a chart comes from the palette", () => {
  /*
   * Vega's defaults are greys and blues chosen for a white page, and they
   * are written *inline* on the SVG, where no stylesheet can reach them.
   * Two have already shipped: `#4c78a8` for a single-series mark, which was
   * a blue from neither palette and identical in both themes; and `#ddd`
   * for `view.stroke`, which rendered as a near-white rectangle outlining
   * the plot on a #0e1113 dark canvas and was invisible in light, which is
   * why it survived.
   *
   * Both were found by looking at a screenshot. This is the general form:
   * resolve every token the palette defines to the colour the browser
   * computes for it, then assert that the chart uses nothing else. A third
   * unthemed default cannot ship without failing here, whichever property
   * carries it.
   */
  const PALETTE = [
    "--series-1",
    "--series-2",
    "--series-3",
    "--series-4",
    "--series-5",
    "--ink-primary",
    "--ink-secondary",
    "--ink-muted",
    "--ink-inverse",
    "--rule-hairline",
    "--rule-strong",
    "--surface-canvas",
    "--surface-paper",
    "--surface-raised",
    "--surface-inset",
    "--signal",
    "--signal-strong",
    "--warning",
  ];

  for (const theme of ["light", "dark"] as const) {
    test(`in ${theme}`, async ({ profiled: page }) => {
      // Toggled, not seeded: `addInitScript` only takes effect on the next
      // navigation, and a shared session is never navigated, so the dataset
      // lives in React state and a reload would throw it away.
      await page.setViewportSize({ width: 1440, height: 900 });
      /*
       * Replayed, with the theme applied before the chart is drawn.
       *
       * The claim is about which colours a chart uses, not about the
       * engine producing one: the same captured payload embeds under
       * either palette, and `reportFor` sets the theme before the report
       * renders because Vega writes `--series-*` inline at embed time. Two
       * admissions for one question in two themes was two admissions for
       * nothing.
       */
      await reportFor(page, "What is the total revenue by region?", { theme });
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expectDrawnChart(page);

      const foreign = await page.evaluate((tokens) => {
        // Resolve each token the way the browser will, so the comparison
        // is between computed colours rather than between a hex and an
        // `rgb()` string that mean the same thing.
        const probe = document.createElement("span");
        probe.style.display = "none";
        document.body.append(probe);
        const allowed = new Set(["none", "rgba(0, 0, 0, 0)", "rgb(0, 0, 0)"]);
        for (const name of tokens) {
          probe.style.color = `var(${name})`;
          const resolved = getComputedStyle(probe).color;
          if (resolved) allowed.add(resolved);
        }
        probe.remove();

        const svg = document.querySelector(".chart-host svg");
        if (!svg) return ["no chart"];
        const seen = new Map<string, string>();
        for (const node of Array.from(svg.querySelectorAll("*"))) {
          const style = getComputedStyle(node);
          for (const property of ["fill", "stroke"] as const) {
            const value = style[property];
            if (!value || allowed.has(value)) continue;
            const where = `${node.tagName}.${node.getAttribute("class") ?? ""}`;
            seen.set(`${property}=${value}`, where);
          }
        }
        return [...seen].map(([colour, where]) => `${colour} on ${where}`);
      }, PALETTE);

      expect(
        foreign,
        `${theme}: colours from outside the palette: ${foreign.join(" | ")}`,
      ).toEqual([]);
    });
  }
});

test.describe("the chart's category labels", () => {
  /*
   * Vega turns a nominal x axis to vertical by default, whatever the
   * labels are: four words at 1440px were printed on their sides, in the
   * same product whose demo-warehouse specifications set `-30` and look as
   * the mockup intends. The angle is decided from the labels now --
   * `categoryLabelAngle`, unit-tested in `axisLabels.test.ts`, and this
   * is the half that only a browser can check: that the decision reaches
   * the rendered SVG.
   */
  async function labelRotations(page: import("@playwright/test").Page) {
    return page.evaluate(() => {
      const labels = Array.from(
        document.querySelectorAll(
          ".chart-host svg g.role-axis-label text, .chart-host svg .mark-text.role-axis-label text",
        ),
      );
      return labels.map((node) => {
        const transform = node.getAttribute("transform") ?? "";
        const match = transform.match(/rotate\(\s*(-?[\d.]+)/);
        return {
          text: (node.textContent ?? "").trim(),
          angle: match ? Number(match[1]) : 0,
        };
      });
    });
  }

  test("four short categories are not printed on their sides", async ({ profiled: page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await reportFor(page, "What is the total revenue by region?");
    await expectDrawnChart(page);

    const labels = await labelRotations(page);
    const named = labels.filter((label) => /East|North|South|West/.test(label.text));
    expect(named.length, "the region labels were not found").toBeGreaterThan(0);
    for (const label of named) {
      expect(
        Math.abs(label.angle),
        `"${label.text}" is rotated ${label.angle} degrees`,
      ).toBeLessThan(1);
    }
  });

  test("and a crowded axis keeps every label rather than dropping any", async ({
    wide: page,
  }) => {
    // The opposite failure. Flat labels on a wide axis collide, and Vega
    // resolves a collision by removing labels, and a chart that silently
    // loses most of its axis is worse than one read at an angle.
    // The `wide` session: 36 categories, which is the crowded axis this
    // test is about. It replaced a 400-row upload of the ordinary sample
    // made for this one assertion.
    await page.setViewportSize({ width: 1440, height: 900 });
    await reportFor(page, "What is the total revenue by category?");

    const chart = page.locator(".chart-host svg");
    if ((await chart.count()) === 0) return;
    const labels = await labelRotations(page);
    if (labels.length < 10) return;
    const angles = new Set(labels.map((label) => Math.round(label.angle)));
    expect(
      [...angles].every((angle) => angle === 0 || angle === -30),
      `unexpected angles: ${[...angles].join(", ")}`,
    ).toBe(true);
  });
});
