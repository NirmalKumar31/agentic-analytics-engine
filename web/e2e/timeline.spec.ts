import { expect, test } from "@playwright/test";

import { ask, openApp, sampleCsv, uploadFile, waitForReport } from "./helpers";

/** Open the demo warehouse from the landing. */
async function openDemo(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
  await page.getByTestId("composer").waitFor();
}

/**
 * The active-run timeline, in a browser.
 *
 * `src/test/timeline.test.ts` tests the derivation against event sequences,
 * including ones a browser cannot easily reach. What is left for a browser
 * is the part that only exists when the page is assembled: that the
 * diagram and the stage-card panel it replaced are *gone*, and that a real
 * deterministic run renders no AI interpretation stage.
 */

test.describe("the run timeline", () => {
  test("replaces the agent DAG and the stage-card panel", async ({ page }) => {
    await openApp(page);
    await openDemo(page);
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    // Removed, not hidden. The DAG drew the same boxes and arrows for every
    // run, and the stage cards restated the same five steps a third time.
    expect(await page.locator(".flow").count()).toBe(0);
    expect(await page.locator(".flow-node").count()).toBe(0);
    expect(await page.locator(".lane-grid").count()).toBe(0);
  });

  test("shows five stages for a deterministic run and no AI stage", async ({
    page,
  }) => {
    await openApp(page);
    await openDemo(page);
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    // In the evidence drawer: once the report exists the answer is the
    // thing on screen, so the timeline moves behind "Show work".
    await page.getByTestId("show-work").click();
    const timeline = page.getByTestId("run-timeline");
    await expect(timeline).toBeVisible();

    for (const stage of ["understand", "plan", "compute", "verify", "publish"]) {
      await expect(page.getByTestId(`stage-${stage}`)).toBeVisible();
    }

    // The claim this product most needs to get right: nothing was sent to a
    // model, so nothing on screen may say a model interpreted the question.
    await expect(page.getByTestId("stage-interpret")).toHaveCount(0);
  });

  test("every stage reached is marked from the engine's own events", async ({
    page,
  }) => {
    await openApp(page);
    await openDemo(page);
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    await page.getByTestId("show-work").click();
    // A finished run has finished stages. Read from `data-state`, which is
    // what the stylesheet colours from, so a stage cannot look complete
    // while reporting something else.
    for (const stage of ["understand", "plan", "compute", "publish"]) {
      await expect(page.getByTestId(`stage-${stage}`)).toHaveAttribute(
        "data-state",
        "complete",
      );
    }

    // Verification either published something or withheld it; both are
    // finished states and neither is `stopped`.
    const verify = page.getByTestId("stage-verify");
    const state = await verify.getAttribute("data-state");
    expect(["complete", "withheld"]).toContain(state);
  });

  test("a refused run marks where it stopped, and nothing later", async ({
    page,
  }) => {
    // The claim the deleted five-step stepper used to carry, now asserted
    // against a real refusal from the engine rather than a phase string.
    //
    // On an upload, not the demo warehouse: the demo has a governed
    // `gross_margin` metric and answers this question, so asking it there
    // produced a completed run and the test waited 90s for a state card
    // that was never going to appear. The uploaded file has no such column,
    // which is a refusal the engine really makes.
    await openApp(page);
    await uploadFile(page, "timeline-refusal.csv", sampleCsv());
    await ask(page, "What is the total gross margin by region?");

    await expect(page.getByTestId("report-panel")).toBeVisible({
      timeout: 90_000,
    });
    await page.getByTestId("show-work").click();

    const stopped = page.locator('[data-state="stopped"]');
    await expect(stopped).toHaveCount(1);

    // And no stage still claims to be running.
    expect(
      await page.locator('.timeline-stage[data-state="active"]').count(),
      "a stage is still active after the run stopped",
    ).toBe(0);
  });

  test("the timeline is not resident before a run starts", async ({ page }) => {
    await openApp(page);
    await openDemo(page);
    // Nothing has happened, so there is no run to narrate. A timeline on
    // screen before the first question is the pipeline-first hierarchy this
    // redesign removes.
    await expect(page.getByTestId("run-timeline")).toHaveCount(0);
  });
});
