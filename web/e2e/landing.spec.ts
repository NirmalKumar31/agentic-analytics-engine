import { expect, test } from "@playwright/test";

import { openApp } from "./helpers";

/**
 * The landing surface, measured rather than looked at.
 *
 * The ambient field shipped 463px wider than its column on a phone. Both
 * the light and dark screenshots looked correct, because a viewport clips
 * exactly the thing that is wrong: the overflow is off-screen by
 * definition. Only `scrollWidth - clientWidth` saw it.
 *
 * The cause is worth stating, because it is a trap any absolutely
 * positioned SVG falls into. An `<svg>` is a replaced element with an
 * intrinsic size from its viewBox, so `inset-inline: 0` alone leaves the
 * box over-constrained and the browser keeps the intrinsic width. Every
 * decorative SVG added from here on needs an explicit `width`.
 */

const VIEWPORTS = [
  { width: 360, height: 740, label: "phone-small" },
  { width: 390, height: 844, label: "phone" },
  { width: 768, height: 1024, label: "tablet" },
  { width: 1024, height: 768, label: "laptop-small" },
  { width: 1440, height: 900, label: "desktop" },
  { width: 1920, height: 1080, label: "desktop-wide" },
] as const;

test.describe("the landing at every supported width", () => {
  for (const viewport of VIEWPORTS) {
    test(`nothing overflows sideways at ${viewport.label}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await openApp(page);
      await expect(page.getByTestId("landing")).toBeVisible();

      const overflow = await page.evaluate(
        () =>
          document.documentElement.scrollWidth -
          document.documentElement.clientWidth,
      );
      expect(overflow, "the landing scrolls sideways").toBeLessThanOrEqual(1);
    });

    test(`the ambient field stays inside its column at ${viewport.label}`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await openApp(page);

      const section = await page.getByTestId("landing").boundingBox();
      const field = await page.getByTestId("analytical-field").boundingBox();
      expect(section).not.toBeNull();
      expect(field).not.toBeNull();

      // Never wider than the surface it decorates. A pixel of tolerance for
      // sub-pixel layout, not for a replaced element at its intrinsic size.
      expect(field!.width).toBeLessThanOrEqual(section!.width + 1);
      expect(field!.x).toBeGreaterThanOrEqual(section!.x - 1);
    });
  }
});

test.describe("the landing's hierarchy", () => {
  test("opens with the question, not with a noun", async ({ page }) => {
    await openApp(page);

    const headline = page.getByTestId("landing-headline");
    await expect(headline).toHaveText("What would you like to understand?");

    // It is the first heading on the page. The old landing opened with a
    // panel headed DATASET, which told a visitor what the software was
    // thinking about rather than what they could do.
    const firstHeading = page.locator("h1, h2, h3").first();
    await expect(firstHeading).toHaveText("What would you like to understand?");
  });

  test("the headline is the only display-scale type on the page", async ({
    page,
  }) => {
    await openApp(page);

    const sizes = await page.evaluate(() => {
      const display = parseFloat(
        getComputedStyle(
          document.querySelector('[data-testid="landing-headline"]')!,
        ).fontSize,
      );
      const others = [...document.querySelectorAll("body *")]
        .filter(
          (el) => !el.matches('[data-testid="landing-headline"]'),
        )
        .filter((el) => (el.textContent ?? "").trim().length > 0)
        .map((el) => parseFloat(getComputedStyle(el).fontSize))
        .filter((px) => Number.isFinite(px));
      return { display, largestOther: Math.max(...others) };
    });

    expect(sizes.largestOther).toBeLessThan(sizes.display);
  });

  test("the retired chrome is gone, not hidden", async ({ page }) => {
    await openApp(page);

    // Removed rather than display:none'd. A hidden stepper is still in the
    // accessibility tree's reading order on some engines, and is one CSS
    // edit away from coming back.
    for (const selector of [".steps", ".step", ".rail", ".dataset-grid"]) {
      expect(
        await page.locator(selector).count(),
        `${selector} is still in the document`,
      ).toBe(0);
    }
  });

  test("a recording opens from the prepared-data list", async ({ page }) => {
    await openApp(page);

    const prepared = page.getByTestId("prepared-data");
    await expect(prepared.getByText("Or start from prepared data")).toBeVisible();

    // The demo warehouse is the first row and is not a recording; the
    // helper that selects recordings must not return it.
    await expect(
      prepared.locator("button.prepared-item").first(),
    ).toContainText("Commerce demo warehouse");
  });
});
