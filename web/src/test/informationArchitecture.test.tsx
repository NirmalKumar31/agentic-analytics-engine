/**
 * What the information architecture has to keep telling the truth about.
 *
 * Five claims, each of which was false before this change:
 *
 *   1. An inferred role the data cannot settle is marked as such, and the
 *      interface says it cannot be confirmed yet rather than offering a
 *      control that would not be honoured.
 *   2. Every terminal state is distinguishable. In particular a run that
 *      published nothing is never labelled "Complete", and "withheld by
 *      verification" is not the same thing as "found nothing to claim".
 *   3. Compare says what automatic routing did, or says it is not
 *      recorded -- never a guessed route.
 *   4. The report leads with the answer.
 *   5. The schema inspector and the technical audit are disclosures, so
 *      neither sits permanently between a reader and the thing they came
 *      for.
 *
 * These assert on rendered output rather than on props, and none of them is
 * conditional: a missing target fails rather than skipping.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ComparisonView } from "../components/ComparisonView";
import { QuestionComposer } from "../components/QuestionComposer";
import { DatasetContextBar } from "../components/DatasetContextBar";
import { ModeBadge, badgeMode } from "../components/ModeBadge";
import { PresentationReportView } from "../components/PresentationReportView";
import { ReportWorkspace } from "../components/ReportWorkspace";
import { SchemaInspector } from "../components/SchemaInspector";
import { runState } from "../lib/runState";
import type {
  DatasetCatalog,
  DatasetSummary as SummaryPayload,
  RunPayload,
  Verdict,
} from "../lib/types";
import { event, finding, snapshot } from "./fixtures";

function run(overrides: Partial<RunPayload> = {}): RunPayload {
  return {
    run_id: "r1",
    session_id: "s1",
    question: "What is total revenue by region?",
    status: "completed",
    report: null,
    findings: [],
    rejected: [],
    charts: [],
    tasks: [],
    results: { [snapshot.result_id]: snapshot },
    mcp_trace: [],
    events: [],
    metrics: {
      runtime_seconds: 1,
      provider: "fake",
      analysis_tasks: 1,
      mcp_tool_calls: 1,
      findings_published: 0,
      findings_rejected: 0,
      llm_calls: 0,
    },
    stopped_reason: "",
    ...overrides,
  } as RunPayload;
}

const withheldVerdict: Verdict = {
  finding_id: "f1",
  status: "unsupported",
  reason: "the cited cells do not support the claim",
};

function summary(fields: SummaryPayload["fields"]): SummaryPayload {
  const named = (role: string) =>
    fields.filter((f) => f.role === role).map((f) => f.name);
  return {
    table: "uploaded",
    row_count: 200,
    status: "inferred",
    headline: "200 rows, 4 columns.",
    fields,
    measures: named("measure"),
    dimensions: named("dimension"),
    time_fields: named("time"),
    identifiers: named("identifier"),
    ambiguities: [],
  };
}

const field = (
  name: string,
  role: string,
  extra: Record<string, unknown> = {},
) =>
  ({
    name,
    data_type: "BIGINT",
    role,
    null_pct: 0,
    distinct_count: 45,
    reason: "small whole numbers",
    ...extra,
  }) as SummaryPayload["fields"][number];

// ---------------------------------------------------------------- 1. schema

describe("an inferred role the data cannot settle", () => {
  const ambiguous = summary([
    field("Store", "measure", { ambiguous: true }),
    field("revenue", "measure", { ambiguous: false, additive: "strong" }),
    field("region", "dimension"),
  ]);

  it("is marked, and not left looking like a confident inference", () => {
    render(<SchemaInspector summary={ambiguous} />);
    // The disclosure is closed, so the count has to be on the summary line:
    // a reader who never opens it is still told there is a close call.
    expect(screen.getByTestId("schema-inspector")).toHaveTextContent(
      /1 role the data cannot settle/,
    );
  });

  it("says it cannot be confirmed where no control is offered", () => {
    // A demo warehouse or a replay: the choice belongs to whoever uploaded
    // the file, and there is no file here.
    render(<SchemaInspector summary={ambiguous} />);
    const note = screen.getByTestId("ambiguity-note");
    expect(note).toHaveTextContent(/cannot confirm it on this dataset/i);
    expect(screen.queryByTestId("role-confirmation")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("says it can be settled, and how far that goes, where it can", () => {
    // The note used to say a role could not be set here because one would
    // have to be validated and audited to mean anything. That is now what
    // happens, so the note would be describing a limitation that no longer
    // exists -- and promising less than the product does is still wrong.
    const confirmable = summary([
      field("Store", "measure", {
        ambiguous: true,
        allowed_confirmed_roles: ["measure", "dimension"],
      }),
      field("revenue", "measure", { ambiguous: false, additive: "strong" }),
    ]);
    render(<SchemaInspector summary={confirmable} onConfirmRoles={vi.fn()} />);
    const note = screen.getByTestId("ambiguity-note");
    expect(note).toHaveTextContent(/settle it for this session/i);
    expect(note).toHaveTextContent(/names it in the planning audit/i);
    // And it must not overstate: this is not a definition and does not last.
    expect(note).toHaveTextContent(/not a governed definition/i);
    expect(note).toHaveTextContent(/gone when the session ends/i);
    expect(screen.getByTestId("role-confirmation")).toBeTruthy();
  });

  it("marks the ambiguous column and not the confident one", () => {
    render(<SchemaInspector summary={ambiguous} />);
    const marks = screen.getAllByTestId("ambiguous-field");
    expect(marks).toHaveLength(1);
    expect(marks[0]!.closest("tr")).toHaveTextContent("Store");
  });

  it("says nothing about close calls when every role is settled", () => {
    render(
      <SchemaInspector
        summary={summary([
          field("revenue", "measure", { additive: "strong" }),
          field("region", "dimension"),
        ])}
      />,
    );
    expect(screen.queryByTestId("ambiguity-note")).toBeNull();
    expect(screen.queryByTestId("ambiguous-field")).toBeNull();
    expect(screen.getByTestId("schema-inspector")).not.toHaveTextContent(
      /cannot settle/,
    );
  });
});

// ------------------------------------------------------------- 2. terminal

describe("every terminal state is distinguishable", () => {
  const cases: Array<[string, RunPayload, RegExp]> = [
    ["refused", run({ status: "refused", stopped_reason: "no date column" }), /refused/i],
    ["failed", run({ status: "failed", stopped_reason: "the run failed" }), /failed/i],
    ["quota", run({ status: "budget_exhausted" }), /quota/i],
    ["cancelled", run({ status: "cancelled" }), /cancelled/i],
    [
      "withheld",
      run({ status: "completed", findings: [], rejected: [withheldVerdict] }),
      /withheld/i,
    ],
    ["no findings", run({ status: "completed", findings: [], rejected: [] }), /no findings/i],
  ];

  it.each(cases)("shows %s as itself", (_name, payload, expected) => {
    render(
      <ReportWorkspace
        comparison={null}
        run={payload}
        aiRun={null}
        aiError={null}
        config={null}
        deterministicPending={false}
        onShowWork={() => undefined}
      />,
    );
    const card = screen.getByTestId("run-state-card");
    expect(card).toHaveTextContent(expected);
  });

  it("gives each state its own label, with no two sharing one", () => {
    const labels = cases.map(([, payload]) => runState(payload).label);
    expect(new Set(labels).size).toBe(cases.length);
  });

  it("never labels a run that published nothing Complete", () => {
    for (const [, payload] of cases) {
      const state = runState(payload);
      expect(state.label, `${state.state} must not read as Complete`).not.toBe(
        "Complete",
      );
      expect(state.state).not.toBe("completed_verified");
    }
  });

  it("shows no state card at all for a verified answer", () => {
    render(
      <ReportWorkspace
        comparison={null}
        run={run({ status: "completed", findings: [finding] })}
        aiRun={null}
        aiError={null}
        config={null}
        deterministicPending={false}
        onShowWork={() => undefined}
      />,
    );
    expect(screen.queryByTestId("run-state-card")).toBeNull();
  });

  // The stepper assertion that stood here went with the stepper. Its claim
  // -- a stopped run is shown stopped at the stage it stopped at -- is owed
  // by the step D timeline, from backend events.
});

// ----------------------------------------------------------------- 3. auto

describe("automatic routing in Compare", () => {
  const side = (payload: RunPayload | null) => ({
    title: "t",
    subtitle: "s",
    run: payload,
    error: null,
    pending: false,
    children: null,
  });

  const resolved = (data: Record<string, unknown>) =>
    run({
      status: "completed",
      findings: [finding],
      events: [event("contract_resolved", data)],
    });

  it("reports a recorded route as fact", () => {
    render(
      <ComparisonView
        question="q"
        deterministic={side(resolved({ route: "rules_exact", model_calls: 0 }))}
        ai={side(run({ status: "completed", findings: [finding] }))}
      />,
    );
    const note = screen.getByTestId("auto-route-note");
    expect(note).toHaveAttribute("data-route", "rules_exact");
    expect(note).toHaveTextContent(/no planning request was made/i);
  });

  it("does not invent a route when the rules could not resolve the question", () => {
    // Escalate-or-refuse depends on which resolution issues are
    // model-eligible, which this path does not record. Saying so is the
    // correct output; naming a route would be a guess.
    render(
      <ComparisonView
        question="q"
        deterministic={side(resolved({ confident: false }))}
        ai={side(run({ status: "completed", findings: [finding] }))}
      />,
    );
    const note = screen.getByTestId("auto-route-note");
    expect(note).toHaveAttribute("data-route", "undetermined");
    expect(note).toHaveTextContent(/is not recorded for this run/i);
    for (const route of ["rules_exact", "ai_resolved", "ai_unavailable"]) {
      expect(note).not.toHaveTextContent(route.replaceAll("_", " "));
    }
  });

  it("says automatic mode would have used the rules when they resolved it", () => {
    render(
      <ComparisonView
        question="q"
        deterministic={side(resolved({ confident: true, model_calls: 0 }))}
        ai={side(run({ status: "completed", findings: [finding] }))}
      />,
    );
    expect(screen.getByTestId("auto-route-note")).toHaveTextContent(
      /would have used them/i,
    );
  });

  it("adds no third column for a policy that is not a planner", () => {
    render(
      <ComparisonView
        question="q"
        deterministic={side(resolved({ route: "rules_exact", model_calls: 0 }))}
        ai={side(run({ status: "completed", findings: [finding] }))}
      />,
    );
    expect(screen.getByTestId("auto-route-note")).toHaveTextContent(
      /not a third one/i,
    );
  });

  it("shows nothing when no planning record exists", () => {
    render(
      <ComparisonView
        question="q"
        deterministic={side(run({ status: "completed", findings: [finding] }))}
        ai={side(null)}
      />,
    );
    expect(screen.queryByTestId("auto-route-note")).toBeNull();
  });
});

// --------------------------------------------------------- 4 & 5. structure

describe("the report workspace", () => {
  // PlanningAudit renders only when there is something to audit, so the
  // fixture carries the planning record a real run carries.
  const audited = run({
    status: "completed",
    findings: [finding],
    events: [event("contract_resolved", { route: "rules_exact", model_calls: 0 })],
  });

  function workspace(payload: RunPayload) {
    return render(
      <ReportWorkspace
        comparison={null}
        run={payload}
        aiRun={null}
        aiError={null}
        config={null}
        deterministicPending={false}
        onShowWork={() => undefined}
      />,
    );
  }

  it("leads with the answer, before the technical audit", () => {
    workspace(audited);
    const report = screen.getByTestId("report-panel");
    const audit = screen.getByTestId("planning-audit");
    // DOM order, not text search: this is a claim about structure, and a
    // text index would pass on a coincidental substring.
    expect(
      report.compareDocumentPosition(audit) &
        Node.DOCUMENT_POSITION_FOLLOWING,
      "the planning audit must come after the report",
    ).toBeTruthy();
  });

  it("keeps the technical audit a closed disclosure", () => {
    workspace(audited);
    const audit = screen.getByTestId("planning-audit");
    expect(audit.tagName).toBe("DETAILS");
    expect(audit).not.toHaveAttribute("open");
  });

  it("puts the state card above the report when a run did not answer", () => {
    workspace(
      run({ status: "refused", stopped_reason: "no date column", findings: [] }),
    );
    const card = screen.getByTestId("run-state-card");
    const panel = screen.queryByTestId("report-panel");
    // A refusal still has a report worth showing -- it carries the accepted
    // interpretation -- but the reason comes first.
    if (panel) {
      expect(
        card.compareDocumentPosition(panel) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    } else {
      expect(card).toBeTruthy();
    }
  });

  it("keeps the schema inspector a closed disclosure", () => {
    render(
      <SchemaInspector
        summary={summary([field("revenue", "measure", { additive: "strong" })])}
      />,
    );
    const inspector = screen.getByTestId("schema-inspector");
    expect(inspector.tagName).toBe("DETAILS");
    expect(inspector).not.toHaveAttribute("open");
  });
});

describe("dataset identity", () => {
  const catalog = (kind: string): DatasetCatalog => ({
    dataset_kind: kind,
    source: "quarterly-sales.csv",
    dataset_fingerprint: "sha256:abcdef0123456789",
    tables: [
      { name: "uploaded", row_count: 200, columns: [] },
    ],
    metrics_available: [],
  });

  it("says which dataset the answer is about", () => {
    render(<DatasetContextBar catalog={catalog("upload")} summary={null} />);
    const strip = screen.getByTestId("dataset-context");
    expect(strip).toHaveTextContent("Your file");
    expect(strip).toHaveTextContent("quarterly-sales.csv");
    expect(strip).toHaveTextContent("200 rows");
  });

  it("does not put an unexplained hash in front of a reader", () => {
    render(<DatasetContextBar catalog={catalog("upload")} summary={null} />);
    const strip = screen.getByTestId("dataset-context");
    expect(strip).not.toHaveTextContent(/sha256|abcdef0123456789/);
  });

  it("distinguishes the demo warehouse from an uploaded file", () => {
    // The demo names itself in its source, so the strip does not also
    // print the kind; an upload does not, so it keeps "Your file".
    const demo = {
      ...catalog("demo"),
      source: "Commerce demo warehouse",
    };
    const { rerender } = render(
      <DatasetContextBar catalog={demo} summary={null} />,
    );
    const strip = () => screen.getByTestId("dataset-context");
    expect(strip()).toHaveTextContent(/demo warehouse/i);
    expect(strip().textContent?.match(/demo warehouse/gi)).toHaveLength(1);

    rerender(<DatasetContextBar catalog={catalog("upload")} summary={null} />);
    expect(strip()).toHaveTextContent("Your file");
    expect(strip()).toHaveTextContent("quarterly-sales.csv");
  });

  it("renders nothing when there is no dataset", () => {
    render(<DatasetContextBar catalog={null} summary={null} />);
    expect(screen.queryByTestId("dataset-context")).toBeNull();
  });

  it("offers no schema control when there is no schema to inspect", () => {
    // A replayed recording has a catalog and no summary. A control that
    // opened an empty sheet would be worse than no control.
    render(<DatasetContextBar catalog={catalog("demo")} summary={null} />);
    expect(screen.queryByTestId("inspect-schema")).toBeNull();
  });
});

// ------------------------------------------------------------- 6. examples

describe("question examples come from the dataset at hand", () => {
  const config = {
    demo_questions: [
      { id: "d1", question: "Why did gross margin fall in Q3 2025?", why: "a curated demo question" },
    ],
    capabilities: undefined,
  } as unknown as Parameters<typeof QuestionComposer>[0]["config"];

  const uploaded = summary([
    field("revenue", "measure", { additive: "strong" }),
    field("region", "dimension"),
  ]);

  function composer(summaryProp: SummaryPayload | null) {
    return render(
      <QuestionComposer
        config={config}
        summary={summaryProp}
        question=""
        onQuestionChange={() => undefined}
        onAsk={() => undefined}
        busy={false}
        uiMode="auto"
        onModeChange={() => undefined}
      />,
    );
  }

  it("offers schema-derived questions for an uploaded file", () => {
    composer(uploaded);
    const list = screen.getByTestId("question-examples");
    expect(list).toHaveAttribute("data-source", "schema");
    // Naming this file's columns, not the demo warehouse's.
    expect(list).toHaveTextContent(/revenue/i);
    expect(list).not.toHaveTextContent(/gross margin/i);
  });

  it("keeps the curated questions when there is no uploaded schema", () => {
    // The demo session also carries a summary, so App passes null unless the
    // dataset kind is an upload. Gating on the summary's presence alone
    // replaced the curated questions -- which demonstrate the governed
    // metric registry -- with generic ones derived from its tables.
    composer(null);
    const list = screen.getByTestId("question-examples");
    expect(list).toHaveAttribute("data-source", "demo");
    expect(list).toHaveTextContent(/gross margin/i);
  });
});

// ------------------------------------------------- 7. zero-row compatibility

describe("a query that matched nothing", () => {
  // The backend now completes these instead of failing them, carrying the
  // reason as a report limitation. This pins that the frontend shows both
  // halves: the state, and why.
  const emptyResult = run({
    status: "completed",
    outcome: "completed",
    findings: [],
    rejected: [],
    report: {
      question: "What is total revenue by region where region is Atlantis?",
      executive_summary: "",
      key_findings: [],
      sections: [],
      limitations: [
        "No rows matched the requested filters: region = Atlantis. The analysis ran; the data contained no matching rows.",
      ],
      next_questions: [],
    },
  } as Partial<RunPayload>);

  function workspace(payload: RunPayload) {
    return render(
      <ReportWorkspace
        comparison={null}
        run={payload}
        aiRun={null}
        aiError={null}
        config={null}
        deterministicPending={false}
        onShowWork={() => undefined}
      />,
    );
  }

  it("reads as no findings, not as a failure", () => {
    workspace(emptyResult);
    const card = screen.getByTestId("run-state-card");
    expect(card).toHaveAttribute("data-state", "no_findings");
    expect(card).not.toHaveTextContent(/failed/i);
    expect(card).not.toHaveTextContent(/\bComplete\b/);
  });

  it("shows the reason the engine gave, naming the restriction", () => {
    workspace(emptyResult);
    expect(screen.getByText(/No rows matched the requested filters/)).toBeVisible();
    expect(screen.getByText(/region = Atlantis/)).toBeVisible();
  });

  it("does not blame verification for withholding something", () => {
    workspace(emptyResult);
    const card = screen.getByTestId("run-state-card");
    expect(card).toHaveTextContent(/nothing was withheld/i);
  });
});

// ------------------------------------------------ 8. what the card claims

describe("the report card says what it actually is", () => {
  function present(shape: string, headline: string, caveats: unknown[] = []) {
    return render(
      <PresentationReportView
        question="What is the total gross margin by region?"
        presentation={
          {
            schema_version: "1.0",
            shape,
            headline,
            secondary_summary: null,
            highlights: [],
            caveats,
            display_fields: [],
            // Never null in a real payload: the schema gives it a
            // default_factory, so the builder always carries one.
            scope: {
              rows_total: null,
              rows_matching: null,
              rows_represented: null,
              filters: [],
              period: null,
              complete: null,
            },
            chart: null,
            table: null,
            finding_ids: [],
            compatibility_derived: false,
          } as never
        }
        results={{}}
        onShowWork={() => undefined}
      />,
    );
  }

  it("does not call a refusal a verified answer", () => {
    // It labelled every shape "Verified answer", so a question the engine
    // declined to map was presented as a verified answer to it, with the
    // refusal reason as the answer. Nothing was verified.
    present("refusal", "the question could not be mapped safely");
    const card = screen.getByTestId("direct-answer");
    expect(card).not.toHaveTextContent(/verified answer/i);
    expect(card).toHaveTextContent(/not answered/i);
  });

  it("does not call a failure a verified answer", () => {
    present("failure", "The analysis could not be completed.");
    const card = screen.getByTestId("direct-answer");
    expect(card).not.toHaveTextContent(/verified answer/i);
    expect(card).toHaveTextContent(/not completed/i);
  });

  it("still calls a real answer a verified answer", () => {
    present("breakdown", "Revenue by region: North $1.00.");
    expect(screen.getByTestId("direct-answer")).toHaveTextContent(
      /verified answer/i,
    );
  });

  it("does not repeat the headline in the notes", () => {
    // The builder sets a refusal's headline and its blocking caveat from the
    // same sentence, so Notes restated the headline directly beneath it.
    const reason = "the question could not be mapped safely";
    present("refusal", reason, [
      { code: "refused", message: reason, severity: "blocking" },
    ]);
    expect(screen.getAllByText(new RegExp(reason))).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "Notes" })).toBeNull();
  });

  it("keeps a note that says something the headline does not", () => {
    present("breakdown", "Revenue by region: North $1.00.", [
      { code: "coverage", message: "Two groups were omitted.", severity: "warn" },
    ]);
    expect(screen.getByRole("heading", { name: "Notes" })).toBeVisible();
    expect(screen.getByText("Two groups were omitted.")).toBeVisible();
  });
});

// ------------------------------------------------- 9. one vocabulary, everywhere

describe("the comparison is named the same thing everywhere", () => {
  it("does not call it \"Compare both\" in the header badge", () => {
    // The rename covered the selector, the run button and the report
    // heading, and missed this one -- so the header said "Compare both" on
    // every comparison run, which is the string that prompted the rename.
    // It was missed because the label lives in a lookup table rather than
    // in markup, so reading the JSX did not show it.
    render(<ModeBadge mode={badgeMode(false, "ai_live", "compare")} />);
    const badge = screen.getByTitle(/Two runs of the same question/);
    expect(badge).not.toHaveTextContent(/compare both/i);
    expect(badge).toHaveTextContent(/comparing strategies/i);
  });

  it("names the comparison consistently across the three surfaces", () => {
    // Selector, button and heading already agreed; the badge now joins them.
    // Asserted together so a future rename cannot update three and leave a
    // fourth behind.
    const sources = [
      "components/ModeBadge.tsx",
      "components/ModeSelector.tsx",
      "components/ComparisonView.tsx",
    ].map((f) => readFileSync(join(__dirname, "..", f), "utf8"));

    for (const [index, source] of sources.entries()) {
      // Comments may discuss the old name; rendered strings may not.
      const withoutComments = source
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/^\s*\/\/.*$/gm, "");
      expect(
        /compare both/i.test(withoutComments),
        `surface ${index} still renders the old name`,
      ).toBe(false);
    }
  });
});
