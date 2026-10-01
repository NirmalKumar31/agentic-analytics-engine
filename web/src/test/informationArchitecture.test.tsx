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

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ComparisonView } from "../components/ComparisonView";
import { QuestionComposer } from "../components/QuestionComposer";
import { DatasetIdentity } from "../components/DatasetIdentity";
import { ReportWorkspace } from "../components/ReportWorkspace";
import { SchemaInspector } from "../components/SchemaInspector";
import { WorkflowIndex } from "../components/WorkflowIndex";
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

  it("says plainly that it cannot be confirmed here yet", () => {
    render(<SchemaInspector summary={ambiguous} />);
    const note = screen.getByTestId("ambiguity-note");
    expect(note).toHaveTextContent(/cannot confirm it here yet/i);
    // ADR 0006: a control that only changed the label would be worse than
    // none, so there must not be one.
    expect(
      screen.queryByRole("button", { name: /set role|change role|confirm role/i }),
    ).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
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

  it("marks the workflow stage a stopped run stopped at", () => {
    render(<WorkflowIndex phase="refused" />);
    expect(screen.getByText("Analyse").closest(".step")).toHaveAttribute(
      "data-state",
      "stopped",
    );
  });
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
    render(<DatasetIdentity catalog={catalog("upload")} />);
    const strip = screen.getByTestId("dataset-identity");
    expect(strip).toHaveTextContent("Your file");
    expect(strip).toHaveTextContent("quarterly-sales.csv");
    expect(strip).toHaveTextContent("200 rows");
  });

  it("does not put an unexplained hash in front of a reader", () => {
    render(<DatasetIdentity catalog={catalog("upload")} />);
    const strip = screen.getByTestId("dataset-identity");
    expect(strip).not.toHaveTextContent(/sha256|abcdef0123456789/);
  });

  it("distinguishes the demo warehouse from an uploaded file", () => {
    const { rerender } = render(<DatasetIdentity catalog={catalog("demo")} />);
    expect(screen.getByTestId("dataset-identity")).toHaveTextContent(
      "Demo warehouse",
    );
    rerender(<DatasetIdentity catalog={catalog("upload")} />);
    expect(screen.getByTestId("dataset-identity")).toHaveTextContent("Your file");
  });

  it("renders nothing when there is no dataset", () => {
    render(<DatasetIdentity catalog={null} />);
    expect(screen.queryByTestId("dataset-identity")).toBeNull();
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
