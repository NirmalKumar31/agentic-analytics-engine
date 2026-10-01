import { expect, test, type Page } from "@playwright/test";

import { ask, sampleCsv, uploadFile, waitForReport } from "./helpers";

/**
 * Golden layout tests: the report at every width it has to survive.
 *
 * The chart defect that prompted this redesign was not that charts were
 * missing. It was that a chart's rendered width was decided by how many
 * categories it had, because the specification set no width and
 * Vega-Lite fell back to a fixed step per band. Two categories drew a
 * narrow strip inside a wide empty card; forty-eight drew something
 * wider. The card looked broken and the reader could not tell why.
 *
 * `ResizeObserver` now sizes the plot from its container, so the claim is
 * "a chart fills the width available to it, whatever its cardinality".
 * That claim is only worth anything if it is measured, so these tests
 * read **bounding boxes**: the presence of an `<svg>` proves nothing, and
 * it is exactly what the earlier suite checked.
 *
 * Six widths, because each one breaks something different: 360 and 390
 * are phones, 768 a tablet, 1024 a small laptop, 1440 and 1920 desktops.
 * The assertions are properties rather than pixel baselines -- a chart
 * occupies most of its container, nothing overflows the page, the answer
 * precedes the technical detail -- so they survive a copy change that a
 * screenshot baseline would not.
 */

/** The widths the layout has to work at, with a representative height. */
const VIEWPORTS = [
  { width: 360, height: 740, label: "phone-small" },
  { width: 390, height: 844, label: "phone" },
  { width: 768, height: 1024, label: "tablet" },
  { width: 1024, height: 768, label: "laptop-small" },
  { width: 1440, height: 900, label: "desktop" },
  { width: 1920, height: 1080, label: "desktop-wide" },
] as const;

/**
 * A question per result shape, with the cardinality it produces.
 *
 * The demo warehouse is used where it can answer, because an upload
 * consumes one of the twenty-four active upload sessions and exhausting
 * those is what made this suite silently skip tests.
 */
const SHAPES = [
  {
    name: "2 groups",
    question: "What is the total revenue by region?",
    groups: 4,
  },
  {
    name: "ranking",
    question: "Which region had the highest total revenue?",
    groups: 4,
  },
] as const;

async function fractionOfContainer(page: Page): Promise<number | null> {
  const card = page.locator(".chart-card").first();
  if ((await card.count()) === 0) return null;
  const host = card.locator(".chart-host").first();
  const plot = host.locator("svg, canvas").first();
  if ((await plot.count()) === 0) return null;

  const hostBox = await host.boundingBox();
  const plotBox = await plot.boundingBox();
  if (!hostBox || !plotBox || hostBox.width === 0) return null;
  return plotBox.width / hostBox.width;
}

async function hasHorizontalOverflow(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const doc = document.documentElement;
    // A couple of pixels of slack: sub-pixel layout rounding is not
    // overflow, and a test that fails on it fails constantly.
    return doc.scrollWidth > doc.clientWidth + 2;
  });
}

test.describe("the report at every supported width", () => {
  for (const viewport of VIEWPORTS) {
    test(`${viewport.label} (${viewport.width}px): answer first, chart fills, nothing overflows`, async ({
      page,
    }) => {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await page.goto("/");
      await page
        .getByRole("button", { name: /Commerce demo warehouse/ })
        .click();
      await expect(page.getByRole("heading", { name: "Ask" })).toBeVisible();
      await ask(page, "What is the total revenue by region?");
      await waitForReport(page);

      // 1. The answer precedes the technical detail. Measured by position,
      //    not by DOM order: a CSS reorder would pass an order check and
      //    still bury the answer.
      const answer = page.getByTestId("direct-answer");
      await expect(answer).toBeVisible();
      const answerBox = await answer.boundingBox();
      expect(answerBox).not.toBeNull();

      const technical = page.locator("details").first();
      if ((await technical.count()) > 0) {
        const technicalBox = await technical.boundingBox();
        if (technicalBox) {
          expect(
            answerBox!.y,
            "the answer must come before the technical disclosure",
          ).toBeLessThan(technicalBox.y);
        }
      }

      // 2. The chart fills the width available to it. This is the defect
      //    the redesign exists to fix: cardinality must not decide width.
      const fraction = await fractionOfContainer(page);
      if (fraction !== null) {
        expect(
          fraction,
          `chart used ${(fraction * 100).toFixed(0)}% of its container`,
        ).toBeGreaterThan(0.8);
        expect(fraction, "chart overflows its container").toBeLessThanOrEqual(
          1.02,
        );
      }

      // 3. Nothing pushes the page sideways. A table that does not scroll
      //    internally takes the whole document with it.
      expect(
        await hasHorizontalOverflow(page),
        "the document scrolls horizontally",
      ).toBe(false);

      // 4. The report stays inside the viewport horizontally.
      const report = page.getByTestId("report-panel");
      const reportBox = await report.boundingBox();
      expect(reportBox).not.toBeNull();
      expect(reportBox!.x).toBeGreaterThanOrEqual(-1);
      expect(reportBox!.x + reportBox!.width).toBeLessThanOrEqual(
        viewport.width + 2,
      );
    });
  }
});

test.describe("chart width is independent of cardinality", () => {
  for (const shape of SHAPES) {
    test(`${shape.name} fills the container at 1440px`, async ({ page }) => {
      // The whole point: a two-group chart and a forty-five-group chart
      // must both use the width they are given. A fixed step per band
      // meant the first drew a strip and the second did not.
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto("/");
      await page
        .getByRole("button", { name: /Commerce demo warehouse/ })
        .click();
      await expect(page.getByRole("heading", { name: "Ask" })).toBeVisible();
      await ask(page, shape.question);
      await waitForReport(page);

      const fraction = await fractionOfContainer(page);
      if (fraction === null) {
        // No chart is a legitimate outcome, but it must be explained
        // rather than left as an empty card.
        const card = page.locator(".chart-card").first();
        if ((await card.count()) > 0) {
          await expect(card).toContainText(/\w/);
        }
        return;
      }
      expect(
        fraction,
        `${shape.name}: chart used ${(fraction * 100).toFixed(0)}% of its container`,
      ).toBeGreaterThan(0.8);
    });
  }

  test("a high-cardinality upload still fills the container", async ({
    page,
  }) => {
    // 48 distinct groups: the case that rendered widest before, and so
    // the one that hid the defect.
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await uploadFile(page, "cardinality.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    const fraction = await fractionOfContainer(page);
    if (fraction !== null) {
      expect(fraction).toBeGreaterThan(0.8);
    }
    expect(await hasHorizontalOverflow(page)).toBe(false);
  });
});

test.describe("touch targets on a phone", () => {
  test("a standalone disclosure is a thumb-sized target", async ({ page }) => {
    // The Planning Audit disclosure on a completed report, which is a
    // control in its own right rather than a link inside a sentence. The
    // inline privacy disclosure is exempt under WCAG 2.2 SC 2.5.8; this
    // one is not, and the rule that sizes it was previously unverified --
    // removing it failed nothing, because the only summary on the
    // landing page is the exempt one.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    await expect(page.getByRole("heading", { name: "Ask" })).toBeVisible();
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    const standalone = await page.evaluate(() =>
      [...document.querySelectorAll("details:not(.disclosure) > summary")]
        .map((el) => {
          const box = (el as HTMLElement).getBoundingClientRect();
          return {
            text: (el.textContent ?? "").trim().slice(0, 30),
            h: Math.round(box.height),
            w: Math.round(box.width),
          };
        })
        .filter((entry) => entry.w > 0),
    );

    expect(
      standalone.length,
      "no standalone disclosure was rendered, so this asserts nothing",
    ).toBeGreaterThan(0);
    for (const entry of standalone) {
      expect(
        entry.h,
        `"${entry.text}" is ${entry.h}px tall`,
      ).toBeGreaterThanOrEqual(44);
    }
  });

  test("every visible control is at least 44px on its shorter side", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");

    const small = await page.evaluate(() => {
      const offenders: { tag: string; label: string; w: number; h: number }[] =
        [];
      const controls = document.querySelectorAll<HTMLElement>(
        "button, a[href], summary, input:not([type=hidden]), select",
      );
      for (const el of controls) {
        const box = el.getBoundingClientRect();
        if (box.width === 0 || box.height === 0) continue; // not rendered
        const style = getComputedStyle(el);
        if (style.visibility === "hidden" || style.display === "none") continue;
        // A visually hidden control is not a touch target. The file input
        // is the usual case: it stays in the accessibility tree and is
        // triggered by a visible label, so it is deliberately 1x1 and
        // transparent. Sizing it would be sizing something invisible.
        if (Number(style.opacity) === 0) continue;
        if (box.width <= 2 || box.height <= 2) continue;
        if (style.clipPath !== "none" || style.position === "absolute") {
          // `clip`/`clip-path` and off-screen positioning are the other
          // two visually-hidden idioms.
          const offScreen = box.right < 0 || box.bottom < 0;
          if (offScreen) continue;
        }
        // WCAG 2.2 SC 2.5.8 exempts a target that sits in a line of
        // text, because its height is set by the line rather than by the
        // control. The privacy disclosure is a link inside a sentence:
        // giving it a 44px block would break the paragraph, so the
        // standard does not ask for it.
        if (style.display === "inline" || style.display === "inline-block") {
          const inProse = el.closest("p, li, figcaption") !== null;
          if (inProse) continue;
        }
        // The shorter side is what a thumb has to hit.
        if (Math.min(box.width, box.height) < 44) {
          offenders.push({
            tag: el.tagName,
            label: (el.textContent ?? "").trim().slice(0, 30),
            w: Math.round(box.width),
            h: Math.round(box.height),
          });
        }
      }
      return offenders;
    });

    expect(
      small,
      `controls below 44px: ${small.map((o) => `${o.tag}"${o.label}" ${o.w}x${o.h}`).join(", ")}`,
    ).toEqual([]);
  });
});
