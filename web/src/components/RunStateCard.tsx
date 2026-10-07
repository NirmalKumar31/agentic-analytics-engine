/**
 * What happened, when it was not a verified answer.
 *
 * This was local to `ComparisonView`, which meant the six terminal states
 * were distinguishable in Compare and invisible everywhere else. In
 * single-mode -- the default -- `ReportWorkspace` rendered the report body
 * with no state card at all, so a refused, quota-stopped, cancelled or
 * failed run looked like an ordinary report that happened to be empty, and
 * the only signal was a generic "the run stopped early" notice.
 *
 * One card, one copy table, both surfaces. The states are a reader's
 * vocabulary, not a layout's, so they should not depend on which layout
 * the reader happens to be in.
 */

import type { RunState } from "../lib/runState";

/**
 * Tone to notice variant.
 *
 * All four tones are mapped. The previous version sent anything that was
 * not `error` to `warn`, which was correct while every non-verified state
 * was a problem, and became wrong once `no_findings` existed, because
 * "the analysis found nothing to claim" is an outcome, not a warning.
 */
const TONE_CLASS: Record<RunState["tone"], string> = {
  error: "error",
  warn: "warn",
  neutral: "info",
  supported: "success",
};

/**
 * What each state means, when the engine gave no reason of its own.
 *
 * The engine's own words are preferred wherever it supplied them; these
 * are the fallback, and each says what the state is rather than apologising
 * for it.
 */
export const RUN_STATE_FALLBACK: Record<string, string> = {
  refused: "The question could not be mapped to this dataset safely.",
  execution_failed: "The analysis could not be completed.",
  quota_stopped:
    "The analysis stopped at its configured usage limit. No further provider request was made.",
  verification_withheld:
    "The analysis ran, but no finding survived the publication checks. The withheld findings below say why.",
  no_findings:
    "The analysis ran and found nothing it could claim about this question. Nothing was withheld: there was no finding to check.",
  cancelled: "The run was stopped before it finished.",
  unavailable: "This mode is not available on this deployment.",
};

export function RunStateCard({ state }: { state: RunState }) {
  // A verified answer speaks for itself, and a running or unstarted run has
  // nothing to report yet.
  if (
    state.state === "completed_verified" ||
    state.state === "running" ||
    state.state === "not_started"
  ) {
    return null;
  }
  return (
    <div
      className={`notice ${TONE_CLASS[state.tone]}`}
      role={state.tone === "error" ? "alert" : "status"}
      data-testid="run-state-card"
      data-state={state.state}
    >
      <strong>{state.label}.</strong>{" "}
      {state.reason || RUN_STATE_FALLBACK[state.state]}
    </div>
  );
}
