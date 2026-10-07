/**
 * What the printed report has to contain, read from the stylesheet.
 *
 * jsdom does not apply print media, so nothing else in this suite can see
 * these rules. The browser suite renders real PDFs and inspects the pages;
 * this is the cheap half that catches a rule deleted by accident.
 *
 * The suite this replaces asserted the old selectors `.direct-answer`,
 * `.answer-text`, `.applied-analysis`, `.contract-diff` and the notice
 * variants, because the screen palette was tuned for a dark background
 * and those blocks printed white on white. None of those blocks exists now,
 * so restating them would be testing a stylesheet against a page that is
 * gone. The claims are rewritten against the hierarchy that replaced it.
 */

import { describe, expect, it } from "vitest";

import { atRuleBlock, moduleSource, modules, stylesheet, withoutComments } from "./stylesheet";

const css = stylesheet();

/**
 * Every `@media print` block, concatenated in cascade order.
 *
 * There are several: the page foundations, the landing's and the
 * composer's screen-only controls, the timeline, the report's own rules and
 * the state overrides last. The claim made here is about the print cascade
 * as a whole; which module states a rule is `cssArchitecture.test.ts`'s
 * business.
 */
function printBlock(): string {
  const blocks: string[] = [];
  for (let nth = 0; ; nth += 1) {
    const block = atRuleBlock(css, "@media print {", nth);
    if (block === null) break;
    blocks.push(block);
  }
  expect(blocks.length, "no @media print block in the assembled stylesheet").toBeGreaterThan(0);
  return blocks.join("\n");
}

describe("the printed page", () => {
  const block = printBlock();

  it("prints on white, in ink, whatever theme the screen was in", () => {
    // A dark-mode page sent to a printer is either an empty sheet or a
    // solid black one.
    expect(block).toMatch(/--surface-canvas:\s*#fff/i);
    expect(block).toMatch(/--ink-primary:\s*#121619/i);
  });

  it("wins against the dark palette, which is an attribute selector", () => {
    /*
     * `ThemeToggle` always writes `data-theme` on the document element, so
     * the dark palette is `:root[data-theme="dark"]`, one attribute more
     * specific than a bare `:root`. A print reset written as `:root` alone
     * loses to it and the page prints on #0e1113, however late in the
     * cascade it sits.
     *
     * Asserted against the stylesheet because nothing else can see it: the
     * declarations are identical either way, and only the selector differs.
     */
    const reset = block.slice(block.indexOf("--surface-canvas"));
    expect(reset.length).toBeGreaterThan(0);
    const selector = block.slice(0, block.indexOf("--surface-canvas"));
    expect(selector, "the print palette reset is not attribute-qualified").toMatch(
      /:root\[data-theme\]/,
    );
    // And the dark palette really is written that way, so this stays the
    // right fix rather than a defence against a selector nobody uses.
    expect(withoutComments(stylesheet())).toMatch(/:root\[data-theme="dark"\]\s*\{/);
  });

  it("preserves the colours that carry meaning", () => {
    // Chrome and Safari drop backgrounds and desaturate without this, and a
    // withheld verdict then prints identical to a verified one.
    expect(block).toMatch(/print-color-adjust:\s*exact/);
  });

  it("gives the page a size and real margins", () => {
    const page = atRuleBlock(css, "@page", 0);
    expect(page).not.toBeNull();
    expect(page!).toMatch(/size:\s*A4/i);
    expect(page!).toMatch(/margin:/);
  });
});

describe("the reading order survives", () => {
  const block = printBlock();

  it("keeps the answer at display scale", () => {
    // The one thing on the page that must not be shrunk to fit.
    expect(block).toMatch(/\.report \.display\s*\{[^}]*font-size:\s*20pt/);
  });

  it("keeps the answer and its context line on the same page", () => {
    expect(block).toMatch(/\.report \.display\s*\{[^}]*break-after:\s*avoid/);
    expect(block).toMatch(/\.report \.context-line\s*\{[^}]*break-before:\s*avoid/);
  });

  it("lets a long report flow while keeping each finding whole", () => {
    expect(block).toMatch(/\.finding-item\s*\{[^}]*break-inside:\s*avoid/);
  });
});

describe("the chart", () => {
  const block = printBlock();

  it("fills the printable width", () => {
    expect(block).toMatch(/\.chart-host svg\s*\{[\s\S]*?max-width:\s*100%/);
  });

  it("is bounded in height, so it cannot take a page of its own", () => {
    expect(block).toMatch(/\.chart-host svg\s*\{[\s\S]*?max-height:\s*\d+mm/);
  });

  it("restates the axis text in ink, over Vega's inline colours", () => {
    // Vega writes its colours inline, which outranks any rule here.
    expect(block).toMatch(/\.chart-host svg text\s*\{[^}]*fill:\s*#[0-9a-f]+\s*!important/i);
  });

  it("does not break across a page", () => {
    expect(block).toMatch(/\.report-visual\s*\{[^}]*break-inside:\s*avoid/);
  });

  it("outlines every mark, whatever theme baked its fill", () => {
    /*
     * Vega resolves `--series-*` at embed time and writes the result inline
     * on the SVG, where no print rule reaches it. A chart embedded in dark
     * mode prints `#54becc` on white, about 2:1, no discernible boundary,
     * and WCAG 1.4.11 is about exactly that. A hairline in ink around each
     * mark satisfies it whatever the fill turns out to be.
     */
    expect(block).toMatch(
      /g\.mark-rect path[\s\S]{0,300}?\{[^}]*stroke:\s*#[0-9a-f]+\s*!important/i,
    );
    expect(block).toMatch(
      /g\.mark-rect path[\s\S]{0,300}?\{[^}]*stroke-width:\s*[\d.]+\s*!important/,
    );
    // The rules are not marks: outlining the gridlines would print a cage.
    const marks = block.slice(block.indexOf("g.mark-rect path"));
    expect(marks.slice(0, marks.indexOf("}"))).not.toMatch(/gridline|path\.domain/);
  });
});

describe("nothing animates on paper", () => {
  const block = printBlock();

  it("cancels every animation and transition", () => {
    /*
     * Not tidiness. `.activity-row` enters with `animation: stream ... both`
     * whose `from` state is `opacity: 0`, and Chromium restarts animations
     * when it lays the page out to print, so the activity trace printed
     * as an empty bordered box. `animation-fill-mode: both` then holds it
     * at zero opacity rather than letting it finish.
     */
    expect(block).toMatch(/\*,[\s\S]{0,60}\{[^}]*animation:\s*none\s*!important/);
    expect(block).toMatch(/\*,[\s\S]{0,60}\{[^}]*transition:\s*none\s*!important/);
  });
});

describe("the result table", () => {
  const block = printBlock();

  it("repeats its header on every page", () => {
    expect(block).toMatch(/table\.data thead\s*\{[^}]*display:\s*table-header-group/);
  });

  it("stops scrolling, so every row prints", () => {
    // A scroll frame prints one screenful and silently drops the rest.
    expect(block).toMatch(/\.table-wrap,\s*\.scroll-x\s*\{[^}]*overflow:\s*visible\s*!important/);
  });

  it("does not split a row across a page break", () => {
    expect(block).toMatch(/table\.data tr\s*\{[^}]*break-inside:\s*avoid/);
  });

  it("unwraps the sort button, or the repeated header loses its names", () => {
    // Chromium does not paint a form control inside a repeated header
    // group. Each column name is a `<button>` because the columns sort, so
    // every continuation page printed the header row with only `#` in it.
    expect(block).toMatch(/\.th-sort\s*\{[^}]*display:\s*contents/);
  });

  it("unsticks the header, or it repeats as an empty row", () => {
    // A `position: sticky` header is painted once, at the scroll position
    // it was stuck to. Every later page then gets the row's box with
    // nothing in it, which reads as a column that lost its name.
    expect(block).toMatch(/table\.data th\s*\{[^}]*position:\s*static/);
  });
});

describe("the evidence appendix", () => {
  const block = printBlock();

  it("makes the hidden appendix visible on paper", () => {
    // `[hidden]` sets `display: none` in the UA sheet, so the override has
    // to name the attribute or it loses on specificity.
    expect(block).toMatch(
      /\[data-print-appendix\]\[hidden\][\s\S]*?display:\s*block\s*!important/,
    );
  });

  it("starts it on its own page", () => {
    expect(block).toMatch(
      /\[data-print-appendix\]\[hidden\][\s\S]*?break-before:\s*page/,
    );
  });

  it("expands every disclosure, because paper has none", () => {
    expect(block).toMatch(/details,[\s\S]*?display:\s*block\s*!important/);
  });

  it("drops the summary that repeats the heading above it", () => {
    // The planning audit's `<summary>` reads "Planning audit", directly
    // under the appendix's own "Planning audit" heading.
    expect(block).toMatch(
      /\.print-appendix \.evidence-plan details > summary\s*\{[^}]*display:\s*none/,
    );
    // And the rule that expands a disclosure's body exempts the summary,
    // or its `!important` outranks the line above and the repeat comes
    // back with nothing to say it had been decided against.
    expect(block).toMatch(/details > \*:not\(summary\)/);
  });

  it("prints no controls inside it", () => {
    expect(block).toMatch(/\.print-appendix button\s*\{[^}]*display:\s*none\s*!important/);
  });

  it("lets the activity trace run to its full length", () => {
    // It scrolls to 340px on screen. A scroll frame on paper prints one
    // screenful and drops the rest of the trace with nothing to say so.
    expect(block).toMatch(
      /\.print-appendix \.activity\s*\{[\s\S]*?max-height:\s*none/,
    );
    expect(block).toMatch(
      /\.print-appendix \.activity\s*\{[\s\S]*?overflow:\s*visible/,
    );
  });

  it("drops the panel head that repeats the section heading", () => {
    // "Activity trace", then "Activity", over a control that does not
    // print.
    expect(block).toMatch(/\.print-appendix \.panel-head\s*\{[^}]*display:\s*none/);
  });

  it("gives each Compare strategy its own page", () => {
    expect(block).toMatch(/\.print-appendix-side\s*\{[^}]*break-before:\s*page/);
    expect(block).toMatch(
      /\.print-appendix-side:first-of-type\s*\{[^}]*break-before:\s*auto/,
    );
  });
});

describe("screen-only chrome does not print", () => {
  const block = printBlock();

  /*
   * A control printed as a grey rounded rectangle is an artefact: it looks
   * like part of the document and does nothing. Each of these is a surface
   * that exists to be operated.
   */
  it.each([
    [".topbar", "the header"],
    [".composer", "the question composer"],
    [".landing", "the landing"],
    [".analytical-field", "the ambient field"],
    [".timeline", "the run timeline"],
    [".side-sheet", "a side sheet"],
    [".scrim", "the scrim behind a sheet"],
    [".report-actions .btn", "the report's own buttons"],
    [".evidence-tabs", "the evidence drawer's tabs"],
    [".suggestions", "the suggested questions"],
    /*
     * The sentence that explains a control, not just the control.
     *
     * A PDF printed "opens one drawer with a tab per strategy" underneath
     * a button print had already hidden, an instruction about an
     * interaction its reader cannot perform. The button was in this list;
     * the microcopy explaining it was not.
     */
    [".compare-action-note", "the hint under Compare's evidence button"],
  ])("%s is hidden (%s)", (selector) => {
    const escaped = selector.replace(".", "\\.");
    expect(
      block,
      `${selector} is not hidden in print`,
    ).toMatch(new RegExp(`${escaped}[^{]*\\{[^}]*display:\\s*none`, "s"));
  });

  it("hides them with one rule rather than ten", () => {
    // They are a single selector list; a per-element rule is how one gets
    // forgotten.
    expect(block).toMatch(
      /\.topbar,[\s\S]{0,900}?\.action-note\s*\{\s*display:\s*none\s*!important/,
    );
  });

  it("keeps the run's stamp, which is not a control", () => {
    // `.report-actions` holds two buttons and the `contract · sha · ms`
    // line. The buttons do not print; the line does, because a printed
    // report that cannot be traced back to its run is the thing the stamp
    // exists to prevent.
    expect(block).toMatch(/\.report-actions\s*\{[^}]*display:\s*block/);
    expect(block).toMatch(/\.report-stamp\s*\{[^}]*color:\s*#/);
    expect(
      block,
      "the actions row is hidden wholesale, taking the stamp with it",
    ).not.toMatch(/\.report-actions,/);
  });
});

describe("no rule in the print cascade names a block that no longer exists", () => {
  /*
   * The old stylesheet restated `.direct-answer`, `.answer-text`,
   * `.answer-scope`, `.applied-analysis` and `[data-testid="run-state-card"]`
   * in ink. Every one of those is gone from the application, and a print
   * rule for a selector that never matches is dead weight that reads as
   * coverage.
   */
  const RETIRED = [
    ".direct-answer",
    ".answer-text",
    ".answer-scope",
    ".applied-analysis",
    '[data-testid="run-state-card"]',
    ".lane",
    ".flow",
    ".steps",
  ];

  const block = withoutComments(printBlock());
  const sources = modules()
    .map(([, source]) => withoutComments(source))
    .join("\n");

  it.each(RETIRED)("%s is not restated in print", (selector) => {
    const escaped = selector.replace(/[.[\]"=]/g, (c) => `\\${c}`);
    expect(block).not.toMatch(new RegExp(`${escaped}(?![-\\w])`));
  });

  it.each(RETIRED)("%s is not styled anywhere else either", (selector) => {
    // If the selector were still in use on screen, removing its print rule
    // would be a regression rather than a cleanup. It is not in use.
    const escaped = selector.replace(/[.[\]"=]/g, (c) => `\\${c}`);
    expect(sources).not.toMatch(new RegExp(`${escaped}(?![-\\w])\\s*[,{]`));
  });
});

describe("the flowchart prints, and its control does not", () => {
  // Absent is a failure rather than a skip: the module losing its print
  // block is exactly the regression this file exists to catch.
  const print = atRuleBlock(moduleSource("styles/print.css"), "@media print") ?? "";

  it("names the control in the one chrome-off rule, in this module", () => {
    /*
     * In `print.css` because in `timeline.css` it did nothing:
     * `[data-testid="run-flow-open"]` ties with `.btn` on specificity and
     * `controls.css` loads after `timeline.css`, so `display: inline-flex`
     * won and a printed sheet carried a button.
     *
     * This is the weaker of the two checks on purpose. It cannot tell
     * whether the rule wins, only that it is here. The hosted print cell
     * reads the computed style, which is what caught the defect.
     */
    expect(print).toMatch(/\[data-testid="run-flow-open"\],/);
    expect(print).toMatch(/\.run-flow-alternative,/);
  });

  it("leaves the spine a column on paper by scoping the row to screen", () => {
    /*
     * Not by overriding `grid-auto-flow` here, which was the first
     * attempt and printed four arrowheads against the right margin: a
     * printed sheet is laid out from the reader's viewport, so
     * `@media (min-width: 1440px)` still matched and the row's
     * *connectors* stayed applied to a column of full-width boxes. One
     * word in the query takes the geometry with it.
     */
    const timeline = withoutComments(moduleSource("styles/timeline.css"));
    expect(timeline).toMatch(/@media screen and \(min-width: 1440px\)/);
    expect(
      withoutComments(print),
      "the print block overrides the spine's flow again",
    ).not.toMatch(/\.run-flow-track/);
  });

  it("does not hide the flowchart itself", () => {
    /*
     * The live timeline is hidden on paper because it is a progress
     * indicator. The flowchart is a record, and a printed report that does
     * not say what ran is the thing it exists to prevent.
     *
     * This asserted that `print.css` carried *no* `.run-flow` rule at all,
     * which stood in for "is not hidden" only while there was nothing
     * legitimate to say about it on paper. There is now: a Compare
     * prints one spine per strategy and each has to stay whole across a
     * page break, so the check names what it actually forbids: a rule
     * that takes the flowchart off the page. A proxy that fails on a rule
     * it was never aimed at teaches the next person to delete the test.
     */
    const source = withoutComments(print);
    const hiding = /([^{}]+)\{([^}]*)\}/g;
    for (const rule of source.matchAll(hiding)) {
      const selectors = rule[1] ?? "";
      const body = rule[2] ?? "";
      if (!/(display\s*:\s*none|visibility\s*:\s*hidden)/.test(body)) continue;
      expect(
        selectors,
        `a print rule hides the flowchart: ${selectors.trim()}`,
        // `.run-flow-open` and `.run-flow-alternative` are *meant* to be
        // in a hiding rule, so this matches the section itself only.
      ).not.toMatch(/\.run-flow(?![\w-])/);
    }
  });
});
