import {
  advertiseAi,
  compareWith,
  startCompare,
  strategyReport,
} from "./compareHelpers";
import { expect, reportFor, test } from "./fixtures";

/** One question for every comparison here, so the job admits it once. */
const COMPARE_QUESTION = "What is the total revenue by region?";
import { fixtureHeaders, openApp, routeConfig, selectMode } from "./helpers";

/**
 * Dual-mode behaviour in a real browser.
 *
 * No test here depends on live paid inference. AI runs are exercised by
 * intercepting the API, so the browser contract is covered without spending
 * anything and without a credential being present.
 */

async function openDemo(page: import("@playwright/test").Page) {
  await openApp(page);
  await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
  await expect(
    page.getByRole("radiogroup", { name: /analysis mode/i }),
  ).toBeVisible();
}

test.describe("choosing a mode", () => {
  test("Governed Analysis is the default and deterministic remains selectable", async ({ page }) => {
    await openDemo(page);
    await expect(
      page.getByRole("radio", { name: /^Governed Analysis/ }),
    ).toBeChecked();
    await expect(page.getByRole("radio", { name: /^Deterministic Analytics/ })).toBeEnabled();
  });

  test("every mode is uniquely addressable and selectable when AI is off", async ({
    page,
  }) => {
    /*
     * The configuration CI runs and a developer's machine usually does
     * not: no provider key, so AI and Compare are unavailable. Two defects
     * hid behind that difference until the container found them, and this
     * is the regression test for both.
     *
     * 1. A radio's accessible name is its label -- the visible mode name
     *    plus the screen-reader description -- and when AI is unavailable
     *    Compare's unavailable message *is* the AI mode's message. So the
     *    Compare radio's name also contains "AI Analytics", and an
     *    unanchored match resolved to two radios. Playwright's strict mode
     *    refused it, and it was the only Chromium and WebKit failure in
     *    the container.
     *
     * 2. The radio input is covered by its own label. `.check()` clicks
     *    the input, and Firefox's hit-testing reports the label as
     *    intercepting those pointer events, so the click retries until the
     *    test timeout. Twenty Firefox tests died that way in one CI run,
     *    every one of them through the shared composer reset.
     *
     * Asserted together because they are the same surface: how a test
     * addresses a mode, and how it selects one.
     */
    await routeConfig(page, (body) => {
      body.capabilities.modes = body.capabilities.modes.map(
        (mode: { mode: string }) =>
          mode.mode === "ai"
            ? {
                ...mode,
                available: false,
                reason: "ai_disabled",
                message: "AI Analytics is turned off for this deployment.",
              }
            : mode,
      );
      body.capabilities.compare_available = false;
    });
    await openDemo(page);

    // Every mode's label identifies exactly one radio, anchored.
    for (const label of [
      "Governed Analysis",
      "Deterministic Analytics",
      "AI Analytics",
      "Compare planning strategies",
    ]) {
      await expect(
        page.getByRole("radio", { name: new RegExp(`^${label}`) }),
        `"${label}" does not identify exactly one mode radio`,
      ).toHaveCount(1);
    }

    // And "AI Analytics" unanchored really does match two of them here,
    // which is what made the anchoring necessary rather than tidy.
    expect(
      await page.getByRole("radio", { name: /AI Analytics/ }).count(),
      "the ambiguity this guards against has gone, so the guard is stale",
    ).toBeGreaterThan(1);

    // The two available modes are selectable through their labels, which
    // is the interaction a reader performs and the one `.check()` could
    // not complete on Firefox.
    await selectMode(page, "deterministic");
    await expect(page.locator("#mode-deterministic")).toBeChecked();
    await selectMode(page, "auto");
    await expect(page.locator("#mode-auto")).toBeChecked();
  });

  test("AI and Compare are disabled, with a stated reason, when AI is off", async ({
    page,
  }) => {
    // The test must not inherit a developer's local cloud configuration.
    // It exercises the unavailable contract, so make that server response
    // explicit rather than relying on the process environment.
    await routeConfig(page, (body) => {
      body.capabilities.modes = body.capabilities.modes.map(
        (mode: { mode: string }) =>
          mode.mode === "ai"
            ? {
                ...mode,
                available: false,
                reason: "ai_disabled",
                message: "AI Analytics is turned off for this deployment.",
              }
            : mode,
      );
      body.capabilities.compare_available = false;
    });
    await openDemo(page);
    const ai = page.getByRole("radio", { name: /^AI Analytics/ });
    await expect(ai).toBeDisabled();
    await expect(
      page.getByRole("radio", { name: /^Compare planning strategies/ }),
    ).toBeDisabled();
    // A reason a visitor can read, not just a greyed control.
    await expect(
      page.getByText(/AI Analytics is (turned off|not configured)/i).first(),
    ).toBeVisible();
  });

  test("the selector is reachable and operable by keyboard", async ({
    page,
  }) => {
    await openDemo(page);
    const deterministic = page.getByRole("radio", {
      name: /^Deterministic Analytics/,
    });
    await deterministic.focus();
    await expect(deterministic).toBeFocused();
  });

  test("no public copy calls deterministic mode fake", async ({ page }) => {
    await openDemo(page);
    const text = (
      (await page.locator("body").textContent()) ?? ""
    ).toLowerCase();
    expect(text).not.toContain("fake");
    expect(text).toContain("deterministic analytics");
  });
});

test.describe("a deterministic run", () => {
  test("completes and reports findings", async ({ demo: page }) => {
    // On the shared warehouse page: the claim is that a deterministic run
    // publishes an answer, and the warehouse's answer to this question is
    // the same one every spec in the job is looking at.
    await reportFor(page, COMPARE_QUESTION);
    // The answer itself, not the findings heading: that panel is hidden
    // when the answer is the only publication, because its whole content
    // was a sentence restating that fact.
    await expect(page.getByTestId("direct-answer")).toBeVisible({
      timeout: 60_000,
    });
  });
});

test.describe("AI and Compare, with the API intercepted", () => {
  test("Compare Both offers both strategies, one report at a time", async ({ page }) => {
    /*
     * Through `compareHelpers`, which owns the comparison route.
     *
     * This test kept its own copy, and that copy started the real
     * deterministic run with `route.fetch` -- a request the traffic
     * recorder cannot see, so the analysis was made and nothing counted
     * it. `compareWith` records the admission where it happens and
     * remembers the settled payload, so one comparison of this question is
     * real for the whole job and the rest replay it. The AI side is
     * mutated into the failure this test is about.
     */
    await advertiseAi(page);
    await compareWith(page, (payload) => ({
      ...payload,
      status: "failed",
      outcome: "failed",
      error: "AI Analytics has reached its public demo usage limit.",
      findings: [],
    }));
    await openDemo(page);
    await startCompare(page, COMPARE_QUESTION);

    /*
     * Two strategies, one report on screen.
     *
     * They were two labelled regions side by side. A full report -- a
     * headline, a chart, highlights and a result table -- in half a
     * laptop's width is not a comparison, so there is a switcher and one
     * report at full width. Each tab is independently identifiable, which
     * is what the two regions were for.
     */
    await expect(page.getByRole("tab")).toHaveCount(2, { timeout: 60_000 });
    await expect(page.getByTestId("compare-tab-deterministic")).toBeVisible();
    await expect(page.getByTestId("compare-tab-ai")).toBeVisible();
    await expect(page.getByRole("tabpanel")).toHaveCount(1);

    // The AI side failed; the deterministic side still produced a report.
    //
    // Scoped to the AI pane's state card. An unscoped `getByRole("alert")`
    // matched both this card and the contract-comparison notice, which also
    // carries `role="alert"` when the two interpretations could not be
    // compared -- a strict-mode violation that appeared on Firefox and not
    // on Chromium, because the two engines differ on when the comparison
    // becomes computable. Naming the element is both stable and a stronger
    // claim than "some alert somewhere says this".
    await expect(
      (await strategyReport(page, "ai")).getByTestId("run-state-card"),
    ).toContainText(/public demo usage limit/i);
    await expect(
      (await strategyReport(page, "deterministic")).getByTestId("direct-answer"),
    ).toBeVisible({ timeout: 60_000 });

    // No winner, anywhere.
    const text = (
      (await page.locator("body").textContent()) ?? ""
    ).toLowerCase();
    for (const banned of ["winner", "more accurate"]) {
      expect(text).not.toContain(banned);
    }
  });

  test("a refused AI pane shows its refusal, not a Complete badge", async ({
    page,
  }) => {
    // The production failure, in a browser. An AI run with status
    // `refused` wore a COMPLETE badge and rendered an empty pane beside a
    // deterministic answer that had worked. The jsdom tests render
    // `ComparisonView` with children already supplied, so they never
    // exercised the decision about whether to supply them.
    //
    // Same setup as above, and for the same reason: one recorded
    // admission for the job, replayed here.
    await advertiseAi(page);
    await compareWith(page, (payload) => ({
      ...payload,
      status: "refused",
      outcome: "refused",
      stopped_reason: "the question could not be mapped safely",
      findings: [],
    }));
    await openDemo(page);
    await startCompare(page, COMPARE_QUESTION);

    const ai = await strategyReport(page, "ai");

    // The status moved from a pane header into the row of the table that
    // compares the two strategies. The claim is unchanged -- the AI side's
    // own outcome, attributable to that side and machine-readable -- and
    // the row is scoped by its header, which is the strategy's name.
    const aiRow = page
      .getByTestId("compare-routes")
      .getByRole("row")
      .filter({ has: page.getByRole("rowheader", { name: "AI Analytics" }) });
    await expect(aiRow.getByTestId("pane-status")).toHaveText(/Refused/);
    await expect(aiRow.getByTestId("pane-status")).toHaveAttribute(
      "data-state",
      "refused",
    );

    const card = ai.getByTestId("run-state-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText(/could not be mapped safely/i);
    await expect(ai.getByTestId("pane-placeholder")).toHaveCount(0);

    // The AI panel wears no Complete badge -- asserted before switching
    // away, because the panel shows one strategy at a time.
    await expect(ai.getByText(/\bComplete\b/)).toHaveCount(0);

    await expect(
      (await strategyReport(page, "deterministic")).getByTestId("direct-answer"),
    ).toBeVisible({ timeout: 60_000 });
  });

  test("an AI run refused by quota is reported without leaking anything", async ({
    page,
  }) => {
    await routeConfig(page, (body) => {
      body.capabilities.modes = body.capabilities.modes.map(
        (m: { mode: string }) =>
          m.mode === "ai" ? { ...m, available: true, reason: "", message: "" } : m,
      );
    });
    await page.route("**/api/analyses", async (route) => {
      await route.fulfill({
        headers: fixtureHeaders("dualmode: the AI quota refusal"),
        status: 429,
        json: {
          detail:
            "AI mode has reached its public demo usage limit. Deterministic Analytics is still available.",
        },
      });
    });

    await openDemo(page);
    await selectMode(page, "ai");
    await page.getByLabel("Business question").fill("What is total revenue?");
    await page.getByTestId("run").click();

    await expect(page.getByText(/public demo usage limit/i)).toBeVisible();
    const text = (await page.locator("body").textContent()) ?? "";
    for (const leak of ["sk-", "redis://", "Traceback"]) {
      expect(text).not.toContain(leak);
    }
  });

  /*
   * "Show work resolves evidence from the pane the visitor clicked" stood
   * here and is retired, because the thing it guarded cannot happen now.
   *
   * It existed because each pane had its own "Show work" button, both runs
   * mint finding ids within themselves, and `f1` on the AI side is a
   * different claim from `f1` on the deterministic side -- so the app had
   * to carry *which side* alongside the id, and once did not.
   *
   * There is one evidence control for the comparison, and the drawer has a
   * tab per strategy. Which trace is shown is the tab, not a resolution
   * step that can be wrong. `compare.spec.ts` asserts that directly and
   * more strongly than this did: the panel is labelled with the strategy
   * (`data-strategy`), switching tabs changes it, both tabs carry the full
   * evidence record, and the switch issues no request.
   */

});
