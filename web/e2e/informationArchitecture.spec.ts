/**
 * The information architecture, in a real browser against the real engine.
 *
 * Where a state can be produced by asking the engine a question, it is
 * produced that way. Three can:
 *
 *   - `refused`     -- a question naming a measure the table does not have.
 *   - `no_findings` -- a filter that matches no rows. This used to come back
 *                      `failed`, and the assertion here used to accept any
 *                      of three states because the classification was wrong.
 *                      The engine now completes it and says why.
 *   - `completed`   -- an ordinary mappable question.
 *
 * The rest cannot be produced locally with the scripted provider and are
 * driven by intercepting the run payload at the network boundary instead:
 * `verification_withheld`, `cancelled`, `quota_stopped`, and `execution_failed`
 * -- the last of which *was* reachable through the empty-filter question
 * until that was corrected, and now needs a genuine tool error to produce.
 * The backend suite covers that case directly, by making the engine's own
 * SQL execution raise. That is the pattern
 * the quota test in `dualmode.spec.ts` already uses. It is weaker than a
 * real run and stronger than a component test: the payload is server-shaped
 * and every derivation, class name and piece of copy is the application's
 * own. Which states are fixtures and which are real is recorded here so the
 * distinction is not lost.
 *
 * The ambiguity dataset is not arbitrary. `age` holds 48 distinct values
 * across 400 rows, which is 12% uniqueness: inside the 20% share ceiling
 * and above the 12-value enumeration ceiling, so it lands in the band where
 * the engine itself says a code list and a genuine count are
 * indistinguishable. Confirmed against the real `infer_schema()` through
 * the upload API, not asserted from a hand-written schema.
 */

import { expect, test, type Page } from "@playwright/test";

import { ask, openApp, uploadFile, waitForReport } from "./helpers";

/**
 * Open the demo warehouse.
 *
 * Used wherever a test is not specifically about uploaded-file behaviour.
 * Uploads consume a bounded server-side session pool
 * (`max_active_upload_sessions`), and a suite that exhausts it gets its
 * uploads refused -- which surfaced here as nine tests failing inside
 * `uploadFile` rather than as anything to do with what they assert. The
 * demo path has no such ceiling.
 */
async function openDemo(page: Page) {
  await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
  await expect(page.getByLabel("Business question")).toBeVisible({
    timeout: 30_000,
  });
}

/** A dataset whose `age` column the engine cannot classify from the data. */
function ambiguousCsv(): string {
  const regions = ["North", "South", "East", "West"];
  const lines = ["region,revenue,age,visits"];
  for (let i = 0; i < 400; i += 1) {
    lines.push(
      `${regions[i % 4]},${100 + i * 7},${18 + (i % 48)},${2 + (i % 5)}`,
    );
  }
  return lines.join("\n");
}

/** An ordinary dataset with nothing ambiguous in it. */
function plainCsv(): string {
  const regions = ["North", "South", "East", "West"];
  const lines = ["region,revenue,month"];
  for (let i = 0; i < 200; i += 1) {
    lines.push(`${regions[i % 4]},${100 + i * 7},2025-${(i % 12) + 1}`);
  }
  return lines.join("\n");
}

test.describe("the schema inspector tells the truth about ambiguity", () => {
  // Serial, with one upload shared across the three tests.
  //
  // Uploads are rate limited per address, and the whole suite runs three
  // times -- once per engine -- against one container. At one upload per
  // test the third engine was refused mid-run and fifteen upload-dependent
  // tests failed, including pre-existing ones that have nothing to do with
  // this file. A profiled dataset does not change between these
  // assertions, so there is no reason to pay for it three times.
  test.describe.configure({ mode: "serial" });
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    await openApp(page);
    await uploadFile(page, "ia-ambiguous.csv", ambiguousCsv());
  });

  test.afterAll(async () => {
    await page.close();
  });

  test.beforeEach(async () => {
    // Each test starts with the schema out of the way, whatever the last
    // one did. The inspector lives in a side sheet now, so "collapsed"
    // means "the sheet is closed" rather than "the disclosure is shut".
    if (await page.getByTestId("schema-sheet").count()) {
      await page.keyboard.press("Escape");
      await expect(page.getByTestId("schema-sheet")).toHaveCount(0);
    }
  });

  test("marks a role the data cannot settle", async () => {
    // The count of unsettled roles is legible without opening anything.
    // It used to be on the collapsed disclosure; it is on the dataset
    // context strip, which is the first line under the header.
    await expect(page.getByTestId("context-ambiguity")).toContainText(
      /ambiguous field/i,
    );
    await expect(page.getByTestId("schema-inspector")).toHaveCount(0);

    await page.getByTestId("inspect-schema").click();
    const inspector = page.getByTestId("schema-inspector");
    await expect(inspector).toHaveAttribute("open", "");
    await expect(inspector).toContainText(/role the data cannot settle/);

    const marks = inspector.getByTestId("ambiguous-field");
    expect(await marks.count()).toBeGreaterThan(0);
    // The engine marked `age`, so the interface must mark `age`.
    const row = inspector.locator('tr[data-ambiguous="true"]');
    await expect(row.first()).toContainText("age");

    // ADR 0006 refused any control, because a label the engine would not
    // honour is worse than no label. ADR 0007 supplies the honouring, so
    // this fixture -- an uploaded file, where the reader is the one who
    // knows what the column means -- now gets the offer.
    await expect(inspector).toContainText(/settle it for this session/i);
    await expect(inspector.getByTestId("role-confirmation").first()).toBeVisible();
    // Still no free-text or dropdown role editing: the choice is between
    // the two readings the engine can actually act on.
    expect(await inspector.locator("select").count()).toBe(0);
    // And it still refuses to overstate what a confirmation is.
    await expect(inspector).toContainText(/not a governed definition/i);
  });

  test("is operable from the keyboard", async () => {
    // The gate moved from a `<details>` summary to the strip's control, so
    // what has to be keyboard-operable moved with it.
    const trigger = page.getByTestId("inspect-schema");
    await trigger.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("schema-sheet")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("schema-sheet")).toHaveCount(0);
    // And focus comes back to where it started, so a keyboard user is not
    // dropped at the top of the document.
    await expect(trigger).toBeFocused();
  });
});

test.describe("a dataset with nothing ambiguous in it", () => {
  // Serial, sharing one upload: both assertions are about the same profiled
  // dataset, and uploads are rate limited per address across three engines.
  test.describe.configure({ mode: "serial" });
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    await openApp(page);
    await uploadFile(page, "ia-plain.csv", plainCsv());
  });

  test.afterAll(async () => {
    await page.close();
  });

  test("says nothing about close calls when every role is settled", async () => {
    // Nothing on the strip either: a dataset with no close calls must not
    // carry an ambiguity warning that happens to say zero.
    await expect(page.getByTestId("context-ambiguity")).toHaveCount(0);

    await page.getByTestId("inspect-schema").click();
    const inspector = page.getByTestId("schema-inspector");
    await expect(inspector).not.toContainText(/cannot settle/);
    await expect(inspector.getByTestId("ambiguous-field")).toHaveCount(0);
    await expect(inspector.getByTestId("ambiguity-note")).toHaveCount(0);
    await page.keyboard.press("Escape");
  });

  test("never gets demo-warehouse questions", async () => {
    // The demo questions, so the test knows what must not appear.
    const demo = await page.evaluate(async () => {
      const response = await fetch("/api/config");
      const body = await response.json();
      return (body.demo_questions ?? []).map(
        (item: { question: string }) => item.question,
      );
    });
    expect(demo.length).toBeGreaterThan(0);

    const examples = page.getByTestId("question-examples");
    await expect(examples).toBeVisible();
    await expect(examples).toHaveAttribute("data-source", "schema");

    const offered = await examples.locator("button.suggestion").allInnerTexts();
    expect(offered.length).toBeGreaterThan(0);
    for (const question of demo) {
      expect(
        offered,
        `a demo-warehouse question was offered for an uploaded file: ${question}`,
      ).not.toContain(question);
    }
    // And they name this file's columns.
    expect(offered.join(" ")).toMatch(/revenue|region|month/i);
  });

});

test.describe("the demo dataset keeps its curated questions", () => {
  test("the demo dataset still gets the curated questions", async ({ page }) => {
    await openApp(page);
    await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
    const examples = page.getByTestId("question-examples");
    await expect(examples).toBeVisible();
    await expect(examples).toHaveAttribute("data-source", "demo");
  });
});

test.describe("terminal states, produced by the engine", () => {
  // Serial, sharing one upload. Both states are reached by asking the same
  // profiled dataset a different question, so a second upload buys nothing,
  // and uploads are rate limited per address across three engine runs.
  //
  // Verified against the real engine before either test was written: on an
  // uploaded dataset a question naming a measure the table does not have
  // comes back `refused`, and a filter matching no rows comes back
  // `failed`. The demo warehouse routes through the governed metric
  // registry instead and does not behave the same way, so these are uploads.
  test.describe.configure({ mode: "serial" });
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
    await openApp(page);
    await uploadFile(page, "ia-terminal.csv", plainCsv());
  });

  test.afterAll(async () => {
    await page.close();
  });

  test.beforeEach(async () => {
    // Back to the composer, so each test starts a fresh run on the session.
    const startOver = page.getByRole("button", { name: "Start over" });
    if (await startOver.count()) await startOver.click();
    await expect(page.getByLabel("Business question")).toBeVisible();
  });

  test("a refused question is reported as refused, not as an empty report", async () => {
    await ask(page, "What is the total gross margin by region?");

    // The terminal state is part of the report now, inside the same
    // reading column as the question. It was a separate card, which is
    // what let a refusal state its reason twice -- once in the card and
    // once as the display headline.
    const report = page.getByTestId("report-panel");
    await expect(report).toBeVisible({ timeout: 90_000 });
    await expect(report).toHaveAttribute("data-state", "refused");
    await expect(report).toContainText(/refused/i);
    // Never the word that would send a reader looking for an answer.
    await expect(report).not.toContainText(/\bComplete\b/);

    // The three `.step` assertions that stood here -- Analyse stopped,
    // Verify idle, Report idle -- went with the five-step pipeline index.
    //
    // Their claim is the important half of this test and is **owed by step
    // D**: a run that stopped must show *where* it stopped, and must not
    // show later stages as idle in a way that reads as still in progress.
    // The timeline must assert it against real backend events rather than
    // against a derived phase string, which is what the index used.
    expect(await page.locator(".step").count()).toBe(0);
  });

  test("a filter matching no rows is reported, and not as a verified answer", async () => {
    // This used to come back `failed`, with the reason "the executed result
    // could not be turned into a direct answer", and the assertion here
    // accepted any of three states because the classification was wrong and
    // out of scope to fix. The engine now completes it: the contract ran and
    // the table held no matching rows, which is a result.
    await ask(page, "What is total revenue by region where region is Atlantis?");

    const report = page.getByTestId("report-panel");
    await expect(report).toBeVisible({ timeout: 90_000 });
    await expect(report).toHaveAttribute("data-state", "no_findings");
    await expect(report).not.toContainText(/\bComplete\b/);
    await expect(report).not.toContainText(/failed/i);
    // A completed run must never wear refusal language.
    await expect(report).not.toContainText(/not answered/i);
    // And the reason names the restriction that emptied it.
    await expect(
      page.getByText(/No rows matched the requested filters/),
    ).toBeVisible();
  });

  test("an answered question carries no terminal state at all", async ({ page }) => {
    await openApp(page);
    await openDemo(page);
    await ask(page, "What is total revenue by region?");
    await waitForReport(page);
    // A verified answer speaks for itself: no eyebrow, no rule bar, no
    // state attribute.
    await expect(page.getByTestId("terminal-state")).toHaveCount(0);
    await expect(page.getByTestId("report-panel")).not.toHaveAttribute(
      "data-state",
      /.+/,
    );

    // And the report has to contain the answer.
    //
    // Asserting only that no state card is present passed while the demo
    // report read "VERIFIED ANSWER: The analysis could not be completed." --
    // the presentation builder returned a FAILURE shape because the metric
    // registry path produces no `aggregate_for_question` snapshot, and the
    // verified finding was replaced by that sentence. The run was
    // `completed`, so no card appeared, and the test passed over it.
    const report = page.getByTestId("report-panel");
    await expect(report).not.toContainText(/could not be completed/i);
    await expect(report).not.toContainText(/not answered/i);
    // A real figure from the demo warehouse, not a sentence about failing.
    await expect(report).toContainText(/\$[\d,]+\.\d{2}/);
    // "And the workflow index reaches Report" went with the index. The
    // claim it added over the assertions above was only that the chrome
    // agreed with the content, and the chrome is gone. Step D's timeline
    // owes the positive half of this: a completed run shows its last stage
    // as completed, from backend events.
    expect(await page.locator(".step").count()).toBe(0);
  });
});

/**
 * The states the scripted provider cannot produce.
 *
 * The run payload is intercepted and replaced with a server-shaped one. The
 * application then derives the state, the label, the tone and the copy
 * itself, which is the behaviour under test.
 */
async function serveRun(page: Page, overrides: Record<string, unknown>) {
  await page.route("**/api/analyses/*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, ...overrides } });
  });
}

test.describe("terminal states that need a payload fixture", () => {
  test.describe.configure({ mode: "serial" });

  const cases: Array<[string, Record<string, unknown>, RegExp]> = [
    [
      "verification_withheld",
      {
        status: "completed",
        outcome: "completed",
        findings: [],
        rejected: [
          {
            finding_id: "f1",
            status: "unsupported",
            reason: "the cited cells do not support the claim",
          },
        ],
      },
      /withheld/i,
    ],
    [
      "no_findings",
      { status: "completed", outcome: "completed", findings: [], rejected: [] },
      /no findings/i,
    ],
    [
      "cancelled",
      {
        status: "cancelled",
        outcome: "cancelled",
        findings: [],
        rejected: [],
        stopped_reason: "the dataset was closed",
      },
      /cancelled/i,
    ],
    [
      "quota_stopped",
      {
        status: "budget_exhausted",
        outcome: "budget_exhausted",
        findings: [],
        rejected: [],
        stopped_reason: "the run reached its token budget",
      },
      /quota/i,
    ],
  ];

  for (const [state, overrides, expected] of cases) {
    test(`${state} is shown as itself in single mode`, async ({ page }) => {
      await openApp(page);
      await openDemo(page);
      await serveRun(page, overrides);
      await ask(page, "What is total revenue by region?");

      const report = page.getByTestId("report-panel");
      await expect(report).toBeVisible({ timeout: 90_000 });
      await expect(report).toHaveAttribute("data-state", state);
      await expect(report).toContainText(expected);
      await expect(report).not.toContainText(/\bComplete\b/);
    });
  }

  test("withheld and no-findings do not share an explanation", async ({
    page,
  }) => {
    // The two were one state, so the copy for "nothing published" pointed at
    // withheld findings that did not exist.
    await openApp(page);
    await openDemo(page);
    await serveRun(page, cases[1]![1]);
    await ask(page, "What is total revenue by region?");
    const card = page.getByTestId("report-panel");
    await expect(card).toBeVisible({ timeout: 90_000 });
    await expect(card).toContainText(/nothing was withheld/i);
    await expect(card).not.toContainText(/withheld findings below/i);
  });
});

test.describe("layout holds at every width", () => {
  const widths = [
    ["phone", 390, 844],
    ["tablet", 768, 1024],
    ["desktop", 1440, 900],
  ] as const;

  for (const [label, width, height] of widths) {
    test(`${label}: no horizontal overflow and the strip stays inside`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height });
      await openApp(page);
      await openDemo(page);
      await ask(page, "What is total revenue by region?");
      await waitForReport(page);

      // The page must not scroll sideways at any width.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      expect(overflow, `${label} scrolls sideways by ${overflow}px`).toBeLessThanOrEqual(1);

      // And the dataset strip has to fit the viewport it is in.
      // `dataset-identity` was the header strip; the context bar replaced
      // it, and carries more: shape, clocks and the ambiguity count.
      const strip = page.getByTestId("dataset-context");
      await expect(strip).toBeVisible();
      const box = await strip.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width + 1);
    });
  }
});
