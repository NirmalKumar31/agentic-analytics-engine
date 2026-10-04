/**
 * Everything the report canvas no longer carries.
 *
 * The redesign removes seven panels from the default report. The brief's
 * rule is that what leaves the canvas is **relocated, not deleted**, and
 * this is where it goes: route, planner fallback, the accepted contract,
 * schema revision, build SHA, coverage, verification outcomes, cited cells,
 * timings, the activity trace and the run's limitations.
 *
 * That list is enforced. `EVIDENCE_SECTIONS` is exported and
 * `evidence.spec.ts` asserts every entry is present for a real run, so a
 * future edit cannot quietly drop a datum from the product by dropping it
 * from this component.
 *
 * It is a modal sheet because it is a disclosure over the argument, not a
 * place to go. Focus behaviour -- in on open, contained while open, back to
 * the trigger on close -- comes from `SideSheet`, which is shared with the
 * schema inspector precisely so there is one implementation of the part
 * that is easy to get wrong and invisible when it is.
 */

import { useState } from "react";

import { ActivityLog } from "./ActivityLog";
import { PlanningAudit } from "./PlanningAudit";
import { RunTimeline } from "./RunTimeline";
import { SideSheet } from "./SideSheet";
import { resolutionOf } from "./PlanningRouteNote";
import type { RunPayload } from "../lib/types";

/** Every technical datum the canvas gave up, by the name it is shown under. */
export const EVIDENCE_SECTIONS = [
  "Outcome",
  "Route",
  "Accepted contract",
  "Coverage",
  "Verification",
  "Cited cells",
  "Timings",
  "Build",
  "Limitations",
  "Activity trace",
  "Planning audit",
  "Run progress",
] as const;

function Row({
  term,
  children,
}: {
  term: string;
  children: React.ReactNode;
}) {
  return (
    <div className="evidence-row">
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function EvidenceDrawer({
  run,
  showTrace,
  onToggleTrace,
  onClose,
}: {
  run: RunPayload;
  showTrace: boolean;
  onToggleTrace: () => void;
  onClose: () => void;
}) {
  return (
    <SideSheet title="Evidence" testId="evidence-drawer" onClose={onClose}>
      <EvidenceBody
        run={run}
        showTrace={showTrace}
        onToggleTrace={onToggleTrace}
      />
    </SideSheet>
  );
}

/**
 * The evidence itself, without the sheet around it.
 *
 * Shared with `CompareEvidenceDrawer`, which shows one of these per tab.
 * Keeping it in one place is what lets `EVIDENCE_SECTIONS` mean something:
 * a datum dropped here is dropped from both drawers, and the test catches
 * it once.
 */
export function EvidenceBody({
  run,
  showTrace,
  onToggleTrace,
}: {
  run: RunPayload;
  /** Optional: the compare drawer renders traces collapsed by default. */
  showTrace?: boolean;
  onToggleTrace?: () => void;
}) {
  const [localTrace, setLocalTrace] = useState(false);
  const traceShown = showTrace ?? localTrace;
  const toggleTrace = onToggleTrace ?? (() => setLocalTrace((v) => !v));

  const resolution = resolutionOf(run);
  const contract = run.query_contract ?? null;
  const timings = run.timings ?? null;
  const coverage = run.question_coverage ?? null;
  const limitations = run.report?.limitations ?? [];

  // Which result cells the published findings actually cite. This is the
  // link between a sentence and the number under it, and it was previously
  // reachable only per-finding through a separate drawer.
  /*
   * Every collection is read defensively, and that is not belt-and-braces.
   *
   * A run payload that is still `running` carries no `findings`, no
   * `rejected` and no `events` -- the API answers with the run's identity
   * and its status and nothing else. `run.findings.flatMap(...)` threw on
   * exactly that payload, React unmounted the subtree, and the evidence
   * drawer *vanished* the moment a reader switched to a strategy that had
   * not finished. It looked like the drawer closing itself.
   */
  const rawReason = (run.stopped_reason || run.error || "").trim();
  const findings = run.findings ?? [];
  const rejected = run.rejected ?? [];
  const citedCells = findings.flatMap((finding) =>
    (finding.evidence_cells ?? []).map(
      (cell) => `${cell.result_id}[${cell.row}].${cell.column}`,
    ),
  );

  return (
    <>
      <dl className="evidence-list">
        {/*
          How the run ended, in the engine's own words.

          The canvas carries a sentence written for a reader; this carries
          the record. For a refusal those differ on purpose -- the raw
          reason begins "the question could not be mapped safely: ...",
          which is the engine describing its own difficulty rather than the
          reader's next move -- and the unedited string has to remain
          somewhere or the rewrite is a loss.
        */}
        <Row term="Outcome">
          <span className="mono">{run.outcome ?? run.status ?? "completed"}</span>
          {rawReason && (
            <span className="evidence-note" data-testid="raw-stop-reason">
              {rawReason}
            </span>
          )}
        </Row>

        <Row term="Route">
          {resolution?.route ?? run.mode ?? "deterministic"}
          {resolution?.modelCalls != null && (
            <>
              {" · "}
              {resolution.modelCalls === 0
                ? "no model call"
                : `${resolution.modelCalls} model call${resolution.modelCalls === 1 ? "" : "s"}`}
            </>
          )}
          {/* Stated whether or not it happened. "Planner fallback: not
              reached" is information; its absence is ambiguous. */}
          {" · "}
          {run.planner_fallback
            ? "planner fell back to the rules contract"
            : "no planner fallback"}
        </Row>

        <Row term="Accepted contract">
          {contract ? (
            <>
              <span className="mono">{contract.contract_hash}</span>
              {contract.interpretation && (
                <span className="evidence-note">{contract.interpretation}</span>
              )}
            </>
          ) : (
            "no contract was accepted"
          )}
          {run.schema_revision != null && (
            <span className="evidence-note">
              schema revision {run.schema_revision}
            </span>
          )}
          {/*
            What was actually executed: the operation, the measure, the
            grouping and the row restrictions.

            This was a resident `applied-analysis` panel under every report.
            Moving the panel without moving its content would have been a
            deletion dressed as a disclosure -- a reader could no longer
            find out what "average revenue by region" had been turned into.
          */}
          {contract && (
            <dl className="evidence-contract" data-testid="applied-analysis">
              <div>
                <dt>calculation</dt>
                <dd>{contract.operation}</dd>
              </div>
              {contract.measure && (
                <div>
                  <dt>measure</dt>
                  <dd>{contract.measure.replaceAll("_", " ")}</dd>
                </div>
              )}
              {(contract.dimensions ?? []).length > 0 && (
                <div>
                  <dt>grouped by</dt>
                  <dd>
                    {(contract.dimensions ?? [])
                      .map((d) => d.replaceAll("_", " "))
                      .join(" then ")}
                  </dd>
                </div>
              )}
              {contract.time_grain && (
                <div>
                  <dt>time grain</dt>
                  <dd>{contract.time_grain}</dd>
                </div>
              )}
              {(contract.filters ?? []).map((filter, index) => (
                <div key={`${filter.column}-${filter.operator}-${index}`}>
                  <dt>filter</dt>
                  <dd>
                    {filter.column.replaceAll("_", " ")} {filter.operator}{" "}
                    {filter.value === null || filter.value === undefined
                      ? ""
                      : String(filter.value)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </Row>

        <Row term="Coverage">
          {coverage ? (
            <>
              {coverage.complete
                ? "every component of the question was applied"
                : "some components of the question were not applied"}
              {coverage.missing_components.length > 0 && (
                <span className="evidence-note">
                  missing:{" "}
                  {coverage.missing_components
                    .map((component) => String(component))
                    .join(", ")}
                </span>
              )}
              {coverage.details.map((detail) => (
                <span className="evidence-note" key={detail}>
                  {detail}
                </span>
              ))}
            </>
          ) : (
            "not recorded for this run"
          )}
        </Row>

        <Row term="Verification">
          {findings.length} published · {rejected.length} withheld
          {rejected.map((verdict) => (
            <span className="evidence-note" key={verdict.finding_id}>
              {/* `status`, not a rule identifier. The backend's `Verdict`
                  carries `verdict_rule` -- `no_evidence`,
                  `causal_from_observational` and so on -- but the API type
                  the frontend is given does not expose it, and inventing
                  the field here would render `undefined` for every withheld
                  finding. Widening the payload is backend work. */}
              <span className="mono">{verdict.status}</span>
              {" — "}
              {verdict.reason}
            </span>
          ))}
        </Row>

        <Row term="Cited cells">
          {citedCells.length === 0 ? (
            "none cited"
          ) : (
            <span className="mono evidence-cells">{citedCells.join(" · ")}</span>
          )}
        </Row>

        <Row term="Timings">
          {timings ? (
            <span className="mono">
              {[
                timings.planning_ms != null && `plan ${timings.planning_ms} ms`,
                timings.execution_ms != null &&
                  `execute ${timings.execution_ms} ms`,
                timings.verification_ms != null &&
                  `verify ${timings.verification_ms} ms`,
                timings.total_ms != null && `total ${timings.total_ms} ms`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          ) : (
            <span className="mono">
              {run.metrics?.runtime_seconds != null
                ? `${run.metrics.runtime_seconds} s total`
                : "not recorded"}
            </span>
          )}
        </Row>

        <Row term="Build">
          <span className="mono">
            {[
              run.engine_version && `engine ${run.engine_version}`,
              run.build_sha && `sha ${run.build_sha}`,
              run.resolved_model && `model ${run.resolved_model}`,
            ]
              .filter(Boolean)
              .join(" · ") || "not recorded"}
          </span>
        </Row>

        <Row term="Limitations">
          {limitations.length === 0 ? (
            "none recorded"
          ) : (
            <ul className="evidence-limitations">
              {limitations.map((limitation) => (
                <li key={limitation}>{limitation}</li>
              ))}
            </ul>
          )}
        </Row>
      </dl>

      {/*
          The run timeline, from `run.events` rather than from the live
          stream. The stream is what arrived; the payload is the engine's
          own complete record, and on a finished run they are not the same
          -- a streamed `contract_resolved` that never landed left the plan
          stage reading "active" under a published report.
      */}
      <section className="evidence-timeline" aria-label="Run progress">
        <h3 className="section-heading">Run progress</h3>
        <RunTimeline events={run.events ?? []} />
      </section>

      {/* The full typed-plan record. The brief places it inside this sheet
          and nowhere else: it is the auditor's view, and it used to be a
          disclosure resident under every report. */}
      <section className="evidence-plan" aria-label="Planning audit">
        <h3 className="section-heading">Planning audit</h3>
        <PlanningAudit run={run} />
      </section>

      <section className="evidence-trace" aria-label="Activity trace">
        <h3 className="section-heading">Activity trace</h3>
        <ActivityLog
          events={run.events ?? []}
          trace={run.mcp_trace ?? []}
          showTrace={traceShown}
          onToggleTrace={toggleTrace}
          running={false}
        />
      </section>
    </>
  );
}
