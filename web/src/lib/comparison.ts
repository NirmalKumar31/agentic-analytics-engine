/**
 * What Compare Both is entitled to say about two runs.
 *
 * Three questions were being answered by one hash comparison, and they are
 * not the same question:
 *
 * 1. **Contract equality** -- did the two planners choose the same
 *    governed request?
 * 2. **Question coverage** -- does that request answer what was asked?
 * 3. **Output equality** -- did the same request produce the same numbers?
 *
 * "Same governed interpretation" only ever meant the first. Two planners
 * agreeing on a contract that drops a grouping agree about the wrong
 * thing, and saying so as a success is how a reader concluded the answer
 * had been double-checked. A fallback makes it worse: the AI pane can
 * carry the engine's own contract, which is identical by construction, and
 * presenting that as independent agreement is untrue.
 *
 * The third has no honest "different" state. Identical contracts over one
 * dataset fingerprint must produce identical numbers; if they do not, the
 * engine is broken and the page must say so rather than invite the reader
 * to pick.
 */

import { contractDifferences, type ContractDifference } from "./contractDiff";
import type { RunPayload } from "./types";

export type ComparisonVerdict =
  | "both_agree"
  | "agree_but_incomplete"
  | "agree_via_fallback"
  | "contracts_differ"
  | "one_refused"
  | "both_refused"
  | "one_failed"
  | "inconsistent_results"
  | "not_comparable";

export interface ComparisonState {
  verdict: ComparisonVerdict;
  headline: string;
  detail: string;
  tone: "supported" | "warn" | "error" | "neutral";
  /** Field-by-field, when the contracts differ. */
  differences: ContractDifference[];
  /** Whether one shared answer may be rendered instead of two panes. */
  shareOneResult: boolean;
}

function answerOf(run: RunPayload | null | undefined): string {
  return (run?.findings ?? []).map((finding) => finding.text).join(" | ");
}

function coverageComplete(run: RunPayload | null | undefined): boolean | null {
  const coverage = run?.question_coverage;
  return coverage ? coverage.complete : null;
}

function missingSummary(run: RunPayload | null | undefined): string {
  const coverage = run?.question_coverage;
  if (!coverage || coverage.complete) return "";
  const parts = coverage.missing_components.join(", ");
  return parts ? `missing: ${parts}` : coverage.rejection_codes.join(", ");
}

export function compareRuns(
  deterministic: RunPayload | null | undefined,
  ai: RunPayload | null | undefined,
): ComparisonState {
  const base: ComparisonState = {
    verdict: "not_comparable",
    headline: "Not comparable yet",
    detail:
      "Both modes need to finish before their interpretations can be compared.",
    tone: "neutral",
    differences: [],
    shareOneResult: false,
  };
  if (!deterministic || !ai) return base;

  const left = deterministic.status ?? "completed";
  const right = ai.status ?? "completed";
  const refused = [left, right].filter((s) => s === "refused").length;
  const failed = [left, right].filter((s) =>
    ["failed", "timeout", "budget_exhausted"].includes(s),
  ).length;

  if (refused === 2) {
    return {
      ...base,
      verdict: "both_refused",
      headline: "Both modes refused",
      detail:
        "Neither interpretation could be mapped to this dataset safely. Each pane gives its reason.",
      tone: "warn",
    };
  }
  if (failed > 0) {
    return {
      ...base,
      verdict: "one_failed",
      headline: "One mode failed to run",
      detail:
        "The failure is shown in its own pane. The other result stands on its own.",
      tone: "error",
    };
  }
  if (refused === 1) {
    return {
      ...base,
      verdict: "one_refused",
      headline: "One mode refused",
      detail:
        "A refusal is not a failed comparison: the refusing pane explains what it could not map. The other pane answered.",
      tone: "warn",
    };
  }

  // A run still in flight has no contract yet, and a missing contract is not
  // a different one.
  //
  // Falling through to the comparison below read the absence as a
  // disagreement and announced "Different governed interpretations -- these
  // panes answered different questions" while the AI pane still read
  // "running". Observed against a real governed run where the two planners
  // went on to produce the identical figure. `base` already says the true
  // thing: both modes need to finish first.
  //
  // Placed after the refusal and failure checks, because those are worth
  // reporting as soon as they are known: a pane that failed has finished.
  if (left === "running" || right === "running") return base;

  const differences = contractDifferences(
    deterministic.query_contract,
    ai.query_contract,
  );
  const sameContract =
    Boolean(deterministic.query_contract && ai.query_contract) &&
    differences.length === 0;

  if (!sameContract) {
    return {
      ...base,
      verdict: "contracts_differ",
      headline: "Different governed interpretations",
      detail:
        "These panes answered different questions, so they are not a like-for-like comparison. The differing parts are below.",
      tone: "warn",
      differences,
    };
  }

  // Identical contract over one dataset. The numbers must match.
  if (answerOf(deterministic) !== answerOf(ai)) {
    return {
      ...base,
      verdict: "inconsistent_results",
      headline: "Same interpretation, different results",
      detail:
        "The same governed request produced different numbers in the two panes. That is an internal inconsistency in this engine, not a difference of opinion between the modes, and neither result should be relied on until it is explained.",
      tone: "error",
    };
  }

  // Matching answers are not matching runs. Verification is per run, so one
  // mode can publish the same figure while having withheld something the
  // other published -- and `shareOneResult` would then hide the withheld
  // finding along with the pane that held it. When the two differ in what
  // they withheld, there is something to compare, so both panes stay.
  if ((deterministic.rejected ?? []).length !== (ai.rejected ?? []).length) {
    return {
      ...base,
      verdict: "agree_but_incomplete",
      headline: "Both modes agreed, but withheld different findings",
      detail:
        "The two panes published the same values from the same contract, and they did not withhold the same things. Each pane lists what it withheld and why.",
      tone: "warn",
    };
  }

  if (ai.planner_fallback) {
    return {
      ...base,
      verdict: "agree_via_fallback",
      headline: "AI planner fell back to the rules contract",
      detail:
        "The cloud planner did not return a usable contract, so the engine executed its own. The panes match because they ran the same contract -- not because the model independently agreed.",
      tone: "warn",
      shareOneResult: true,
    };
  }

  if (
    coverageComplete(deterministic) === false ||
    coverageComplete(ai) === false
  ) {
    return {
      ...base,
      verdict: "agree_but_incomplete",
      headline: "Both modes agreed, on an incomplete interpretation",
      detail: `Both planners chose the same contract, and it does not cover everything the question asked for (${
        missingSummary(deterministic) || missingSummary(ai)
      }). Agreement is not correctness.`,
      tone: "warn",
      shareOneResult: true,
    };
  }

  return {
    ...base,
    verdict: "both_agree",
    headline: "Both modes agreed",
    detail:
      "Both planners produced the same governed contract, it covers the question, and both executed it to the same values. One result is shown; the planning differed, not the arithmetic.",
    tone: "supported",
    shareOneResult: true,
  };
}
