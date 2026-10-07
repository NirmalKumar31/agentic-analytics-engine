import { expect, test } from "./fixtures";

import {
  advertiseAi,
  compareWith,
  configBeforeMount,
  configInterceptions,
  openDemo,
  settle,
  startCompare,
} from "./compareHelpers";
import { openApp } from "./helpers";

/**
 * The configuration route has to exist before the application mounts.
 *
 * `advertiseAi` answers `/api/config` with AI available, and the app
 * requests that config as it mounts. Install the route afterwards and it
 * never sees the request: the real config says AI is unavailable, the
 * Compare strategy is never offered, and `startCompare` waits out the full
 * test timeout reporting only that a radio did not appear.
 *
 * This is a regression test for a real defect, not a precaution. Swapping
 * two lines in `print.spec.ts` passed on Chromium, which happened to win
 * the race, and timed out **every test in that file** on Firefox: eleven
 * tests, ninety seconds each, with the eventual message pointing at a
 * missing control rather than at a missing route.
 *
 * It runs on all three engines on purpose. The race is a race, so the
 * engine that loses it is the engine that reports the bug.
 */

test.describe("Compare's configuration route", () => {
  test("is installed before the application asks for it", async ({ page }) => {
    await advertiseAi(page);
    await openApp(page);

    /*
     * Polled, not sampled once. `openApp` resolves on the app shell being
     * mounted, and the `request` event for `/api/config` is delivered on
     * its own turn of the event loop, so reading the record immediately
     * can see `null` ("not asked yet") rather than the ordering. Polling
     * distinguishes "has not happened yet" from "happened in the wrong
     * order", which is the thing being asserted.
     */
    await expect
      .poll(() => configBeforeMount(page), {
        timeout: 10_000,
        message: "the app requested /api/config before advertiseAi routed it",
      })
      .toBe(true);

    /*
     * And the override actually answered it.
     *
     * Ordering on its own does not prove this fixture does anything. The
     * local strict server already advertises AI, so Compare is offered
     * whether or not the route applied. Forcing the handler to fail left
     * all three tests in this file green. The CI container, which has no
     * provider key, is the only place the difference showed. Counting the
     * interceptions is what makes the claim independent of what the server
     * happens to say.
     */
    await expect
      .poll(() => configInterceptions(page), {
        timeout: 10_000,
        message: "the configuration override never answered /api/config",
      })
      .toBeGreaterThan(0);
  });

  test("and the Compare strategy is therefore offered", async ({ page }) => {
    /*
     * The consequence, stated separately: the ordering is only interesting
     * because of what it enables. A deployment with no AI key advertises
     * `compare_available: false`, and without the route the radio is
     * disabled, which is what every timed-out test was waiting on.
     */
    await advertiseAi(page);
    await openApp(page);
    await openDemo(page);

    const compare = page.getByRole("radio", {
      name: /Compare planning strategies/,
    });
    await expect(compare).toBeEnabled();
  });

  test("and a comparison started this way actually finishes", async ({ page }) => {
    // End to end through the same ordering, so the regression test fails
    // for the same reason the real specs would.
    await advertiseAi(page);
    await compareWith(page);
    await openApp(page);
    await openDemo(page);
    await startCompare(page, "What is the total revenue by region?");
    await settle(page);

    await expect(page.getByTestId("compare-workspace")).toBeVisible();
    await expect(page.getByTestId("contract-comparison")).toBeVisible();
  });
});
