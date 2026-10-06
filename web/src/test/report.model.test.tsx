/**
 * The report, built from real run payloads.
 *
 * Every fixture in `src/test/runs/` was captured from a local server in
 * fake mode by actually asking the question and saving what came back. None
 * of them is hand-written, which matters because hand-written data is
 * written to render nicely: the two real shapes only became visible when
 * the payloads were read.
 *
 *   an uploaded file   -> `presentation` is populated
 *   the demo warehouse -> `presentation` is **null**, because it is
 *                         answered through the metric registry
 *
 * A redesign of the presentation path alone would have left the demo
 * warehouse -- the first thing most visitors open -- on the old report.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ActivityLog } from "../components/ActivityLog";
import { EvidenceBody } from "../components/EvidenceDrawer";
import { AnswerReport } from "../components/AnswerReport";
import { reportModel } from "../lib/reportModel";
import type { RunPayload } from "../lib/types";

const RUNS = join(__dirname, "runs");

function load(name: string): RunPayload {
  return JSON.parse(readFileSync(join(RUNS, `${name}.json`), "utf8"));
}

const NAMES = readdirSync(RUNS)
  .filter((file) => file.endsWith(".json"))
  .map((file) => file.replace(/\.json$/, ""));

function modelFor(run: RunPayload) {
  return reportModel({
    presentation: run.presentation,
    report: run.report,
    findings: run.findings,
    rejected: run.rejected,
    charts: run.charts,
    results: run.results,
    queryContract: run.query_contract ?? null,
  });
}

function renderReport(run: RunPayload) {
  return render(
    <AnswerReport
      question={run.question}
      model={modelFor(run)}
      publishedCount={run.findings.length}
      withheldCount={run.rejected.length}
      onShowEvidence={() => undefined}
    />,
  );
}

describe("the captured fixtures are real and cover the shapes that matter", () => {
  it("includes both payload shapes", () => {
    const withPresentation = NAMES.filter((name) => load(name).presentation);
    const without = NAMES.filter((name) => !load(name).presentation);
    expect(withPresentation.length, "no presentation-path fixture").toBeGreaterThan(0);
    expect(without.length, "no metric-registry fixture").toBeGreaterThan(0);
  });

  it("covers grouped, trend, ungrouped, withheld and high cardinality", () => {
    const rowsOf = (run: RunPayload) =>
      Object.values(run.results ?? {})[0]?.rows?.length ?? 0;

    // A grouped breakdown.
    expect(rowsOf(load("grouped"))).toBeGreaterThan(1);
    // A time trend: more rows than a handful of categories.
    expect(rowsOf(load("trend"))).toBeGreaterThanOrEqual(12);
    // An ungrouped headline figure.
    expect(rowsOf(load("scalar"))).toBe(1);
    // Something was withheld by verification, somewhere.
    expect(
      NAMES.some((name) => load(name).rejected.length > 0),
      "no fixture exercises a withheld finding",
    ).toBe(true);
    // 33+ groups, which is the cardinality the chart-width defect needed.
    expect(rowsOf(load("high-cardinality"))).toBeGreaterThanOrEqual(33);
  });

  it("every fixture is a run that actually completed", () => {
    for (const name of NAMES) {
      expect(load(name).status, `${name} did not complete`).toBe("completed");
    }
  });
});

describe("every real payload renders an answer-first report", () => {
  it.each(NAMES)("%s", (name) => {
    const run = load(name);
    renderReport(run);

    const report = screen.getByTestId("report-panel");
    const answer = screen.getByTestId("direct-answer");

    // The answer is the first heading, and it is the display-scale one.
    expect(answer.tagName).toBe("H1");
    expect(report.querySelectorAll("h1, h2, h3")[0]).toBe(answer);
    expect(answer).toHaveClass("display");
    expect((answer.textContent ?? "").trim().length).toBeGreaterThan(0);

    // The question precedes it, so the answer has a subject.
    const question = screen.getByTestId("report-question");
    expect(
      question.compareDocumentPosition(answer) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // Exactly one evidence trigger for the whole report.
    expect(screen.getAllByTestId("inspect-evidence")).toHaveLength(1);
  });
});

describe("the initial canvas carries only what the brief allows", () => {
  /*
   * question, answer, context, chart, findings, table, one evidence
   * trigger. Anything technical is behind the trigger.
   */
  const FORBIDDEN = [
    ["planning-audit", "the planning audit"],
    ["activity", "the activity log"],
    ["run-timeline", "the run timeline"],
  ] as const;

  it.each(NAMES)("%s has no resident technical material", (name) => {
    renderReport(load(name));
    for (const [testId, label] of FORBIDDEN) {
      expect(screen.queryByTestId(testId), `${label} is resident`).toBeNull();
    }
  });

  it.each(NAMES)("%s has no duplicated section heading", (name) => {
    renderReport(load(name));
    const report = screen.getByTestId("report-panel");
    const headings = [...report.querySelectorAll("h1, h2, h3")].map((node) =>
      (node.textContent ?? "").trim().toLowerCase(),
    );
    expect(new Set(headings).size, headings.join(" | ")).toBe(headings.length);
  });

  it.each(NAMES)("%s uses no ALL-CAPS heading", (name) => {
    renderReport(load(name));
    const report = screen.getByTestId("report-panel");
    for (const node of report.querySelectorAll("h1, h2, h3")) {
      const text = (node.textContent ?? "").trim();
      if (text.length < 4) continue;
      // Sentence case, not capitals. Capitals are slower to read and were
      // signalling an importance the position already signals.
      expect(text, `"${text}" is ALL CAPS`).not.toBe(text.toUpperCase());
    }
  });
});

describe("the model reads the payload rather than inventing from it", () => {
  it("states the real verified and withheld counts", () => {
    const run = load("grouped");
    expect(run.rejected.length).toBeGreaterThan(0);
    renderReport(run);
    const context = screen.getByTestId("answer-coverage");
    expect(context).toHaveTextContent(
      `${run.findings.length} verified, ${run.rejected.length} withheld`,
    );
  });

  it("ranks the supporting findings beneath the answer", () => {
    const run = load("trend");
    const model = modelFor(run);
    // Three published findings; one becomes the answer, the rest are ranked.
    expect(run.findings.length).toBeGreaterThan(1);
    expect(model.highlights.length).toBe(run.findings.length - 1);

    renderReport(run);
    const list = screen.getByLabelText("What the numbers show");
    expect(within(list).getAllByRole("listitem")).toHaveLength(
      model.highlights.length,
    );
  });

  it("shows the table the run produced, with its real row count", () => {
    const run = load("high-cardinality");
    const model = modelFor(run);
    const rows = Object.values(run.results ?? {})[0]?.rows?.length ?? 0;
    expect(model.tableSnapshot?.rows).toHaveLength(rows);
  });

  it("never prints a placeholder for a value the payload does not carry", () => {
    for (const name of NAMES) {
      const { unmount } = renderReport(load(name));
      const report = screen.getByTestId("report-panel");
      const text = report.textContent ?? "";
      for (const placeholder of ["undefined", "null", "NaN", "[object Object]"]) {
        expect(text, `${name} printed ${placeholder}`).not.toContain(placeholder);
      }
      unmount();
    }
  });
});

describe("the activity trace reads the fields the engine emits", () => {
  /*
   * Two defects, both found by reading a captured payload rather than the
   * code. `chart_created` carries `chart_kind` and `result_id`; the trace
   * read `mark` and `title` and rendered "undefined chart · undefined".
   * `finding_verified` carries `status` and `rule`; the trace read `text`
   * and rendered "supported:" followed by nothing.
   */
  const run = load("high-cardinality");

  function renderTrace() {
    return render(
      <ActivityLog
        events={run.events}
        trace={run.mcp_trace ?? []}
        showTrace={false}
        onToggleTrace={() => undefined}
        running={false}
      />,
    );
  }

  it("prints no undefined for any event the run emitted", () => {
    renderTrace();
    const log = screen.getByTestId("activity");
    expect(log.textContent ?? "").not.toContain("undefined");
  });

  it("names the chart by the kind the engine recorded", () => {
    const created = run.events.find((event) => event.type === "chart_created");
    expect(created, "the fixture has no chart_created event").toBeTruthy();
    const kind = String((created!.data as Record<string, unknown>).chart_kind);
    renderTrace();
    expect(screen.getByTestId("activity")).toHaveTextContent(`${kind} chart`);
  });

  it("names the verification rule rather than an absent finding text", () => {
    const verified = run.events.find(
      (event) => event.type === "finding_verified",
    );
    expect(verified, "the fixture has no finding_verified event").toBeTruthy();
    const data = verified!.data as Record<string, unknown>;
    renderTrace();
    const log = screen.getByTestId("activity");
    expect(log).toHaveTextContent(String(data.status));
    expect(log).toHaveTextContent(String(data.rule));
  });
});

describe("the evidence drawer survives an unfinished payload", () => {
  /*
   * A run that is still `running` carries its identity and its status and
   * nothing else -- no `findings`, no `rejected`, no `events`.
   *
   * `run.findings.flatMap(...)` threw on exactly that payload. React
   * unmounted the subtree, so the Compare evidence drawer *vanished* the
   * moment a reader switched to a strategy that had not finished, and it
   * looked like the drawer closing itself rather than like a crash.
   */
  const unfinished = {
    run_id: "run_in_flight",
    status: "running",
  } as unknown as RunPayload;

  it("renders without throwing when the collections are absent", () => {
    expect(() =>
      render(<EvidenceBody run={unfinished} />),
    ).not.toThrow();
  });

  it("reports nothing published rather than crashing", () => {
    render(<EvidenceBody run={unfinished} />);
    expect(screen.getByText(/0 published/)).toBeInTheDocument();
  });
})
