/**
 * What the printed report carries that the screen hid.
 *
 * Step H's rule, stated once: **a reader who prints a report must not get
 * less than a reader who clicks through it.** On screen the technical
 * record lives behind one control: "Show work" for a single run, "Inspect
 * both traces" for a Compare, and a control is not a thing that exists on
 * paper. Without an appendix, printing is a silent loss of provenance that
 * looks like a clean document.
 *
 * `printStyles.test.ts` is the other half and reads the stylesheet: that
 * the appendix is revealed in `@media print`, starts its own page, expands
 * every disclosure and prints no controls. This half is about the DOM --
 * that there is something there to reveal, that it holds the same evidence
 * the drawer does, and that it is inert on screen.
 *
 * The browser suite renders real PDFs from this markup and inspects the
 * pages.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CompareWorkspace } from "../components/CompareWorkspace";
import { EVIDENCE_SECTIONS, EvidenceBody } from "../components/EvidenceDrawer";
import { ReportWorkspace } from "../components/ReportWorkspace";
import type { RunPayload } from "../lib/types";

function load(...parts: string[]): RunPayload {
  return JSON.parse(readFileSync(join(__dirname, "runs", ...parts), "utf8"));
}

const grouped = load("grouped.json");

function workspace(run: RunPayload) {
  return render(
    <ReportWorkspace
      comparison={null}
      run={run}
      aiRun={null}
      aiError={null}
      config={null}
      deterministicPending={false}
    />,
  );
}

function side(title: string, run: RunPayload | null) {
  return { title, subtitle: "", run, error: null, pending: false, children: null };
}

describe("a printed report carries the evidence the screen hid", () => {
  it("renders an appendix inside the report, not beside it", () => {
    workspace(grouped);
    const appendix = screen.getByTestId("print-appendix");
    // Inside the report article, so it prints in the report's reading
    // order rather than as a detached block after the page.
    expect(screen.getByTestId("report-panel").contains(appendix)).toBe(true);
  });

  it("puts it last, after the result table", () => {
    workspace(grouped);
    const report = screen.getByTestId("report-panel");
    const appendix = screen.getByTestId("print-appendix");
    const table = report.querySelector(".report-table");
    expect(table, "the report has no result table to order against").not.toBeNull();
    // `DOCUMENT_POSITION_FOLLOWING`: the appendix comes after the table.
    expect(table!.compareDocumentPosition(appendix) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  it("holds every section the evidence drawer holds", () => {
    /*
     * Against `EVIDENCE_SECTIONS` rather than a list written here, so that
     * a datum added to the drawer and forgotten in print fails, and a datum
     * deleted from both fails the drawer's own test rather than passing
     * quietly in two places.
     */
    workspace(grouped);
    const text = screen.getByTestId("print-appendix").textContent ?? "";
    for (const section of EVIDENCE_SECTIONS) {
      expect(text, `the appendix drops "${section}"`).toContain(section);
    }
  });

  it("holds the same evidence the drawer renders, not a summary of it", () => {
    /*
     * Every term/value pair the drawer shows, matched in the appendix.
     *
     * Compared by structure rather than by raw text because the appendix is
     * deliberately *not* a copy: it renders with `expanded`, so the
     * planning audit is open and the MCP trace is shown, and the activity
     * log's toggle reads the other way round. Comparing strings would make
     * the test fail for the reason the appendix is correct.
     */
    const rows = (root: ParentNode) =>
      [...root.querySelectorAll(".evidence-row")].map((row) =>
        [
          (row.querySelector("dt")?.textContent ?? "").trim(),
          (row.querySelector("dd")?.textContent ?? "").replace(/\s+/g, " ").trim(),
        ].join(" = "),
      );

    const { unmount } = render(<EvidenceBody run={grouped} />);
    const drawerRows = rows(document.body);
    unmount();
    expect(drawerRows.length).toBeGreaterThan(5);

    workspace(grouped);
    const printed = rows(screen.getByTestId("print-appendix"));
    for (const row of drawerRows) {
      expect(printed, `the appendix drops "${row}"`).toContain(row);
    }
  });

  it("opens every disclosure, because paper has none", () => {
    // Not a CSS claim; `print.css` makes one too, and it is not enough.
    // Chromium lays a closed `<details>` out and then skips painting it, so
    // the planning audit printed as a heading over an empty block while
    // `getComputedStyle` reported `display: block` on its body.
    workspace(grouped);
    const appendix = screen.getByTestId("print-appendix");
    const closed = appendix.querySelectorAll("details:not([open])");
    expect(closed, "a disclosure is closed in the print appendix").toHaveLength(0);
    expect(appendix.querySelectorAll("details").length).toBeGreaterThan(0);
  });

  it("is inert on screen", () => {
    /*
     * `hidden`, not a class. The attribute keeps the appendix out of the
     * accessibility tree and out of the tab order, which a visually-hidden
     * class would not: a screen reader would otherwise read the whole
     * technical record aloud after every report, and a keyboard user would
     * tab through its disclosures.
     */
    workspace(grouped);
    const appendix = screen.getByTestId("print-appendix");
    expect(appendix).toHaveAttribute("hidden");
    expect(appendix).not.toBeVisible();
  });

  it("names each region once, not three times", () => {
    /*
     * `RunTimeline` carried its own `sr-only` "Run progress" heading, for
     * when it stands alone on the canvas during a run. In the evidence
     * drawer it sits inside a section that is already labelled "Run
     * progress" and carries a visible heading saying the same, so a screen
     * reader announced the name three times, and once the appendix
     * rendered the drawer's body unconditionally, every report had two
     * identical headings in its document.
     */
    workspace(grouped);
    const appendix = screen.getByTestId("print-appendix");
    const headings = [...appendix.querySelectorAll("h1, h2, h3")].map((h) =>
      (h.textContent ?? "").trim().toLowerCase(),
    );
    expect(headings.length).toBeGreaterThan(3);
    expect(new Set(headings).size, headings.join(" | ")).toBe(headings.length);
  });

  it("carries the unedited stop reason for a run that did not answer", () => {
    // The canvas leads with what the reader can do about it. The record of
    // what the engine actually said has to survive to paper.
    const refused = load("states", "refused.json");
    const raw = String(refused.stopped_reason ?? "");
    expect(raw.length).toBeGreaterThan(0);

    workspace(refused);
    expect(screen.getByTestId("print-appendix")).toHaveTextContent(raw);
  });
});

describe("a printed Compare carries both traces", () => {
  const ai = load("grouped.json");

  it("renders one appendix with a section per strategy", () => {
    render(
      <CompareWorkspace
        question="What is the total revenue by region?"
        deterministic={side("Deterministic", grouped)}
        ai={side("AI-planned", ai)}
      />,
    );
    const appendix = screen.getByTestId("compare-print-appendix");
    expect(appendix).toHaveAttribute("hidden");

    const sides = appendix.querySelectorAll("[data-strategy]");
    expect(sides).toHaveLength(2);
    expect([...sides].map((s) => s.getAttribute("data-strategy"))).toEqual([
      "Deterministic",
      "AI-planned",
    ]);
  });

  it("gives each strategy the full evidence, because a tab is a screen device", () => {
    render(
      <CompareWorkspace
        question="What is the total revenue by region?"
        deterministic={side("Deterministic", grouped)}
        ai={side("AI-planned", ai)}
      />,
    );
    for (const strategy of ["Deterministic", "AI-planned"]) {
      const block = screen
        .getByTestId("compare-print-appendix")
        .querySelector(`[data-strategy="${strategy}"]`)!;
      const text = block.textContent ?? "";
      for (const section of EVIDENCE_SECTIONS) {
        expect(text, `${strategy} drops "${section}"`).toContain(section);
      }
    }
  });

  it("prints nothing for a side that never produced a run", () => {
    // A heading over an empty block would read as evidence that is missing
    // rather than as a run that did not happen; the pane itself says so.
    render(
      <CompareWorkspace
        question="What is the total revenue by region?"
        deterministic={side("Deterministic", grouped)}
        ai={side("AI-planned", null)}
      />,
    );
    const sides = screen
      .getByTestId("compare-print-appendix")
      .querySelectorAll("[data-strategy]");
    expect(sides).toHaveLength(1);
    expect(sides[0]!.getAttribute("data-strategy")).toBe("Deterministic");
  });
});
