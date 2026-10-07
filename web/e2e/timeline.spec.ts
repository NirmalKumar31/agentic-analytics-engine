import { type Page } from "@playwright/test";

import { expect, freshComposer, reportFor, test } from "./fixtures";

import { ask, inDrawer, openApp } from "./helpers";

/** Open the demo warehouse from the landing. */
async function openDemo(page: Page) {
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
  /*
   * One real run, three tests.
   *
   * All three assert on the timeline of the same finished deterministic
   * run -- that the DAG is gone, that five stages are shown and no AI
   * stage is, and that each stage's state comes from the engine's own
   * events. Running it three times admitted three analyses to look at one
   * thing three ways. Serial, with the run in `beforeAll`, so each remains
   * its own reported test: a failure in the first still leaves the other
   * two to run and name themselves.
   *
   * On its own page rather than the shared upload, because the claim is
   * about a *demo warehouse* run reaching publish.
   */
  test.describe.configure({ mode: "serial" });

  test.beforeEach(async ({ demo: page }) => {
    /*
     * The shared warehouse page, with the report replayed onto it.
     *
     * All three tests assert on the timeline of the same finished
     * deterministic run. Each used to perform its own, so one thing was
     * looked at three ways at three times the price, and the warehouse's
     * answer to this question is the same one five other specs are looking
     * at, so the job admits it once in total. The payload is a real run's,
     * events included, which is what these assertions read.
     */
    await reportFor(page, "What is the total revenue by region?");
  });

  test("replaces the agent DAG and the stage-card panel", async ({
    demo: page,
  }) => {
    // Removed, not hidden. The DAG drew the same boxes and arrows for every
    // run, and the stage cards restated the same five steps a third time.
    expect(await page.locator(".flow").count()).toBe(0);
    expect(await page.locator(".flow-node").count()).toBe(0);
    expect(await page.locator(".lane-grid").count()).toBe(0);
  });

  test("shows five stages for a deterministic run and no AI stage", async ({
    demo: page,
  }) => {
    // In the evidence drawer: once the report exists the answer is the
    // thing on screen, so the timeline moves behind "Show work".
    await page.getByTestId("inspect-evidence").click();
    const timeline = inDrawer(page, "run-timeline");
    await expect(timeline).toBeVisible();

    for (const stage of ["understand", "plan", "compute", "verify", "publish"]) {
      await expect(timeline.getByTestId(`stage-${stage}`)).toBeVisible();
    }

    // The claim this product most needs to get right: nothing was sent to a
    // model, so nothing on screen may say a model interpreted the question.
    await expect(page.getByTestId("stage-interpret")).toHaveCount(0);
    // Including in the print appendix: a stage that never happened must not
    // reach paper either.
  });

  test("every stage reached is marked from the engine's own events", async ({
    demo: page,
  }) => {
    await page.getByTestId("inspect-evidence").click();
    // A finished run has finished stages. Read from `data-state`, which is
    // what the stylesheet colours from, so a stage cannot look complete
    // while reporting something else.
    const timeline = inDrawer(page, "run-timeline");
    for (const stage of ["understand", "plan", "compute", "publish"]) {
      await expect(timeline.getByTestId(`stage-${stage}`)).toHaveAttribute(
        "data-state",
        "complete",
      );
    }

    // Verification either published something or withheld it; both are
    // finished states and neither is `stopped`.
    const verify = timeline.getByTestId("stage-verify");
    const state = await verify.getAttribute("data-state");
    expect(["complete", "withheld"]).toContain(state);
  });

  test("a refused run marks where it stopped, and nothing later", async ({
    profiled: page,
  }) => {
    // The claim the deleted five-step stepper used to carry, now asserted
    // against a real refusal from the engine rather than a phase string.
    //
    // On an upload, not the demo warehouse: the demo has a governed
    // `gross_margin` metric and answers this question, so asking it there
    // produced a completed run and the test waited 90s for a state card
    // that was never going to appear. The uploaded file has no such column,
    // which is a refusal the engine really makes.
    await freshComposer(page);
    await ask(page, "What is the total gross margin by region?");

    await expect(page.getByTestId("report-panel")).toBeVisible({
      timeout: 90_000,
    });
    await page.getByTestId("inspect-evidence").click();

    const drawerTimeline = inDrawer(page, "run-timeline");
    await expect(drawerTimeline.locator('[data-state="stopped"]')).toHaveCount(1);

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
