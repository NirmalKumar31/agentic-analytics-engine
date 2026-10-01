/**
 * Two results from one question, reported side by side.
 *
 * There is deliberately no winner badge, score or accuracy ranking. The two
 * sides differ in how the analysis was planned; that is not evidence that
 * either is more accurate, and a badge saying otherwise would be a claim
 * the engine cannot support.
 */

import type { ReactNode } from "react";
import { compareRuns } from "../lib/comparison";
import { ExecutionLane } from "./ExecutionLanes";
import { RunStateCard } from "./RunStateCard";
import { runState } from "../lib/runState";
import type { RunPayload, RunUsage } from "../lib/types";

interface Side {
  title: string;
  subtitle: string;
  run: RunPayload | null;
  error: string | null;
  pending: boolean;
  usage?: RunUsage;
  /** AI off by capability, quota or configuration -- not a failed run. */
  unavailable?: boolean;
  children: ReactNode;
}

interface Props {
  question: string;
  deterministic: Side;
  ai: Side;
}

function stateOf(side: Side) {
  return runState(side.run, {
    error: side.error,
    pending: side.pending,
    unavailable: side.unavailable,
  });
}

/**
 * What one mode spent. Kept visible even when the result is shared,
 * because the cost difference is the substantive difference between two
 * modes that produced the same numbers.
 */
function UsageSummary({
  usage,
  label,
}: {
  usage: RunUsage | undefined;
  label?: string;
}) {
  if (!usage) return null;
  return (
    <dl className="usage-summary small dim">
      {label ? (
        <div>
          <dt>Mode</dt>
          <dd>{label}</dd>
        </div>
      ) : null}
      <div>
        <dt>Model calls</dt>
        <dd>{usage.provider_attempts}</dd>
      </div>
      <div>
        <dt>Input tokens</dt>
        <dd>{usage.input_tokens.toLocaleString()}</dd>
      </div>
      <div>
        <dt>Output tokens</dt>
        <dd>{usage.output_tokens.toLocaleString()}</dd>
      </div>
    </dl>
  );
}

function Pane({ side }: { side: Side }) {
  const state = stateOf(side);
  return (
    <section className="compare-pane" aria-label={side.title}>
      <div className="panel-head">
        <h2>{side.title}</h2>
        <span className="spacer" style={{ flex: 1 }} />
        <span
          className={`tag ${state.tone}`}
          data-testid="pane-status"
          data-state={state.state}
          aria-live="polite"
        >
          {state.label}
        </span>
      </div>
      <p className="small dim compare-subtitle">{side.subtitle}</p>

      <RunStateCard state={state} />

      <UsageSummary usage={side.usage} />

      <div className="compare-body">{side.children}</div>
      {!state.showsReport && !side.children ? (
        <p className="small dim" data-testid="pane-placeholder">
          {state.state === "running"
            ? "This side is still running."
            : "This side has not started."}
        </p>
      ) : null}
    </section>
  );
}

export function ComparisonView({ question, deterministic, ai }: Props) {
  // Three separate questions, answered separately: did the two planners
  // agree, does the agreed contract cover what was asked, and did it
  // produce the same numbers. One hash comparison used to stand in for all
  // three, so "same governed interpretation" was shown for a contract that
  // dropped a grouping -- and for a fallback, where the AI pane was
  // running the engine's own contract.
  const comparison = compareRuns(deterministic.run, ai.run);
  const comparable = comparison.verdict !== "not_comparable";
  const differences = comparison.differences;

  return (
    <div className="stack report-workspace">
      <section className="panel">
        <div className="panel-head">
          <h2>Planning strategies compared</h2>
        </div>
        <div className="panel-body stack">
          <p style={{ margin: 0 }}>{question}</p>
          <p className="small dim" style={{ margin: 0 }}>
            The same question, planned two ways. Deterministic Analytics uses
            rule-based planning; AI Analytics uses a cloud model to translate
            the question into a typed analytical contract. Everything after that
            is identical and deterministic: both run the same analytics engine,
            the same SQL guard, the same DuckDB execution through MCP, the same
            coverage checks, the same verification and the same publication
            checks. The model never calculates a result. The two are shown
            independently and are not ranked.
          </p>
          {comparable ? (
            <div
              className={`notice ${
                comparison.tone === "supported"
                  ? "success"
                  : comparison.tone === "error"
                    ? "error"
                    : "warn"
              }`}
              role={comparison.tone === "error" ? "alert" : "status"}
              data-testid="contract-comparison"
              data-verdict={comparison.verdict}
            >
              <strong>{comparison.headline}.</strong> {comparison.detail}
              {differences.length > 0 ? (
                <table className="contract-diff" data-testid="contract-diff">
                  <caption className="small dim">
                    Where the two governed interpretations differ
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">Part of the question</th>
                      <th scope="col">Deterministic Analytics</th>
                      <th scope="col">AI Analytics</th>
                    </tr>
                  </thead>
                  <tbody>
                    {differences.map((row) => (
                      <tr key={row.label}>
                        <th scope="row">{row.label}</th>
                        <td>{row.deterministic}</td>
                        <td>{row.ai}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
              {comparison.verdict === "contracts_differ" &&
              differences.length === 0 ? (
                <p className="small" style={{ margin: "0.5rem 0 0" }}>
                  The differing part of the interpretation is not one this
                  report breaks out. Review the applied analysis in each pane.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>What each mode did</h2>
        </div>
        <div className="panel-body">
          <div className="lane-grid">
            <ExecutionLane
              run={deterministic.run}
              mode="deterministic"
              title="Deterministic Analytics"
            />
            <ExecutionLane run={ai.run} mode="ai" title="AI Analytics" />
          </div>
        </div>
      </section>

      {comparison.shareOneResult ? (
        /*
         * One result, not two copies of it.
         *
         * When the contracts match, the coverage matches and the executed
         * values match, the two panes were rendering the same table and the
         * same chart twice. That is not a comparison -- it reads as two
         * independent confirmations, and it pushed the planning lanes, which
         * are the part that actually differed, off the top of the screen.
         * The lanes stay above; the result below is shown once.
         *
         * This branch is only ever reached for a verdict that already
         * established the outputs are identical. A disagreement of any kind
         * keeps two panes.
         */
        <section className="panel" data-testid="shared-result">
          <div className="panel-head">
            <h2>Result</h2>
            <span className="spacer" style={{ flex: 1 }} />
            <span className="tag supported">Identical in both modes</span>
          </div>
          <div className="panel-body stack">
            <p className="small dim" style={{ margin: 0 }}>
              Both modes executed the same contract and returned the same
              values, so one result is shown. What differed is above: how the
              question was planned, and what that cost.
            </p>
            <div className="shared-usage">
              <UsageSummary
                usage={deterministic.usage}
                label={deterministic.title}
              />
              <UsageSummary usage={ai.usage} label={ai.title} />
            </div>
            <div className="compare-body">{deterministic.children}</div>
          </div>
        </section>
      ) : (
        <div className="compare-grid">
          <Pane side={deterministic} />
          <Pane side={ai} />
        </div>
      )}
    </div>
  );
}
