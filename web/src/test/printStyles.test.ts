/**
 * A block added to the report has to be added to the print rules too.
 *
 * The screen palette is tuned for a dark background: `--supported` is pale
 * and the `.notice` variants are paler, which on paper is white on white.
 * The report offers "Print / Save PDF" as a first-class path, so a block
 * that renders blank in a PDF is a silent loss of the thing a reader was
 * trying to keep.
 *
 * Read from the stylesheet rather than from a rendered page: jsdom does not
 * apply print media, so nothing else in this suite can see these rules.
 */

import { describe, expect, it } from "vitest";

import { atRuleBlock, stylesheet } from "./stylesheet";

// The twelve modules concatenated in `main.tsx`'s import order, which is
// what the bundler emits. Reading one module would miss the second
// `@media print` block, which lives at the end of `states.css` so that no
// earlier state rule can override print treatment.
const css = stylesheet();

/**
 * Every `@media print` block, concatenated in cascade order.
 *
 * There are three: the page foundations, the report's own rules, and the
 * state overrides last. Reading only the first was fine while the first was
 * `print.css`, and became wrong the moment a page-level block was added
 * ahead of it -- every assertion below then searched a block that was never
 * going to contain a report selector.
 *
 * The claim these tests make is about the print cascade as a whole: a block
 * added to the report has to be restated somewhere in print. Which module
 * states it is `cssArchitecture.test.ts`'s business, not this file's.
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

describe("print stylesheet", () => {
  const block = printBlock();

  it.each([
    [".direct-answer", "the answer a reader came for"],
    [".answer-text", "the answer sentence"],
    [".answer-scope", "the population and row count"],
    [".contract-diff", "where two interpretations differ"],
    [".applied-analysis", "the governed contract"],
    [".notice", "withheld findings"],
    ['[data-testid="partial-answer"]', "the partial-breakdown warning"],
    ['[data-testid="run-state-card"]', "a refusal or failure reason"],
  ])("restates %s in ink (%s)", (selector) => {
    // Matched to a selector boundary, not as a substring.
    //
    // `toContain(".answer-text")` passed after the rule was renamed to
    // `.answer-text-DISABLED`, because the old name is a prefix of the new
    // one. A mutation that deleted the print treatment for the answer
    // sentence therefore survived. The selector has to be followed by
    // something that cannot continue an identifier -- `,` `{` whitespace
    // or a combinator -- for the match to mean the rule is still there.
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    expect(block, `${selector} is no longer a selector in @media print`).toMatch(
      new RegExp(`${escaped}(?![-\\w])`),
    );
  });

  it("lets a large answer card and its table split across pages", () => {
    // The opposite of what this asserted before, and the cause of the
    // nearly blank pages in the saved PDFs: a card taller than a page that
    // may not be divided is pushed whole to the next one, leaving the
    // first mostly empty. Rows stay intact; the containers do not.
    expect(block).toMatch(/\.direct-answer \{[^}]*break-inside: auto/);
    expect(block).toMatch(/table\.data \{[^}]*break-inside: auto/);
    expect(block).toMatch(/table\.data tr \{[^}]*break-inside: avoid/);
  });

  it("repeats table headers on every printed page", () => {
    expect(block).toMatch(
      /table\.data thead \{[^}]*display: table-header-group/,
    );
  });

  it("does not leave a heading as the last thing on a page", () => {
    expect(block).toMatch(/break-after: avoid/);
  });

  it("hides controls that cannot be used on paper", () => {
    expect(block).toMatch(/\.result-foot \.btn[^}]*display: none/);
  });

  it("does not print a control that cannot be used on paper", () => {
    expect(block).toMatch(/\.direct-answer \.btn[^}]*display: none/);
  });

  it("leaves no screen-only colour variable inside the print block", () => {
    // `--supported` and friends resolve against the dark theme.
    expect(block).not.toMatch(/var\(--(supported|warning|rejected)/);
  });

  it("restates the chart's screen palette in ink", () => {
    // The chart is drawn for a dark background: axis labels at #9aa5b8 are
    // close to invisible on white paper, and the chart was the whole point
    // of printing the page.
    const block = printBlock();
    expect(block).toMatch(/\.chart-host svg text/);
    expect(block).toMatch(/fill: #222 !important/);
  });

  it("keeps the chart within the page rather than clipping it", () => {
    const block = printBlock();
    expect(block).toMatch(/\.chart-host svg[\s\S]*max-height: 230px !important/);
    expect(block).toMatch(/\.chart-card[\s\S]*break-inside: avoid/);
  });
});
