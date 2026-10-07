import { type Page } from "@playwright/test";

import { advertiseAi } from "./compareHelpers";
import { expect, reportFor, test } from "./fixtures";

import { onCanvas, openApp } from "./helpers";
import {
  INLINE_DISCLOSURE_CLASS,
  MIN_TOUCH_PX,
  STANDALONE_DISCLOSURE_SELECTOR,
} from "../src/test/touchTargets";

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
 * The assertions are properties rather than pixel baselines, so a chart
 * occupies most of its container, nothing overflows the page, the answer
 * precedes the technical detail, so they survive a copy change that a
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
  { name: "ranking", question: "Which region had the highest total revenue?" },
  { name: "time series", question: "Show the monthly trend of revenue" },
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
  test("answer first, chart fills, nothing overflows, at all six widths", async ({
    profiled: page,
  }) => {
    // One session, six measurements, rather than one session per width.
    //
    // A test per breakpoint opened a demo session each, and the server
    // keeps a bounded pool of them: later tests found no dataset, so no
    // report, so no chart: the same bounded-resource failure that made
    // this suite silently skip before the guard was added.
    //
    // Resizing an existing report is also the stronger test: it exercises
    // the ResizeObserver path, which is what actually broke. A fresh load
    // at each width would only prove the initial measurement.
    // The upload path, because it is the only one whose report carries
    // both an answer and a chart. On the demo warehouse the two do not
    // co-occur: "total revenue by region" renders an answer and no chart,
    // and the ranking question renders a chart and no `direct-answer`.
    // Testing layout needs a report with both in it.
    await page.setViewportSize({ width: 1440, height: 900 });
    await reportFor(page, "What is the total revenue by region?");
    await expect(
      page.locator(".chart-card .chart-host svg").first(),
    ).toBeVisible({ timeout: 20_000 });

    for (const viewport of VIEWPORTS) {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });

      // The answer precedes the technical detail. Measured by position
      // rather than DOM order: a CSS reorder would pass an order check
      // and still bury the answer.
      const answer = page.getByTestId("direct-answer");
      await expect(answer, `${viewport.label}: no answer`).toBeVisible();
      const answerBox = await answer.boundingBox();
      expect(answerBox, `${viewport.label}: answer has no box`).not.toBeNull();

      // On the canvas: a `details` inside the hidden print appendix has no
      // bounding box, and the ordering assertion below would have stopped
      // running without saying so.
      const technical = onCanvas(page, "details").first();
      if ((await technical.count()) > 0) {
        const technicalBox = await technical.boundingBox();
        if (technicalBox) {
          expect(
            answerBox!.y,
            `${viewport.label}: the answer must precede the technical disclosure`,
          ).toBeLessThan(technicalBox.y);
        }
      }

      /*
       * The chart fills the width available to it. This is the defect the
       * redesign exists to fix: cardinality must not decide width.
       *
       * The threshold was `> 0.8`, which was the loose form written before
       * the redesign landed; the brief asks for >= 90% at each of the six
       * widths. Measured on this branch it is 90% at phone-small and 98%
       * at the rest, in all three engines, so the assertion is tightened
       * to the number the brief actually states rather than left at a
       * value the product clears by eight points.
       */
      await expect
        .poll(() => fractionOfContainer(page), { timeout: 10_000 })
        .not.toBeNull();
      const fraction = await fractionOfContainer(page);
      expect(
        fraction!,
        `${viewport.label}: chart used ${((fraction ?? 0) * 100).toFixed(0)}% of its container`,
      ).toBeGreaterThanOrEqual(0.9);
      expect(
        fraction!,
        `${viewport.label}: chart overflows its container`,
      ).toBeLessThanOrEqual(1.02);

      // Nothing pushes the page sideways. A table that does not scroll
      // internally takes the whole document with it.
      expect(
        await hasHorizontalOverflow(page),
        `${viewport.label}: the document scrolls horizontally`,
      ).toBe(false);

      // And the report stays inside the viewport.
      const report = page.getByTestId("report-panel");
      const reportBox = await report.boundingBox();
      expect(reportBox, `${viewport.label}: no report box`).not.toBeNull();
      expect(reportBox!.x).toBeGreaterThanOrEqual(-1);
      expect(reportBox!.x + reportBox!.width).toBeLessThanOrEqual(
        viewport.width + 2,
      );
    }
  });
});

test.describe("chart width is independent of cardinality", () => {
  for (const shape of SHAPES) {
    test(`${shape.name} fills the container at 1440px`, async ({ demo: page }) => {
      // The whole point: a two-group chart and a forty-five-group chart
      // must both use the width they are given. A fixed step per band
      // meant the first drew a strip and the second did not.
      //
      // The two cardinalities are two distinct chart specifications, so
      // each is admitted once; the shared warehouse page is what stops
      // them being admitted again by every other spec that asks the same
      // thing.
      await page.setViewportSize({ width: 1440, height: 900 });
      await reportFor(page, shape.question);

      await expect(
        page.locator(".chart-card .chart-host svg").first(),
      ).toBeVisible({ timeout: 20_000 });
      const fraction = await fractionOfContainer(page);
      expect(fraction, `${shape.name}: no chart was rendered`).not.toBeNull();
      expect(
        fraction!,
        `${shape.name}: chart used ${((fraction ?? 0) * 100).toFixed(0)}% of its container`,
      ).toBeGreaterThanOrEqual(0.9);
    });
  }

  test("a high-cardinality upload still fills the container", async ({
    profiled: page,
  }) => {
    // 48 distinct groups: the case that rendered widest before, and so
    // the one that hid the defect.
    await page.setViewportSize({ width: 1440, height: 900 });
    await reportFor(page, "What is the total revenue by region?");

    await expect(
      page.locator(".chart-card .chart-host svg").first(),
    ).toBeVisible({ timeout: 20_000 });
    const fraction = await fractionOfContainer(page);
    expect(fraction, "no chart was rendered on the upload path").not.toBeNull();
    expect(fraction!).toBeGreaterThanOrEqual(0.9);
    expect(await hasHorizontalOverflow(page)).toBe(false);
  });
});

test.describe("touch targets on a phone", () => {
  test("a standalone disclosure is a thumb-sized target", async ({
    profiled: page,
  }) => {
    // The Planning Audit disclosure, which is a control in its own right
    // rather than a link inside a sentence. The inline privacy disclosure
    // is exempt under WCAG 2.2 SC 2.5.8; this one is not.
    //
    // It is inside the evidence drawer now, which is where a phone meets it,
    // and a drawer is exactly where a cramped target hurts most.
    // On the shared upload rather than the demo warehouse, and replayed:
    // the size of a hit area does not depend on which dataset produced the
    // report behind it, so this does not need an admission of its own.
    /*
      The phone width is set *after* the report, not before it.

      `reportFor` resets the shared session when the report it wants is
      not already on screen, and that reset deliberately restores the
      viewport the page started with -- one test's phone width left
      behind is a layout no later test chose. So setting 390px first and
      calling `reportFor` second threw the 390px away whenever the reset
      ran, and this test measured a 1280px desktop while asserting a
      floor that `responsive.css` only promises below 640px.

      It passed anyway, because the test before it in this file leaves
      the same report on screen and `reportFor` then takes its fast path
      and skips the reset. Run this file with `-g`, reorder it, or shard
      it, and the slow path returns: three summaries at 19, 19 and 25px,
      and a failure that blames the stylesheet for a width nobody asked
      about. Setting the viewport last is true on both paths.
    */
    await reportFor(page, "What is the total revenue by region?");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByTestId("inspect-evidence").click();
    await expect(page.getByTestId("evidence-drawer")).toBeVisible();

    const standalone = await page.evaluate(
      (selector) =>
        [...document.querySelectorAll(selector)]
          .map((el) => {
            const box = (el as HTMLElement).getBoundingClientRect();
            return {
              text: (el.textContent ?? "").trim().slice(0, 30),
              h: Math.round(box.height),
              w: Math.round(box.width),
            };
          })
          .filter((entry) => entry.w > 0),
      STANDALONE_DISCLOSURE_SELECTOR,
    );

    expect(
      standalone.length,
      "no standalone disclosure was rendered, so this asserts nothing",
    ).toBeGreaterThan(0);
    for (const entry of standalone) {
      expect(
        entry.h,
        `"${entry.text}" is ${entry.h}px tall`,
      ).toBeGreaterThanOrEqual(MIN_TOUCH_PX);
    }
  });

  test("every visible control is at least 44px on its shorter side", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    /*
      Advertised, so the sweep sees the controls a keyed deployment has.

      Without this the landing renders with `model_inference_remote:
      false` and the privacy disclosure -- the one control the floor's
      exemption was written for -- does not exist on the page. CI has no
      provider key and production has one, so this sweep was measuring a
      strictly smaller page than the one visitors touch, and the gap was
      invisible in exactly the way a missing element always is: nothing
      fails, the count just quietly drops.

      Before `openApp`, which is when the application asks for its
      configuration. `compareHelpers` documents why, and
      `compareSetup.spec.ts` pins the ordering.
    */
    await advertiseAi(page);
    await openApp(page);
    // The route can fail silently, and a sweep over a page missing its
    // riskiest control passes. Prove the control is there before
    // measuring anything.
    await expect(
      page.locator(`details.${INLINE_DISCLOSURE_CLASS}`).first(),
    ).toBeVisible();

    const small = await page.evaluate(({ exemptClass, floor }) => {
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
        // control. Two ways to be that: inline inside a prose element,
        // or inside a `<details>` the author declared inline.
        //
        // The second is not redundant. `<details>` is not permitted
        // content for `<p>`, so the privacy disclosure (the control
        // this exemption was written for) can never have a `p`
        // ancestor, and the prose sniff structurally cannot see it. See
        // `touchTargets.ts`; `responsive.css` exempts the same class.
        //
        // Both halves are still required: a declared class with a
        // block `display` is a block control and gets no exemption,
        // which is also why an *open* disclosure, whose summary becomes
        // `display: block`, has to meet the floor.
        if (style.display === "inline" || style.display === "inline-block") {
          const inProse = el.closest("p, li, figcaption") !== null;
          const declaredInline = el.closest(`details.${exemptClass}`) !== null;
          if (inProse || declaredInline) continue;
        }
        // The shorter side is what a thumb has to hit.
        if (Math.min(box.width, box.height) < floor) {
          offenders.push({
            tag: el.tagName,
            label: (el.textContent ?? "").trim().slice(0, 30),
            w: Math.round(box.width),
            h: Math.round(box.height),
          });
        }
      }
      return offenders;
    }, { exemptClass: INLINE_DISCLOSURE_CLASS, floor: MIN_TOUCH_PX });

    expect(
      small,
      `controls below ${MIN_TOUCH_PX}px: ${small.map((o) => `${o.tag}"${o.label}" ${o.w}x${o.h}`).join(", ")}`,
    ).toEqual([]);
  });
});
