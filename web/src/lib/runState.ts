/**
 * The one truthful terminal state of a run.
 *
 * Every component derived this for itself, and both derivations were
 * wrong. `ComparisonView.statusLabel` returned "Complete" whenever a run
 * object existed, so a refused run wore a COMPLETE badge; `App.tsx`
 * rendered the AI report only for `status === 'completed'`, so the same
 * refused run rendered nothing at all. A visitor saw an empty pane labelled
 * COMPLETE beside a deterministic answer that had worked.
 *
 * The API's status vocabulary is not quite the reader's: `completed` covers
 * both "here is a verified answer" and "the run finished and published
 * nothing", which are different things to be told. The mapping lives here,
 * once.
 */

import type { RunPayload } from "./types";

export type TerminalState =
  | "running"
  | "completed_verified"
  | "verification_withheld"
  | "no_findings"
  | "refused"
  | "execution_failed"
  | "quota_stopped"
  | "cancelled"
  | "unavailable"
  | "not_started";

export interface RunState {
  state: TerminalState;
  /** Badge text. Never "Complete" for anything but a verified answer. */
  label: string;
  /** Styling hint: matches the `.tag` and `.notice` variants. */
  tone: "supported" | "warn" | "error" | "neutral";
  /** One sentence a reader can act on. Empty when the report says it. */
  reason: string;
  /** Whether a report is worth rendering for this state. */
  showsReport: boolean;
}

const LABELS: Record<TerminalState, { label: string; tone: RunState["tone"] }> =
  {
    running: { label: "Running", tone: "neutral" },
    completed_verified: { label: "Complete", tone: "supported" },
    verification_withheld: { label: "Withheld by verification", tone: "warn" },
    no_findings: { label: "No findings", tone: "neutral" },
    refused: { label: "Refused", tone: "warn" },
    execution_failed: { label: "Failed", tone: "error" },
    quota_stopped: { label: "Quota limit reached", tone: "warn" },
    cancelled: { label: "Cancelled", tone: "neutral" },
    unavailable: { label: "Unavailable", tone: "neutral" },
    not_started: { label: "Not started", tone: "neutral" },
  };

/** Statuses that mean the run could not run, rather than could not answer. */
const FAILED = new Set(["failed", "timeout"]);

export function runState(
  run: RunPayload | null | undefined,
  options: {
    error?: string | null;
    pending?: boolean;
    unavailable?: boolean;
  } = {},
): RunState {
  const { error = null, pending = false, unavailable = false } = options;

  let state: TerminalState;
  let reason = "";

  if (unavailable) {
    state = "unavailable";
    reason = error ?? "";
  } else if (error) {
    state = "execution_failed";
    reason = error;
  } else if (pending) {
    state = "running";
  } else if (!run) {
    state = "not_started";
  } else {
    const status = run.status ?? "completed";
    if (status === "running") {
      state = "running";
    } else if (status === "cancelled") {
      state = "cancelled";
      reason = run.stopped_reason ?? "";
    } else if (status === "refused") {
      state = "refused";
      reason = run.stopped_reason ?? "";
    } else if (status === "budget_exhausted") {
      state = "quota_stopped";
      reason = run.stopped_reason ?? "";
    } else if (FAILED.has(status)) {
      state = "execution_failed";
      reason = run.error ?? run.stopped_reason ?? "";
    } else if ((run.findings?.length ?? 0) > 0) {
      state = "completed_verified";
    } else if ((run.rejected?.length ?? 0) > 0) {
      // Verification checked claims and declined to publish them. The
      // engine computed an answer and then refused to stand behind it,
      // which is a different thing to be told than "there was nothing
      // here" -- and the reader can see the withheld claims and why.
      state = "verification_withheld";
      reason = run.stopped_reason || "";
    } else {
      // Nothing was published and nothing was withheld: the analysis ran
      // and found no claim to make. Collapsing this into
      // `verification_withheld` blamed a verifier that never ran, and
      // calling it "Complete" invites a reader to look for an answer that
      // was never made.
      state = "no_findings";
      reason = run.stopped_reason || "";
    }
  }

  const { label, tone } = LABELS[state];
  return {
    state,
    label,
    tone,
    reason,
    // A refusal still has a report worth showing: it carries the accepted
    // interpretation and the limitations. Only states with no run at all,
    // or no report, have nothing to render.
    showsReport: Boolean(run) && state !== "not_started" && state !== "running",
  };
}
