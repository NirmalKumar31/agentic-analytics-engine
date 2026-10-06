import { type Page } from "@playwright/test";

import { expect, freshComposer, reportFor, test } from "./fixtures";

import { advertiseAi, compareWith, openDemo, settle, startCompare } from "./compareHelpers";
import { openApp } from "./helpers";

/**
 * `prefers-reduced-motion`, honoured by every effect in the storyboard.
 *
 * The storyboard names four: the landing's ambient field, the upload-to-
 * context swap, the run timeline's stage marks and the report's entrance.
 * Each has a reduced-motion row, and all four reduce to the same promise --
 * **nothing decorative moves, and nothing loops.**
 *
 * Measured with `document.getAnimations()` rather than by reading
 * `animationName` off the elements we happen to think about. That API
 * reports what the engine is *actually running*, including transitions,
 * animations declared by a rule nobody remembered, and anything a future
 * component adds. An assertion written against a selector list only ever
 * covers the effects whose names were known when it was written.
 *
 * The first test in each pair is the control. Without it the suite would
 * pass just as happily on a product that animates nothing at all, which is
 * not what is being claimed: the claim is that motion exists and is
 * withdrawn on request.
 */

/** `".1s"` and `"1ms"` are both durations; compare them in milliseconds. */
function ms(value: string): number {
  const amount = Number.parseFloat(value);
  if (Number.isNaN(amount)) return Number.NaN;
  return value.trim().endsWith("ms") ? amount : amount * 1000;
}

/** Everything the engine is running, with its duration and iteration count. */
async function running(page: Page) {
  return page.evaluate(() =>
    document.getAnimations().map((animation) => {
      const timing = animation.effect?.getComputedTiming() ?? {};
      const target =
        (animation.effect as KeyframeEffect | undefined)?.target ?? null;
      return {
        name: (animation as Animation & { animationName?: string }).animationName ??
          (animation as Animation & { transitionProperty?: string }).transitionProperty ??
          "unnamed",
        state: animation.playState,
        duration: Number(timing.duration ?? 0),
        iterations: Number(timing.iterations ?? 1),
        where: target
          ? `${target.tagName.toLowerCase()}.${String(target.className || "").split(" ")[0]}`
          : "?",
      };
    }),
  );
}

/** A dataset open, a question asked, a report on screen. */
/** The one question this file asks, so its report can be replayed. */
const REPORT_QUESTION = "What is the total revenue by region?";

async function report(page: Page) {
  await reportFor(page, REPORT_QUESTION);
}

test.describe("motion exists before it is withdrawn", () => {
  test("the report animates something on arrival", async ({ profiled: page }) => {
    /*
     * The control for everything below. `motion.css` gives findings an
     * entrance and cited cells a confirming flash; if a refactor removed
     * all of it, every reduced-motion assertion would still pass and would
     * be proving nothing.
     *
     * The animations are short, so this is asserted from the stylesheet's
     * own durations rather than by catching one mid-flight: a 260ms
     * entrance is reliably over before a Playwright round trip.
     */
    await report(page);
    const budgets = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      return {
        feedback: root.getPropertyValue("--motion-feedback").trim(),
        panel: root.getPropertyValue("--motion-panel").trim(),
        sequence: root.getPropertyValue("--motion-sequence").trim(),
      };
    });
    for (const [name, value] of Object.entries(budgets)) {
      expect(value, `--motion-${name} is not set`).not.toBe("");
      expect(
        ms(value),
        `--motion-${name} is ${value}, which is already imperceptible`,
      ).toBeGreaterThan(1);
    }

    // And a rule really does spend one of them.
    const animated = await page.evaluate(
      () =>
        Array.from(document.querySelectorAll("*")).filter(
          (node) => getComputedStyle(node).animationName !== "none",
        ).length,
    );
    expect(animated, "nothing in the report declares an animation").toBeGreaterThan(0);
  });
});

test.describe("with reduced motion asked for", () => {
  /*
   * `emulateMedia`, not `test.use({ reducedMotion })`.
   *
   * The option is a *context* option, and the shared `profiled` session
   * lives in a worker-scoped context that was created before any test
   * declared it. Emulating the preference on the live page reaches the
   * same `prefers-reduced-motion` query, works on a session that is being
   * reused, and is put back afterwards so the next spec is not silently
   * running under a preference it never asked for.
   */
  test.afterEach(async ({ page, profiled }) => {
    for (const target of [page, profiled]) {
      if (!target.isClosed()) {
        await target.emulateMedia({ reducedMotion: "no-preference" });
      }
    }
  });

  test("the motion budgets collapse", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openApp(page);
    const budgets = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      return [
        root.getPropertyValue("--motion-feedback").trim(),
        root.getPropertyValue("--motion-control").trim(),
        root.getPropertyValue("--motion-panel").trim(),
        root.getPropertyValue("--motion-sequence").trim(),
        root.getPropertyValue("--motion-ambient").trim(),
      ];
    });
    // Collapsed, not removed: a state change that snaps is harder to
    // follow than one that settles imperceptibly.
    for (const value of budgets) {
      expect(ms(value), `a budget is still ${value}`).toBeLessThanOrEqual(1);
    }
  });

  /*
   * `shared` says whether the surface needs the uploaded session. The
   * landing is the one that must not have one -- it is the screen before a
   * dataset exists -- so it takes a page of its own, which costs nothing.
   *
   * Each `reach` sets the preference itself, at the one moment that works:
   * after any reset -- which puts media emulation back, so that one spec
   * cannot leave the shared session under a preference the next never
   * asked for -- and before the action that animates. Setting it earlier
   * is undone by the reset; setting it afterwards is too late, because an
   * entrance that has already started keeps its original duration.
   */
  const reduce = (page: Page) => page.emulateMedia({ reducedMotion: "reduce" });

  const SURFACES = [
    {
      label: "the landing",
      shared: false,
      reach: async (page: Page) => {
        await reduce(page);
        await openApp(page);
        await expect(page.getByTestId("analytical-field")).toBeVisible();
      },
    },
    {
      label: "an open dataset",
      shared: true,
      reach: async (page: Page) => {
        await freshComposer(page);
        await reduce(page);
        await expect(page.getByTestId("composer")).toBeVisible();
      },
    },
    {
      label: "a finished report",
      shared: true,
      reach: async (page: Page) => {
        await reportFor(page, REPORT_QUESTION, { media: { reducedMotion: "reduce" } });
      },
    },
    {
      label: "the evidence drawer",
      shared: true,
      reach: async (page: Page) => {
        await reportFor(page, REPORT_QUESTION, { media: { reducedMotion: "reduce" } });
        await page.getByTestId("inspect-evidence").click();
        await expect(page.getByTestId("evidence-drawer")).toBeVisible();
      },
    },
  ] as const;

  for (const surface of SURFACES) {
    test(`${surface.label} runs nothing that loops`, async ({ page, profiled }) => {
      const target = surface.shared ? profiled : page;
      await surface.reach(target);
      const animations = await running(target);
      const looping = animations.filter((a) => !Number.isFinite(a.iterations));
      expect(
        looping,
        `looping: ${looping.map((a) => `${a.name} on ${a.where}`).join(", ")}`,
      ).toEqual([]);
    });

    test(`${surface.label} runs nothing perceptible`, async ({ page, profiled }) => {
      const target = surface.shared ? profiled : page;
      await surface.reach(target);
      const animations = await running(target);
      // 1ms budgets, so anything over a frame is an effect that escaped
      // the reduced-motion block -- an inline duration, a Web Animations
      // call, or a rule with its own `!important`.
      const perceptible = animations.filter(
        (a) => a.state === "running" && a.duration > 16,
      );
      expect(
        perceptible,
        `running: ${perceptible.map((a) => `${a.name} ${a.duration}ms on ${a.where}`).join(", ")}`,
      ).toEqual([]);
    });
  }

  test("the ambient field is a texture, not a thing that drifts", async ({ page }) => {
    // The storyboard's one looping effect, and the only one whose
    // reduced-motion row says "renders static" rather than "swaps
    // instantly". It is still drawn -- stillness loses nothing, because it
    // is a texture and not information.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openApp(page);
    const field = page.getByTestId("analytical-field");
    await expect(field).toBeVisible();
    const moving = await field.evaluate((node) =>
      [node, ...Array.from(node.querySelectorAll("*"))].some(
        (el) => getComputedStyle(el).animationName !== "none",
      ),
    );
    expect(moving, "the ambient field animates under reduced motion").toBe(false);
  });

  test("Compare settles without anything still moving", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await advertiseAi(page);
    await compareWith(page);
    await openApp(page);
    await openDemo(page);
    await startCompare(page, "What is the total revenue by region?");
    await settle(page);

    const animations = await running(page);
    const alive = animations.filter(
      (a) => a.state === "running" && (a.duration > 16 || !Number.isFinite(a.iterations)),
    );
    expect(
      alive,
      `still moving: ${alive.map((a) => `${a.name} on ${a.where}`).join(", ")}`,
    ).toEqual([]);
  });
});
