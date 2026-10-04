/**
 * The composer: an analytical command bar, not a textarea in a card.
 *
 * What was here was a `<section class="panel">` headed **ASK**, containing a
 * strategy selector, a planning disclosure, a three-row textarea, a run
 * button and a flat list of example questions -- six stacked things of
 * roughly equal weight, inside a bordered box, under a stepper.
 *
 * The composer is the one interaction on this screen, so it is the one
 * focal element: full width, set at a scale that says "type here", with
 * everything that qualifies it placed *after* it in the reading order.
 * Chips state what the engine will do with the question; the strategy
 * control sits beside the run button because it modifies that action.
 *
 * Suggestions are grouped by what they ask for, which is how a reader
 * chooses one -- "I want a trend" rather than "I want the second item".
 */

import { ModeSelector } from "./ModeSelector";
import { PlanningMethodDisclosure } from "./PlanningMethodDisclosure";
import { groupedSuggestions } from "./DatasetSummary";
import type {
  DatasetSummary as DatasetSummaryPayload,
  ServerConfig,
  UiMode,
} from "../lib/types";

const MAX = 500;

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
   * `groupedSuggestions()` derives them from the inferred schema instead,
   * and respects additive confidence: it will not propose summing a column
   * that only looks like a quantity.
   */
  const derived = summary ? groupedSuggestions(summary) : [];
  const examples = derived.length
    ? derived.map((item, index) => ({
        id: `derived-${index}`,
        intent: item.intent as string,
        question: item.question,
      }))
    : (config?.demo_questions ?? []).map((item) => ({
        id: item.id,
        // A curated demo question carries the engine's own reason for
        // existing, which is a better label than an intent guessed from
        // its wording.
        intent: item.why,
        question: item.question,
      }));

  const clocks = summary?.time_fields ?? [];
  const label =
    uiMode === "compare"
      ? "Compare strategies"
      : uiMode === "ai"
        ? "Run with AI"
        : "Run analysis";

  return (
    <section className="composer" data-testid="composer">
      <label className="composer-label" htmlFor="composer-field">
        Ask a question of this dataset
      </label>

      <textarea
        id="composer-field"
        className="composer-field"
        rows={3}
        value={question}
        maxLength={MAX}
        placeholder="Why did gross margin fall in Q3 2025?"
        onChange={(event) => onQuestionChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) onAsk();
        }}
        aria-label="Business question"
      />

      {/* What the engine will do with what you typed, stated before you run
          it rather than explained afterwards in the report. */}
      <div className="composer-chips" data-testid="composer-chips">
        {clocks.length === 1 && (
          <span className="composer-chip">
            time: <span className="mono">{clocks[0]}</span>
          </span>
        )}
        {clocks.length > 1 && (
          <span className="composer-chip composer-chip--warn" data-testid="chip-two-clocks">
            <span aria-hidden="true">⚠</span> {clocks.length} date columns — say
            which one you mean
          </span>
        )}
        <span className="composer-chip composer-chip--quiet">
          {question.length}/{MAX}
        </span>
        <span className="composer-chip composer-chip--quiet">⌘↵ to run</span>
      </div>

      {/* The action sits with the field it acts on. It was below the
          strategy control and two paragraphs of explanation, which put the
          one button a reader came to press off the bottom of a phone. */}
      <div className="composer-actions">
        <button
          type="button"
          className="btn primary"
          onClick={onAsk}
          disabled={busy || !question.trim()}
        >
          {busy ? "Starting…" : label}
        </button>
      </div>

      {config?.capabilities && (
        <ModeSelector
          capabilities={config.capabilities}
          value={uiMode}
          onChange={onModeChange}
          disabled={busy}
        />
      )}

      <div className="composer-disclosure">
        <PlanningMethodDisclosure mode={uiMode} />
      </div>

      {examples.length > 0 && (
        <div className="suggestions" data-testid="question-examples" data-source={derived.length ? "schema" : "demo"}>
          <h3 className="suggestions-heading">Suggested questions</h3>
          <ul className="suggestion-list">
            {examples.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="suggestion"
                  onClick={() => onQuestionChange(item.question)}
                >
                  <span className="suggestion-intent">{item.intent}</span>
                  <span className="suggestion-text">{item.question}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
