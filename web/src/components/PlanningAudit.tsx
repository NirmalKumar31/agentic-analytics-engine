import type { RunPayload } from "../lib/types";

/**
 * Tolerant of a missing value, like every other read in this drawer.
 *
 * A contract arriving without `operation` crashed the audit outright --
 * React unmounts the subtree and the drawer appears to close itself, which
 * is the same failure mode that once made the Compare evidence drawer
 * vanish on a strategy that had not finished. Every collection and every
 * string here is read defensively for that reason.
 */
function humanize(value: string | null | undefined) {
  return (value ?? "").replaceAll("_", " ");
}

/** The engine's role words are not a reader's. */
function readable(role: string): string {
  if (role === "measure") return "quantity";
  if (role === "dimension") return "category";
  return humanize(role);
}

/**
 * The compact, optional record of how a governed answer was obtained.
 *
 * It is deliberately a disclosure rather than a permanent dashboard: most
 * readers need the answer and its table; auditors need the accepted contract,
 * route, coverage and timings. Neither group should have to parse the other
 * group's interface.
 */
export function PlanningAudit({
  run,
  open = false,
}: {
  run: RunPayload;
  /** Open from the start, for the print appendix. See `EvidenceBody`. */
  open?: boolean;
}) {
  // Optional: a run that is still in flight has no `events` at all, and
  // the drawer renders this component for whichever strategy a reader
  // selects -- finished or not.
  const event = (run.events ?? []).find(
    (item) => item.type === "contract_resolved",
  );
  const data = event?.data ?? {};
  const contract = run.query_contract;
  const coverage = run.question_coverage;
  const route = typeof data.route === "string" ? data.route : null;
  const issues = Array.isArray(data.issues) ? data.issues.map(String) : [];
  const applied = coverage?.applied_components ?? [];
  const missing = coverage?.missing_components ?? [];

  const roleEvidence = (run.role_evidence ?? []).filter(
    (item) => item.role_source === "user_confirmed",
  );

  if (!contract && !coverage && !event && roleEvidence.length === 0) return null;
  return (
    <details className="technical-audit" data-testid="planning-audit" open={open}>
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
          {run.engine_version ? <div><dt>Engine</dt><dd>{run.engine_version}</dd></div> : null}
          {run.build_sha && run.build_sha !== "unknown" ? (
            <div><dt>Build</dt><dd><code>{run.build_sha.slice(0, 12)}</code></dd></div>
          ) : null}
        </dl>
        {roleEvidence.length > 0 && (
          <div data-testid="role-evidence">
            <p className="small dim" style={{ margin: "8px 0 4px" }}>
              {/*
                Only the confirmed columns, and only those the contract used.
                A reader asking why something is grouped that way is not
                asking about columns nothing touched, and a column the engine
                classified on its own needs no explanation here.
              */}
              Roles confirmed for this dataset session
            </p>
            <dl className="audit-grid">
              {roleEvidence.map((item) => (
                <div key={item.column}>
                  <dt>{item.column}</dt>
                  <dd>
                    Used as {item.used_as.join(", ")} · read as{" "}
                    {readable(item.effective_role)} · originally inferred{" "}
                    {readable(item.inferred_role)} · confirmed for this dataset
                    session
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}
        {contract ? (
          <div className="audit-contract">
            <h3>Accepted contract</h3>
            <p>
              {humanize(contract.operation)}
              {contract.measure ? ` ${humanize(contract.measure)}` : ""}
              {(contract.dimensions ?? []).length ? ` by ${(contract.dimensions ?? []).map(humanize).join(" then ")}` : ""}
              {(contract.filters ?? []).length ? ` · ${(contract.filters ?? []).map((f) => `${humanize(f.column)} ${f.operator} ${f.value ?? ""}`).join("; ")}` : ""}
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
