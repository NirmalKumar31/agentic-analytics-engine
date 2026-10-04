import { AnswerReport } from "./AnswerReport";
import { ComparisonView } from "./ComparisonView";
import { ReportView } from "./ReportView";
import { RunStateCard } from "./RunStateCard";
import { reportModel } from "../lib/reportModel";
import { runState } from "../lib/runState";
import type {
  ComparisonStarted,
  RunPayload,
  ServerConfig,
} from "../lib/types";

export type ProvenanceSide = "deterministic" | "ai";

export function ReportWorkspace({
  comparison,
  run,
  aiRun,
  aiError,
  config,
  deterministicPending,
  onShowWork,
  onShowEvidence,
}: {
  comparison: ComparisonStarted | null;
  run: RunPayload | null;
  aiRun: RunPayload | null;
  aiError: string | null;
  config: ServerConfig | null;
  deterministicPending: boolean;
  onShowWork: (side: ProvenanceSide, findingId: string) => void;
  /** Opens the evidence drawer for the single-run report. */
  onShowEvidence?: () => void;
}) {
  if (comparison) {
    return (
      <ComparisonView
        question={comparison.question}
        deterministic={{
          title: "Deterministic Analytics",
          subtitle: "Rule-based planning over the governed analytics engine.",
          run,
          error: null,
          pending: deterministicPending,
          children: run ? (
            <RunReport
              run={run}
              onShowWork={(id) => onShowWork("deterministic", id)}
            />
          ) : null,
        }}
        ai={{
          title: "AI Analytics",
          subtitle:
            "A cloud model plans and interprets; the engine computes and verifies.",
          run: aiRun,
          error: aiError,
          unavailable:
            !aiRun &&
            !aiError &&
            Boolean(
              config?.capabilities?.modes.some(
                (mode) => mode.mode === "ai" && !mode.available,
              ),
            ),
          pending: Boolean(aiRun && aiRun.status === "running"),
          usage: aiRun?.usage,
          children:
            aiRun && runState(aiRun).showsReport ? (
              <RunReport
                run={aiRun}
                onShowWork={(id) => onShowWork("ai", id)}
              />
            ) : null,
        }}
      />
    );
  }

  if (!run) return null;

  // Single mode is the default, and it had no terminal-state card at all:
  // a refused, quota-stopped, cancelled or failed run rendered the report
  // body as though it were an ordinary answer. The state comes first,
  // because when it is not `completed_verified` it is the most useful thing
  // on the screen -- and `showsReport` decides whether there is a report
  // worth putting under it. A refusal has one; a failure does not.
  const state = runState(run);
  return (
    <>
      <RunStateCard state={state} />
      {/*
        One report for both payload shapes. An uploaded file comes back with
        a `presentation`; the demo warehouse does not, because it is
        answered through the metric registry. `reportModel` derives the same
        seven elements from either, so the demo is not left on the old
        seven-panel report.
      */}
      {state.showsReport && (
        <AnswerReport
          question={run.question}
          model={reportModel({
            presentation: run.presentation,
            report: run.report,
            findings: run.findings,
            rejected: run.rejected,
            charts: run.charts,
            results: run.results,
            queryContract: run.query_contract ?? null,
          })}
          publishedCount={run.findings.length}
          withheldCount={run.rejected.length}
          onShowEvidence={onShowEvidence ?? (() => undefined)}
        />
      )}
      {/*
        `TechnicalInspector` -- the planning audit -- used to render here,
        resident under every report. It is in the evidence drawer now,
        together with the activity trace, the contract, the route, coverage,
        verification, cited cells, timings and the limitations. Relocated,
        not deleted: `EVIDENCE_SECTIONS` names each one and a test asserts
        they are all reachable.
      */}
    </>
  );
}

function RunReport({
  run,
  onShowWork,
}: {
  run: RunPayload;
  onShowWork: (findingId: string) => void;
}) {
  return (
    <ReportView
      question={run.question}
      report={run.report}
      findings={run.findings}
      rejected={run.rejected}
      charts={run.charts}
      results={run.results}
      queryContract={run.query_contract}
      presentation={run.presentation}
      onShowWork={onShowWork}
    />
  );
}
