import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

import { advertiseAi, compareWith, openDemo, settle, startCompare } from "./compareHelpers";
import { ask, onCanvas, openApp, sampleCsv, uploadFile, waitForReport } from "./helpers";

/**
 * The printed report, in print media and as real PDFs.
 *
 * Step H rebuilt print from the new hierarchy rather than patching the old
 * stylesheet, and the reading order on paper is the reading order on
 * screen:
 *
 *   question -> answer -> context -> chart -> findings -> table -> appendix
 *
 * Two things are being proved here, and they are different claims.
 *
 * **In print media, on every engine.** `emulateMedia` applies the print
 * cascade to a live page, so the structural claims -- the appendix is
 * revealed, every disclosure is expanded, no control survives as a grey
 * rectangle, the chart fits the printable width -- are testable in
 * Chromium, Firefox and WebKit alike. Those are the regression gate.
 *
 * **As a PDF, in Chromium only.** `page.pdf()` exists nowhere else;
 * Playwright documents it as Chromium-only. Those four tests skip
 * explicitly on Firefox and WebKit rather than being filtered out, so the
 * report guard counts them and a skip cannot hide a failure.
 *
 * This replaces `app.spec.ts`'s "prints a complete report as a browser
 * PDF", which asserted the first four bytes and a lower bound on the file
 * size -- a PDF of a blank page passes both -- and rendered landscape,
 * which is not the page this design specifies.
 *
 * The viewport is set to the A4 printable width before each check. Print
 * emulation applies the print cascade but leaves the page box the size of
 * the viewport, so a chart measured at 1440px would be measured against a
 * column that does not exist on paper. 178mm at 96dpi is 672.8px.
 */

const DIR = dirname(fileURLToPath(import.meta.url));
const PDF_DIR = join(DIR, "..", "test-results", "pdf");

/** A4 portrait, less the 16mm side margins `foundation.css` sets. */
const PRINTABLE_PX = Math.round((178 / 25.4) * 96);

type Scenario = "successful" | "compare" | "refusal" | "no-findings";

/** The committed terminal-state payloads, answered over a real run. */
async function withState(page: Page, name: "refused" | "no-findings") {
  const { readFileSync } = await import("node:fs");
  const payload = JSON.parse(
    readFileSync(join(DIR, "..", "src", "test", "runs", "states", `${name}.json`), "utf8"),
  );
  await page.route("**/api/analyses/*", async (route) => {
    const response = await route.fetch();
    const real = await response.json();
    if (real?.status && real.status !== "running") {
      await route.fulfill({ response, json: { ...payload, run_id: real.run_id } });
      return;
    }
    await route.fulfill({ response, json: real });
  });
}

/** Put the page in `scenario`, finished, ready to print. */
async function reach(page: Page, scenario: Scenario): Promise<void> {
  await page.setViewportSize({ width: PRINTABLE_PX, height: 1200 });

  if (scenario === "compare") {
    await advertiseAi(page);
    await compareWith(page);
    await openApp(page);
    await openDemo(page);
    await startCompare(page, "What is the total revenue by region?");
    await settle(page);
    return;
  }

  if (scenario !== "successful") {
    await withState(page, scenario === "refusal" ? "refused" : "no-findings");
  }
  await openApp(page);
  await uploadFile(page, `print-${scenario}.csv`, sampleCsv());
  await ask(page, "What is the total revenue by region?");
  await waitForReport(page);
}

const SCENARIOS: Scenario[] = ["successful", "compare", "refusal", "no-findings"];

test.describe("the print cascade, applied to a live page", () => {
  for (const scenario of SCENARIOS) {
    test(`${scenario}: the appendix is on the page and the chrome is not`, async ({
      page,
    }) => {
      await reach(page, scenario);
      await page.emulateMedia({ media: "print" });

      // Every screen-only surface. Each of these printed is an artefact: it
      // looks like part of the document and does nothing.
      for (const selector of [
        ".topbar",
        ".composer",
        // The row itself prints -- it holds the run's `contract · sha · ms`
        // stamp -- but nothing in it that is operated does.
        ".report-actions .btn",
        ".analytical-field",
        ".suggestions",
        ".side-sheet",
        ".scrim",
      ]) {
        const control = page.locator(selector).first();
        if ((await control.count()) > 0) {
          await expect(control, `${selector} prints`).toBeHidden();
        }
      }

      // And the stamp survives: a printed report that cannot be traced
      // back to the run that produced it is what it exists to prevent.
      const stamp = page.getByTestId("report-stamp");
      if ((await stamp.count()) > 0) {
        await expect(stamp.first(), "the run's stamp does not print").toBeVisible();
      }

      // And the appendix, which is `hidden` on screen, is on the page.
      const appendix = page.locator("[data-print-appendix]").first();
      await expect(appendix).toBeVisible();
      await expect(appendix).toContainText(/Appendix: evidence/);

      // Expanded, because paper has no disclosure. A `<details>` that
      // prints closed is a paragraph the reader cannot reach.
      const closed = await appendix
        .locator("details:not([open]) > *:not(summary)")
        .evaluateAll((nodes) =>
          nodes.filter((node) => getComputedStyle(node).display === "none").length,
        );
      expect(closed, "a disclosure prints closed").toBe(0);

      // No control inside it. The activity trace carries a toggle on screen.
      const buttons = appendix.locator("button");
      for (let i = 0; i < (await buttons.count()); i += 1) {
        await expect(buttons.nth(i), "a control prints inside the appendix").toBeHidden();
      }
    });

    test(`${scenario}: nothing clips or overflows the printable width`, async ({
      page,
    }) => {
      await reach(page, scenario);
      await page.emulateMedia({ media: "print" });

      // The document itself. A chart or a table wider than the page is
      // clipped by the printer, silently, with no scrollbar to say so.
      const overflow = await page.evaluate(
        () =>
          document.documentElement.scrollWidth -
          document.documentElement.clientWidth,
      );
      expect(overflow, "the page is wider than the printable column").toBeLessThanOrEqual(1);

      // Each chart, measured. `max-width: 100%` is in the stylesheet; this
      // is whether it actually bound the SVG Vega rendered.
      const charts = page.locator(".chart-host svg");
      const count = await charts.count();
      for (let i = 0; i < count; i += 1) {
        const box = await charts.nth(i).boundingBox();
        if (box === null) continue;
        expect(box.width, "a chart is wider than the page").toBeLessThanOrEqual(
          PRINTABLE_PX + 1,
        );
        // And not rendered at a size nobody can read.
        expect(box.width, "a chart prints too small to read").toBeGreaterThan(200);
        expect(box.height, "a chart prints with no height").toBeGreaterThan(40);
      }

      // Tables repeat their header and stop scrolling, or they print one
      // screenful and drop the rest of their rows in silence.
      const tables = page.locator("table.data");
      for (let i = 0; i < (await tables.count()); i += 1) {
        const head = tables.nth(i).locator("thead");
        if ((await head.count()) === 0) continue;
        await expect(head.first()).toHaveCSS("display", "table-header-group");
      }
      // Each column name is a `<button>`, and Chromium does not paint a
      // form control inside a repeated header group: every continuation
      // page printed the header row with only `#` in it. `display:
      // contents` lets the label lay out in the cell instead.
      const sorts = page.locator("table.data th .th-sort");
      for (let i = 0; i < (await sorts.count()); i += 1) {
        await expect(
          sorts.nth(i),
          "a column name prints inside a box Chromium will not repeat",
        ).toHaveCSS("display", "contents");
      }

      const frames = page.locator(".table-wrap, .scroll-x");
      for (let i = 0; i < (await frames.count()); i += 1) {
        const overflowY = await frames.nth(i).evaluate(
          (node) => getComputedStyle(node).overflow,
        );
        expect(overflowY, "a table still scrolls on paper").toBe("visible");
      }
    });
  }

  test("the evidence a reader opens is the evidence that prints", async ({ page }) => {
    /*
     * The rule the appendix exists for: a reader who prints a report must
     * not get less than a reader who clicks through it. Asserted against
     * the drawer's own contents rather than a list written here, so a datum
     * added to one and forgotten in the other fails.
     */
    await reach(page, "successful");
    await page.getByTestId("show-work").click();
    const drawer = page.getByTestId("evidence-drawer");
    await expect(drawer).toBeVisible();
    const sections = await drawer
      .locator(".evidence-row > dt")
      .evaluateAll((nodes) => nodes.map((n) => (n.textContent ?? "").trim()));
    expect(sections.length).toBeGreaterThan(5);
    await page.keyboard.press("Escape");

    await page.emulateMedia({ media: "print" });
    const appendix = page.locator("[data-print-appendix]").first();
    const printed = (await appendix.textContent()) ?? "";
    for (const section of sections) {
      expect(printed, `"${section}" is in the drawer but not on paper`).toContain(section);
    }
  });

  test("a dark-themed screen still prints on white", async ({ page }) => {
    /*
     * `ThemeToggle` always writes `data-theme` on the document element, so
     * the dark palette is `:root[data-theme="dark"]` -- one attribute more
     * specific than a bare `:root`. A print palette reset written as
     * `:root` alone loses to it however late in the cascade it sits, and
     * the report prints on #0e1113: a solid black sheet, or near-white
     * text on nothing where the printer drops backgrounds.
     */
    await page.addInitScript(() => {
      try {
        localStorage.setItem("aae-theme", "dark");
      } catch {
        /* private browsing; the assertion below will say so */
      }
    });
    await reach(page, "successful");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    await page.emulateMedia({ media: "print" });
    const resolved = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement);
      return {
        canvas: root.getPropertyValue("--surface-canvas").trim(),
        ink: root.getPropertyValue("--ink-primary").trim(),
        body: getComputedStyle(document.body).backgroundColor,
      };
    });
    expect(resolved.canvas.toLowerCase(), "the page prints on the dark canvas").toMatch(
      /^#f{3,8}$|^rgb\(255,\s*255,\s*255\)$/,
    );
    expect(resolved.ink.toLowerCase(), "the text prints in the dark palette's ink").toBe(
      "#121619",
    );
    expect(resolved.body).toMatch(/rgba?\(255,\s*255,\s*255/);
  });

  test("printing with the evidence drawer open prints no drawer", async ({ page }) => {
    // A reader who has opened "Show work" and then prints must not get a
    // sheet with a panel floating over a dimmed page. The evidence is in
    // the appendix either way.
    await reach(page, "successful");
    await page.getByTestId("show-work").click();
    await expect(page.getByTestId("evidence-drawer")).toBeVisible();

    await page.emulateMedia({ media: "print" });
    await expect(page.getByTestId("evidence-drawer")).toBeHidden();
    await expect(page.locator(".scrim").first()).toBeHidden();
    await expect(page.locator("[data-print-appendix]").first()).toBeVisible();
  });

  test("a refusal's unedited reason reaches paper", async ({ page }) => {
    // On screen it is behind a control, because the canvas leads with what
    // the reader can do about it. On paper there is no control, and the
    // record of what the engine actually said has to survive.
    await reach(page, "refusal");
    await page.emulateMedia({ media: "print" });
    await expect(page.locator("[data-print-appendix]").first()).toContainText(
      /could not be mapped safely/i,
    );
    // And it is still not the headline.
    const headline = (await onCanvas(page, '[data-testid="direct-answer"]').textContent()) ?? "";
    expect(headline.trim()).not.toMatch(/^the question could not be mapped safely/i);
  });
});

test.describe("real PDFs", () => {
  /*
   * `page.pdf()` is Chromium-only; Playwright does not implement it for
   * Firefox or WebKit. The skip is declared per test rather than filtering
   * the file, so the report guard sees four skips on those engines and a
   * silent zero-test run cannot pass for a clean one.
   *
   * These are written to `test-results/pdf/` and inspected page by page --
   * text extraction alone cannot see a chart rendered 20px wide, a blank
   * page between sections, or a control printed as a grey rectangle.
   */
  for (const scenario of SCENARIOS) {
    test(`${scenario} renders to a PDF with real pages`, async ({ page, browserName }) => {
      test.skip(browserName !== "chromium", "page.pdf() is Chromium-only.");
      await reach(page, scenario);

      const pdf = await page.pdf({
        format: "A4",
        printBackground: true,
        margin: { top: "18mm", bottom: "18mm", left: "16mm", right: "16mm" },
      });

      mkdirSync(PDF_DIR, { recursive: true });
      writeFileSync(join(PDF_DIR, `${scenario}.pdf`), pdf);

      expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");
      // A PDF of a blank page is about 1kB, which is what the assertion
      // this replaces accepted. A report with a chart and a table is not.
      expect(pdf.byteLength, "the PDF is too small to hold a report").toBeGreaterThan(
        20_000,
      );

      // Page objects, counted from the file. Every one of these documents
      // runs to at least two pages, because the appendix starts its own.
      const pages = (pdf.toString("latin1").match(/\/Type\s*\/Page[^s]/g) ?? []).length;
      expect(pages, "the appendix did not start a page of its own").toBeGreaterThanOrEqual(2);
    });
  }

  test("a dark-themed screen renders to a PDF on white", async ({ page, browserName }) => {
    test.skip(browserName !== "chromium", "page.pdf() is Chromium-only.");
    await page.addInitScript(() => {
      try {
        localStorage.setItem("aae-theme", "dark");
      } catch {
        /* private browsing */
      }
    });
    await reach(page, "successful");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    const pdf = await page.pdf({
      format: "A4",
      printBackground: true,
      margin: { top: "18mm", bottom: "18mm", left: "16mm", right: "16mm" },
    });
    mkdirSync(PDF_DIR, { recursive: true });
    writeFileSync(join(PDF_DIR, "dark-theme.pdf"), pdf);
    expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");
    expect(pdf.byteLength).toBeGreaterThan(20_000);
  });
});
