import { expect, reportFor, test } from "./fixtures";

import { onCanvas } from "./helpers";

/**
 * The evidence drawer holds everything the report canvas gave up.
 *
 * The brief's rule is that what leaves the canvas is **relocated, not
 * deleted**. Seven panels left; this asserts each datum arrived. A redesign
 * that quietly dropped the planner fallback, or the cited cells, would
 * otherwise look like a tidy-up and be a loss of provenance.
 *
 * Focus is asserted directly rather than through a visual check, because
 * the half that is easy to get wrong is the half that is invisible.
 */

/** Every section the drawer must carry, by the heading it is shown under. */
const REQUIRED = [
  "Route",
  "Accepted contract",
  "Coverage",
  "Verification",
  "Cited cells",
  "Timings",
  "Build",
  "Limitations",
  "Activity trace",
  "Planning audit",
];

const QUESTION = "What is the total revenue by region?";

async function openReport(page: import("@playwright/test").Page) {
  /*
   * The shared session, and the report already on it.
   *
   * Every test in this file asks the same question of the same dataset and
   * then inspects the drawer. The ten uploads this replaced differed in
   * nothing but their filename, and the ten analyses differed in nothing at
   * all -- so the report is produced once and the drawer is closed between
   * tests rather than the run being repeated.
   */
  await reportFor(page, QUESTION);
}


test.describe("the evidence drawer", () => {
  test("is behind one control, and not resident on the canvas", async ({ profiled: page }) => {
    await openReport(page);

    await expect(page.getByTestId("evidence-drawer")).toHaveCount(0);
    // Exactly one trigger for the whole report. The old report put a
    // "Show work →" on every finding card.
    await expect(page.getByTestId("show-work")).toHaveCount(1);

    await page.getByTestId("show-work").click();
    await expect(page.getByTestId("evidence-drawer")).toBeVisible();
  });

  test("carries every technical datum the canvas gave up", async ({ profiled: page }) => {
    await openReport(page);
    await page.getByTestId("show-work").click();

    const drawer = page.getByTestId("evidence-drawer");
    await expect(drawer).toBeVisible();
    for (const section of REQUIRED) {
      await expect(drawer, `${section} is missing from the drawer`).toContainText(
        section,
      );
    }
  });

  test("states the planner fallback whether or not it happened", async ({ profiled: page }) => {
    // "not reached" is information. Its absence is ambiguous, and a reader
    // cannot tell a run that did not fall back from a field nobody rendered.
    await openReport(page);
    await page.getByTestId("show-work").click();
    await expect(page.getByTestId("evidence-drawer")).toContainText(
      /planner fall|no planner fallback/i,
    );
  });

  test("names the cited cells that link a sentence to its number", async ({ profiled: page }) => {
    await openReport(page);
    await page.getByTestId("show-work").click();
    const drawer = page.getByTestId("evidence-drawer");
    // Either real references or an explicit "none cited" -- never blank.
    const text = (await drawer.textContent()) ?? "";
    const cited = text.slice(text.indexOf("Cited cells"));
    expect(cited.length).toBeGreaterThan("Cited cells".length + 3);
  });
});

test.describe("the evidence drawer's focus behaviour", () => {
  test("moves focus into the drawer on open", async ({ profiled: page }) => {
    await openReport(page);
    await page.getByTestId("show-work").click();

    const inside = await page.evaluate(() => {
      const drawer = document.querySelector('[data-testid="evidence-drawer"]');
      return drawer ? drawer.contains(document.activeElement) : false;
    });
    expect(inside, "focus did not enter the drawer").toBe(true);
  });

  test("traps Tab inside the drawer", async ({ profiled: page }) => {
    await openReport(page);
    await page.getByTestId("show-work").click();

    // More tabs than the drawer has stops. The page behind is under a
    // scrim, so focus landing there simply disappears.
    for (let i = 0; i < 30; i += 1) {
      await page.keyboard.press("Tab");
      // Polled, not read on the frame after the key.
      //
      // Containment is a backstop: when focus leaves the sheet it is pulled
      // back on the next tick, because at `focusout` time the new target
      // has not been focused yet and reading `activeElement` synchronously
      // would always see the old one. The guarantee is that focus cannot
      // *settle* outside the sheet, which is what a reader experiences; a
      // single read catches the frame in between and fails on whichever
      // engine happens to be quickest.
      await expect
        .poll(
          () =>
            page.evaluate(() => {
              const drawer = document.querySelector(
                '[data-testid="evidence-drawer"]',
              );
              return drawer ? drawer.contains(document.activeElement) : false;
            }),
          { message: `focus settled outside the drawer after ${i + 1} tabs` },
        )
        .toBe(true);
    }
  });

  test("Escape closes it and returns focus to the trigger", async ({ profiled: page }) => {
    await openReport(page);
    const trigger = page.getByTestId("show-work");
    await trigger.click();
    await expect(page.getByTestId("evidence-drawer")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("evidence-drawer")).toHaveCount(0);
    // Auto-retrying: the restore is deferred a frame on purpose.
    await expect(trigger).toBeFocused();
  });

  test("the Close control also returns focus to the trigger", async ({ profiled: page }) => {
    await openReport(page);
    const trigger = page.getByTestId("show-work");
    await trigger.click();
    await page
      .getByTestId("evidence-drawer")
      .getByRole("button", { name: "Close" })
      .click();
    await expect(page.getByTestId("evidence-drawer")).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });
});

test.describe("the report canvas keeps nothing technical", () => {
  test("no rail, no DAG, no stage cards, no resident activity panel", async ({ profiled: page }) => {
    await openReport(page);

    for (const selector of [
      ".rail",
      ".flow",
      ".flow-node",
      ".lane-grid",
      ".lane",
      ".activity",
      '[data-testid="planning-audit"]',
    ]) {
      // `onCanvas`, not a bare locator: the activity trace and the planning
      // audit are both in the print appendix, where they are required to
      // be. The claim is that the reader is not shown them.
      expect(
        await onCanvas(page, selector).count(),
        `${selector} is resident on the report canvas`,
      ).toBe(0);
    }
  });

  test("no heading appears twice", async ({ profiled: page }) => {
    // The old report rendered ANALYSIS twice: once for the finding chips
    // and once for the agent diagram.
    await openReport(page);
    // Excluding the print appendix, which is a second copy of the evidence
    // drawer and repeats its headings on purpose: on paper the reader has
    // no control to open, so the record is laid out in full. `innerText`
    // on a `display: none` element returns its `textContent`, so the
    // appendix's headings would otherwise collide with the drawer's.
    const headings = await page
      .locator("main :is(h1, h2, h3):not([data-print-appendix] *)")
      .allInnerTexts();
    const normalised = headings.map((text) => text.trim().toLowerCase());
    expect(new Set(normalised).size, normalised.join(" | ")).toBe(
      normalised.length,
    );
  });
});
