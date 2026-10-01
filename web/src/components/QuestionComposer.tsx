import { ModeSelector } from "./ModeSelector";
import { PlanningMethodDisclosure } from "./PlanningMethodDisclosure";
import type { ServerConfig, UiMode } from "../lib/types";

export function QuestionComposer({
  config,
  question,
  onQuestionChange,
  onAsk,
  busy,
  uiMode,
  onModeChange,
}: {
  config: ServerConfig | null;
  question: string;
  onQuestionChange: (question: string) => void;
  onAsk: () => void;
  busy: boolean;
  uiMode: UiMode;
  onModeChange: (mode: UiMode) => void;
}) {
  return (
    <section className="panel">
      <div className="panel-head"><h2>Ask</h2></div>
      <div className="panel-body stack">
        {config?.capabilities && (
          <ModeSelector
            capabilities={config.capabilities}
            value={uiMode}
            onChange={onModeChange}
            disabled={busy}
          />
        )}
        <PlanningMethodDisclosure mode={uiMode} />
        <textarea
          className="field"
          rows={3}
          value={question}
          maxLength={500}
          placeholder="Why did gross margin fall in Q3 2025?"
          onChange={(event) => onQuestionChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) onAsk();
          }}
          aria-label="Business question"
        />
        <div className="row">
          <button
            className="btn primary"
            onClick={onAsk}
            disabled={busy || !question.trim()}
          >
            {busy
              ? "Starting…"
              : uiMode === "compare"
                ? "Compare strategies"
                : uiMode === "ai"
                  ? "Run with AI"
                  : "Run analysis"}
          </button>
          <span className="small dim">{question.length}/500 · ⌘↵ to run</span>
        </div>
        {config && config.demo_questions.length > 0 && (
          <div className="example-list">
            {config.demo_questions.map((item) => (
              <button
                className="example"
                key={item.id}
                onClick={() => onQuestionChange(item.question)}
              >
                {item.question}
                <small>{item.why}</small>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
