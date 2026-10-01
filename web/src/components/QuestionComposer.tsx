import { ModeSelector } from "./ModeSelector";
import { PlanningMethodDisclosure } from "./PlanningMethodDisclosure";
import { suggestions } from "./DatasetSummary";
import type {
  DatasetSummary as DatasetSummaryPayload,
  ServerConfig,
  UiMode,
} from "../lib/types";

export function QuestionComposer({
  config,
  question,
  onQuestionChange,
  onAsk,
  busy,
  uiMode,
  onModeChange,
  summary,
}: {
  config: ServerConfig | null;
  question: string;
  onQuestionChange: (question: string) => void;
  onAsk: () => void;
  busy: boolean;
  uiMode: UiMode;
  onModeChange: (mode: UiMode) => void;
  /** Present for an uploaded file; absent for the demo warehouse. */
  summary?: DatasetSummaryPayload | null;
}) {
  /*
   * Examples have to come from the dataset in front of the reader.
   *
   * This offered `config.demo_questions` unconditionally, so someone who
   * had just uploaded their own file was given three questions about the
   * demo warehouse's gross margin -- naming columns their file does not
   * contain. Clicking one produced a refusal, which read as the engine
   * failing rather than the suggestion being wrong.
   *
   * `suggestions()` derives them from the inferred schema instead, and
   * respects additive confidence: it will not propose summing a column
   * that only looks like a quantity.
   */
  const derived = summary ? suggestions(summary) : [];
  const examples = derived.length
    ? derived.map((question, index) => ({
        id: `derived-${index}`,
        question,
        label: question,
      }))
    : (config?.demo_questions ?? []).map((item) => ({
        id: item.id,
        question: item.question,
        label: item.question,
      }));
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
        {examples.length > 0 && (
          <div
            className="example-list"
            data-testid="question-examples"
            data-source={derived.length ? "schema" : "demo"}
          >
            {examples.map((item) => (
              <button
                className="example"
                key={item.id}
                onClick={() => onQuestionChange(item.question)}
              >
                {item.label}
                {"why" in item && typeof item.why === "string" ? (
                  <small>{item.why}</small>
                ) : null}
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
