/**
 * What automatic routing did, or would have done, with this question.
 *
 * Compare runs the two planners side by side and never said which one
 * `auto` (the default mode) would have picked. The fact is useful and
 * is already computed: a question the rules settle exactly costs nothing,
 * and that is the substantive difference between the two columns. The
 * owner noticed its absence.
 *
 * There is deliberately no third column. `auto` is a routing policy over
 * these two planners, not a third planner, and giving it a column would
 * imply a third result that does not exist.
 *
 * What this will and will not claim
 * ---------------------------------
 * The engine emits `route` on the automatic path, with the planning-request
 * count beside it. Where that is present it is reported as fact.
 *
 * Where it is absent, this does **not** guess a route. A run that resolved
 * from rules can be reported as such, because `confident` says so and the
 * request count says what it cost. But when the rules could not resolve a
 * question, whether `auto` would escalate to a model or refuse outright
 * depends on which resolution issues are model-eligible, a decision
 * `assess()` makes and this path does not record. Printing a guess there
 * would be inventing the one fact the panel exists to report.
 */

import type { RunEvent, RunPayload } from "../lib/types";

/** The route names the engine uses, in the reader's words. */
const ROUTE_COPY: Record<string, string> = {
  rules_exact:
    "The rules resolved this question exactly. No planning request was made.",
  ai_resolved:
    "The rules could not resolve this question, so one governed planning request was made.",
  ai_unavailable:
    "The rules could not resolve this question and no planner was available, so the engine's own contract was used.",
  refused: "The question could not be mapped onto this dataset safely.",
};

interface Resolution {
  route: string | null;
  modelCalls: number | null;
  confident: boolean | null;
}

/** Read the planning record out of a run's event stream. */
export function resolutionOf(run: RunPayload | null): Resolution | null {
  if (!run) return null;
  const event: RunEvent | undefined = run.events?.find(
    (item) => item.type === "contract_resolved",
  );
  if (!event) return null;
  const data = (event.data ?? {}) as Record<string, unknown>;
  return {
    route: typeof data.route === "string" ? data.route : null,
    modelCalls: typeof data.model_calls === "number" ? data.model_calls : null,
    confident: typeof data.confident === "boolean" ? data.confident : null,
  };
}

export function PlanningRouteNote({
  deterministic,
  ai,
}: {
  deterministic: RunPayload | null;
  ai: RunPayload | null;
}) {
  const rules = resolutionOf(deterministic);
  const model = resolutionOf(ai);
  if (!rules && !model) return null;

  // A recorded route is the answer, wherever it appears. The automatic path
  // is the only one that emits it, so either lane having it means the
  // decision was actually made rather than reconstructed.
  const recorded = rules?.route ?? model?.route ?? null;

  let statement: string;
  let determined = true;
  if (recorded && ROUTE_COPY[recorded]) {
    statement = ROUTE_COPY[recorded]!;
  } else if (rules?.confident === true) {
    const cost =
      rules.modelCalls === 0
        ? " No planning request was made."
        : "";
    statement = `The rules resolved this question, so automatic mode would have used them.${cost}`;
  } else if (rules?.confident === false) {
    // The honest stopping point: escalate-or-refuse is decided by which
    // resolution issues are model-eligible, and this run did not record
    // that. Naming a route here would be a guess.
    statement =
      "The rules could not resolve this question on their own. Whether automatic mode would have asked a model or declined is not recorded for this run.";
    determined = false;
  } else {
    return null;
  }

  return (
    <div
      className="notice info"
      data-testid="auto-route-note"
      data-route={recorded ?? (determined ? "rules" : "undetermined")}
    >
      <strong>Automatic mode.</strong> {statement}{" "}
      <span className="small dim">
        Automatic routing is a policy over these two planners, not a third
        one, which is why there is no third column.
      </span>
    </div>
  );
}
