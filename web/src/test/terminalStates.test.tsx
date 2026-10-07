/**
 * The six terminal states, from real payloads.
 *
 * A run reaches exactly one of these and the page renders exactly that one.
 * The fixtures in `src/test/runs/states/` are captured from a local server
 * in fake mode:
 *
 *   refused, no-findings, completed    asked of the engine and saved as-is
 *   verification-withheld              a real completed run carrying the
 *                                      withheld verdict from a committed
 *                                      recording, with the published
 *                                      findings removed so the state is
 *                                      reached
 *   quota-stopped, failed, cancelled   the real completed payload with
 *                                      exactly the fields the API sets for
 *                                      that outcome, and nothing else
 *                                      changed
 *
 * The last three are derived because the server classifies them from how a
 * run *ended*: a raise, a budget exception, a withdrawn dataset, and a
 * scripted provider cannot be made to raise them from a question. The
 * derivation is one dictionary of fields, written down beside the fixture.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EvidenceBody } from "../components/EvidenceDrawer";
import { canvasText, expectOffCanvas } from "./canvas";
import { ReportWorkspace } from "../components/ReportWorkspace";
import { runState } from "../lib/runState";
import type { RunPayload } from "../lib/types";

const DIR = join(__dirname, "runs", "states");

function load(name: string): RunPayload {
  return JSON.parse(readFileSync(join(DIR, `${name}.json`), "utf8"));
}

/** Every state that is not a verified answer. */
const STATES = [
  ["refused", "refused"],
  ["no-findings", "no_findings"],
  ["verification-withheld", "verification_withheld"],
  ["quota-stopped", "quota_stopped"],
  ["failed", "execution_failed"],
  ["cancelled", "cancelled"],
] as const;

function show(name: string) {
  const run = load(name);
  render(
    <ReportWorkspace
      comparison={null}
      run={run}
      aiRun={null}
      aiError={null}
      config={null}
      deterministicPending={false}
    />,
  );
  return run;
}

describe("the fixtures really are the states they claim", () => {
  it.each(STATES)("%s classifies as %s", (name, expected) => {
    expect(runState(load(name)).state).toBe(expected);
  });

  it("the six are distinct", () => {
    const seen = STATES.map(([name]) => runState(load(name)).state);
    expect(new Set(seen).size).toBe(STATES.length);
  });
});

describe("each state is rendered once, inside the report", () => {
  it.each(STATES)("%s", (name) => {
    show(name);
    const report = screen.getByTestId("report-panel");

    // One headline, and it is inside the report column, not a banner
    // above the dataset strip, which is where an `order: -2` rule in
    // states.css used to put the state card.
    const answers = screen.getAllByTestId("direct-answer");
    expect(answers).toHaveLength(1);
    expect(report.contains(answers[0]!)).toBe(true);

    // And the headline is said once, on the canvas. The print appendix
    // is a second, hidden copy of the evidence drawer, and for a state whose
    // headline is derived from the stop reason the same words legitimately
    // appear there too.
    const text = (answers[0]!.textContent ?? "").trim();
    expect(text.length).toBeGreaterThan(0);
    const said = canvasText(report).split(text).length - 1;
    expect(said, `the headline appears ${said} times`).toBe(1);
  });
});

describe("a completed run that published nothing is not a refusal", () => {
  /*
   * The distinction these states exist for. "No findings" is a `completed`
   * run: the engine executed the contract and had nothing to claim. It was
   * carrying the eyebrow "Not answered", which is the language of a run
   * that declined, and once a reader has read that, the difference
   * between the two is gone.
   */
  it("says the execution completed, in those words", () => {
    show("no-findings");
    expect(screen.getByTestId("report-panel")).toHaveTextContent(
      /ran and completed/i,
    );
  });

  it("never uses refusal language", () => {
    show("no-findings");
    const report = screen.getByTestId("report-panel");
    for (const forbidden of [/not answered/i, /refus/i, /could not be mapped/i]) {
      expect(report, `"${forbidden}" on a completed run`).not.toHaveTextContent(
        forbidden,
      );
    }
  });

  it("says nothing was withheld, rather than blaming a verifier", () => {
    show("no-findings");
    expect(screen.getByTestId("report-panel")).toHaveTextContent(
      /nothing was withheld/i,
    );
  });

  it("is told apart from verification withholding something", () => {
    show("verification-withheld");
    const report = screen.getByTestId("report-panel");
    expect(report).toHaveTextContent(/withheld at verification/i);
    // The opposite claim: this run *did* have a claim to check.
    expect(report).not.toHaveTextContent(/no finding to check/i);
  });
});

describe("a refusal leads with what to do about it", () => {
  it("does not use the engine's own framing as the headline", () => {
    const run = show("refused");
    const headline = (
      screen.getByTestId("direct-answer").textContent ?? ""
    ).trim();

    // The raw stop reason begins "the question could not be mapped
    // safely: ...", mid-sentence, lower case, describing the engine's
    // difficulty rather than the reader's next move.
    expect(run.stopped_reason).toMatch(/could not be mapped safely/i);
    expect(headline).not.toMatch(/^the question could not be mapped safely/i);

    // What is left is the actionable part, and it is a real sentence.
    expect(headline.charAt(0)).toBe(headline.charAt(0).toUpperCase());
    expect(headline.length).toBeGreaterThan(10);
  });

  it("keeps the actionable instruction the engine gave", () => {
    show("refused");
    // The fixture's reason ends by naming what the reader should do.
    expect(screen.getByTestId("report-panel")).toHaveTextContent(
      /name a numeric column/i,
    );
  });

  it("says nothing was published", () => {
    show("refused");
    expect(screen.getByTestId("report-panel")).toHaveTextContent(
      /nothing was published/i,
    );
  });
});

describe("the engine's internal framing stays out of the canvas", () => {
  /*
   * The rule is about *framing*, not about every string the engine wrote.
   *
   * A refusal's raw reason begins "the question could not be mapped
   * safely: ...", mid-sentence, lower case, describing the engine's own
   * difficulty. That must not be the headline. The remainder is the
   * actionable part and belongs on the canvas, which is exactly what
   * requirement 4 asks for.
   *
   * The quota message is different in kind: `governed.py` raises with
   * "This AI run reached its input token limit.", a sentence already
   * written for a reader. Suppressing it would be hiding the clearest
   * statement of what happened in order to satisfy a rule about internal
   * phrasing.
   *
   * So: no internally-framed prefix on the canvas, and the unedited record
   * in the drawer for every state that has one.
   */
  const INTERNAL = [
    /the question could not be mapped safely:/i,
    /the question was not executed because/i,
  ];

  it.each(STATES)("%s shows no internal framing", (name) => {
    show(name);
    // The canvas, not the DOM: the unedited reason is *required* to be in
    // the evidence appendix, which is the next test but one.
    const canvas = canvasText(screen.getByTestId("report-panel"));
    for (const pattern of INTERNAL) {
      expect(canvas, `${name} carries internal framing`).not.toMatch(pattern);
    }
  });

  it.each([["refused"], ["quota-stopped"], ["cancelled"]])(
    "%s keeps the unedited reason in the evidence drawer",
    (name) => {
      const run = load(name);
      const raw = (run.stopped_reason || run.error || "").trim();
      expect(raw.length, `${name} has no reason to preserve`).toBeGreaterThan(0);

      render(<EvidenceBody run={run} />);
      expect(screen.getByTestId("raw-stop-reason")).toHaveTextContent(raw);
    },
  );

  it("failed keeps its error in the evidence drawer", () => {
    const run = load("failed");
    render(<EvidenceBody run={run} />);
    expect(screen.getByTestId("raw-stop-reason")).toHaveTextContent(
      String(run.error),
    );
  });

  it("keeps no technical panel resident for any state", () => {
    for (const [name] of STATES) {
      const { unmount } = render(
        <ReportWorkspace
          comparison={null}
          run={load(name)}
          aiRun={null}
          aiError={null}
          config={null}
          deterministicPending={false}
        />,
      );
      // Not resident on the canvas. A copy inside the `hidden` print
      // appendix is the point of step H and is checked for separately; what
      // must not happen is the reader being shown one.
      for (const testId of ["planning-audit", "activity", "run-timeline"]) {
        expectOffCanvas(document.body, `[data-testid="${testId}"]`);
      }
      unmount();
    }
  });
});

describe("a completed run never wears failure language", () => {
  /*
   * The brief names four words that must not appear on a `completed`
   * state: `failed`, `error`, `went wrong`, `problem`. Each is a word a
   * reader takes as "something broke, your result may be wrong", and two
   * of the six states (`completed` with findings, and `no_findings`)
   * are runs where nothing broke at all.
   *
   * This existed only as a single `/failed/i` check in one browser test.
   * The whole vocabulary is pinned here, on the canvas rather than on the
   * document: the print appendix carries the engine's own `Outcome` row,
   * where the literal word is the record and belongs.
   */
  // No `\b` on the trailing edge: this matches against concatenated
  // `textContent`, where "Failed" is immediately followed by the next
  // element's first letter and a word boundary never occurs.
  const FAILURE_WORDS = [/failed/i, /error/i, /went wrong/i, /problem/i];

  it.each([["completed"], ["no-findings"]])(
    "%s says nothing broke",
    (name) => {
      show(name);
      const canvas = canvasText(screen.getByTestId("report-panel"));
      for (const word of FAILURE_WORDS) {
        expect(canvas, `${name} uses "${word}"`).not.toMatch(word);
      }
    },
  );

  it("and the states that did break still say so", () => {
    // The control. If the words were simply absent from every string in
    // the application, the assertion above would be proving nothing.
    show("failed");
    const canvas = canvasText(screen.getByTestId("report-panel"));
    expect(
      FAILURE_WORDS.some((word) => word.test(canvas)),
      "an execution failure does not name itself",
    ).toBe(true);
  });
});

describe("a failure does not contradict what is on the page", () => {
  /*
   * The copy said "Nothing partial has been kept" unconditionally, and the
   * derived `failed` fixture (a real completed payload with exactly the
   * fields the API sets for that outcome) renders a chart, two findings
   * and a full result table underneath it. A reader is told nothing was
   * kept while looking at what was kept.
   */
  it("says nothing was kept only when nothing was", () => {
    // The signal is what the canvas draws, not `findings`: an uploaded run
    // carries its result in `presentation`, and this fixture has an empty
    // `findings` array alongside a chart, two highlights and a table.
    const run = load("failed");
    expect((run.findings ?? []).length).toBe(0);
    expect(
      (run.presentation?.highlights ?? []).length,
      "the fixture draws nothing, so there is no contradiction to catch",
    ).toBeGreaterThan(0);

    show("failed");
    const canvas = canvasText(screen.getByTestId("report-panel"));
    expect(canvas, "a visible result is described as not kept").not.toMatch(
      /nothing partial has been kept/i,
    );
    expect(canvas).toMatch(/what had been published before the run stopped/i);
  });

  it("and says it when the run really kept nothing", () => {
    const run: RunPayload = {
      ...load("failed"),
      findings: [],
      presentation: undefined,
      results: {},
    };
    render(
      <ReportWorkspace
        comparison={null}
        run={run}
        aiRun={null}
        aiError={null}
        config={null}
        deterministicPending={false}
      />,
    );
    expect(canvasText(screen.getByTestId("report-panel"))).toMatch(
      /nothing partial has been kept/i,
    );
  });
});

describe("severity is carried by the state, not by a control", () => {
  it.each(STATES)("%s marks its own tone", (name) => {
    show(name);
    const report = screen.getByTestId("report-panel");
    expect(report).toHaveAttribute("data-tone");
    const tone = report.getAttribute("data-tone");
    expect(["warn", "error", "neutral", "supported"]).toContain(tone);
  });

  it("a verified answer carries no terminal tone at all", () => {
    show("completed");
    expect(screen.getByTestId("report-panel")).not.toHaveAttribute("data-tone");
    expect(screen.queryByTestId("terminal-state")).toBeNull();
  });
});
