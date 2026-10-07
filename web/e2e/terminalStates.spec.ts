import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { type Page } from "@playwright/test";

import { answerRunWith, ask, inDrawer, onCanvas } from "./helpers";
import { expect, freshComposer, test } from "./fixtures";

/**
 * The six terminal states, in a browser, at both widths.
 *
 * The payloads are the committed fixtures in `src/test/runs/states/`, which
 * were captured from a local server in fake mode. See the header of
 * `src/test/terminalStates.test.tsx` for which were asked of the engine and
 * which were derived, and how.
 *
 * A real run is still started for every case: the upload, the session, the
 * composer and the request are genuine, and only the finished payload is
 * substituted. That keeps the page assembling itself the way it does in
 * production, which is where the three defects this step fixes came from --
 * a `order: -2` rule, a duplicated headline and refusal language on a
 * completed run were all invisible in a unit test.
 */

// The e2e suite is ESM, so `__dirname` does not exist here.
const DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "src",
  "test",
  "runs",
  "states",
);

type StateName =
  | "refused"
  | "no-findings"
  | "verification-withheld"
  | "quota-stopped"
  | "failed"
  | "cancelled";

const STATES: StateName[] = [
  "refused",
  "no-findings",
  "verification-withheld",
  "quota-stopped",
  "failed",
  "cancelled",
];

function fixture(name: StateName): Record<string, unknown> {
  return JSON.parse(readFileSync(join(DIR, `${name}.json`), "utf8"));
}

/**
 * Answer the run with a captured payload, without asking the engine.
 *
 * This used to let a real analysis run and substitute its result, which
 * cost one of the container's 200 analyses per IP per hour for each of the
 * sixteen tests here. The state is a fixture either way; what these tests
 * assert is how it *renders*. The tests that are about reaching a state --
 * `app.spec.ts`'s refusal, `timeline.spec.ts`'s stopped stage, still
 * drive the real engine.
 */
let release: (() => Promise<void>) | null = null;

async function withState(page: Page, name: StateName) {
  release = await answerRunWith(page, fixture(name));
  return release;
}

/**
 * Drive the shared session to `name`, and give it back afterwards.
 *
 * This uploaded `states.csv` for every one of the sixteen tests in this
 * file -- sixteen datasets per engine for a set of outcomes produced by
 * answering the finished run with a committed payload, not by the file.
 * The upload is the `profiled` fixture's, made once per engine; the route
 * is installed per test and removed in `finally`, because one left behind
 * would answer the next test's run with this test's payload.
 */
async function runWith(page: Page, name: StateName) {
  await freshComposer(page);
  await withState(page, name);
  await ask(page, "What is the total revenue by region?");
  await expect(page.getByTestId("report-panel")).toBeVisible({ timeout: 90_000 });
}

/*
 * The route comes off after every test. On a shared session a route is not
 * discarded with the context, so one left installed would answer the next
 * test's run with this test's payload -- a terminal-state fixture quietly
 * standing in for a real run.
 */
test.afterEach(async () => {
  /*
   * Through the `release()` the route handler handed back, not by
   * unrouting the pattern.
   *
   * Unrouting by pattern removed the route and left everything else the
   * installer had set up, which, while the accounting lived in a
   * client-side marker, meant every later real analysis in the worker was
   * recorded as a free replay. Accounting no longer depends on this (it is
   * taken at the network boundary now), but one cleanup path is still
   * better than two, and the second one was wrong for a fortnight without
   * anything noticing.
   */
  await release?.();
  release = null;
});

test.describe("each terminal state, at desktop and phone widths", () => {
  for (const name of STATES) {
    for (const [label, width, height] of [
      ["desktop", 1440, 900],
      ["phone", 390, 844],
    ] as const) {
      test(`${name} at ${label}`, async ({ profiled: page }) => {
        await page.setViewportSize({ width, height });
        await runWith(page, name);

        const report = page.getByTestId("report-panel");

        // Inside the reading column, below the dataset strip. An `order:
        // -2` rule used to lift the state above the strip: a banner across
        // the page, before the reader had been told which file it was
        // about.
        const strip = await page.getByTestId("dataset-context").boundingBox();
        const box = await report.boundingBox();
        expect(strip).not.toBeNull();
        expect(box).not.toBeNull();
        expect(
          box!.y,
          "the outcome renders above the dataset context strip",
        ).toBeGreaterThan(strip!.y);
        expect(
          box!.x,
          "the outcome is not held to the reading column",
        ).toBeGreaterThanOrEqual(strip!.x - 4);

        // Exactly one headline, said once.
        await expect(page.getByTestId("direct-answer")).toHaveCount(1);
        const headline = (
          (await page.getByTestId("direct-answer").textContent()) ?? ""
        ).trim();
        expect(headline.length).toBeGreaterThan(0);
        // The canvas, not the whole report element. For a state whose
        // headline is derived from the stop reason -- quota-stopped is one
        // -- the same words legitimately appear again in the print
        // appendix, which is the unedited record.
        const canvas = await page.evaluate(() => {
          const panel = document.querySelector('[data-testid="report-panel"]')!;
          const copy = panel.cloneNode(true) as HTMLElement;
          copy.querySelector("[data-print-appendix]")?.remove();
          return copy.textContent ?? "";
        });
        expect(
          canvas.split(headline).length - 1,
          "the headline is stated more than once",
        ).toBe(1);

        // Nothing technical is resident, in any state.
        for (const selector of [
          '[data-testid="planning-audit"]',
          '[data-testid="activity"]',
          '[data-testid="run-timeline"]',
          ".lane",
          ".flow",
        ]) {
          // On the canvas. The first three are also in the hidden print
          // appendix, which is what step H put there deliberately.
          expect(
            await onCanvas(page, selector).count(),
            `${selector} is resident on ${name}`,
          ).toBe(0);
        }

        // And nothing overflows sideways.
        const overflow = await page.evaluate(
          () =>
            document.documentElement.scrollWidth -
            document.documentElement.clientWidth,
        );
        expect(overflow).toBeLessThanOrEqual(1);
      });
    }
  }
});

test.describe("a completed run that published nothing is not a refusal", () => {
  test("no findings says the execution completed, never 'not answered'", async ({
    profiled: page,
  }) => {
    await runWith(page, "no-findings");
    const report = page.getByTestId("report-panel");

    await expect(report).toContainText(/no findings to publish/i);
    await expect(report).toContainText(/ran and completed/i);
    await expect(report).toContainText(/nothing was withheld/i);
    await expect(report).not.toContainText(/not answered/i);
    await expect(report).not.toContainText(/refus/i);
  });

  test("verification withheld is distinguishable from no findings", async ({
    profiled: page,
  }) => {
    await runWith(page, "verification-withheld");
    const report = page.getByTestId("report-panel");
    await expect(report).toContainText(/withheld at verification/i);
    // The opposite claim: there *was* something to check.
    await expect(report).not.toContainText(/no finding to check/i);
  });
});

test.describe("a refusal leads with what to do about it", () => {
  test("not with the engine's own framing", async ({ profiled: page }) => {
    await runWith(page, "refused");
    const headline = (
      (await page.getByTestId("direct-answer").textContent()) ?? ""
    ).trim();

    expect(headline).not.toMatch(/^the question could not be mapped safely/i);
    await expect(page.getByTestId("report-panel")).toContainText(
      /name a numeric column/i,
    );
  });

  test("and the unedited reason is in the evidence drawer", async ({ profiled: page }) => {
    await runWith(page, "refused");
    const raw = String(fixture("refused").stopped_reason ?? "");
    expect(raw.length).toBeGreaterThan(0);

    // Not on the canvas -- the appendix is excluded, because the unedited
    // reason is required to be in it.
    const canvas = await page.evaluate(() => {
      const panel = document.querySelector('[data-testid="report-panel"]')!;
      const copy = panel.cloneNode(true) as HTMLElement;
      copy.querySelector("[data-print-appendix]")?.remove();
      return copy.textContent ?? "";
    });
    expect(canvas.includes(raw)).toBe(false);

    // In the drawer, verbatim.
    await page.getByTestId("inspect-evidence").click();
    await expect(inDrawer(page, "raw-stop-reason")).toContainText(raw);
  });
});
