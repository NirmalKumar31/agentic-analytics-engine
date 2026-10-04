import { expect, test } from "@playwright/test";

import { ask, openApp, waitForReport } from "./helpers";

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
    await openApp(page);
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
    await openApp(page);
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
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

  test("mirrors document visibility, so a hidden tab animates nothing", async ({
    page,
  }) => {
    await openApp(page);
    await expect(page.locator("body")).toHaveAttribute("data-hidden", "false");
  });
});

test.describe("motion restraint", () => {
  test("the landing's ambient field is inert", async ({ page }) => {
    await openApp(page);
    await expect(page.locator("body")).toHaveAttribute(
      "data-phase",
      "choose_dataset",
    );

    // This replaces "the ambient grid holds still once a report is on
    // screen". That test watched `body::before`, a 46px plotting grid that
    // drifted while idle and faded to 12% once a report existed. The grid
    // is gone: the landing now carries the ambient analytical field, which
    // is scoped to the one surface that wants it, and every other surface
    // has a plain canvas. Stacking contours over graph paper would have
    // been two textures behind the same content.
    //
    // The claim is stronger than the one it replaces -- the field does not
    // animate at all, in any phase -- so there is no "holds still once a
    // report arrives" case to test. When the storyboard's 48s drift is
    // implemented, this becomes a reduced-motion assertion.
    const field = page.getByTestId("analytical-field");
    await expect(field).toBeVisible();
    expect(
      await field.evaluate((el) => getComputedStyle(el).animationName),
    ).toBe("none");

    // And the texture it replaced is not merely hidden.
    expect(
      await page.evaluate(
        () => getComputedStyle(document.body, "::before").content,
      ),
    ).toBe("none");
  });

  test("no panel moves under the pointer", async ({ page }) => {
    // Opened on a report rather than on the landing: the landing has no
    // `.panel` any more, because it is no longer built out of panels.
    await openApp(page);
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

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
    await openApp(page);

    // `--glow` is gone. It was the old halo -- a blurred translucent wash
    // of the accent hue, invisible on a light surface and meaningless to a
    // reader who cannot separate the hue from its background. The bridge
    // in tokens.css kept the name alive, redefined as a solid ring, while
    // call sites were migrated; both the bridge and the last call site are
    // now removed, and focus is an `outline`, not a shadow at all.
    //
    // So this pins the removal rather than the redefinition. An
    // unresolvable custom property returns "", and a `var(--glow)` that
    // crept back would resolve to nothing and silently drop its
    // declaration -- which reads as a missing style, not an error.
    const resolved = await page.evaluate(() =>
      getComputedStyle(document.documentElement)
        .getPropertyValue("--glow")
        .trim(),
    );
    expect(resolved, "--glow is retired; nothing should define it").toBe("");

    // What replaced it, asserted on the real thing: focus is a solid
    // outline, and it carries no blur because an outline has none.
    await page.keyboard.press("Tab");
    const focusRing = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const s = getComputedStyle(el);
      return { style: s.outlineStyle, width: s.outlineWidth, shadow: s.boxShadow };
    });
    expect(focusRing, "nothing took focus on the first Tab").not.toBeNull();
    expect(focusRing!.style).toBe("solid");
    expect(parseFloat(focusRing!.width)).toBeGreaterThanOrEqual(2);

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

  test("every tab stop carries a visible ring", async ({ page }) => {
    // Driven with real key presses. `:focus-visible` is gated on keyboard
    // interaction, so calling `element.focus()` from script does not match
    // it -- an earlier version of this test did exactly that, matched
    // nothing, and asserted over an empty list.
    await openApp(page);
    const openDemo = page.getByRole("button", { name: /Commerce demo warehouse/ });
    await expect(openDemo).toBeEnabled();
    await openDemo.click();
    await expect(page.getByRole("heading", { name: "Ask" })).toBeVisible();

    const seen: { tag: string; style: string; width: number }[] = [];
    for (let i = 0; i < 12; i += 1) {
      await page.keyboard.press("Tab");
      const entry = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body) return null;
        const style = getComputedStyle(el);
        return {
          tag: el.tagName,
          style: style.outlineStyle,
          width: parseFloat(style.outlineWidth),
        };
      });
      if (entry) seen.push(entry);
    }

    expect(seen.length, "Tab reached no focusable element").toBeGreaterThan(3);
    for (const entry of seen) {
      expect(entry.style, `${entry.tag} has no focus ring`).not.toBe("none");
      expect(
        entry.width,
        `${entry.tag} ring is too thin`,
      ).toBeGreaterThanOrEqual(2);
    }
  });

  test("a disclosure summary is styled like every other control", async ({
    page,
  }) => {
    // `summary` was missing from the focus-visible selector, so the
    // Planning Audit disclosure fell back to the user-agent ring: visible,
    // but not the action colour the rest of the interface uses, and
    // different on every engine. WebKit focuses a summary first, which is
    // how this surfaced.
    //
    // Asserted against the served stylesheet because `:focus-visible`
    // cannot be triggered reliably from script, and the rule is the thing
    // that was wrong.
    await openApp(page);
    const css = await page.evaluate(async () => {
      const hrefs = [
        ...document.querySelectorAll('link[rel="stylesheet"]'),
      ].map((link) => (link as HTMLLinkElement).href);
      const texts = await Promise.all(
        hrefs.map((href) => fetch(href).then((response) => response.text())),
      );
      return texts.join("\n");
    });

    const rules = css.match(/:where\([^)]*\):focus-visible/g) ?? [];
    expect(rules.length, "no focus-visible rule was served").toBeGreaterThan(0);
    for (const rule of rules) {
      expect(rule, "a focus-visible selector omits summary").toContain(
        "summary",
      );
    }
  });
});

test.describe("no runtime third-party requests", () => {
  test("the page loads no external font or script", async ({ page, baseURL }) => {
    // Against the configured base URL, not a hardcoded one. This read
    // `http://127.0.0.1:8000`, so running the suite on any other port made
    // the app's own bundle look like a third party -- and, the other way
    // round, would have let a genuine third-party request on port 8000 pass
    // unnoticed.
    const origin = new URL(baseURL ?? "http://127.0.0.1:8000").origin;
    const external: string[] = [];
    page.on("request", (request) => {
      const url = request.url();
      if (!url.startsWith(origin) && !url.startsWith("data:")) {
        external.push(url);
      }
    });
    await openApp(page);
    // A webfont on the critical path is a third-party dependency, and a
    // report that reflows when one lands is a report that looked broken
    // first.
    expect(external).toEqual([]);
  });
});
