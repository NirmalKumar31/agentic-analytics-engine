import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

import { openApp, sampleCsv, uploadFile } from "./helpers";

/**
 * The six terminal states, in a browser, at both widths.
 *
 * The payloads are the committed fixtures in `src/test/runs/states/`, which
 * were captured from a local server in fake mode -- see the header of
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

/** Answer the finished run with a captured payload. */
async function withState(page: Page, name: StateName) {
  const payload = fixture(name);
  await page.route("**/api/analyses/*", async (route) => {
    const response = await route.fetch();
    const real = await response.json();
    if (real?.status && real.status !== "running") {
      await route.fulfill({ response, json: { ...payload, run_id: real.run_id } });
      return;
    }
    await route.fulfill({ response, json: real });
  });
}

async function runWith(page: Page, name: StateName) {
  await withState(page, name);
  await openApp(page);
  await uploadFile(page, "states.csv", sampleCsv());
  await page.getByLabel("Business question").fill("What is the total revenue by region?");
  await page.getByRole("button", { name: "Run analysis" }).click();
  await expect(page.getByTestId("report-panel")).toBeVisible({ timeout: 90_000 });
}

test.describe("each terminal state, at desktop and phone widths", () => {
  for (const name of STATES) {
    for (const [label, width, height] of [
      ["desktop", 1440, 900],
      ["phone", 390, 844],
    ] as const) {
      test(`${name} at ${label}`, async ({ page }) => {
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
        const canvas = (await report.textContent()) ?? "";
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
          expect(
            await page.locator(selector).count(),
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
    page,
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
    page,
  }) => {
    await runWith(page, "verification-withheld");
    const report = page.getByTestId("report-panel");
    await expect(report).toContainText(/withheld at verification/i);
    // The opposite claim: there *was* something to check.
    await expect(report).not.toContainText(/no finding to check/i);
  });
});

test.describe("a refusal leads with what to do about it", () => {
  test("not with the engine's own framing", async ({ page }) => {
    await runWith(page, "refused");
    const headline = (
      (await page.getByTestId("direct-answer").textContent()) ?? ""
    ).trim();

    expect(headline).not.toMatch(/^the question could not be mapped safely/i);
    await expect(page.getByTestId("report-panel")).toContainText(
      /name a numeric column/i,
    );
  });

  test("and the unedited reason is in the evidence drawer", async ({ page }) => {
    await runWith(page, "refused");
    const raw = String(fixture("refused").stopped_reason ?? "");
    expect(raw.length).toBeGreaterThan(0);

    // Not on the canvas.
    const canvas =
      (await page.getByTestId("report-panel").textContent()) ?? "";
    expect(canvas.includes(raw)).toBe(false);

    // In the drawer, verbatim.
    await page.getByTestId("show-work").click();
    await expect(page.getByTestId("raw-stop-reason")).toContainText(raw);
  });
});
