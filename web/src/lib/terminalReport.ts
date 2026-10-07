/**
 * What a terminal state says on the report canvas.
 *
 * A run reaches exactly one terminal state, and the page renders exactly
 * that one. Three defects in the composition this replaces, all found by
 * looking at real payloads rendered at real sizes:
 *
 *  1. The state was a card *outside* the report column, pulled above the
 *     dataset strip by an `order: -2` rule left over from when execution
 *     panels streamed before the report.
 *  2. A refusal said its reason twice, once in the card, once verbatim as
 *     the display headline, and the raw backend stop reason is not a
 *     headline. It begins mid-sentence, in lower case, with the engine's
 *     own framing.
 *  3. "No findings" is a **completed** run and was carrying the eyebrow
 *     "Not answered", which is refusal language. The whole point of
 *     separating these states is that a reader can tell a run that declined
 *     to answer from one that answered and found nothing.
 *
 * So each state gets its own headline, written for a reader, and the
 * engine's own sentence sits underneath it as the explanation. The **raw**
 * `stopped_reason` stays out of the canvas and is reachable in the evidence
 * drawer, which is where the unedited record belongs.
 */

import type { RunPayload } from "./types";
import type { RunState, TerminalState } from "./runState";

export interface TerminalPresentation {
  /** The state, in the reader's words. Never "Not answered" for a completion. */
  eyebrow: string | null;
  headline: string;
  /** One sentence a reader can act on, or null when the headline says it. */
  explanation: string | null;
  /** Severity for the rule bar. Never carried by the primary action. */
  tone: RunState["tone"];
}

/**
 * Boilerplate the engine prefixes its refusal reasons with.
 *
 * `_abort` writes "the question could not be mapped safely: <the useful
 * part>". The useful part is what a reader can act on; the prefix restates
 * the state they have already been told. Stripping it is the only text
 * transformation here, and the untouched string is still in evidence.
 */
const PREFIXES = [
  /^the question could not be mapped safely:\s*/i,
  /^the question was not executed because\s*/i,
];

function actionable(reason: string): string {
  let text = reason.trim();
  for (const prefix of PREFIXES) text = text.replace(prefix, "");
  if (!text) return "";
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** How many rows the run matched, when it recorded one. */
function matchedRows(run: RunPayload | null): number | null {
  const snapshot = Object.values(run?.results ?? {})[0];
  const rows = snapshot?.row_count;
  return typeof rows === "number" ? rows : null;
}

export function terminalPresentation(
  state: RunState,
  run: RunPayload | null,
): TerminalPresentation | null {
  const kind: TerminalState = state.state;
  const reason = actionable(state.reason ?? "");

  switch (kind) {
    case "refused":
      return {
        eyebrow: "Refused",
        // The ask, not the engine's framing of its own failure. A reader
        // needs to know what to change about the question.
        headline: reason || "This question could not be mapped to your data",
        // "the full stop reason" read as a sentence about punctuation on a
        // rendered screen -- `stop_reason` is the field's name, not a phrase.
        explanation: reason
          ? "Nothing was published. The engine's unedited reason is in the evidence."
          : null,
        tone: "warn",
      };

    case "no_findings": {
      const rows = matchedRows(run);
      return {
        // Never "Not answered". This run completed.
        eyebrow: "No findings",
        headline: "No findings to publish",
        explanation:
          "The analysis ran and completed." +
          (rows !== null
            ? ` ${rows.toLocaleString()} ${rows === 1 ? "row" : "rows"} matched, and nothing in them could be claimed.`
            : " Nothing in the matching rows could be claimed.") +
          " Nothing was withheld: there was no finding to check.",
        tone: "neutral",
      };
    }

    case "verification_withheld": {
      const withheld = run?.rejected?.length ?? 0;
      return {
        eyebrow: "Withheld by verification",
        headline:
          withheld === 1
            ? "One finding was withheld at verification"
            : `${withheld} findings were withheld at verification`,
        explanation:
          "The analysis ran and completed. A claim that cannot be checked is " +
          "withheld rather than shown with a caveat attached. Each withheld " +
          "claim and its rule are in the evidence.",
        tone: "warn",
      };
    }

    case "quota_stopped":
      return {
        eyebrow: "Quota limit reached",
        headline: reason || "This run reached its budget before it finished",
        explanation:
          "Nothing was published: partial work is not presented as an answer. " +
          "What had already been spent is recorded in the evidence.",
        tone: "warn",
      };

    case "execution_failed": {
      /*
       * "Nothing partial has been kept" is a claim about the payload, and
       * it was stated unconditionally. A run that failed after publishing
       * something would have carried it over a visible chart and result
       * table -- the page contradicting itself in the one state where a
       * reader most needs to trust what it says.
       *
       * So it is said only when it is true, and the alternative says what
       * is actually on the page rather than going quiet about it.
       */
      // What the canvas will actually draw, not just `findings`: an
      // uploaded run carries its result in `presentation`, and the derived
      // `failed` fixture has an empty `findings` array *and* a chart, two
      // highlights and a four-row table.
      const presentation = run?.presentation ?? null;
      const published =
        (run?.findings ?? []).length > 0 ||
        (presentation?.highlights ?? []).length > 0 ||
        Boolean(presentation?.table) ||
        Boolean(presentation?.chart);

      return {
        eyebrow: "Failed",
        headline: "The analysis did not complete",
        explanation:
          (reason ? `${reason}. ` : "") +
          "This is not a statement about your data and not a refusal. " +
          (published
            ? "What is shown below is what had been published before the run stopped, not a complete answer."
            : "Nothing partial has been kept."),
        tone: "error",
      };
    }

    case "cancelled":
      return {
        eyebrow: "Cancelled",
        headline: "This run stopped because its dataset was closed",
        explanation:
          "The file was deleted, replaced, or its session expired while the " +
          "analysis was running. Nothing went wrong with the analysis — the " +
          "thing it was analysing was withdrawn.",
        tone: "neutral",
      };

    case "unavailable":
      return {
        eyebrow: "Unavailable",
        headline: reason || "This strategy is not available on this deployment",
        explanation: null,
        tone: "neutral",
      };

    // A verified answer speaks for itself, and a run that has not finished
    // has nothing terminal to say.
    case "completed_verified":
    case "running":
    case "not_started":
    default:
      return null;
  }
}
