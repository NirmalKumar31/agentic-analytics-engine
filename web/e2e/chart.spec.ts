import { expect, test } from "@playwright/test";

import { ask, inDrawer, openApp, sampleCsv, suggestedQuestions, uploadFile, waitForCompare, waitForReport } from "./helpers";

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
    page,
  }) => {
    await openApp(page);
    await uploadFile(page, "charted.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    await expectDrawnChart(page);
    await expectNoChartFailure(page);
  });

  test("the chart survives being printed", async ({ page }) => {
    // The report offers "Print / Save PDF" as a first-class path, and the
    // PDF is the artefact a reader keeps. A chart that renders on screen
    // and vanishes on paper is the same loss.
    await openApp(page);
    await uploadFile(page, "printed.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);
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

  test("draws a trend as a line, not just a bar chart", async ({ page }) => {
    // The hydration walks encoding channels, so it is kind-agnostic -- but
    // a trend plots the engine's synthesised `period` column rather than a
    // column of the uploaded file, which is the one field name that could
    // fail to resolve against the result.
    await openApp(page);
    await uploadFile(page, "trend.csv", sampleCsv());
    await ask(page, "Show the monthly trend of revenue");
    await waitForReport(page);

    await expectDrawnChart(page);
    await expectNoChartFailure(page);
    const card = page.locator(".chart-card").first();
    // A line chart draws its series as a path; a bar chart would not.
    expect(
      await card.locator(".chart-host svg path.line, .chart-host svg path").count(),
    ).toBeGreaterThan(0);
    await expect(card.locator("h4")).toContainText(/over time/i);
  });

  test("a revenue axis reads at the same precision as the table", async ({
    page,
  }) => {
    // A total shown as 83,373,290.48 in the table and 83M on the axis
    // beside it reads as two different figures.
    await openApp(page);
    await uploadFile(page, "precision.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);
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
   * validation: not accepted" -- two false statements about a run that
   * succeeded. The timeline derives from the events a run actually emitted,
   * so the equivalent assertion is that a successful run has no stage
   * reading as stopped or not-reached.
   */
  test("a successful demo run shows no stage as stopped or unreached", async ({
    page,
  }) => {
    await openApp(page);
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    await expect(page.getByTestId("composer")).toBeVisible();
    await suggestedQuestions(page).first().click();
    await page.getByRole("button", { name: "Run analysis" }).click();
    await waitForReport(page);

    await page.getByTestId("show-work").click();
    const timeline = inDrawer(page, "run-timeline");
    await expect(timeline).toBeVisible();
    expect(await timeline.locator('[data-state="stopped"]').count()).toBe(0);
    expect(await timeline.locator('[data-state="skipped"]').count()).toBe(0);
    await expect(timeline).not.toContainText(/not reached/i);
  });

  test("a successful uploaded run reaches publish", async ({ page }) => {
    await openApp(page);
    await uploadFile(page, "staged.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    await page.getByTestId("show-work").click();
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
  /**
   * Advertise AI so the selector offers Compare, then answer the
   * comparison with one real deterministic run used for both sides.
   *
   * Pointing both panes at the same run is the honest way to reach the
   * shared-result path with real engine data: identical contracts,
   * identical values and identical withheld findings are exactly the
   * conditions `shareOneResult` requires, and no model is called to
   * manufacture them.
   */
  async function compareOverOneRun(page: import("@playwright/test").Page) {
    await page.route("**/api/config", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.capabilities.modes = body.capabilities.modes.map(
        (m: { mode: string }) =>
          m.mode === "ai"
            ? { ...m, available: true, reason: "", message: "" }
            : m,
      );
      body.capabilities.compare_available = true;
      body.capabilities.ai_limits = {
        runs_per_session: 3,
        max_model_calls_per_run: 24,
        max_runtime_seconds: 180,
      };
      await route.fulfill({ response, json: body });
    });

    await page.route("**/api/comparisons", async (route) => {
      const request = route.request().postDataJSON() as {
        session_id: string;
        question: string;
      };
      const started = await route.fetch({
        url: new URL("/api/analyses", page.url()).toString(),
        method: "POST",
        postData: JSON.stringify({ ...request, mode: "deterministic" }),
        headers: { "content-type": "application/json" },
      });
      const { run_id } = (await started.json()) as { run_id: string };
      await route.fulfill({
        status: 202,
        json: {
          comparison_id: "cmp_shared",
          session_id: request.session_id,
          question: request.question,
          deterministic_run_id: run_id,
          ai_run_id: run_id,
        },
      });
    });
  }

  test("shows one result, with both planning lanes above it", async ({
    page,
  }) => {
    await compareOverOneRun(page);
    await openApp(page);
    await uploadFile(page, "shared.csv", sampleCsv());
    await page.getByRole("radio", { name: /^Compare planning strategies/ }).click();
    await page.getByLabel("Business question").fill(
      "What is the total revenue by region?",
    );
    await page.getByRole("button", { name: /Compare strategies/ }).click();
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
    test(`in ${theme}`, async ({ page }) => {
      await page.addInitScript((value) => {
        try {
          localStorage.setItem("aae-theme", value);
        } catch {
          /* private browsing; the attribute check below catches it */
        }
      }, theme);
      await page.setViewportSize({ width: 1440, height: 900 });
      await openApp(page);
      await uploadFile(page, `palette-${theme}.csv`, sampleCsv());
      await ask(page, "What is the total revenue by region?");
      await waitForReport(page);
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
