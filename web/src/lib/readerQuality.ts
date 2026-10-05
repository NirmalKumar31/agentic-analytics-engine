/**
 * What a reader must never be shown on a primary surface.
 *
 * Every rule here exists because a live run broke it, in a PDF a person
 * actually read:
 *
 *   "a sustained month-over-month increase from 9.170305676855895 in
 *    2024-01-01T00:00:00 to 10.763569457221712 in 2024-04-01T00:00:00"
 *
 * One sentence with a binary floating-point tail, an ISO timestamp, and a
 * metric named `return_rate` in the prose beside it. Each of those is a
 * serialisation detail standing where a number, a date or a name belongs.
 *
 * This is a gate, not a transformer. It reports; it never rewrites. A
 * formatter that silently fixed prose would hide the fact that something
 * upstream was producing prose that needed fixing -- and the fix belongs
 * where the value is formatted, which is the backend's presentation.
 *
 * Identifiers, hashes, evidence quotations and provenance are out of
 * scope by construction: callers pass the text of a primary surface, and
 * the evidence appendix is not one.
 */

export type ReaderDefect =
  | "excess_precision"
  | "iso_timestamp"
  | "snake_case"
  | "missing_value"
  | "planner_vocabulary"
  | "malformed_punctuation";

export interface ReaderFinding {
  defect: ReaderDefect;
  /** The exact substring that triggered it, for a failure message. */
  evidence: string;
}

/**
 * More than two decimals.
 *
 * Two is the product's stated precision for a measurement. The pattern
 * deliberately looks for a *long* tail rather than counting digits: a
 * p-value written as `2.62e-12` is precision a reader needs, and
 * `0.0391` in a Cramér's V is not a floating-point artefact. What it
 * catches is `9.170305676855895` -- fifteen digits that came from a
 * double, not from a decision.
 */
const EXCESS_PRECISION = /\d+\.\d{6,}/;

/** A stored instant where a date belongs. */
const ISO_TIMESTAMP = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

/**
 * An engine identifier in prose.
 *
 * Two or more lower-case words joined by underscores. One underscore is
 * enough -- `return_rate` and `customer_segment` are both two words --
 * and a trailing or leading underscore is not a word at all.
 */
const SNAKE_CASE = /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/;

/** A value that did not arrive, rendered as the absence of one. */
const MISSING_VALUE = /\b(?:undefined|null|NaN|\[object Object\])\b/;

/**
 * Words from the planner's own vocabulary.
 *
 * A reader is owed the answer, not the machinery that produced it.
 * "Aggregate return rate is highest for new" put the operation in front of
 * the measure; "analysis_type" and "task_id" belong in the audit trail.
 */
const PLANNER_VOCABULARY =
  /\b(?:analysis type|analysis_type|task id|task_id|result id|result_id|tool name|snapshot|payload)\b/i;

/** Doubled punctuation, or a label that ran out before its value. */
const MALFORMED = /[.,;:]{2,}|\(\s*\)|\s+[.,;:]|:\s*$/;

const RULES: Array<[ReaderDefect, RegExp]> = [
  ["excess_precision", EXCESS_PRECISION],
  ["iso_timestamp", ISO_TIMESTAMP],
  ["snake_case", SNAKE_CASE],
  ["missing_value", MISSING_VALUE],
  ["planner_vocabulary", PLANNER_VOCABULARY],
  ["malformed_punctuation", MALFORMED],
];

/**
 * Every rule a piece of reader-facing text breaks.
 *
 * Returns all of them rather than the first, because a sentence that
 * breaks three is three separate things to fix and a message naming one
 * sends the next hour in the wrong direction.
 */
export function readerDefects(text: string): ReaderFinding[] {
  const out: ReaderFinding[] = [];
  for (const [defect, pattern] of RULES) {
    const match = pattern.exec(text);
    if (match) out.push({ defect, evidence: match[0] });
  }
  return out;
}

/** A failure message naming what was wrong and where. */
export function describeDefects(where: string, findings: ReaderFinding[]): string {
  const parts = findings.map((f) => `${f.defect} (${f.evidence})`);
  return `${where} is not fit for a reader: ${parts.join("; ")}`;
}
