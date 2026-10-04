import { expect, test } from "@playwright/test";

import { openApp, uploadFile } from "./helpers";

/**
 * The composer, the dataset context strip, and the schema side sheet.
 *
 * Two date columns, so the ambiguity signals have something real to report.
 * Every assertion here is about a property of the composed screen rather
 * than about an element existing -- "the composer is the largest control",
 * "the landing is not also on screen" -- because element-existence is what
 * the previous suite checked and it is what let a panel-in-a-panel layout
 * pass review.
 */
function twoClockCsv(rows = 240): string {
  const regions = ["North", "South", "East", "West"];
  const lines = ["order_id,order_date,signup_date,region,revenue"];
  for (let i = 0; i < rows; i += 1) {
    const month = String((i % 12) + 1).padStart(2, "0");
    lines.push(
      `${i},2025-${month}-15,2024-${month}-02,${regions[i % 4]},${(10 + ((i * 7) % 490)).toFixed(2)}`,
    );
  }
  return lines.join("\n");
}

test.describe("the composer, on a dataset with two clocks", () => {
  test.beforeEach(async ({ page }) => {
    await openApp(page);
    await uploadFile(page, "sales.csv", twoClockCsv());
  });

  test("the landing is replaced, not stacked under it", async ({ page }) => {
    // The old Dataset panel stayed mounted below the Ask panel for the
    // whole session, so after an upload a reader saw their file's composer
    // above a drop zone still inviting them to choose one. With a
    // full-page landing that is two products on one screen.
    await expect(page.getByTestId("composer")).toBeVisible();
    await expect(page.getByTestId("landing")).toHaveCount(0);
    await expect(page.getByTestId("dropzone")).toHaveCount(0);
  });

  test("the context strip names the file, its shape and both clocks", async ({
    page,
  }) => {
    const strip = page.getByTestId("dataset-context");
    await expect(strip).toContainText("sales.csv");
    await expect(strip).toContainText("240 rows");
    await expect(strip).toContainText("order_date");
    await expect(strip).toContainText("signup_date");
  });

  test("the composer warns about two clocks before the question is run", async ({
    page,
  }) => {
    // A reader who learns "this table has two date columns" from the
    // refusal has learned it too late to have phrased the question better.
    await expect(page.getByTestId("chip-two-clocks")).toBeVisible();
    await expect(page.getByTestId("chip-two-clocks")).toContainText(
      /2 date columns/i,
    );
  });

  test("the question field is the focal control on the screen", async ({
    page,
  }) => {
    // Measured, not asserted. The strategy selector used to be four
    // bordered cards carrying four paragraphs, occupying more of the screen
    // than the field they qualify.
    //
    // Both measurements are of *interactive surface*: the question field
    // against the segmented control, which is the comparison that means
    // something. An earlier version measured the whole `.mode-selector`
    // block, prose included, which is not a like-for-like comparison -- a
    // one-sentence description is explanation a reader wants, not control
    // chrome competing with the field.
    const field = await page.locator("#composer-field").boundingBox();
    const modes = await page.locator(".mode-options").boundingBox();
    expect(field).not.toBeNull();
    expect(modes).not.toBeNull();

    const area = (b: { width: number; height: number }) => b.width * b.height;
    expect(
      area(field!),
      "the strategy control is larger than the question field",
    ).toBeGreaterThan(area(modes!));

    // And the explanation it carries stays bounded: one description, not
    // one per strategy. This is the assertion that would have failed on the
    // layout this test was written against.
    expect(await page.locator(".mode-description").count()).toBe(1);
  });

  test("only the selected strategy explains itself", async ({ page }) => {
    const description = page.getByTestId("mode-description");
    await expect(description).toBeVisible();

    // One description, not four. Four asked a reader to compare four
    // paragraphs before asking their first question.
    await expect(description).toHaveCount(1);
    await expect(description).toContainText(/rule-based planning/i);

    await page.getByRole("radio", { name: /Deterministic Analytics/ }).check();
    await expect(description).toContainText(/scripted provider/i);
  });

  test("every strategy keeps its own description in the accessibility tree", async ({
    page,
  }) => {
    // Collapsing to one *visible* description must not collapse what a
    // screen reader is told: each radio still names what choosing it means.
    const names = ["Governed Analysis", "Deterministic Analytics", "AI Analytics"];
    for (const name of names) {
      const radio = page.getByRole("radio", { name: new RegExp(name) });
      const describedBy = await radio.getAttribute("aria-describedby");
      expect(describedBy, `${name} has no description`).toBeTruthy();
      const text = await page.locator(`#${describedBy}`).textContent();
      expect((text ?? "").trim().length, `${name}'s description is empty`).toBeGreaterThan(20);
    }
  });

  test("suggestions are labelled by what they ask for", async ({ page }) => {
    const suggestions = page.getByTestId("question-examples");
    await expect(suggestions).toHaveAttribute("data-source", "schema");

    const intents = await suggestions.locator(".suggestion-intent").allInnerTexts();
    expect(intents.length).toBeGreaterThan(0);
    // Derived from the schema, so they describe this file's columns.
    await expect(suggestions).toContainText(/revenue/i);
    for (const intent of intents) {
      expect(["Trend", "Compare", "Rank", "Count"]).toContain(intent.trim());
    }
  });

  test("choosing a suggestion puts it in the field", async ({ page }) => {
    const first = page.locator("button.suggestion").first();
    const text = (await first.locator(".suggestion-text").textContent()) ?? "";
    await first.click();
    await expect(page.locator("#composer-field")).toHaveValue(text.trim());
  });
});

test.describe("the schema side sheet", () => {
  test.beforeEach(async ({ page }) => {
    await openApp(page);
    await uploadFile(page, "sales.csv", twoClockCsv());
  });

  test("the schema is one control away, not resident on the canvas", async ({
    page,
  }) => {
    // A full field list on the canvas is a reference document every reader
    // scrolls past. It is behind a control now.
    await expect(page.getByTestId("schema-sheet")).toHaveCount(0);
    await page.getByTestId("inspect-schema").click();
    await expect(page.getByTestId("schema-sheet")).toBeVisible();
  });

  test("it opens expanded, because the reader already asked", async ({
    page,
  }) => {
    await page.getByTestId("inspect-schema").click();
    const inspector = page.getByTestId("schema-inspector");
    await expect(inspector).toHaveAttribute("open", "");
  });

  test("Escape closes it and gives focus back to the trigger", async ({
    page,
  }) => {
    // The half that is easy to get wrong is invisible. Closing used to
    // leave focus on `<body>`, which drops a keyboard user at the top of
    // the document.
    const trigger = page.getByTestId("inspect-schema");
    await trigger.click();
    await expect(page.getByTestId("schema-sheet")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("schema-sheet")).toHaveCount(0);

    // `toBeFocused` rather than a one-shot read of `document.activeElement`.
    // The restore is deliberately deferred by a frame -- cleanup runs
    // before React unmounts the sheet, and WebKit moves focus to `<body>`
    // as the focused node inside it disappears, undoing a synchronous
    // restore. A single read raced that frame: it passed on Chromium and
    // Firefox, which happened to be slower, and failed on WebKit.
    await expect(trigger).toBeFocused();
  });

  test("focus does not walk out of the open sheet", async ({ page }) => {
    await page.getByTestId("inspect-schema").click();
    await expect(page.getByTestId("schema-sheet")).toBeVisible();

    // Tab through more stops than the sheet contains. Focus must stay
    // inside it: the page behind is covered by a scrim, so focus landing
    // there simply disappears.
    for (let i = 0; i < 30; i += 1) {
      await page.keyboard.press("Tab");
      const inside = await page.evaluate(() => {
        const sheet = document.querySelector('[data-testid="schema-sheet"]');
        return sheet ? sheet.contains(document.activeElement) : false;
      });
      expect(inside, `focus left the sheet after ${i + 1} tabs`).toBe(true);
    }
  });
});
