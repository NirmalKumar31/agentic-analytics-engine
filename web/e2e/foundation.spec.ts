import { expect, test } from "@playwright/test";

import { ask, sampleCsv, uploadFile, waitForReport } from "./helpers";

/**
 * The visual foundation, asserted from the rendered page.
 *
 * These are the claims the stylesheet makes that a unit test cannot
 * check: that the phase reaches the body, that the ambient grid holds
 * still once there is something to read, that nothing moves under the
 * pointer, and that focus is visible.
 *
 * They are written against computed style and bounding boxes rather than
 * against class names, because a class that is present but overridden
 * looks identical to a class that works.
 */

test.describe("the application phase", () => {
  test("reaches the body, so the stylesheet can key on it", async ({
    page,
  }) => {
    await page.goto("/");
    // The stylesheet's ambient rules select on this. Without it the grid
    // animates in every state, which is what it used to do.
    await expect(page.locator("body")).toHaveAttribute(
      "data-phase",
      "choose_dataset",
    );
  });

  test("advances as a dataset is opened and a question answered", async ({
    page,
  }) => {
    await page.goto("/");
    await uploadFile(page, "phases.csv", sampleCsv());
    await expect(page.locator("body")).toHaveAttribute(
      "data-phase",
      "ready_to_ask",
    );

    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);
    await expect(page.locator("body")).toHaveAttribute(
      "data-phase",
      "completed",
    );
  });

  test("reports a refusal as a refusal, not a failure", async ({ page }) => {
    await page.goto("/");
    await uploadFile(page, "refuse.csv", sampleCsv());
    await ask(page, "What is the average gross_margin by region?");
    // A refusal is a terminal phase of its own: it is not a broken run,
    // and the page must not present it as one.
    await expect(page.locator("body")).toHaveAttribute(
      "data-phase",
      "refused",
      {
        timeout: 60_000,
      },
    );
  });

  test("mirrors document visibility, so a hidden tab animates nothing", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.locator("body")).toHaveAttribute("data-hidden", "false");
  });
});

test.describe("motion restraint", () => {
  test("the ambient grid holds still once a report is on screen", async ({
    page,
  }) => {
    await page.goto("/");
    // The phase is published by an effect once the config fetch resolves,
    // so the ambient rule does not apply on the first frame. Waiting for
    // the attribute is waiting for the state the rule selects on.
    await expect(page.locator("body")).toHaveAttribute(
      "data-phase",
      "choose_dataset",
    );
    const idle = await page.evaluate(
      () => getComputedStyle(document.body, "::before").animationName,
    );
    expect(idle).toBe("grid-drift");

    await uploadFile(page, "still.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    // A report exists. The texture stops and recedes, because the most
    // useful background behind something being read is one that is not
    // competing with it.
    await expect(page.locator("body")).toHaveAttribute(
      "data-phase",
      "completed",
    );
    expect(
      await page.evaluate(
        () => getComputedStyle(document.body, "::before").animationName,
      ),
    ).toBe("none");

    // Polled, because the opacity is transitioned over a panel duration:
    // reading it on the frame the report arrives catches the start of the
    // fade rather than its end.
    await expect
      .poll(
        () =>
          page.evaluate(() =>
            Number(getComputedStyle(document.body, "::before").opacity),
          ),
        { timeout: 5_000 },
      )
      .toBeLessThan(0.2);
  });

  test("no panel moves under the pointer", async ({ page }) => {
    await page.goto("/");
    await uploadFile(page, "hover.csv", sampleCsv());
    const panel = page.locator("section.panel").first();
    await expect(panel).toBeVisible();

    await panel.hover();
    await expect(panel).toBeVisible();

    // The transform, not the viewport position: hovering scrolls the
    // element into view, which moves its box without anything having
    // animated. The first version of this test read the scroll and
    // reported a 34px "lift".
    const transform = await panel.evaluate(
      (el) => getComputedStyle(el).transform,
    );
    expect(["none", "matrix(1, 0, 0, 1, 0, 0)"]).toContain(transform);

    // And hovering adds no shadow. A panel may carry a static hairline
    // shadow; what it must not do is gain one under the pointer, which is
    // how the lift used to be reinforced. So this compares before with
    // after rather than asserting there is none at all.
    const resting = await page
      .locator("section.panel")
      .last()
      .evaluate((el) => getComputedStyle(el).boxShadow);
    const hovered = await panel.evaluate(
      (el) => getComputedStyle(el).boxShadow,
    );
    expect(hovered).toBe(resting);
  });

  test("nothing wears a coloured halo", async ({ page }) => {
    await page.goto("/");
    await uploadFile(page, "glow.csv", sampleCsv());

    // `--glow` still exists as a name: the bridge in tokens.css keeps the
    // old call sites working and redefines it as a solid ring. What must
    // be gone is the halo it used to be -- a blurred translucent wash of
    // the accent hue, invisible on a light surface and meaningless to a
    // reader who cannot separate the hue from its background. So this
    // asserts the resolved value, not the absence of the name.
    const resolved = await page.evaluate(() =>
      getComputedStyle(document.documentElement)
        .getPropertyValue("--glow")
        .trim(),
    );
    expect(resolved).not.toBe("");
    // A ring has zero blur: "0 0 0 <width> <colour>".
    expect(resolved).toMatch(/^0(px)?\s+0(px)?\s+0(px)?\s/);

    // And no element on the page renders a blurred coloured shadow.
    const haloed = await page.evaluate(
      () =>
        [...document.querySelectorAll("*")].filter((el) => {
          const shadow = getComputedStyle(el).boxShadow;
          if (shadow === "none") return false;
          // Third length is the blur radius; a halo has a non-zero one
          // together with a saturated colour.
          const blur = /(?:-?\d+px\s+){2}(-?\d+)px/.exec(shadow);
          return Boolean(blur) && Number(blur?.[1]) > 6;
        }).length,
    );
    expect(haloed).toBe(0);
  });

  test("focus is a visible solid ring", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el) return null;
      const style = getComputedStyle(el);
      return { width: style.outlineWidth, style: style.outlineStyle };
    });
    expect(focused).not.toBeNull();
    expect(focused?.style).toBe("solid");
    expect(parseFloat(focused?.width ?? "0")).toBeGreaterThanOrEqual(2);
  });
});

test.describe("no runtime third-party requests", () => {
  test("the page loads no external font or script", async ({ page }) => {
    const external: string[] = [];
    page.on("request", (request) => {
      const url = request.url();
      if (
        !url.startsWith("http://127.0.0.1:8000") &&
        !url.startsWith("data:")
      ) {
        external.push(url);
      }
    });
    await page.goto("/");
    await uploadFile(page, "offline.csv", sampleCsv());
    // A webfont on the critical path is a third-party dependency, and a
    // report that reflows when one lands is a report that looked broken
    // first.
    expect(external).toEqual([]);
  });
});
