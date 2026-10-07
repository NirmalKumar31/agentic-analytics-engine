import { TWO_CLOCKS_FILE, expect, freshComposer, test } from "./fixtures";

import { selectMode } from "./helpers";





test.describe("the composer, on a dataset with two clocks", () => {
  test.beforeEach(async ({ twoClocks: page }) => {
    await freshComposer(page);
  });

  test("the landing is replaced, not stacked under it", async ({ twoClocks: page }) => {
    // The old Dataset panel stayed mounted below the Ask panel for the
    // whole session, so after an upload a reader saw their file's composer
    // above a drop zone still inviting them to choose one. With a
    // full-page landing that is two products on one screen.
    await expect(page.getByTestId("composer")).toBeVisible();
    await expect(page.getByTestId("landing")).toHaveCount(0);
    await expect(page.getByTestId("dropzone")).toHaveCount(0);
  });

  test("the context strip names the file, its shape and both clocks", async ({
    twoClocks: page,
  }) => {
    const strip = page.getByTestId("dataset-context");
    // The name the shared `twoClocks` session was uploaded under. The strip
    // echoes the reader's own filename, so this is the thing being tested.
    await expect(strip).toContainText(TWO_CLOCKS_FILE);
    await expect(strip).toContainText("240 rows");
    await expect(strip).toContainText("order_date");
    await expect(strip).toContainText("signup_date");
  });

  test("the composer warns about two clocks before the question is run", async ({
    twoClocks: page,
  }) => {
    // A reader who learns "this table has two date columns" from the
    // refusal has learned it too late to have phrased the question better.
    await expect(page.getByTestId("chip-two-clocks")).toBeVisible();
    await expect(page.getByTestId("chip-two-clocks")).toContainText(
      /2 date columns/i,
    );
  });

  test("the question field is the focal control on the screen", async ({
    twoClocks: page,
  }) => {
    /*
     * The mode choices are four cards now, and that changes what this test
     * can honestly measure.
     *
     * It used to compare the question field's area against the segmented
     * control's. Cards are deliberately larger than a pill strip -- that is
     * the point of making them look selectable, so the old comparison
     * would now fail for a design that was chosen on purpose, which makes
     * it a measurement of the wrong thing rather than a guard.
     *
     * What still has to hold, and is what the original was protecting:
     * the field comes first, no single card competes with it for width, and
     * the explanations are *not resident*. That last one is the assertion
     * that would have failed on the four-paragraph layout this test was
     * written against. It is kept, and strengthened from "one description"
     * to "none on screen until asked for".
     */
    const field = page.locator("#composer-field");
    const cards = page.locator(".mode-cards");
    await expect(field).toBeVisible();
    await expect(cards).toBeVisible();

    const fieldBox = (await field.boundingBox())!;
    const order = await page.evaluate(() => {
      const f = document.querySelector("#composer-field");
      const m = document.querySelector(".mode-cards");
      if (!f || !m) return 0;
      // 4 === DOCUMENT_POSITION_FOLLOWING: the cards come after the field.
      return f.compareDocumentPosition(m) & 4;
    });
    expect(order, "the mode cards come before the question field").toBe(4);

    for (const card of await page.locator(".mode-option label").all()) {
      const box = (await card.boundingBox())!;
      expect(
        box.width,
        "a single mode card is wider than the question field",
      ).toBeLessThanOrEqual(fieldBox.width);
    }

    // No prose about the modes on screen. One sentence per card is the
    // card's own purpose; the descriptions, the taxonomy and the quota
    // note are all behind the disclosure.
    for (const testId of ["mode-description", "mode-taxonomy"]) {
      expect(
        await page.locator(`[data-testid="${testId}"]:visible`).count(),
        `${testId} is resident beside the composer`,
      ).toBe(0);
    }
  });

  test("each card states its purpose, and the full text is one click away", async ({
    twoClocks: page,
  }) => {
    // Every card says what it is for, in a sentence, without being
    // selected first. Three of the four used to say nothing until chosen.
    const purposes = page.locator(".mode-option-purpose");
    expect(await purposes.count()).toBeGreaterThanOrEqual(3);
    for (const purpose of await purposes.all()) {
      await expect(purpose).toBeVisible();
      expect((await purpose.textContent())?.trim().length ?? 0).toBeGreaterThan(10);
    }

    // And the full description follows the selection, inside the
    // disclosure. Asserted as a change rather than as an initial value:
    // the shared session is reset to Deterministic and the default
    // otherwise depends on what the deployment advertises.
    const explainer = page.getByTestId("mode-explainer");
    await explainer.locator("summary").click();
    const description = page.getByTestId("mode-description");
    await expect(description).toBeVisible();
    await expect(description).toHaveCount(1);

    await selectMode(page, "deterministic");
    await expect(description).toContainText(/scripted provider/i);

    const other = page.getByRole("radio", { name: /Compare planning strategies/ });
    if (await other.isEnabled()) {
      await other.check();
      await expect(description).toContainText(/one full report at a time/i);
      await expect(description).not.toContainText(/scripted provider/i);
    }
  });

  test("every strategy keeps its own description in the accessibility tree", async ({
    twoClocks: page,
  }) => {
    // Collapsing to one *visible* description must not collapse what a
    // screen reader is told: each radio still names what choosing it means.
    /*
     * Anchored to the start of the accessible name.
     *
     * A radio's accessible name is its label: the visible mode name
     * followed by the screen-reader description. When AI is unavailable --
     * which is every deployment without a provider key, including CI --
     * Compare's unavailable message *is* the AI mode's message, so the
     * Compare radio's name also contains "AI Analytics" and an unanchored
     * `/AI Analytics/` matches two radios. Playwright's strict mode
     * refused it, and this test was the only Chromium and WebKit failure
     * in the container.
     *
     * Anchoring is the right fix rather than a wider net: each mode's
     * visible label begins its accessible name, so `^` identifies exactly
     * one radio and says so.
     */
    const names = ["Governed Analysis", "Deterministic Analytics", "AI Analytics"];
    for (const name of names) {
      const radio = page.getByRole("radio", { name: new RegExp(`^${name}`) });
      await expect(
        radio,
        `"${name}" does not identify exactly one mode radio`,
      ).toHaveCount(1);
      const describedBy = await radio.getAttribute("aria-describedby");
      expect(describedBy, `${name} has no description`).toBeTruthy();
      const text = await page.locator(`#${describedBy}`).textContent();
      expect((text ?? "").trim().length, `${name}'s description is empty`).toBeGreaterThan(20);
    }
  });

  test("suggestions are labelled by what they ask for", async ({ twoClocks: page }) => {
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

  test("choosing a suggestion puts it in the field", async ({ twoClocks: page }) => {
    const first = page.locator("button.suggestion").first();
    const text = (await first.locator(".suggestion-text").textContent()) ?? "";
    await first.click();
    await expect(page.locator("#composer-field")).toHaveValue(text.trim());
  });
});

test.describe("the schema side sheet", () => {
  test.beforeEach(async ({ twoClocks: page }) => {
    await freshComposer(page);
  });

  test("the schema is one control away, not resident on the canvas", async ({
    twoClocks: page,
  }) => {
    // A full field list on the canvas is a reference document every reader
    // scrolls past. It is behind a control now.
    await expect(page.getByTestId("schema-sheet")).toHaveCount(0);
    await page.getByTestId("inspect-schema").click();
    await expect(page.getByTestId("schema-sheet")).toBeVisible();
  });

  test("it opens expanded, because the reader already asked", async ({
    twoClocks: page,
  }) => {
    await page.getByTestId("inspect-schema").click();
    const inspector = page.getByTestId("schema-inspector");
    await expect(inspector).toHaveAttribute("open", "");
  });

  test("Escape closes it and gives focus back to the trigger", async ({
    twoClocks: page,
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

  test("focus does not walk out of the open sheet", async ({ twoClocks: page }) => {
    await page.getByTestId("inspect-schema").click();
    await expect(page.getByTestId("schema-sheet")).toBeVisible();

    // Tab through more stops than the sheet contains. Focus must stay
    // inside it: the page behind is covered by a scrim, so focus landing
    // there simply disappears.
    for (let i = 0; i < 30; i += 1) {
      await page.keyboard.press("Tab");
      // Polled, not read on the frame after the key.
      //
      // Containment is a backstop: when focus leaves the sheet it is pulled
      // back on the next tick, because at `focusout` time the new target
      // has not been focused yet and reading `activeElement` synchronously
      // would always see the old one. The guarantee is that focus cannot
      // *settle* outside the sheet, which is what a reader experiences; a
      // single read catches the frame in between and fails on whichever
      // engine happens to be quickest.
      await expect
        .poll(
          () =>
            page.evaluate(() => {
              const sheet = document.querySelector(
                '[data-testid="schema-sheet"]',
              );
              return sheet ? sheet.contains(document.activeElement) : false;
            }),
          { message: `focus settled outside the sheet after ${i + 1} tabs` },
        )
        .toBe(true);
    }
  });
});
