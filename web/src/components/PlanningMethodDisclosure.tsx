import type { UiMode } from "../lib/types";

export function PlanningMethodDisclosure({ mode }: { mode: UiMode }) {
  if (mode === "auto") {
    return (
      <p
        className="notice info small"
        data-testid="interpretation-notice"
        style={{ margin: 0 }}
      >
        Clear questions are resolved by rules without contacting a model. If
        the question is genuinely ambiguous, one governed AI planning call may
        be used; the engine still executes and verifies the calculation
        deterministically.
      </p>
    );
  }
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
