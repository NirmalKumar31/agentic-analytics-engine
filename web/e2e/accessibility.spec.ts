import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { ask, sampleCsv, uploadFile, waitForReport } from "./helpers";

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
 * recorded in LIMITATIONS with a follow-up -- because a silent exclusion
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
    await page.goto("/");
    await expect(
      page.getByRole("button", { name: /Commerce demo warehouse/ }),
    ).toBeVisible();
    await scan(page, "landing");
  });

  test("an uploaded dataset after profiling", async ({ page }) => {
    await page.goto("/");
    await uploadFile(page, "a11y-profile.csv", sampleCsv());
    // The inspector is a closed disclosure now, so assert on the disclosure
    // rather than a heading that no longer exists -- and scan it both
    // closed and open, because a `<details>` hides its body from axe while
    // collapsed and the table inside it is the part most likely to have a
    // contrast or header-association problem.
    const inspector = page.getByTestId("schema-inspector");
    await expect(inspector).toBeVisible();
    await scan(page, "profiled upload, inspector collapsed");
    await inspector.locator("summary").click();
    await expect(inspector).toHaveAttribute("open", "");
    await scan(page, "profiled upload, inspector open");
  });

  test("a completed deterministic report", async ({ page }) => {
    await page.goto("/");
    await uploadFile(page, "a11y-report.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);
    await scan(page, "completed report");
  });

  test("a refused report", async ({ page }) => {
    // A refusal is a state a visitor reaches, so it is a state that has to
    // be navigable. An unreadable explanation of why nothing was answered
    // is worse than an unreadable answer.
    await page.goto("/");
    await uploadFile(page, "a11y-refusal.csv", sampleCsv());
    await ask(page, "What is the average gross_margin by region?");
    await expect(page.locator("body")).toHaveAttribute(
      "data-phase",
      "refused",
      { timeout: 60_000 },
    );
    await scan(page, "refused report");
  });

  test("the planning audit, expanded", async ({ page }) => {
    await page.goto("/");
    await uploadFile(page, "a11y-audit.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    // Unconditional. This was wrapped in `if (count > 0)`, so an audit that
    // stopped rendering would have left the scan passing over a page that no
    // longer contained the thing the test is named for.
    const audit = page.getByTestId('planning-audit');
    await expect(audit).toBeVisible();
    await audit.locator('summary').first().click();
    await expect(audit).toHaveAttribute('open', '');
    await scan(page, "planning audit open");
  });

  test("the Compare view, with the automatic-route disclosure", async ({
    page,
  }) => {
    // AI mode is usually unavailable on a local or CI deployment, so the
    // capability is enabled and the right-hand run is stubbed. The left pane
    // is a real deterministic run, and the route disclosure derives from its
    // real event stream.
    await page.route("**/api/config", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.capabilities.modes = body.capabilities.modes.map(
        (mode: { mode: string }) =>
          mode.mode === "ai"
            ? { ...mode, available: true, reason: "", message: "" }
            : mode,
      );
      body.capabilities.compare_available = true;
      await route.fulfill({ response, json: body });
    });
    await page.route("**/api/comparisons", async (route) => {
      const request = route.request().postDataJSON() as {
        session_id: string;
        question: string;
      };
      const started = await route.fetch({
        url: new URL("/api/analyses", page.url()).toString(),
        method: "POST",
        postData: JSON.stringify({ ...request, mode: "deterministic" }),
        headers: { "content-type": "application/json" },
      });
      const { run_id } = (await started.json()) as { run_id: string };
      await route.fulfill({
        status: 202,
        json: {
          comparison_id: "cmp_a11y",
          session_id: request.session_id,
          question: request.question,
          deterministic_run_id: run_id,
          ai_run_id: "run_a11y_ai",
        },
      });
    });
    await page.route("**/api/analyses/run_a11y_ai", async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          run_id: "run_a11y_ai",
          session_id: "x",
          question: "What is total revenue by region?",
          status: "refused",
          outcome: "refused",
          created_at: Date.now() / 1000,
          mode: "ai",
          provider_kind: "cloud",
          stopped_reason: "the question could not be mapped safely",
          findings: [],
          rejected: [],
          charts: [],
          results: {},
          tasks: [],
          events: [],
          mcp_trace: [],
        },
      });
    });

    await page.goto("/");
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    await page
      .getByRole("radio", { name: /^Compare planning strategies/ })
      .click();
    await page
      .getByLabel("Business question")
      .fill("What is total revenue by region?");
    await page.getByRole("button", { name: /Compare strategies/ }).click();

    await expect(
      page.getByRole("region", { name: "Deterministic Analytics", exact: true }),
    ).toBeVisible({ timeout: 90_000 });
    // The surfaces this scan exists for: the route disclosure, and a state
    // card for the refused lane.
    await expect(page.getByTestId("auto-route-note")).toBeVisible();
    await expect(page.getByTestId("run-state-card").first()).toBeVisible();
    await scan(page, "compare with route disclosure");
  });

  test("dark mode, on the states that carry a verdict colour", async ({
    page,
  }) => {
    // Every scan above runs in the default colour scheme, which on CI is
    // light. So the dark palette was never scanned, and a token left
    // undefined in the dark block -- which inherits the light value rather
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
    await page.goto("/");
    await page.getByRole("button", { name: /Switch to dark theme/i }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await uploadFile(page, "a11y-dark.csv", sampleCsv());

    // A warning-coloured surface has to be on screen for this to mean
    // anything, so drive a refusal and assert its card is there.
    await ask(page, "What is the average gross_margin by region?");
    await expect(page.locator("body")).toHaveAttribute("data-phase", "refused", {
      timeout: 60_000,
    });
    await expect(page.getByTestId("run-state-card")).toBeVisible();
    await scan(page, "dark mode, refused report");

    // And the ordinary report, where the muted ink does most of the work.
    await page.getByRole("button", { name: "Start over" }).click();
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);
    await scan(page, "dark mode, completed report");
  });

  test("a narrow-phone report", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 });
    await page.goto("/");
    await uploadFile(page, "a11y-phone.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);
    await scan(page, "phone report");
  });
});

test.describe("keyboard operation", () => {
  test("focus is always visible as it moves", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    await expect(page.getByRole("heading", { name: "Ask" })).toBeVisible();

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
    await page.goto("/");
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    await expect(page.getByRole("heading", { name: "Ask" })).toBeVisible();

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
    // Focus must reach more than a couple of distinct places. A trap shows
    // up as the same one or two identities repeating forever.
    const distinct = new Set(seen);
    expect(
      distinct.size,
      `focus visited only ${distinct.size} distinct controls in 25 tabs`,
    ).toBeGreaterThan(3);
  });

  test("every control has an accessible name", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    await expect(page.getByRole("heading", { name: "Ask" })).toBeVisible();

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

  test("a disclosure is operable from the keyboard", async ({ page }) => {
    await page.goto("/");
    await uploadFile(page, "a11y-kbd.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    const details = page.locator("details").first();
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
    page,
  }) => {
    await page.goto("/");
    await uploadFile(page, "a11y-drawer.csv", sampleCsv());
    await ask(page, "What is the total revenue by region?");
    await waitForReport(page);

    const opener = page.getByRole("button", { name: /show work/i }).first();
    if ((await opener.count()) === 0) return;

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

    const drawer = page.locator(".drawer").first();
    await expect(drawer).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(opener).toBeFocused();
  });
});
