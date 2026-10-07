import AxeBuilder from "@axe-core/playwright";
import { type Page, type Request } from "@playwright/test";

import { expect, test } from "./fixtures";

import { ask, closeCallCsv, endSession, inDrawer, openApp, suggestedQuestions, uploadFile, waitForReport, watchTraffic } from "./helpers";

/**
 * Settling a role in a real browser.
 *
 * The jsdom suite proves the component's logic. What it cannot prove is
 * that a keyboard reaches the control, that focus lands on something that
 * can actually take it, that the announcement is in the accessible tree,
 * or that the thing fits on a phone. Each of those failed at least once
 * during development in a way jsdom reported as green -- the focus
 * restoration was focusing a disabled button, which jsdom and every
 * browser both treat as a no-op.
 *
 * Uploads are the scarce resource: the server holds a limited pool of
 * upload sessions and an exhausted pool surfaces as a skip, which reads as
 * a pass. So related assertions share one profiled dataset through a
 * serial describe, which is this repository's existing convention, and
 * every group below costs exactly one upload.
 */



/** Hosts that would mean real money. Never contacted, and asserted so. */
const PROVIDER_HOSTS = [
  "api.openai.com",
  "api.anthropic.com",
  "openai.azure.com",
  "generativelanguage.googleapis.com",
  "api.cohere.ai",
  "api.mistral.ai",
  "bedrock-runtime",
];

/**
 * Record every request the page makes, so a test can assert what was not
 * contacted. `/api/health` establishing fake mode is necessary but not
 * sufficient: it says what the server would do, not what was done.
 */
function watchRequests(page: Page): { offOrigin: string[]; provider: string[] } {
  const seen = { offOrigin: [] as string[], provider: [] as string[] };
  const origin = new URL(page.url() || "http://127.0.0.1").origin;
  page.on("request", (request: Request) => {
    const url = request.url();
    if (PROVIDER_HOSTS.some((host) => url.includes(host))) seen.provider.push(url);
    if (!url.startsWith(origin) && !url.startsWith("data:") && !url.startsWith("blob:")) {
      seen.offOrigin.push(url);
    }
  });
  return seen;
}

/**
 * Open the schema side sheet, asserting the schema was not already in the
 * reader's way.
 *
 * The inspector used to be a `<details>` resident on the canvas and this
 * helper opened the disclosure. It is a side sheet now, reached from the
 * dataset context strip, so the gate moved up a level: what is asserted is
 * the same claim -- a reader is told the ambiguity *count* without opening
 * anything, and the field-by-field detail is behind one control.
 *
 * Idempotent, because the sheet persists across the tests in a serial
 * describe that share a page.
 */
async function openInspector(page: Page) {
  const inspector = page.getByTestId("schema-inspector");
  if ((await inspector.count()) === 0) {
    await page.getByTestId("inspect-schema").click();
  }
  await expect(inspector).toBeVisible();
  // Opened expanded: the reader already asked by pressing the control.
  await expect(inspector).toHaveAttribute("open", "");
  return inspector;
}

/**
 * Close it again.
 *
 * The sheet is modal. It has a scrim, and focus is contained, so the
 * page behind it cannot be used until it is closed. That is the real
 * sequence a reader follows: settle the column, close the schema, ask the
 * question. Idempotent, so a test can declare what it needs without
 * tracking what the previous one left behind.
 */
async function closeInspector(page: Page) {
  if ((await page.getByTestId("schema-sheet").count()) === 0) return;
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("schema-sheet")).toHaveCount(0);
}

async function confirmButton(page: Page) {
  return page
    .getByTestId("role-confirmation")
    .getByRole("button", { name: /Confirm for this session/ });
}

// --------------------------------------------------------------- lifecycle

test.describe("settling a close call", () => {
  test.describe.configure({ mode: "serial" });
  let page: Page;
  let seen: { offOrigin: string[]; provider: string[] };

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    watchTraffic(page);
    await openApp(page);
    seen = watchRequests(page);
    await uploadFile(page, "clinical.csv", closeCallCsv());
  });

  test.afterAll(async () => {
    // The session, not just the page. Closing a browser context does not
    // free a server-side upload session -- the server holds it until the
    // capability deletes it or the TTL expires, and the TTL outlives a CI
    // run. Three engines leaving their sessions behind is what exhausted
    // the 24-session pool.
    await endSession(page);
    await page.close();
  });

  test("the schema stays out of the way until it is asked for", async () => {
    const inspector = await openInspector(page);
    await expect(inspector).toContainText(/role the data cannot settle/);

    // The engine marked `reading`, so the interface must mark `reading`.
    const marked = inspector.locator('tr[data-ambiguous="true"]');
    await expect(marked.first()).toContainText("reading");
    await expect(inspector.getByTestId("ambiguous-field").first()).toBeVisible();

    // And it says the choice can be settled here, because it can.
    await expect(inspector).toContainText(/settle it for this session/i);
    await expect(inspector).toContainText(/not a governed definition/i);
  });

  test("what it offers to ask does not mention the unsettled column", async () => {
    await closeInspector(page);
    // Recorded before any confirmation, so the next test can prove the
    // confirmation is what changed it.
    const offered = await suggestedQuestions(page).allInnerTexts();
    expect(offered.join(" ")).not.toMatch(/reading/);
  });

  test("selecting a reading does not submit it", async () => {
    await openInspector(page);
    const control = page.getByTestId("role-confirmation");
    const category = control.getByRole("radio", { name: /Category/ });

    // By keyboard, because a control only a pointer can work is not
    // operable.
    await category.focus();
    await expect(category).toBeFocused();
    await page.keyboard.press("Space");
    await expect(category).toBeChecked();

    // Choosing is considering. Nothing has been claimed yet.
    await expect(page.getByTestId("role-confirmed")).toHaveCount(0);
    await expect(control).toBeVisible();
  });

  test("confirming calls the backend, and the request says what it means", async () => {
    await openInspector(page);
    const control = page.getByTestId("role-confirmation");
    const button = await confirmButton(page);

    const [request] = await Promise.all([
      page.waitForRequest(
        (r) => r.url().includes("/schema/roles") && r.method() === "PATCH",
      ),
      button.press("Enter"),
    ]);

    const body = request.postDataJSON() as {
      expected_revision: number;
      changes: Array<{ column: string; action: string; role?: string }>;
    };
    expect(body.expected_revision).toBe(0);
    expect(body.changes).toEqual([
      { column: "reading", action: "confirm", role: "dimension" },
    ]);

    await expect(page.getByTestId("role-confirmed")).toBeVisible();
    await expect(control).toHaveCount(0);
  });

  test("the outcome is announced through a live region", async () => {
    const status = page.getByTestId("role-confirmed").getByRole("status");
    await expect(status).toContainText(/reading is confirmed for this session/);
    // Polite, so it does not interrupt whatever a screen reader is saying.
    await expect(status).toHaveAttribute("aria-live", "polite");
  });

  test("focus is on the replacement, and the replacement is enabled", async () => {
    // The first implementation focused it while still disabled, which is a
    // no-op in every browser. Focused AND enabled is the whole claim.
    const reset = page
      .getByTestId("role-confirmed")
      .getByRole("button", { name: /Reset to inferred/ });
    await expect(reset).toBeEnabled();
    await expect(reset).toBeFocused();
  });

  test("both readings are shown: what was inferred and what is used", async () => {
    const card = page.getByTestId("role-confirmed");
    await expect(card).toContainText(/Confirmed for this session/);
    await expect(card).toContainText(/Category/);
    await expect(card).toContainText(/inferred/i);
    await expect(card).toContainText(/Quantity/);
  });

  test("the row now reads as a category, settled rather than open", async () => {
    await openInspector(page);
    // The row is where the effective role is published. What the page
    // offers to ask is asserted separately, below.
    const row = page.locator("tr", { has: page.getByText("reading", { exact: true }) });
    await expect(row.locator(".tag.role-dimension")).toBeVisible();
    await expect(row.locator(".tag.role-measure")).toHaveCount(0);
    // And it is no longer presented as an open question.
    await expect(row.getByTestId("ambiguous-field")).toContainText("confirmed");
    await expect(row.getByTestId("ambiguous-field")).not.toContainText("close call");
  });

  test("a suggested question now names the confirmed category", async () => {
    await closeInspector(page);
    // The defect this covers: `dimensions.find(usable)` took the first
    // dimension the schema listed, so `site` won and the column the reader
    // had just settled went unmentioned everywhere they were looking.
    const offered = await suggestedQuestions(page).allInnerTexts();
    expect(offered.join(" "), "no offered question names the confirmed column").toMatch(
      /reading/,
    );
    // `dose` is a measure the engine will not vouch for either way, so the
    // honest offer over this dataset is a row count rather than a total.
    expect(offered.join(" ")).toMatch(/How many rows by reading\?/);
  });

  test("clicking it puts the question in the composer", async () => {
    await closeInspector(page);
    const button = suggestedQuestions(page).filter({ hasText: /reading/ }).first();
    // The question, not the whole button: a suggestion now carries an
    // intent label above its sentence ("Count" / "How many rows by
    // reading?"), and `innerText` would include it.
    const text = (await button.locator(".suggestion-text").innerText()).trim();
    await button.click();
    await expect(page.getByLabel("Business question")).toHaveValue(text);
  });

  test("a reset restores the engine's own reading", async () => {
    await openInspector(page);
    const settled = page.getByTestId("role-confirmed");
    await settled.getByRole("button", { name: /Reset to inferred/ }).click();

    await expect(page.getByTestId("role-confirmation")).toBeVisible();
    await expect(page.getByRole("status").first()).toContainText(
      /reading is back to the role the engine inferred/,
    );

    // All the way back: the role the engine inferred, marked open again.
    const row = page.locator("tr", { has: page.getByText("reading", { exact: true }) });
    await expect(row.locator(".tag.role-measure")).toBeVisible();
    await expect(row.getByTestId("ambiguous-field")).toContainText("close call");
    await expect(page.getByTestId("schema-inspector")).toContainText(
      /role the data cannot settle/,
    );

    // And the offered questions stop prioritising it: `reading` is a
    // measure again, so it leaves the dimension list and `site` is first.
    const offered = (await suggestedQuestions(page).allInnerTexts()).join(" ");
    expect(offered).not.toMatch(/by reading/);
    expect(offered).toMatch(/by site/);
  });

  test("confirming again brings the question back, and running it groups by the column", async () => {
    // Ordered last in this group deliberately. Running an analysis replaces
    // the composer with the report workspace, so the inspector, and the
    // control inside it -- is no longer reachable: a reset asserted after a
    // run has nothing to click. This is also the only place the
    // reset-then-confirm-again path is exercised, which is why the run is
    // reached through it rather than from a second upload.
    //
    // The sheet has to be open to settle the column and closed to use the
    // composer, which is the sequence a reader actually follows.
    await openInspector(page);
    const control = page.getByTestId("role-confirmation");
    await control.getByRole("radio", { name: /Category/ }).check();
    await control.getByRole("button", { name: /Confirm for this session/ }).click();
    await expect(page.getByTestId("role-confirmed")).toBeVisible();
    await closeInspector(page);

    const button = suggestedQuestions(page).filter({ hasText: /reading/ }).first();
    const text = (await button.locator(".suggestion-text").innerText()).trim();
    await button.click();
    await expect(page.getByLabel("Business question")).toHaveValue(text);

    const started = page.waitForResponse(
      (r) => r.url().includes("/api/analyses") && r.request().method() === "POST",
    );
    await page.getByTestId("run").click();
    const runId = (await (await started).json()).run_id as string;
    expect(runId).toBeTruthy();
    await waitForReport(page);

    // The whole point of the feature: the accepted contract, not the label.
    const payload = await (await page.request.get(`/api/analyses/${runId}`)).json();
    expect(payload.query_contract?.dimensions).toEqual(["reading"]);
    // Three generations: confirm, reset, confirm again.
    expect(payload.schema_revision).toBe(3);
  });

  test("nothing in any of that reached a provider", async () => {
    expect(seen.provider, "a provider host was contacted").toEqual([]);
    expect(seen.offOrigin, "an off-origin request was made").toEqual([]);
  });
});

// ------------------------------------------------- the arithmetic and audit

test.describe("what the engine then does with it", () => {
  test.describe.configure({ mode: "serial" });
  let page: Page;
  let seen: { offOrigin: string[]; provider: string[] };

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    watchTraffic(page);
    await openApp(page);
    seen = watchRequests(page);
    await uploadFile(page, "clinical.csv", closeCallCsv());
    await openInspector(page);
    await page.getByTestId("role-confirmation").getByRole("radio", { name: /Category/ }).check();
    await (await confirmButton(page)).click();
    await expect(page.getByTestId("role-confirmed")).toBeVisible();
    // The composer is behind the sheet's scrim until it is closed.
    await closeInspector(page);
    await ask(page, "What is total dose by reading?");
    await waitForReport(page);
  });

  test.afterAll(async () => {
    // The session, not just the page. Closing a browser context does not
    // free a server-side upload session -- the server holds it until the
    // capability deletes it or the TTL expires, and the TTL outlives a CI
    // run. Three engines leaving their sessions behind is what exhausted
    // the 24-session pool.
    await endSession(page);
    await page.close();
  });

  test("the audit names the confirmation, per entry", async () => {
    // The audit is in the evidence drawer now, and is still a closed
    // `<details>` inside it: its body is hidden from the accessibility tree
    // and from `toBeVisible` until a reader opens it.
    await page.getByTestId("inspect-evidence").click();
    const audit = inDrawer(page, "planning-audit");
    await expect(audit).toBeVisible();
    await audit.locator("summary").first().click();

    // In the drawer. The print appendix holds a second copy of the whole
    // evidence body, which is what step H put there; the reader's copy is
    // the one that has to say this.
    const evidence = inDrawer(page, "role-evidence");
    await expect(evidence).toBeVisible();

    // Scoped to the column's own entry. Asserting against the whole block
    // matched the section heading, so deleting the source from every entry
    // left the assertion green.
    // The column is named by the term, the provenance by its description.
    await expect(evidence.locator("dt").first()).toContainText(/reading/);
    const entry = evidence.locator("dd").first();
    await expect(entry).toContainText(/Used as grouping/);
    await expect(entry).toContainText(/read as category/);
    await expect(entry).toContainText(/originally inferred quantity/);
    await expect(entry).toContainText(/confirmed for this dataset session/);
  });

  test("the audit does not claim more than one person's statement", async () => {
    const evidence = inDrawer(page, "role-evidence");
    for (const overclaim of [/governed/i, /verified/i, /saved preference/i]) {
      await expect(evidence).not.toContainText(overclaim);
    }
  });

  test("the run recorded no provider attempt and no cost", async () => {
    // `/api/health` says what the server would do. This says what it did.
    const runId = await page.evaluate(() => {
      const match = window.location.href.match(/run[_=/]([A-Za-z0-9_-]+)/);
      return match ? match[1] : null;
    });
    const response = await page.request.get("/api/analyses/" + (runId ?? ""));
    const payload = runId && response.ok() ? await response.json() : null;
    if (payload?.usage) {
      expect(payload.usage.provider_attempts, "a provider was attempted").toBe(0);
      expect(payload.usage.estimated_cost_microdollars).toBe(0);
    }
    // Whatever the payload shape, nothing left the origin.
    expect(seen.provider, "a provider host was contacted").toEqual([]);
    expect(seen.offOrigin, "an off-origin request was made").toEqual([]);

    const health = await (await page.request.get("/api/health")).json();
    expect(health.provider_mode).toBe("fake");
  });
});

// ------------------------------------------- confirming the inferred reading

test.describe("confirming the reading the engine already had", () => {
  test.describe.configure({ mode: "serial" });
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    watchTraffic(page);
    await openApp(page);
    await uploadFile(page, "clinical.csv", closeCallCsv());
    await openInspector(page);
    // No radio touched: confirm whatever inference chose, which is a
    // meaningful act even when it changes no label.
    await (await confirmButton(page)).click();
    await expect(page.getByTestId("role-confirmed")).toBeVisible();
  });

  test.afterAll(async () => {
    // The session, not just the page. Closing a browser context does not
    // free a server-side upload session -- the server holds it until the
    // capability deletes it or the TTL expires, and the TTL outlives a CI
    // run. Three engines leaving their sessions behind is what exhausted
    // the 24-session pool.
    await endSession(page);
    await page.close();
  });

  test("is recorded as the reader's, not left looking inferred", async () => {
    const card = page.getByTestId("role-confirmed");
    await expect(card).toContainText(/Confirmed for this session/);
    // The row must no longer read as an open question.
    await expect(page.getByTestId("schema-inspector")).not.toContainText(
      /1 role the data cannot settle/,
    );
  });

  test("offers an average of it, and never a total", async () => {
    await closeInspector(page);
    // The control said "can be averaged or totalled". Averaging is what
    // was asserted; additivity is a separate property nobody established,
    // so a sum would be a claim the reader did not make.
    const offered = (await suggestedQuestions(page).allInnerTexts()).join(" ");
    expect(offered).toMatch(/average reading/i);
    expect(offered).not.toMatch(/total reading/i);
    expect(offered).not.toMatch(/reading contributes most/i);
  });
});

// ------------------------------------------------------------- refusals

test.describe("when the server refuses", () => {
  /*
   * Each of these tests uploads its own dataset. They mutate the schema
   * revision, so they cannot share one, and a server-side upload session
   * is not freed by the test ending. Three engines leaving theirs behind is
   * what exhausted the 24-session pool.
   */
  test.afterEach(async ({ page }) => {
    await endSession(page);
  });

  test("a stale revision is recovered from, not papered over", async ({ page }) => {
    await openApp(page);

    // The session id comes from the upload's own response. There is no
    // endpoint that reports the current session, and a conditional skip
    // would have been worse than no test: a skip reads as a pass and would
    // have broken the suite's exact-skip guard.
    const uploaded = page.waitForResponse(
      (r) => r.url().includes("/api/datasets/upload") && r.request().method() === "POST",
    );
    await uploadFile(page, "clinical.csv", closeCallCsv());
    const sessionId = (await (await uploaded).json()).session_id as string;
    expect(sessionId, "the upload returned no session id").toBeTruthy();

    await openInspector(page);

    // Move the server's revision on behind the page's back, which is what a
    // second tab on the same session does.
    await page.request.patch(`/api/datasets/${sessionId}/schema/roles`, {
      data: {
        expected_revision: 0,
        changes: [{ column: "reading", action: "confirm", role: "dimension" }],
      },
    });

    // The page still believes revision 0, so its confirmation is stale.
    await (await confirmButton(page)).click();
    const alert = page.getByRole("alert");
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(/again|refresh|changed|moved/i);
  });

  test("a refusal does not optimistically change the label", async ({ page }) => {
    await openApp(page);
    await uploadFile(page, "clinical.csv", closeCallCsv());
    await openInspector(page);

    // Refuse the write at the network boundary. The engine never accepted
    // the role, so the interface must not show it as accepted.
    await page.route("**/schema/roles", (route) =>
      route.fulfill({
        status: 409,
        contentType: "application/json",
        headers: { "X-Refusal-Reason": "active_run" },
        body: JSON.stringify({ detail: "an analysis is running on this dataset" }),
      }),
    );

    const control = page.getByTestId("role-confirmation");
    await control.getByRole("radio", { name: /Category/ }).check();
    await (await confirmButton(page)).click();

    await expect(page.getByRole("alert")).toBeVisible();
    // Still the offer, not the settled card.
    await expect(page.getByTestId("role-confirmation")).toBeVisible();
    await expect(page.getByTestId("role-confirmed")).toHaveCount(0);
  });

  test("the control is disabled while the request is in flight", async ({ page }) => {
    await openApp(page);
    await uploadFile(page, "clinical.csv", closeCallCsv());
    await openInspector(page);

    // Hold the response open so the in-flight state is observable rather
    // than inferred from a race.
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/schema/roles", async (route) => {
      await held;
      await route.continue();
    });

    const button = await confirmButton(page);
    await button.click();
    const pending = page
      .getByTestId("role-confirmation")
      .getByRole("button", { name: /Confirming…/ });
    await expect(pending).toBeDisabled();
    // The radios are disabled too: the choice is no longer changeable.
    await expect(
      page.getByTestId("role-confirmation").getByRole("radio", { name: /Category/ }),
    ).toBeDisabled();

    release();
    await expect(page.getByTestId("role-confirmed")).toBeVisible();
  });
});

// ------------------------------------------------------------ layout & axe

test.describe("the control at every width", () => {
  /*
   * Each of these tests uploads its own dataset. They mutate the schema
   * revision, so they cannot share one, and a server-side upload session
   * is not freed by the test ending. Three engines leaving theirs behind is
   * what exhausted the 24-session pool.
   */
  test.afterEach(async ({ page }) => {
    await endSession(page);
  });

  const widths = [
    { label: "phone", width: 360, height: 740 },
    { label: "tablet", width: 768, height: 1024 },
    { label: "desktop", width: 1280, height: 900 },
  ];

  // One upload, three widths. Resizing does not need a new dataset.
  test("fits and stays operable on a phone, a tablet and a desktop", async ({ page }) => {
    await openApp(page);
    await uploadFile(page, "clinical.csv", closeCallCsv());
    await openInspector(page);
    const control = page.getByTestId("role-confirmation");

    for (const { label, width, height } of widths) {
      await page.setViewportSize({ width, height });
      await expect(control, `${label}: the control is not visible`).toBeVisible();

      // Nothing may push the page into a horizontal scroll: on a phone that
      // is how a control becomes unreachable rather than merely cramped.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(
        overflow,
        `${label}: the page scrolls sideways by ${overflow}px`,
      ).toBeLessThanOrEqual(1);

      const box = await control
        .getByRole("button", { name: /Confirm for this session/ })
        .boundingBox();
      expect(box, `${label}: the confirm button has no box`).not.toBeNull();
      expect(
        box!.height,
        `${label}: confirm button is ${box!.height}px tall`,
      ).toBeGreaterThanOrEqual(24);

      for (const name of [/Quantity/, /Category/]) {
        const radioBox = await control.getByRole("radio", { name }).boundingBox();
        expect(radioBox, `${label}: a radio has no box`).not.toBeNull();
        expect(
          radioBox!.height,
          `${label}: radio is ${radioBox!.height}px`,
        ).toBeGreaterThanOrEqual(12);
      }
    }
  });
});

test.describe("accessibility of the control", () => {
  /*
   * Each of these tests uploads its own dataset. They mutate the schema
   * revision, so they cannot share one, and a server-side upload session
   * is not freed by the test ending. Three engines leaving theirs behind is
   * what exhausted the 24-session pool.
   */
  test.afterEach(async ({ page }) => {
    await endSession(page);
  });

  test("no serious or critical violations, offered or settled", async ({ page }) => {
    await openApp(page);
    await uploadFile(page, "clinical.csv", closeCallCsv());
    await openInspector(page);

    const scan = async (label: string) => {
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
        .analyze();
      const blocking = results.violations.filter((violation) =>
        ["serious", "critical"].includes(violation.impact ?? ""),
      );
      const detail = blocking
        .map((v) => `  [${v.impact}] ${v.id}: ${v.help} at ${v.nodes[0]?.target.join(" ")}`)
        .join("\n");
      expect(blocking.length, `${label}:\n${detail}`).toBe(0);
    };

    // Both states, because the settled card is a different subtree and a
    // scan of only the offer would never have seen it.
    await scan("the control as offered");
    await (await confirmButton(page)).click();
    await expect(page.getByTestId("role-confirmed")).toBeVisible();
    await scan("the control once settled");
  });
});
