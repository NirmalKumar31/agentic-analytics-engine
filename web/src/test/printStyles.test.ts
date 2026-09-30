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

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "..", "styles.css"), "utf8");

function printBlock(): string {
  const start = css.indexOf("@media print {");
  expect(start).toBeGreaterThan(-1);
  let depth = 0;
  for (let i = css.indexOf("{", start); i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(start, i + 1);
    }
  }
  throw new Error("unterminated @media print block");
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
    expect(block).toContain(selector);
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
});
