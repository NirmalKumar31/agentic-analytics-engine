import { expect, test } from "@playwright/test";

import {
  ask,
  sampleCsv,
  suggestedQuestions,
  uploadFile,
  waitForReport,
} from "./helpers";

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
    await page.goto("/");
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
    await page.goto("/");
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
    await page.goto("/");
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
    await page.goto("/");
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

test.describe("the stage lane", () => {
  test("does not report the demo dataset's stages as not reached", async ({
    page,
  }) => {
    // The demo warehouse is answered from the metric registry by planning
    // agents, which never build an upload contract. The lane described
    // every run with the contract path's stages, so a working demo run
    // reported "Rule resolver: not reached" and "Contract validation: not
    // accepted" -- two false statements about a run that succeeded.
    await page.goto("/");
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    // The demo warehouse has no upload profile panel; its questions are
    // offered directly in the Ask panel.
    await expect(page.getByRole("heading", { name: "Ask" })).toBeVisible();
    await suggestedQuestions(page).first().click();
    await page.getByRole("button", { name: "Run analysis" }).click();
    await waitForReport(page);

    const lane = page.locator(".lane").first();
    await expect(lane).toBeVisible();
    const stages = lane.locator(".lane-stage");
    await expect(stages).toHaveCount(5);

    // The first two stages are the ones that used to read as failures.
    for (const index of [0, 1]) {
      const text = (await stages.nth(index).textContent()) ?? "";
      expect(text, `stage ${index + 1} reads as not reached`).not.toMatch(
        /not reached|not accepted/i,
      );
      await expect(stages.nth(index)).not.toHaveClass(/state-idle/);
    }
  });

  test("shows an uploaded run its governed stages", async ({ page }) => {
    // The branch diagram draws one branch for a one-query fast path, which
    // made the more governed path look like it did less. The lane names the
    // contract stages the diagram has no nodes for.
    await page.goto("/");
    await uploadFile(page, "staged.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    const lane = page.locator(".lane").first();
    await expect(lane).toBeVisible();
    await expect(lane.locator(".lane-stage")).toHaveCount(5);
    await expect(lane).toContainText(/contract validation/i);
    await expect(lane).toContainText(/DuckDB via MCP/i);
    await expect(lane).not.toContainText(/not reached|not accepted/i);
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
    await page.goto("/");
    await uploadFile(page, "shared.csv", sampleCsv());
    await page.getByRole("radio", { name: /^Compare Both/ }).click();
    await page.getByLabel("Business question").fill(
      "What is the total revenue by region?",
    );
    await page.getByRole("button", { name: /Run both/ }).click();
    await waitForReport(page);

    // One shared result, not two copies of it.
    await expect(page.getByTestId("shared-result")).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.locator('[data-testid="pane-status"]')).toHaveCount(0);
    // One report, not the same report twice.
    await expect(page.getByTestId("report-panel")).toHaveCount(1);

    // The two planning lanes stay: that is what actually differed.
    await expect(page.locator(".lane")).toHaveCount(2);

    // And the one chart it shows is drawn, not described.
    await expectDrawnChart(page);
    await expectNoChartFailure(page);
  });
});
