import { type Page } from "@playwright/test";

import { closeAnySheet, expect, test as base } from "./fixtures";

import { endSession, openApp, sampleCsv, uploadFile } from "./helpers";
import {
  advertiseAi,
  compareWith,
  openDemo,
  settle,
  startCompare,
} from "./compareHelpers";

/**
 * Compare, over real runs.
 *
 * Six scenarios, because each breaks something different: the demo
 * warehouse and an upload take different paths through the engine, and
 * agreement, a differing contract, a differing output and a refused side
 * are four different things the workspace has to say without inviting the
 * reader to pick a number.
 *
 * **The left side is always a real run.** AI mode is unavailable on a local
 * or CI deployment, so `compareWith` starts a genuine deterministic
 * analysis, mints an id for the right side, and answers that id with the
 * real payload passed through `mutate`. The two sides therefore differ only
 * in the field a scenario is about, and the agreement case is agreement
 * between two copies of a real engine result rather than between two
 * fixtures written to match.
 *
 * Nothing here contacts a provider: the suite refuses to start unless
 * `/api/health` reports `provider_mode=fake`.
 */

/**
 * One Compare-capable session for the whole file.
 *
 * Compare needs two things an ordinary session does not: `/api/config`
 * answered with AI advertised, which has to be routed *before* the app
 * loads its config, and an uploaded dataset for the tests that are about
 * upload semantics. Both are worker-scoped here, so the eight uploads this
 * file used to make are one.
 *
 * `compareWith` stays per test: it closes over the run it mirrors and the
 * payload it returns, which is the thing each scenario varies. Each test
 * installs it and `afterEach` takes it off again, because a route left
 * behind on a shared page answers the next test's comparison with the
 * previous test's result.
 */
const test = base.extend<object, { comparable: Page }>({
  comparable: [
    async ({ browser }, use) => {
      const project = base.info().project.name;
      const context = await browser.newContext();
      const page = await context.newPage();
      await advertiseAi(page);
      await openApp(page);
      await uploadFile(page, "compare.csv", sampleCsv());
      await use(page);
      await endSession(page, project);
      try {
        await context.close();
      } catch {
        // Disposed with the worker's browser; the close above is the one
        // that returns the session to the server.
      }
    },
    { scope: "worker" },
  ],
});

/** Back to the composer, with no route left over from the last scenario. */
async function resetCompare(page: Page): Promise<void> {
  await closeAnySheet(page);
  await page.unroute("**/api/comparisons");
  await page.unroute(`**/api/analyses/**`);
  const startOver = page.getByRole("button", { name: "Start over" });
  if ((await startOver.count()) > 0) {
    await startOver.first().click({ timeout: 15_000 });
  }
  await expect(page.getByTestId("composer")).toBeVisible({ timeout: 20_000 });
}

test.describe("Compare over the demo warehouse", () => {
  test("converges on one shared answer when the strategies agree", async ({
    page,
  }) => {
    await advertiseAi(page);
    await compareWith(page);
    await openApp(page);
    await openDemo(page);
    await startCompare(page, "What is the total revenue by region?");

    const workspace = page.getByTestId("compare-workspace");

    // Both sides are the same engine over the same dataset; the comparison
    // must say so rather than leaving a reader to guess that a model
    // produced the numbers.
    await expect(workspace).toContainText(/same analytics engine/i);
    await expect(workspace).toContainText(/never calculates a result/i);
    await expect(workspace).toContainText(/are not ranked/i);
  });

  test("states contract, coverage and output equality independently", async ({
    page,
  }) => {
    // Three separate facts. Collapsing them into one "they agree" hides
    // the case where two identical contracts covered the question
    // differently, which is a real outcome the verdict distinguishes.
    await advertiseAi(page);
    await compareWith(page);
    await openApp(page);
    await openDemo(page);
    await startCompare(page, "What is the total revenue by region?");

    const facts = page.getByTestId("contract-comparison");
    await expect(facts).toBeVisible();
    for (const term of ["contracts", "coverage", "output"]) {
      await expect(facts).toContainText(term);
    }
    // And the recorded route, which is a policy decision about which
    // strategy runs -- never presented as a third strategy.
    await expect(facts).toContainText(/recorded route/i);
    expect(
      await page.getByTestId("pane-status").count(),
      "a third execution lane appeared",
    ).toBe(2);
  });
});

test.describe("Compare over an uploaded dataset", () => {
  test.beforeEach(async ({ comparable }) => {
    await resetCompare(comparable);
  });
  test.afterEach(async ({ comparable }) => {
    await resetCompare(comparable);
  });

  test("shows one answer, one chart and one table when they agree", async ({
    comparable: page,
  }) => {
    await compareWith(page, undefined, "compare.csv");
    await startCompare(page, "What is the total revenue by region?");

    const shared = page.getByTestId("shared-result");
    await expect(shared).toBeVisible();

    // Rendered once. Two identical tables and two identical charts read as
    // two independent confirmations of the same number.
    expect(await page.getByTestId("compare-report").count()).toBe(1);
    expect(await page.locator(".compare-pane").count()).toBe(0);
    expect(await page.locator(".chart-host").count()).toBeLessThanOrEqual(1);

    // And the reclaimed space says what agreement was measured over.
    await expect(page.getByTestId("agreement-basis")).toBeVisible();
    await expect(page.getByTestId("agreement-basis")).toContainText(
      /accepted contract/i,
    );
  });

  test("puts the structured difference before either detailed report", async ({
    comparable: page,
  }) => {
    // The same question, planned two ways: one groups by region, the other
    // by region then channel.
    await compareWith(page, (payload) => {
      const contract = payload.query_contract as Record<string, unknown>;
      const changed = {
        ...contract,
        dimensions: ["region", "channel"],
        canonical_contract: {
          ...((contract?.canonical_contract as Record<string, unknown>) ??
            contract),
          dimensions: ["region", "channel"],
        },
      };
      return { ...payload, query_contract: changed };
    }, "compare.csv");
    await startCompare(page, "What is the total revenue by region?");

    const divergence = page.getByTestId("divergence");
    await expect(divergence).toBeVisible();
    await expect(divergence).toContainText(
      /neither result is presented as the answer/i,
    );
    await expect(page.getByTestId("shared-result")).toHaveCount(0);

    const diff = page.getByTestId("contract-diff");
    await expect(diff).toBeVisible();
    await expect(diff).toContainText(/channel/);

    // The difference precedes both detailed reports in the document: it is
    // what a reader opened Compare for.
    const order = await page.evaluate(() => {
      const diffEl = document.querySelector('[data-testid="contract-diff"]');
      const pane = document.querySelector(".compare-pane");
      if (!diffEl || !pane) return null;
      return Boolean(
        diffEl.compareDocumentPosition(pane) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      );
    });
    expect(order, "the difference must come before the two reports").toBe(true);
  });

  test("keeps two results when the outputs differ", async ({ comparable: page }) => {
    // Identical contracts, different numbers. That is an engine defect and
    // the page must say so rather than invite the reader to pick one.
    await compareWith(page, (payload) => {
      const findings = (payload.findings as Array<Record<string, unknown>>) ?? [];
      return {
        ...payload,
        findings: findings.map((finding) => ({
          ...finding,
          text: `${String(finding.text)} (adjusted)`,
        })),
      };
    }, "compare.csv");
    await startCompare(page, "What is the total revenue by region?");
    // Settled first. The claim is about two *finished* runs that disagree,
    // and both assertions below are also true of a run still in flight --
    // so without this the test could pass before the AI side had answered,
    // and ended while a poll was still being served.
    await settle(page);

    await expect(page.getByTestId("shared-result")).toHaveCount(0);
    expect(await page.locator(".compare-pane").count()).toBe(2);
  });

  test("keeps the finished side when the other refuses", async ({ comparable: page }) => {
    // A result on hand is not withheld because its counterpart is missing.
    await compareWith(page, (payload) => ({
      ...payload,
      status: "refused",
      outcome: "refused",
      stopped_reason: "the question could not be mapped safely",
      findings: [],
    }), "compare.csv");
    await startCompare(page, "What is the total revenue by region?");

    // The refusal's reason is on screen, not only in the trace.
    await expect(page.getByTestId("run-state-card").first()).toContainText(
      /could not be mapped safely/i,
    );

    // Each side's own status, machine-readable and distinct.
    const states = await page
      .getByTestId("pane-status")
      .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-state")));
    expect(new Set(states).size, `both sides read ${states.join(" / ")}`).toBe(2);

    // And the side that finished still shows what it found.
    expect(await page.locator(".compare-pane").count()).toBe(2);
  });
});

test.describe("the Compare evidence drawer", () => {
  test.beforeEach(async ({ comparable }) => {
    await resetCompare(comparable);
    await compareWith(comparable, undefined, "compare.csv");
    await startCompare(comparable, "What is the total revenue by region?");
    await settle(comparable);
  });
  test.afterEach(async ({ comparable }) => {
    await resetCompare(comparable);
  });

  test("is one control, with a tab per strategy", async ({ comparable: page }) => {
    // Two persistent evidence buttons implied two destinations and made
    // the reader pick a side before reading anything.
    await expect(page.getByTestId("inspect-both-traces")).toHaveCount(1);
    expect(
      await page.getByRole("button", { name: /Evidence ·/ }).count(),
      "a per-strategy evidence button is still present",
    ).toBe(0);

    await page.getByTestId("inspect-both-traces").click();
    const drawer = page.getByTestId("compare-evidence-drawer");
    await expect(drawer).toBeVisible();
    const tabs = drawer.getByRole("tab");
    await expect(tabs).toHaveCount(2);
    await expect(tabs.nth(0)).toHaveText(/Deterministic/);
    await expect(tabs.nth(1)).toHaveText(/AI/);
  });

  test("switching tabs changes the panel and issues no request", async ({
    comparable: page,
  }) => {
    const requests: string[] = [];
    page.on("request", (request) => {
      // Only requests the *page* makes. `page.request` calls have no frame,
      // and this file's own AI-mirroring route handler uses one to read the
      // real deterministic run -- counting the harness's fetch as the
      // application's would fail a claim about the application.
      if (request.frame() === null) return;
      if (request.url().includes("/api/")) requests.push(request.url());
    });

    await page.getByTestId("inspect-both-traces").click();
    const drawer = page.getByTestId("compare-evidence-drawer");
    await expect(drawer).toBeVisible();

    const panel = page.getByTestId("evidence-panel");
    const first = await panel.getAttribute("data-strategy");

    const before = requests.length;
    await drawer.getByRole("tab").nth(1).click();
    await expect(panel).not.toHaveAttribute("data-strategy", first ?? "");

    // Both payloads are already in memory. An evidence view that can fail
    // is not evidence.
    expect(
      requests.slice(before),
      "switching tabs issued a request",
    ).toEqual([]);
  });

  test("the tablist is operable with the arrow keys", async ({ comparable: page }) => {
    await page.getByTestId("inspect-both-traces").click();
    const drawer = page.getByTestId("compare-evidence-drawer");
    const tabs = drawer.getByRole("tab");
    await tabs.nth(0).focus();
    await page.keyboard.press("ArrowRight");
    await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("ArrowLeft");
    await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
  });

  test("both traces carry the full evidence record", async ({ comparable: page }) => {
    await page.getByTestId("inspect-both-traces").click();
    const drawer = page.getByTestId("compare-evidence-drawer");

    for (const index of [0, 1]) {
      await drawer.getByRole("tab").nth(index).click();
      const panel = page.getByTestId("evidence-panel");
      for (const section of [
        "Route",
        "Accepted contract",
        "Coverage",
        "Verification",
        "Cited cells",
        "Timings",
        "Build",
      ]) {
        await expect(
          panel,
          `${section} missing from trace ${index}`,
        ).toContainText(section);
      }
    }
  });
});
