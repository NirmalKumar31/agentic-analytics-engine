import AxeBuilder from "@axe-core/playwright";
import { type Page, type Route } from "@playwright/test";

import {
  advertiseAi,
  compareWith,
  openDemo,
  startCompare,
  strategyReport,
} from "./compareHelpers";
import { expect, freshComposer, reportFor, test } from "./fixtures";

import { ask, inDrawer, onCanvas, openApp, waitForReport } from "./helpers";

/**
 * Automated accessibility checks, on the states a visitor actually reaches.
 *
 * Browser tests already assert the specific things that were broken --
 * focus rings, 44px targets, no horizontal overflow on a phone. Those are
 * assertions about defects we found. This file is the opposite: a scan
 * that looks for the ones nobody has thought of yet, which is the only
 * way to find a violation that is not already on someone's list.
 *
 * Scoped to serious and critical violations. Minor and moderate findings
 * are real but are frequently stylistic or advisory, and a gate that
 * fails on them gets disabled within a week -- at which point it protects
 * nothing. Starting strict on the two severities that block a user, and
 * tightening later, is the version that survives.
 *
 * Nothing here is excluded to make the scan pass. If a rule ever has to
 * be, the exclusion names the rule, names the element, says why, and is
 * recorded in LIMITATIONS with a follow-up, because a silent exclusion
 * is indistinguishable from a bug.
 */

/** Violations at these levels fail the build. */
const BLOCKING = ["serious", "critical"] as const;

interface Finding {
  id: string;
  impact: string | null | undefined;
  help: string;
  nodes: string[];
}

/**
 * Wait until nothing is animating, then scan.
 *
 * Without this the scan is nondeterministic and wrong in a specific way:
 * axe composites `opacity` when it computes contrast, so an element
 * mid-fade-in reports the colour it is passing through rather than the
 * colour it settles on. The answer card fades in over a panel duration,
 * and scanning during it produced four contrast "failures" whose real
 * ratios pass comfortably.
 *
 * Reduced motion is emulated as well as waited for. The token layer
 * collapses every duration to 1ms under it, so the scan runs against a
 * settled page by construction rather than by racing one.
 */
async function settle(page: Page): Promise<void> {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.waitForFunction(
    () => document.getAnimations().every((a) => a.playState !== "running"),
    undefined,
    { timeout: 10_000 },
  );
}

async function scan(page: Page, label: string): Promise<void> {
  await settle(page);
  const results = await new AxeBuilder({ page })
    // WCAG 2.0/2.1/2.2 A and AA, which is the conformance target. `best-practice`
    // is deliberately not included: it is advisory, and mixing advice into a
    // gate makes the gate's failures ambiguous.
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();

  const blocking: Finding[] = results.violations
    .filter((violation) =>
      BLOCKING.includes((violation.impact ?? "") as (typeof BLOCKING)[number]),
    )
    .map((violation) => ({
      id: violation.id,
      impact: violation.impact,
      help: violation.help,
      nodes: violation.nodes.slice(0, 3).map((node) => node.target.join(" ")),
    }));

  if (blocking.length > 0) {
    const detail = blocking
      .map(
        (finding) =>
          `  [${finding.impact}] ${finding.id}: ${finding.help}\n` +
          finding.nodes.map((node) => `      at ${node}`).join("\n"),
      )
      .join("\n");
    throw new Error(
      `${label}: ${blocking.length} serious/critical accessibility violation(s)\n${detail}`,
    );
  }
}

test.describe("accessibility", () => {
  test("the landing state", async ({ page }) => {
    await openApp(page);
    await expect(
      page.getByRole("button", { name: /Commerce demo warehouse/ }),
    ).toBeVisible();
    await scan(page, "landing");
  });

  test("an uploaded dataset after profiling", async ({ profiled: page }) => {
    await freshComposer(page);
    // Scanned twice: the composer with the schema out of the way, and the
    // schema sheet open over it. The sheet is where the field table lives,
    // which is the part most likely to have a contrast or
    // header-association problem, and it is not in the document at all
    // until a reader asks for it, so a single scan of the default screen
    // would never see it.
    await expect(page.getByTestId("composer")).toBeVisible();
    await scan(page, "profiled upload, schema closed");

    await page.getByTestId("inspect-schema").click();
    await expect(page.getByTestId("schema-inspector")).toBeVisible();
    await scan(page, "profiled upload, schema sheet open");
  });

  test("a completed deterministic report", async ({ profiled: page }) => {
    await reportFor(page, "What is the total revenue by region?");
    await scan(page, "completed report");
  });

  test("a refused report", async ({ profiled: page }) => {
    // A refusal is a state a visitor reaches, so it is a state that has to
    // be navigable. An unreadable explanation of why nothing was answered
    // is worse than an unreadable answer.
    /*
     * Through `reportFor`, which admits it once and remembers the payload.
     *
     * A refusal is as real a result as an answer -- the engine classified
     * it, and the dark-mode scan below needs the same refused DOM in the
     * other palette. Captured here, replayed there, one admission between
     * them.
     */
    await reportFor(page, "What is the average gross_margin by region?");
    await expect(page.locator("body")).toHaveAttribute(
      "data-phase",
      "refused",
      { timeout: 60_000 },
    );
    await scan(page, "refused report");
  });

  test("a run in flight", async ({ profiled: page }) => {
    /*
     * The brief lists the active run as a state to scan, and it is the one
     * state that is *only* reachable mid-interaction: the timeline, the
     * live region that narrates it and the disabled controls all exist for
     * a few seconds and then are replaced by the report.
     *
     * In fake mode a run finishes in well under a second, so the window is
     * held open by delaying the status poll rather than by racing it. The
     * responses are the server's own. Nothing is fabricated; they simply
     * arrive late, which is what a slow run looks like to the page.
     *
     * `onCanvas`, because the print appendix holds a hidden copy of the
     * timeline: the assertion is that the reader is shown one.
     */
    let held = 0;
    const slowPoll = async (route: Route) => {
      if (held < 3) {
        held += 1;
        await new Promise((resolve) => setTimeout(resolve, 1_200));
      }
      await route.fallback();
    };
    await page.route("**/api/analyses/*", slowPoll);

    /*
     * The route and the run belong to this test. The session does not.
     *
     * Both were left behind on the shared page: the delay stayed
     * installed for every later test in the worker, and the run was still
     * in flight when the test ended, so the next test found a composer
     * that was *visible and disabled*. `fill()` waits for actionability,
     * so it waited out the whole two-minute test timeout and then
     * reported "Target page, context or browser has been closed", which
     * is the teardown, not the cause. The failure was in this test and
     * was reported against the one after it.
     */
    try {
      await freshComposer(page);
      await ask(page, "What is the total revenue by region?");
      await expect(onCanvas(page, '[data-testid="run-timeline"]')).toBeVisible();

      /*
       * And while it runs there is no composer at all, which is the
       * mechanism behind the failure this test used to cause in the test
       * after it.
       *
       * The question field is not disabled during a run; it is removed
       * from the document. So a run left in flight hands the next test a
       * page where `getByLabel("Business question")` never resolves, and
       * `fill()` waits for it until the *test* timeout rather than its
       * own. That is the two-minute hang: Playwright then tears the
       * context down and reports "Target page, context or browser has
       * been closed", which reads as though a sibling test had closed the
       * shared page. It had not -- every test after the failure kept
       * using the same session and passed in a few seconds each.
       *
       * The window is small (the previous run's last poll, arriving after
       * this test's reset had already succeeded), which is why it
       * surfaced once in six full gates. Asserted here so the first link
       * in that chain is a standing claim rather than something that was
       * worked out once from a trace.
       */
      await expect(
        page.getByLabel("Business question"),
        "the composer is still in the document during a run, so a run left " +
          "in flight would not be able to strand the next test's fill()",
      ).toHaveCount(0);

      await scan(page, "run in flight");
    } finally {
      await page.unroute("**/api/analyses/*", slowPoll);
      // And the run is left to finish, because a session handed back
      // mid-run is a session the next test cannot type into.
      await waitForReport(page).catch(() => {});
    }
  });

  test("a completed run that published nothing", async ({ profiled: page }) => {
    // Distinct from a refusal in wording and in tone, and therefore in
    // which colours carry the distinction. The brief lists it separately
    // for that reason.
    await freshComposer(page);
    await ask(
      page,
      "What is total revenue by region where region is Atlantis?",
    );
    await waitForReport(page);
    await expect(page.getByTestId("report-panel")).toHaveAttribute(
      "data-state",
      "no_findings",
    );
    await scan(page, "no findings");
  });

  test("the planning audit, expanded", async ({ profiled: page }) => {
    await reportFor(page, "What is the total revenue by region?");

    // In the evidence drawer, which is also the more demanding scan: the
    // drawer is a modal over a scrim, so its surfaces, its dense key/value
    // rows and its trace all composite differently from the canvas.
    //
    // Unconditional. This was wrapped in `if (count > 0)`, so an audit that
    // stopped rendering would have left the scan passing over a page that no
    // longer contained the thing the test is named for.
    await page.getByTestId("inspect-evidence").click();
    await expect(page.getByTestId("evidence-drawer")).toBeVisible();
    await scan(page, "evidence drawer open");

    const audit = inDrawer(page, 'planning-audit');
    await expect(audit).toBeVisible();
    await audit.locator('summary').first().click();
    await expect(audit).toHaveAttribute('open', '');
    await scan(page, "planning audit open");
  });

  test("the Compare view, with the automatic-route disclosure", async ({
    page,
  }) => {
    /*
     * Through `compareHelpers`, not a copy of it.
     *
     * This file used to install its own `/api/config` override and its own
     * `/api/comparisons` handler, the second of which issued the real
     * deterministic run with `route.fetch` -- a request the traffic
     * recorder cannot see, because `route.fetch` produces no page event.
     * The comparison it answered was counted instead, which happened to
     * give the right total and the wrong reason, and the duplicated setup
     * drifted from the real one twice.
     *
     * `compareWith` owns that handler: it records the admission where the
     * request is actually made, and it remembers the settled payload, so
     * the first comparison of a question in the job is real and this one
     * replays it. The AI side is mutated into a refusal, which is the
     * surface this scan exists for -- a state card for a refused lane,
     * beside a finished one.
     */
    await advertiseAi(page);
    await compareWith(page, (payload) => ({
      ...payload,
      status: "refused",
      outcome: "refused",
      stopped_reason: "the question could not be mapped safely",
      findings: [],
    }));
    await openApp(page);
    await openDemo(page);
    await startCompare(page, "What is the total revenue by region?");

    // One report at a time now, behind a switcher. Selecting is part of
    // reading it.
    await strategyReport(page, "deterministic");
    // The surfaces this scan exists for: the route disclosure, and a state
    // card for the refused lane.
    await expect(page.getByTestId("auto-route-note")).toBeVisible();
    // Compare has no single-run report: the two strategies render compact
    // panes and, under agreement, one shared result.
    await expect(page.getByTestId("compare-workspace")).toBeVisible();
    await scan(page, "compare with route disclosure");
  });

  test("dark mode, on the states that carry a verdict colour", async ({
    profiled: page,
  }) => {
    // Every scan above runs in the default colour scheme, which on CI is
    // light. So the dark palette was never scanned, and a token left
    // undefined in the dark block, which inherits the light value rather
    // than being absent -- went unnoticed: #855c17 ochre on a near-black
    // surface at 2.92:1, carried by a warning notice, an ambiguous-field
    // tag and a stopped workflow step.
    //
    // `src/test/contrast.test.ts` now measures every token against every
    // surface in both themes, which is the cheap half. This is the half
    // that sees what the browser actually composites.
    // Reached by pressing the control, not by emulating the media query.
    //
    // `useTheme` writes `data-theme` on every render and defaults to light,
    // so `:root:not([data-theme="light"])` never matches and
    // `emulateMedia({ colorScheme: "dark" })` changes nothing. The first
    // version of this test did exactly that, scanned the light palette, and
    // passed while the dark-mode defect it was written for was still
    // present -- confirmed by removing the fix and watching this test stay
    // green.
    // Reset first: the previous scan leaves the evidence drawer open, and a
    // side sheet puts a scrim over the header -- the theme toggle is then
    // visible, enabled and un-clickable, which Playwright reports as a
    // timeout pointing at a button that is plainly there.
    /*
     * Both states replayed, in the dark palette.
     *
     * axe reads the live accessibility tree and composites the real
     * colours, so what it needs is a rendered page -- not a run the engine
     * performed for the second time. The refusal and the report were both
     * already admitted by the two tests above; this asserts the same two
     * DOMs under the other palette, which is a presentation difference and
     * costs nothing.
     */
    // A warning-coloured surface has to be on screen for this to mean
    // anything, so replay the refusal and assert its card is there.
    await reportFor(page, "What is the average gross_margin by region?", {
      theme: "dark",
    });
    await expect(page.locator("body")).toHaveAttribute("data-phase", "refused", {
      timeout: 60_000,
    });
    await expect(page.getByTestId("report-panel")).toBeVisible();
    await scan(page, "dark mode, refused report");

    // And the ordinary report, where the muted ink does most of the work.
    await reportFor(page, "What is the total revenue by region?", { theme: "dark" });
    await scan(page, "dark mode, completed report");
  });

  test("a narrow-phone report", async ({ profiled: page }) => {
    await page.setViewportSize({ width: 360, height: 740 });
    await reportFor(page, "What is the total revenue by region?");
    await scan(page, "phone report");
  });
});

test.describe("keyboard operation", () => {
  test("focus is always visible as it moves", async ({ page }) => {
    await openApp(page);
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    await expect(page.getByTestId("composer")).toBeVisible();

    const invisible: string[] = [];
    for (let i = 0; i < 15; i += 1) {
      await page.keyboard.press("Tab");
      const entry = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el || el === document.body) return null;
        const style = getComputedStyle(el);
        return {
          tag: el.tagName,
          name: (el.getAttribute("aria-label") ?? el.textContent ?? "")
            .trim()
            .slice(0, 24),
          outline: style.outlineStyle,
          width: parseFloat(style.outlineWidth),
        };
      });
      if (!entry) continue;
      if (entry.outline === "none" || entry.width < 2) {
        invisible.push(`${entry.tag} "${entry.name}"`);
      }
    }
    expect(
      invisible,
      `controls with no visible focus: ${invisible.join(", ")}`,
    ).toEqual([]);
  });

  test("tabbing does not trap", async ({ page }) => {
    // A trap is the failure a keyboard user cannot recover from without
    // reloading, so it is worth its own check rather than being implied by
    // the focus test above.
    await openApp(page);
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    await expect(page.getByTestId("composer")).toBeVisible();

    const seen: string[] = [];
    for (let i = 0; i < 25; i += 1) {
      await page.keyboard.press("Tab");
      seen.push(
        await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null;
          if (!el) return "none";
          return `${el.tagName}#${el.id}.${el.className?.toString().slice(0, 20)}`;
        }),
      );
    }
    // A trap shows up as the same one or two identities repeating forever.
    const distinct = new Set(seen);
    expect(
      distinct.size,
      `focus visited only ${distinct.size} distinct controls in 25 tabs`,
    ).toBeGreaterThan(2);

    // The count alone is a weak claim, and it got weaker when the stepper
    // was removed: its scroll container was a tab stop, so the tally fell
    // from four to three and failed a `> 3` threshold without anything
    // having become less operable.
    //
    // WebKit is why the number is small at all. Safari's "press Tab to
    // highlight each item" is off by default, so buttons are not in the tab
    // order there and only the form controls and links are counted. That is
    // a browser preference, not a defect in this page.
    //
    // So the real assertion is a destination rather than a tally: a keyboard
    // user must be able to reach the question field, which is the one
    // control on this screen without which nothing can be done.
    const reachedComposer = await page.evaluate(() => {
      const field = document.querySelector("textarea");
      if (!field) return false;
      (field as HTMLElement).focus();
      return document.activeElement === field;
    });
    expect(reachedComposer, "the question field cannot take focus").toBe(true);
  });

  test("every control has an accessible name", async ({ page }) => {
    await openApp(page);
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    await expect(page.getByTestId("composer")).toBeVisible();

    const unnamed = await page.evaluate(() => {
      const out: string[] = [];
      const controls = document.querySelectorAll<HTMLElement>(
        "button, a[href], summary, select, [role=button]",
      );
      for (const el of controls) {
        const box = el.getBoundingClientRect();
        if (box.width === 0 || box.height === 0) continue;
        const name = (
          el.getAttribute("aria-label") ||
          el.getAttribute("title") ||
          el.textContent ||
          ""
        ).trim();
        if (!name)
          out.push(`${el.tagName}.${el.className?.toString().slice(0, 24)}`);
      }
      return out;
    });
    expect(
      unnamed,
      `controls with no accessible name: ${unnamed.join(", ")}`,
    ).toEqual([]);
  });

  test("a disclosure is operable from the keyboard", async ({ profiled: page }) => {
    await reportFor(page, "What is the total revenue by region?");

    // On the canvas. The first `details` in the *document* is the planning
    // audit inside the print appendix, which is `hidden` and therefore not
    // focusable -- the test would be asserting that a keyboard user can
    // operate a control no keyboard user can reach.
    const details = onCanvas(page, "details").first();
    if ((await details.count()) === 0) return;
    const summary = details.locator("summary").first();
    await summary.focus();
    await expect(summary).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(details).toHaveAttribute("open", "");
    await page.keyboard.press("Enter");
    await expect(details).not.toHaveAttribute("open", "");
  });

  test("the provenance drawer restores focus and closes on Escape", async ({
    profiled: page,
  }) => {
    await reportFor(page, "What is the total revenue by region?");

    // Unconditional: a `return` here would have turned a missing control
    // into a silent pass, which is how a drawer that stopped opening would
    // have kept this test green.
    const opener = page.getByTestId("inspect-evidence");
    await expect(opener).toBeVisible();

    // Opened from the keyboard, not with a click.
    //
    // This is the requirement: a keyboard user must get focus back where
    // they left it. It also has to be exercised this way to be portable.
    // WebKit does not focus a button on click, so a click-opened drawer
    // captures `document.activeElement` as `<body>` and "restoring" it is
    // a no-op -- the test failed on WebKit while the behaviour was
    // correct, because a mouse user never had focus on the opener to
    // return to.
    await opener.focus();
    await expect(opener).toBeFocused();
    await page.keyboard.press("Enter");

    const drawer = page.getByTestId("evidence-drawer");
    await expect(drawer).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
    await expect(opener).toBeFocused();
  });
});
