/**
 * Where the reader is in the work, from the phase the engine is actually in.
 *
 * This used to take a four-value `WorkflowStage` -- dataset, ask, running,
 * report -- derived in `App` from whether a run object existed. It rendered
 * five steps. "Verify" could therefore never be active: it went from idle
 * straight to done, so the interface named a stage it never showed the
 * reader being in, while the engine genuinely has a verification phase and
 * reports it.
 *
 * It now reads `phaseOf`'s twelve states, which are derived from the event
 * stream and already distinguish routing, executing, verifying and
 * presenting. Five steps, every one of which can be reached.
 *
 * A stopped run marks the stage it stopped at rather than quietly showing
 * the earlier stages as done and the rest as idle, which read as a run
 * still in progress.
 */

import type { Phase } from "../lib/phase";

type StepState = "idle" | "active" | "done" | "stopped";

/** The five stages, in order. Labels are the reader's, not the code's. */
const STEPS = ["Dataset", "Ask", "Analyse", "Verify", "Report"] as const;

/**
 * Which stage each phase is in, and whether it stopped there.
 *
 * `index` is the stage the phase occupies. Earlier stages are done, later
 * ones idle, and the occupied one is active -- or stopped, when the run
 * ended there without finishing.
 */
const PHASE_STAGE: Record<Phase, { index: number; stopped?: boolean }> = {
  booting: { index: 0 },
  choose_dataset: { index: 0 },
  profiling: { index: 0 },
  ready_to_ask: { index: 1 },
  routing: { index: 2 },
  executing: { index: 2 },
  verifying: { index: 3 },
  presenting: { index: 4 },
  completed: { index: 4 },
  // A refusal happens while the question is being mapped onto the dataset,
  // which is the Analyse stage. Nothing was executed and nothing verified.
  refused: { index: 2, stopped: true },
  failed: { index: 2, stopped: true },
  // The dataset went away, so the stage that failed is the dataset itself.
  expired: { index: 0, stopped: true },
};

export function stepStates(phase: Phase): StepState[] {
  const { index, stopped } = PHASE_STAGE[phase];
  return STEPS.map((_, position) => {
    if (position < index) return "done";
    if (position > index) return "idle";
    return stopped ? "stopped" : "active";
  });
}

export function WorkflowIndex({ phase }: { phase: Phase }) {
  const states = stepStates(phase);
  return (
    <nav className="steps" aria-label="Progress" tabIndex={0}>
      {STEPS.map((label, position) => (
        <Step
          key={label}
          index={position + 1}
          label={label}
          state={states[position]!}
        />
      ))}
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
  state: StepState;
}) {
  return (
    <div
      className="step"
      data-state={state}
      // The state is carried by colour and by the glyph; `aria-current`
      // carries it to a screen reader, which cannot see either.
      aria-current={state === "active" || state === "stopped" ? "step" : undefined}
    >
      <span className="step-index" aria-hidden="true">
        {state === "done" ? "✓" : state === "stopped" ? "!" : index}
      </span>
      {label}
      {state === "stopped" && <span className="sr-only"> — stopped here</span>}
    </div>
  );
}
