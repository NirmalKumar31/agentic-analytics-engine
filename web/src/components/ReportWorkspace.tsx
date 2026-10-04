import { AnswerReport } from "./AnswerReport";
import { CompareWorkspace } from "./CompareWorkspace";
import { reportModel } from "../lib/reportModel";
import { runState } from "../lib/runState";
import type {
  ComparisonStarted,
  RunPayload,
  ServerConfig,
} from "../lib/types";

export function ReportWorkspace({
  comparison,
  run,
  aiRun,
  aiError,
  config,
  deterministicPending,
  onShowEvidence,
}: {
  comparison: ComparisonStarted | null;
  run: RunPayload | null;
  aiRun: RunPayload | null;
  aiError: string | null;
  config: ServerConfig | null;
  deterministicPending: boolean;
  /** Opens the evidence drawer for the single-run report. */
  onShowEvidence?: () => void;
}) {
  if (comparison) {
    return (
      <CompareWorkspace
        question={comparison.question}
        deterministic={{
          title: "Deterministic Analytics",
          subtitle: "Rule-based planning over the governed analytics engine.",
          run,
          error: null,
          pending: deterministicPending,
          usage: run?.usage,
          children: run ? <PaneReport run={run} /> : null,
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
              <PaneReport run={aiRun} />
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
      {/*
        No separate `RunStateCard` here.

        It was a sibling of the report, which meant a refusal said its
        reason twice -- once in the card and once as the display headline,
        because the presentation builder sets the headline from the same
        stop reason. The terminal state is part of the report now, inside
        the same reading column as the question, the context line and the
        chart, and `reportModel` decides what it says.
      */}
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
            state,
            run,
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

/**
 * One strategy's report, inside a Compare pane.
 *
 * The same answer-first report as a single run, compact: no repeated
 * question and no second evidence control, because Compare states the
 * question once and carries one "Inspect both traces".
 */
function PaneReport({ run }: { run: RunPayload }) {
  return (
    <AnswerReport
      compact
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
    />
  );
}
