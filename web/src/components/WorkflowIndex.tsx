export type WorkflowStage = "dataset" | "ask" | "running" | "report";

export function WorkflowIndex({ stage }: { stage: WorkflowStage }) {
  return (
    <nav className="steps" aria-label="Progress" tabIndex={0}>
      <Step index={1} label="Dataset" state={stage === "dataset" ? "active" : "done"} />
      <Step
        index={2}
        label="Ask"
        state={stage === "ask" ? "active" : stage === "dataset" ? "idle" : "done"}
      />
      <Step
        index={3}
        label="Analyse"
        state={stage === "running" ? "active" : stage === "report" ? "done" : "idle"}
      />
      <Step index={4} label="Verify" state={stage === "report" ? "done" : "idle"} />
      <Step index={5} label="Report" state={stage === "report" ? "active" : "idle"} />
    </nav>
  );
}

function Step({
  index,
  label,
  state,
}: {
  index: number;
  label: string;
  state: "idle" | "active" | "done";
}) {
  return (
    <div className="step" data-state={state}>
      <span className="step-index">{state === "done" ? "✓" : index}</span>
      {label}
    </div>
  );
}
