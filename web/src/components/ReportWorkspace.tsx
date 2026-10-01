import { ComparisonView } from "./ComparisonView";
import { ReportView } from "./ReportView";
import { TechnicalInspector } from "./TechnicalInspector";
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
}: {
  comparison: ComparisonStarted | null;
  run: RunPayload | null;
  aiRun: RunPayload | null;
  aiError: string | null;
  config: ServerConfig | null;
  deterministicPending: boolean;
  onShowWork: (side: ProvenanceSide, findingId: string) => void;
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
  return (
    <>
      <RunReport
        run={run}
        onShowWork={(id) => onShowWork("deterministic", id)}
      />
      <TechnicalInspector run={run} />
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
