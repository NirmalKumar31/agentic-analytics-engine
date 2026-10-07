import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { type Page } from "@playwright/test";

import { advertiseAi, compareWith, openDemo, settle, startCompare } from "./compareHelpers";
import { expect, freshComposer, reportFor, test } from "./fixtures";
import { answerRunWith, ask, onCanvas, openApp, setTheme, watchTraffic } from "./helpers";

/**
 * The acceptance sweep: every approved width, both themes, real payloads.
 *
 * Steps B-H each proved one surface. This proves the composition: that
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
 * Chromium only, so one set of artefacts rather than three, and capturing is not
 * an assertion, so the other engines lose no coverage by not writing files.
 *
 * --------------------------------------------------------------- uploads
 *
 * This file uploaded a dataset for every cell: 36 per engine, 108 across
 * the CI job, for a matrix whose cells differ only in viewport width,
 * theme and which payload the response is answered with. None of that
 * needs a new file. The matrices run on the shared `profiled` session and
 * iterate as steps, so the whole file costs **one** upload per engine --
 * and a failing cell still names itself, because a step carries its own
 * title in the report.
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

/**
 * Answer the run with a committed terminal-state payload, asking the engine
 * for nothing.
 *
 * Twenty-four cells rendering six states at two widths in two themes cost
 * twenty-four of the container's 200 analyses per IP per hour, every one of
 * them discarded the moment it arrived. The state is a fixture; the cell is
 * about how it looks.
 */
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
  return answerRunWith(page, payload);
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

const REPORT_QUESTION = "What is the total revenue by region?";

/**
 * The matrix is the size the brief says it is.
 *
 * Every cell below is generated from these three lists, so dropping an
 * entry removes a test rather than failing one, so the suite gets smaller
 * and greener at the same time, and the skip guard reconciles discovered
 * against executed, which both fall together. The only thing that catches
 * it is a claim about the size of the matrix itself.
 *
 * 6 widths x 2 themes for a real report, 6 terminal states x 2 themes x 2
 * widths, and Compare at 2 themes x 2 widths: forty cells, which is what
 * `docs/design/ACCEPTANCE.md` says was reviewed.
 */
test("the visual matrix covers every approved cell", () => {
  expect(WIDTHS.map((viewport) => viewport.width)).toEqual([
    360, 390, 768, 1024, 1440, 1920,
  ]);
  expect(THEMES).toEqual(["light", "dark"]);
  expect(STATES).toEqual([
    "refused",
    "no-findings",
    "verification-withheld",
    "quota-stopped",
    "failed",
    "cancelled",
  ]);

  const report = WIDTHS.length * THEMES.length;
  const terminal = STATES.length * THEMES.length * 2;
  const compare = THEMES.length * 2;
  expect(
    report + terminal + compare,
    "the review matrix is no longer forty cells",
  ).toBe(40);
});

test.describe("a real report holds at every approved width, in both themes", () => {
  /*
   * One upload, one admission, twelve cells.
   *
   * Every cell asserts the same report; only the viewport and the theme
   * change, and neither needs a new dataset or a new run. `reportFor`
   * admits the question once and replays the captured payload for the
   * other eleven.
   *
   * One test per cell rather than twelve `test.step`s in a single case.
   * The steps kept the cell names in the report, but a failure in the
   * first one stopped the other eleven from running at all, and a reader
   * of the summary saw one result where there are twelve claims.
   */
  for (const theme of THEMES) {
    for (const viewport of WIDTHS) {
      test(`${viewport.label} ${theme}`, async ({ profiled, browserName }) => {
        await reportFor(profiled, REPORT_QUESTION, { theme });
        await profiled.setViewportSize({
          width: viewport.width,
          height: viewport.height,
        });
        await holds(profiled, `report ${viewport.label} ${theme}`, theme);

        // The answer is above the first viewport break at phone widths: a
        // reader on a phone must not scroll to find out what the answer
        // was. Asserted where it can fail, because a desktop viewport is tall
        // enough for anything.
        if (viewport.width <= 390) {
          const answer = await profiled.getByTestId("direct-answer").boundingBox();
          expect(answer, `${viewport.label}: the answer has no box`).not.toBeNull();
          expect(
            answer!.y + answer!.height,
            `${viewport.label}: the answer is below the fold`,
          ).toBeLessThan(viewport.height);

          /*
           * And no finding is clipped, and none is folded away.
           *
           * The phone-width fold this used to allow for is gone: the
           * presentation contract emits at most two highlights and the
           * fold's threshold was three, so it never ran in product output.
           * `src/test/highlightsAreNotFolded.test.tsx` asserts the cap and
           * the absence of the control over every committed payload; this
           * asserts the same thing in a browser, at every width in the
           * sweep.
           */
          const findings = profiled.locator(".finding-item");
          const count = await findings.count();
          expect(count, `${viewport.label}: no findings rendered`).toBeGreaterThan(0);
          expect(
            await profiled.locator(".findings-more").count(),
            `${viewport.label}: a fold control is back on the report`,
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
            await profiled.locator(".findings details").count(),
            `${viewport.label}: a finding is behind a disclosure`,
          ).toBe(0);
        }

        await capture(profiled, browserName, `report-${viewport.label}-${theme}`);
      });
    }
  }
});

test.describe("every terminal state renders itself, in both themes", () => {
  /*
   * Six states, two widths, two themes: twenty-four cells and, before
   * this, twenty-four uploads. A terminal state is produced by answering
   * the finished run with a committed payload fixture, not by the file
   * that was uploaded, so the dataset is the same every time and the
   * shared session serves all of them.
   *
   * One test per cell, not one test with twenty-four `test.step`s.
   * Twenty-four cells in a single case do not fit inside a per-test
   * timeout. WebKit spent the full two minutes and failed the lot, and
   * a failure in the first cell stopped the other twenty-three from ever
   * running. Each cell now reports itself, and they still share the one
   * session underneath.
   */
  for (const name of STATES) {
    for (const theme of THEMES) {
      for (const viewport of [WIDTHS[1], WIDTHS[4]] as const) {
        test(`${name} ${viewport.label} ${theme}`, async ({
          profiled,
          browserName,
        }) => {
          await freshComposer(profiled);
          await profiled.setViewportSize({
            width: viewport.width,
            height: viewport.height,
          });
          await setTheme(profiled, theme);
          const release = await withState(profiled, name);
          try {
            await ask(profiled, "What is the total revenue by region?");
            await expect(profiled.getByTestId("report-panel")).toBeVisible({
              timeout: 90_000,
            });

            await holds(profiled, `${name} ${viewport.label} ${theme}`, theme);

            // The outcome is stated, visibly, in this theme. A state whose
            // headline resolved to the background colour would pass every
            // structural check above.
            const headline = profiled.getByTestId("direct-answer");
            await expect(headline).toBeVisible();
            expect(
              ((await headline.textContent()) ?? "").trim().length,
            ).toBeGreaterThan(0);

            await capture(
              profiled,
              browserName,
              `${name}-${viewport.label}-${theme}`,
            );
          } finally {
            // Always: a route left installed would answer the next cell's
            // run with the previous cell's payload.
            await release();
          }
        });
      }
    }
  }
});

test.describe("Compare, in both themes", () => {
  /*
   * The demo warehouse, not an upload: Compare is about two planners over
   * one dataset, and the warehouse is a session the server already holds.
   *
   * One page and one comparison for all four cells, in a serial describe.
   * Each cell used to build its own: advertise AI, open a page, open the
   * warehouse, start a comparison, wait for both lanes to settle, then
   * screenshot. On Chromium that is a couple of seconds; on WebKit a page
   * creation plus a comparison plus a full-page screenshot is tens of
   * seconds, and two of these four cells exceeded the per-test timeout
   * while a twenty-second assertion was still retrying inside it.
   *
   * The cells are still four independently reported tests, so a failure at
   * desktop dark does not hide phone light, and what they assert is
   * unchanged. What they no longer do is rebuild the comparison four times
   * to look at it from four angles. The comparison itself is replayed from
   * the job's one captured warehouse comparison, so it costs no admission
   * either.
   *
   * `compareWith` installs routes for the whole context, which is why this
   * owns its page rather than borrowing a shared one.
   */
  test.describe.configure({ mode: "serial" });

  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    watchTraffic(page);
    await advertiseAi(page);
    await compareWith(page);
    await openApp(page);
    await openDemo(page);
    await startCompare(page, "What is the total revenue by region?");
    await settle(page);
  });

  test.afterAll(async () => {
    // The warehouse holds no upload session, so there is nothing to hand
    // back, only the page this describe opened.
    await page.close();
  });

  for (const theme of THEMES) {
    for (const viewport of [WIDTHS[1], WIDTHS[4]] as const) {
      test(`${viewport.label} ${theme}`, async ({}, info) => {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        await setTheme(page, theme);

        await holds(page, `compare ${viewport.label} ${theme}`, theme);

        // The verdict is the thing Compare exists to say, and it is the
        // one element whose colour carries meaning in both themes.
        await expect(page.getByTestId("contract-comparison")).toBeVisible();

        await capture(page, info.project.name, `compare-${viewport.label}-${theme}`);
      });
    }
  }
});
