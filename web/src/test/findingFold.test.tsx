/**
 * One finding expanded on arrival at phone widths.
 *
 * The brief asks for it; the implementation did not have it, on the
 * reasonable ground that the engine's one-line label/value rows have
 * nothing worth folding. That is true of two rows and false of six: a
 * recorded run publishes six full-sentence findings, and on a 390px screen
 * they push the result table a long way down the page.
 *
 * So the fold is conditional, and these pin both halves of the condition.
 * A fold that triggered on two rows would hide a line to save a line, and
 * one that never triggered would be the requirement quietly dropped.
 */

import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnswerReport } from "../components/AnswerReport";
import type { ReportModel } from "../lib/reportModel";

/** `useMediaQuery` reads `matchMedia`, which jsdom does not implement. */
function atWidth(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  );
}

function model(count: number): ReportModel {
  return {
    eyebrow: null,
    headline: "Revenue rose 21%",
    contextLine: "502,819 rows",
    summary: null,
    chart: null,
    chartSnapshot: undefined,
    noChartReason: null,
    highlights: Array.from({ length: count }, (_, i) => ({
      id: `f${i}`,
      label: `Finding number ${i + 1}, stated as a whole sentence.`,
      value: null,
      compare: null,
    })),
    notes: [],
    tableSnapshot: undefined,
    previewRows: 12,
    displayFields: null,
    partial: false,
    partialDetail: null,
    terminalState: null,
    terminalTone: null,
    compatibilityDerived: false,
  } as unknown as ReportModel;
}

function show(count: number, compact = false) {
  return render(
    <AnswerReport
      question="Revenue increased in Q3 2025, but gross margin fell. What caused it?"
      model={model(count)}
      publishedCount={count}
      withheldCount={0}
      onShowEvidence={() => {}}
      compact={compact}
      run={null}
    />,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("on a phone, with more findings than fit", () => {
  beforeEach(() => atWidth(true));

  it("shows one and folds the rest", () => {
    show(6);
    const list = screen.getAllByRole("list")[0]!;
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);

    const more = screen.getByText(/5 more findings/i);
    expect(more).toBeVisible();
  });

  it("folds them behind a disclosure, not out of the document", () => {
    // A reader who prints gets all six: the print cascade expands every
    // `<details>`, and the fold is a `<details>` for that reason.
    show(6);
    const details = document.querySelector(".findings-more");
    expect(details?.tagName.toLowerCase()).toBe("details");
    expect(within(details as HTMLElement).getAllByRole("listitem")).toHaveLength(5);
  });

  it("numbers the folded findings on from the one above", () => {
    show(6);
    const folded = document.querySelector(".findings-more ol") as HTMLOListElement;
    expect(folded.getAttribute("start")).toBe("2");
    const ranks = [...folded.querySelectorAll(".finding-rank")].map(
      (node) => node.textContent,
    );
    expect(ranks).toEqual(["2", "3", "4", "5", "6"]);
  });

  it("leaves three or fewer alone", () => {
    // Folding two one-line rows hides a line to save a line.
    show(3);
    expect(document.querySelector(".findings-more")).toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("leaves a Compare pane alone, however many it has", () => {
    // A pane is already a summary beside another summary; folding inside
    // one buries the comparison.
    show(6, true);
    expect(document.querySelector(".findings-more")).toBeNull();
  });
});

describe("on anything wider", () => {
  beforeEach(() => atWidth(false));

  it("shows every finding", () => {
    show(6);
    expect(document.querySelector(".findings-more")).toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
  });
});
