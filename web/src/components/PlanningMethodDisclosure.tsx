import type { UiMode } from "../lib/types";

/**
 * What is real and what is scripted, for the modes where that is not
 * already said.
 *
 * The `auto` notice is gone. It read "Clear questions are resolved by rules
 * without contacting a model. If the question is genuinely ambiguous, one
 * governed AI planning call may be used; the engine still executes and
 * verifies the calculation deterministically" -- directly under the
 * server's own description of Governed Analysis, which says the same thing
 * in different words. Two paragraphs making one claim is how a reader
 * learns that the second paragraph can be skipped.
 *
 * The deterministic notice stays, because it makes a claim the capability
 * description does not: that the *interpretation* is scripted in this
 * public demo while the SQL, statistics, tool execution, verification and
 * provenance are real. That distinction is the whole basis on which a
 * reader should trust the numbers, and nothing else on the screen states it.
 */
export function PlanningMethodDisclosure({ mode }: { mode: UiMode }) {
  if (mode === "deterministic") {
    return (
      <p
        className="notice info small"
        data-testid="interpretation-notice"
        style={{ margin: 0 }}
      >
        Question interpretation is rule-based in this public demo: a scripted
        provider maps your wording onto the dataset, and says so when it cannot.
        The SQL, statistics, MCP tool execution, verification and provenance
        are real.
      </p>
    );
  }
  return null;
}
