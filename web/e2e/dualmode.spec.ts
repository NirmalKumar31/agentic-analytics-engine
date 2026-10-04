import { expect, test } from "@playwright/test";
import { openApp } from "./helpers";

/**
 * Dual-mode behaviour in a real browser.
 *
 * No test here depends on live paid inference. AI runs are exercised by
 * intercepting the API, so the browser contract is covered without spending
 * anything and without a credential being present.
 */

async function openDemo(page: import("@playwright/test").Page) {
  await openApp(page);
  await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
  await expect(
    page.getByRole("radiogroup", { name: /analysis mode/i }),
  ).toBeVisible();
}

test.describe("choosing a mode", () => {
  test("Governed Analysis is the default and deterministic remains selectable", async ({ page }) => {
    await openDemo(page);
    await expect(
      page.getByRole("radio", { name: /^Governed Analysis/ }),
    ).toBeChecked();
    await expect(page.getByRole("radio", { name: /^Deterministic Analytics/ })).toBeEnabled();
  });

  test("AI and Compare are disabled, with a stated reason, when AI is off", async ({
    page,
  }) => {
    // The test must not inherit a developer's local cloud configuration.
    // It exercises the unavailable contract, so make that server response
    // explicit rather than relying on the process environment.
    await page.route("**/api/config", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.capabilities.modes = body.capabilities.modes.map(
        (mode: { mode: string }) =>
          mode.mode === "ai"
            ? {
                ...mode,
                available: false,
                reason: "ai_disabled",
                message: "AI Analytics is turned off for this deployment.",
              }
            : mode,
      );
      body.capabilities.compare_available = false;
      await route.fulfill({ response, json: body });
    });
    await openDemo(page);
    const ai = page.getByRole("radio", { name: /^AI Analytics/ });
    await expect(ai).toBeDisabled();
    await expect(
      page.getByRole("radio", { name: /^Compare planning strategies/ }),
    ).toBeDisabled();
    // A reason a visitor can read, not just a greyed control.
    await expect(
      page.getByText(/AI Analytics is (turned off|not configured)/i).first(),
    ).toBeVisible();
  });

  test("the selector is reachable and operable by keyboard", async ({
    page,
  }) => {
    await openDemo(page);
    const deterministic = page.getByRole("radio", {
      name: /^Deterministic Analytics/,
    });
    await deterministic.focus();
    await expect(deterministic).toBeFocused();
  });

  test("no public copy calls deterministic mode fake", async ({ page }) => {
    await openDemo(page);
    const text = (
      (await page.locator("body").textContent()) ?? ""
    ).toLowerCase();
    expect(text).not.toContain("fake");
    expect(text).toContain("deterministic analytics");
  });
});

test.describe("a deterministic run", () => {
  test("completes and reports findings", async ({ page }) => {
    await openDemo(page);
    await page.getByLabel("Business question").fill("What is total revenue?");
    await page.getByRole("button", { name: /Run analysis/ }).click();
    // The answer itself, not the findings heading: that panel is hidden
    // when the answer is the only publication, because its whole content
    // was a sentence restating that fact.
    await expect(page.getByTestId("direct-answer")).toBeVisible({
      timeout: 60_000,
    });
  });
});

test.describe("AI and Compare, with the API intercepted", () => {
  test("Compare Both shows two independent panes", async ({ page }) => {
    // Advertise both modes so the selector offers Compare.
    await page.route("**/api/config", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.capabilities.modes = body.capabilities.modes.map(
        (m: { mode: string }) =>
          m.mode === "ai"
            ? { ...m, available: true, reason: "", message: "" }
            : m,
      );
      body.capabilities.compare_available = true;
      body.capabilities.ai_limits = {
        runs_per_session: 3,
        max_model_calls_per_run: 24,
        max_runtime_seconds: 180,
      };
      await route.fulfill({ response, json: body });
    });

    let aiRunId = "";
    await page.route("**/api/comparisons", async (route) => {
      // Start a real deterministic run so the left pane is genuine, and
      // mint an id for the right one that the stub below answers.
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
      aiRunId = "run_stubbed_ai";
      await route.fulfill({
        status: 202,
        json: {
          comparison_id: "cmp_stub",
          session_id: request.session_id,
          question: request.question,
          deterministic_run_id: run_id,
          ai_run_id: aiRunId,
        },
      });
    });

    await page.route("**/api/analyses/run_stubbed_ai", async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          run_id: "run_stubbed_ai",
          session_id: "x",
          question: "What is total revenue?",
          status: "failed",
          created_at: Date.now() / 1000,
          mode: "ai",
          provider_kind: "cloud",
          error: "AI Analytics has reached its public demo usage limit.",
          findings: [],
          rejected: [],
          charts: [],
          results: {},
          events: [],
          mcp_trace: [],
        },
      });
    });

    await openDemo(page);
    await page.getByRole("radio", { name: /^Compare planning strategies/ }).click();
    await page.getByLabel("Business question").fill("What is total revenue?");
    await page.getByRole("button", { name: /Compare strategies/ }).click();

    // Two labelled panes.
    await expect(
      page.getByRole("region", {
        name: "Deterministic Analytics",
        exact: true,
      }),
    ).toBeVisible({
      timeout: 60_000,
    });
    await expect(
      page.getByRole("region", { name: "AI Analytics", exact: true }),
    ).toBeVisible();

    // The AI side failed; the deterministic side still produced a report.
    //
    // Scoped to the AI pane's state card. An unscoped `getByRole("alert")`
    // matched both this card and the contract-comparison notice, which also
    // carries `role="alert"` when the two interpretations could not be
    // compared -- a strict-mode violation that appeared on Firefox and not
    // on Chromium, because the two engines differ on when the comparison
    // becomes computable. Naming the element is both stable and a stronger
    // claim than "some alert somewhere says this".
    await expect(
      page
        .getByRole("region", { name: "AI Analytics", exact: true })
        .getByTestId("run-state-card"),
    ).toContainText(/public demo usage limit/i);
    await expect(
      page
        .getByRole("region", { name: "Deterministic Analytics", exact: true })
        .getByTestId("direct-answer"),
    ).toBeVisible({ timeout: 60_000 });

    // No winner, anywhere.
    const text = (
      (await page.locator("body").textContent()) ?? ""
    ).toLowerCase();
    for (const banned of ["winner", "more accurate"]) {
      expect(text).not.toContain(banned);
    }
  });

  test("a refused AI pane shows its refusal, not a Complete badge", async ({
    page,
  }) => {
    // The production failure, in a browser. An AI run with status
    // `refused` wore a COMPLETE badge and rendered an empty pane beside a
    // deterministic answer that had worked. The jsdom tests render
    // `ComparisonView` with children already supplied, so they never
    // exercised the decision about whether to supply them.
    await page.route("**/api/config", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.capabilities.modes = body.capabilities.modes.map(
        (m: { mode: string }) =>
          m.mode === "ai"
            ? { ...m, available: true, reason: "", message: "" }
            : m,
      );
      body.capabilities.compare_available = true;
      body.capabilities.ai_limits = {
        runs_per_session: 3,
        max_model_calls_per_run: 24,
        max_runtime_seconds: 180,
      };
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
          comparison_id: "cmp_refused",
          session_id: request.session_id,
          question: request.question,
          deterministic_run_id: run_id,
          ai_run_id: "run_refused_ai",
        },
      });
    });

    await page.route("**/api/analyses/run_refused_ai", async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          run_id: "run_refused_ai",
          session_id: "x",
          question: "What is total revenue?",
          status: "refused",
          created_at: Date.now() / 1000,
          mode: "ai",
          provider_kind: "cloud",
          findings: [],
          rejected: [],
          charts: [],
          results: {},
          events: [],
          mcp_trace: [],
          report: null,
          stopped_reason:
            "the question could not be mapped safely: the AI plan named a grouping this engine does not offer",
          query_contract: null,
        },
      });
    });

    await openDemo(page);
    await page.getByRole("radio", { name: /^Compare planning strategies/ }).click();
    await page.getByLabel("Business question").fill("What is total revenue?");
    await page.getByRole("button", { name: /Compare strategies/ }).click();

    const ai = page.getByRole("region", { name: "AI Analytics", exact: true });
    await expect(ai).toBeVisible({ timeout: 60_000 });

    // The status moved from a pane header into the row of the table that
    // compares the two strategies. The claim is unchanged -- the AI side's
    // own outcome, attributable to that side and machine-readable -- and
    // the row is scoped by its header, which is the strategy's name.
    const aiRow = page
      .getByTestId("compare-routes")
      .getByRole("row")
      .filter({ has: page.getByRole("rowheader", { name: "AI Analytics" }) });
    await expect(aiRow.getByTestId("pane-status")).toHaveText(/Refused/);
    await expect(aiRow.getByTestId("pane-status")).toHaveAttribute(
      "data-state",
      "refused",
    );

    const card = ai.getByTestId("run-state-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText(/could not be mapped safely/i);
    await expect(ai.getByTestId("pane-placeholder")).toHaveCount(0);

    await expect(
      page
        .getByRole("region", { name: "Deterministic Analytics", exact: true })
        .getByTestId("direct-answer"),
    ).toBeVisible({ timeout: 60_000 });

    await expect(ai.getByText(/\bComplete\b/)).toHaveCount(0);
  });

  test("an AI run refused by quota is reported without leaking anything", async ({
    page,
  }) => {
    await page.route("**/api/config", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.capabilities.modes = body.capabilities.modes.map(
        (m: { mode: string }) =>
          m.mode === "ai"
            ? { ...m, available: true, reason: "", message: "" }
            : m,
      );
      await route.fulfill({ response, json: body });
    });
    await page.route("**/api/analyses", async (route) => {
      await route.fulfill({
        status: 429,
        json: {
          detail:
            "AI mode has reached its public demo usage limit. Deterministic Analytics is still available.",
        },
      });
    });

    await openDemo(page);
    await page.getByRole("radio", { name: /^AI Analytics/ }).click();
    await page.getByLabel("Business question").fill("What is total revenue?");
    await page.getByRole("button", { name: /Run with AI/ }).click();

    await expect(page.getByText(/public demo usage limit/i)).toBeVisible();
    const text = (await page.locator("body").textContent()) ?? "";
    for (const leak of ["sk-", "redis://", "Traceback"]) {
      expect(text).not.toContain(leak);
    }
  });

  /*
   * "Show work resolves evidence from the pane the visitor clicked" stood
   * here and is retired, because the thing it guarded cannot happen now.
   *
   * It existed because each pane had its own "Show work" button, both runs
   * mint finding ids within themselves, and `f1` on the AI side is a
   * different claim from `f1` on the deterministic side -- so the app had
   * to carry *which side* alongside the id, and once did not.
   *
   * There is one evidence control for the comparison, and the drawer has a
   * tab per strategy. Which trace is shown is the tab, not a resolution
   * step that can be wrong. `compare.spec.ts` asserts that directly and
   * more strongly than this did: the panel is labelled with the strategy
   * (`data-strategy`), switching tabs changes it, both tabs carry the full
   * evidence record, and the switch issues no request.
   */

});
