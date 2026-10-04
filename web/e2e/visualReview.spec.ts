import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

import { advertiseAi, compareWith, openDemo, settle, startCompare } from "./compareHelpers";
import { ask, onCanvas, openApp, sampleCsv, uploadFile, waitForReport } from "./helpers";

/**
 * The acceptance sweep: every approved width, both themes, real payloads.
 *
 * Steps B-H each proved one surface. This proves the composition -- that
 * the same report, driven by the same engine, holds together at 360px and
 * at 1920px, in light and in dark, without any of the failures a redesign
 * produces quietly:
 *
 *   - the page scrolling sideways, which a screenshot cannot show because
 *     the viewport clips the overflow;
 *   - content escaping the reading column at a width nobody opened;
 *   - an ALL-CAPS heading surviving in a surface that was not rewritten;
 *   - rounded containers creeping back as each new control brings its own;
 *   - technical material becoming resident again on the default canvas.
 *
 * The measures come from the brief's comparative table, and each is the
 * *rendered* number rather than a count of rules: `0` ALL-CAPS headings and
 * `<= 12` rounded containers are claims about what a reader sees.
 *
 * Screenshots are written to `test-results/review/` for the visual half of
 * the review, which is a person looking at them. They are captured in
 * Chromium only -- one set of artefacts, not three -- and capturing is not
 * an assertion, so the other engines lose no coverage by not writing files.
 */

const REVIEW_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "test-results",
  "review",
);

/** The six approved widths, as `landing.spec.ts` and `golden.spec.ts` use them. */
const WIDTHS = [
  { width: 360, height: 740, label: "phone-small" },
  { width: 390, height: 844, label: "phone" },
  { width: 768, height: 1024, label: "tablet" },
  { width: 1024, height: 768, label: "laptop-small" },
  { width: 1440, height: 900, label: "desktop" },
  { width: 1920, height: 1080, label: "desktop-wide" },
] as const;

const THEMES = ["light", "dark"] as const;

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

/** `ThemeToggle` reads this on first render, so it must be set before load. */
async function withTheme(page: Page, theme: (typeof THEMES)[number]) {
  await page.addInitScript((value) => {
    try {
      localStorage.setItem("aae-theme", value);
    } catch {
      /* private browsing; the attribute assertion below will catch it */
    }
  }, theme);
}

/** Answer the finished run with a committed terminal-state payload. */
async function withState(page: Page, name: StateName) {
  const { readFileSync } = await import("node:fs");
  const payload = JSON.parse(
    readFileSync(
      join(
        dirname(fileURLToPath(import.meta.url)),
        "..",
        "src",
        "test",
        "runs",
        "states",
        `${name}.json`,
      ),
      "utf8",
    ),
  );
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

/** What a reader is shown, measured rather than counted from the source. */
async function rendered(page: Page) {
  return page.evaluate(() => {
    const seen = (el: Element) => {
      const box = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return box.width > 0 && box.height > 0 && style.visibility !== "hidden";
    };
    const all = Array.from(document.querySelectorAll("*")).filter(seen);

    // Own text only: an ancestor inherits every descendant's words, so
    // reading `textContent` would report the whole page as shouting the
    // moment one chip did.
    const shouting = all
      .filter((el) => {
        const own = Array.from(el.childNodes)
          .filter((node) => node.nodeType === Node.TEXT_NODE)
          .map((node) => node.textContent ?? "")
          .join("")
          .trim();
        if (own.length <= 3 || !/[A-Za-z]{4}/.test(own)) return false;
        const style = getComputedStyle(el);
        return (
          style.textTransform === "uppercase" ||
          (own === own.toUpperCase() && /[A-Z]{4}/.test(own))
        );
      })
      .map((el) => `${el.tagName.toLowerCase()}.${String(el.className || "").split(" ")[0]}`);

    const rounded = all
      .filter((el) => {
        const style = getComputedStyle(el);
        const radius = Number.parseFloat(style.borderTopLeftRadius);
        if (!(radius > 0)) return false;
        const edged =
          style.borderTopWidth !== "0px" ||
          (style.backgroundColor !== "rgba(0, 0, 0, 0)" &&
            style.backgroundColor !== "transparent");
        if (!edged) return false;
        // A dot or a pill is deliberately round, not a rounded *container*.
        const box = el.getBoundingClientRect();
        return radius < Math.min(box.width, box.height) / 2;
      })
      .map((el) => `${el.tagName.toLowerCase()}.${String(el.className || "").split(" ")[0]}`);

    return {
      shouting,
      rounded,
      overflow:
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
      theme: document.documentElement.dataset.theme ?? "",
    };
  });
}

/**
 * The checks every cell of the sweep makes.
 *
 * `label` is what a failure names, so it says which width and which theme
 * rather than which test index.
 */
async function holds(page: Page, label: string, theme: string) {
  const measured = await rendered(page);

  expect(measured.theme, `${label}: the theme did not apply`).toBe(theme);

  expect(
    measured.overflow,
    `${label}: the page scrolls sideways by ${measured.overflow}px`,
  ).toBeLessThanOrEqual(1);

  expect(
    measured.shouting,
    `${label}: ALL-CAPS text: ${measured.shouting.join(", ")}`,
  ).toEqual([]);

  expect(
    measured.rounded.length,
    `${label}: ${measured.rounded.length} rounded containers: ${measured.rounded.join(", ")}`,
  ).toBeLessThanOrEqual(12);

  // Nothing technical resident on the canvas. The print appendix holds a
  // hidden copy, which is step H's subject and not what a reader is shown.
  for (const testId of ["planning-audit", "activity", "run-timeline"]) {
    expect(
      await onCanvas(page, `[data-testid="${testId}"]`).count(),
      `${label}: ${testId} is resident`,
    ).toBe(0);
  }
}

/** One artefact per cell, for the half of this review that is looking. */
async function capture(page: Page, browserName: string, name: string) {
  if (browserName !== "chromium") return;
  mkdirSync(REVIEW_DIR, { recursive: true });
  await page.screenshot({ path: join(REVIEW_DIR, `${name}.png`), fullPage: true });
}

test.describe("a real report, at every approved width and in both themes", () => {
  for (const theme of THEMES) {
    for (const viewport of WIDTHS) {
      test(`${viewport.label} ${theme}`, async ({ page, browserName }) => {
        await withTheme(page, theme);
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await openApp(page);
        await uploadFile(page, `review-${viewport.label}.csv`, sampleCsv());
        await ask(page, "What is the total revenue by region?");
        await waitForReport(page);

        await holds(page, `report ${viewport.label} ${theme}`, theme);

        // The answer is above the first viewport break at phone widths:
        // a reader on a phone must not scroll to find out what the answer
        // was. Asserted where it can fail -- a desktop viewport is tall
        // enough for anything.
        if (viewport.width <= 390) {
          const answer = await page.getByTestId("direct-answer").boundingBox();
          expect(answer, `${viewport.label}: the answer has no box`).not.toBeNull();
          expect(
            answer!.y + answer!.height,
            `${viewport.label}: the answer is below the fold`,
          ).toBeLessThan(viewport.height);

          /*
           * And no finding is clipped.
           *
           * The brief's "exactly one finding expanded on arrival at mobile
           * widths" is implemented, but only where there is something to
           * fold: this payload publishes two one-line rows, so all of it is
           * shown. `findingFold.test.tsx` covers the threshold, and
           * `report.spec.ts` covers a recorded run with six.
           */
          const findings = page.locator(".finding-item");
          const count = await findings.count();
          expect(count, `${viewport.label}: no findings rendered`).toBeGreaterThan(0);
          // Two one-line rows: nothing to fold, and nothing folded.
          expect(
            await page.locator(".findings-more").count(),
            `${viewport.label}: two findings were folded`,
          ).toBe(0);
          for (let i = 0; i < count; i += 1) {
            const clipped = await findings.nth(i).evaluate((node) => {
              const style = getComputedStyle(node);
              return (
                node.scrollHeight - node.clientHeight > 1 ||
                style.textOverflow === "ellipsis" ||
                style.webkitLineClamp !== "none"
              );
            });
            expect(clipped, `${viewport.label}: finding ${i + 1} is truncated`).toBe(
              false,
            );
          }
          expect(
            await page.locator(".findings details").count(),
            `${viewport.label}: a finding is behind a disclosure`,
          ).toBe(0);
        }

        await capture(page, browserName, `report-${viewport.label}-${theme}`);
      });
    }
  }
});

test.describe("every terminal state, in both themes", () => {
  for (const name of STATES) {
    for (const theme of THEMES) {
      for (const viewport of [WIDTHS[1], WIDTHS[4]] as const) {
        test(`${name} ${viewport.label} ${theme}`, async ({ page, browserName }) => {
          await withTheme(page, theme);
          await withState(page, name);
          await page.setViewportSize({ width: viewport.width, height: viewport.height });
          await openApp(page);
          await uploadFile(page, `review-${name}.csv`, sampleCsv());
          await page.getByLabel("Business question").fill(
            "What is the total revenue by region?",
          );
          await page.getByRole("button", { name: "Run analysis" }).click();
          await expect(page.getByTestId("report-panel")).toBeVisible({
            timeout: 90_000,
          });

          await holds(page, `${name} ${viewport.label} ${theme}`, theme);

          // The outcome is stated, visibly, in this theme. A state whose
          // headline resolved to the background colour would pass every
          // structural check above.
          const headline = page.getByTestId("direct-answer");
          await expect(headline).toBeVisible();
          expect(((await headline.textContent()) ?? "").trim().length).toBeGreaterThan(0);

          await capture(page, browserName, `${name}-${viewport.label}-${theme}`);
        });
      }
    }
  }
});

test.describe("Compare, in both themes", () => {
  for (const theme of THEMES) {
    for (const viewport of [WIDTHS[1], WIDTHS[4]] as const) {
      test(`${viewport.label} ${theme}`, async ({ page, browserName }) => {
        await withTheme(page, theme);
        await advertiseAi(page);
        await compareWith(page);
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await openApp(page);
        await openDemo(page);
        await startCompare(page, "What is the total revenue by region?");
        await settle(page);

        await holds(page, `compare ${viewport.label} ${theme}`, theme);

        // The verdict is the thing Compare exists to say, and it is the
        // one element whose colour carries meaning in both themes.
        await expect(page.getByTestId("contract-comparison")).toBeVisible();

        await capture(page, browserName, `compare-${viewport.label}-${theme}`);
      });
    }
  }
});
