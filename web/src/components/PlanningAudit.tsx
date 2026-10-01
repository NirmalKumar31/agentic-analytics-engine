import type { RunPayload } from "../lib/types";

function humanize(value: string) {
  return value.replaceAll("_", " ");
}

/**
 * The compact, optional record of how a governed answer was obtained.
 *
 * It is deliberately a disclosure rather than a permanent dashboard: most
 * readers need the answer and its table; auditors need the accepted contract,
 * route, coverage and timings. Neither group should have to parse the other
 * group's interface.
 */
export function PlanningAudit({ run }: { run: RunPayload }) {
  const event = run.events.find((item) => item.type === "contract_resolved");
  const data = event?.data ?? {};
  const contract = run.query_contract;
  const coverage = run.question_coverage;
  const route = typeof data.route === "string" ? data.route : null;
  const issues = Array.isArray(data.issues) ? data.issues.map(String) : [];
  const applied = coverage?.applied_components ?? [];
  const missing = coverage?.missing_components ?? [];

  if (!contract && !coverage && !event) return null;
  return (
    <details className="technical-audit" data-testid="planning-audit">
      <summary>Planning audit</summary>
      <div className="technical-audit-body">
        <p className="small dim">
          This is the governed interpretation record, not a model chain of thought.
        </p>
        <dl className="audit-grid">
          {route ? <div><dt>Route</dt><dd>{humanize(route)}</dd></div> : null}
          {event ? <div><dt>Planner calls</dt><dd>{String(data.model_calls ?? 0)}</dd></div> : null}
          {run.planner_fallback ? <div><dt>Planner fallback</dt><dd>Rules contract used</dd></div> : null}
          {run.timings?.planning_ms != null ? <div><dt>Planning</dt><dd>{run.timings.planning_ms} ms</dd></div> : null}
          {run.timings?.execution_ms != null ? <div><dt>Execution</dt><dd>{run.timings.execution_ms} ms</dd></div> : null}
          {run.timings?.verification_ms != null ? <div><dt>Verification</dt><dd>{run.timings.verification_ms} ms</dd></div> : null}
        </dl>
        {contract ? (
          <div className="audit-contract">
            <h3>Accepted contract</h3>
            <p>
              {humanize(contract.operation)}
              {contract.measure ? ` ${humanize(contract.measure)}` : ""}
              {contract.dimensions.length ? ` by ${contract.dimensions.map(humanize).join(" then ")}` : ""}
              {contract.filters.length ? ` · ${contract.filters.map((f) => `${humanize(f.column)} ${f.operator} ${f.value ?? ""}`).join("; ")}` : ""}
            </p>
          </div>
        ) : null}
        {coverage ? (
          <div className="audit-contract">
            <h3>Question coverage</h3>
            <p>{coverage.complete ? "Every required component was applied." : "The question was not fully covered."}</p>
            {applied.length ? <p className="small dim">Applied: {applied.map(humanize).join(", ")}</p> : null}
            {missing.length ? <p className="small dim">Missing: {missing.map(humanize).join(", ")}</p> : null}
          </div>
        ) : null}
        {issues.length ? <p className="small dim">Resolver notes: {issues.map(humanize).join(", ")}.</p> : null}
      </div>
    </details>
  );
}
