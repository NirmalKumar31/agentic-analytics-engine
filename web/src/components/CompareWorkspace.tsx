/**
 * Two strategies, one question.
 *
 * There is deliberately no winner badge, score or accuracy ranking. The two
 * sides differ in how the analysis was *planned*; that is not evidence that
 * either is more accurate, and a badge saying otherwise would be a claim the
 * engine cannot support.
 *
 * The approved composition, and what each part replaces:
 *
 *   verdict strip       four facts -- contracts, coverage, output, recorded
 *                       route -- read straight from `compareRuns`.
 *   how each got there  one table: route, calls, cost, runtime, contract,
 *                       status. This replaces two `ExecutionLane` stage
 *                       stacks, which restated the same five steps twice.
 *   the answer, once    when the strategies agree. The reclaimed space
 *                       carries *what agreement was measured over*, because
 *                       agreement asserted but not specified is a slogan.
 *   the difference      when they do not. Field by field, with the plain
 *                       statement that neither result is presented as the
 *                       answer. Not two results side by side, which invites
 *                       picking the preferred number.
 *   one action          "Inspect both traces", opening a drawer with a tab
 *                       per strategy. Two persistent evidence buttons
 *                       implied two destinations and made the reader pick a
 *                       side before reading anything.
 *
 * `compareRuns` is untouched. Convergence onto one shared answer happens
 * only where it already decided the contracts, the coverage and the executed
 * values all match.
 */

import { useState } from "react";

import { compareRuns } from "../lib/comparison";
import { runState } from "../lib/runState";
import { opensSheet } from "./SideSheet";
import { CompareEvidenceDrawer } from "./CompareEvidenceDrawer";
import { PlanningRouteNote } from "./PlanningRouteNote";
import { RunStateCard } from "./RunStateCard";
import type { ReactNode } from "react";
import type { RunPayload, RunUsage } from "../lib/types";

export interface CompareSide {
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

function stateOf(side: CompareSide) {
  return runState(side.run, {
    error: side.error,
    pending: side.pending,
    unavailable: side.unavailable,
  });
}

function money(usage: RunUsage | undefined): string {
  if (!usage) return "—";
  return `$${(usage.estimated_cost_microdollars / 1_000_000).toFixed(6)}`;
}

function calls(usage: RunUsage | undefined): string {
  return usage ? String(usage.provider_attempts) : "—";
}

function runtime(run: RunPayload | null): string {
  const seconds = run?.metrics?.runtime_seconds;
  return seconds == null ? "—" : `${seconds} s`;
}

function contractHash(run: RunPayload | null): string {
  return run?.query_contract?.contract_hash?.slice(0, 6) ?? "—";
}

export function CompareWorkspace({
  question,
  deterministic,
  ai,
}: {
  question: string;
  deterministic: CompareSide;
  ai: CompareSide;
}) {
  const [evidenceOpen, setEvidenceOpen] = useState(false);

  const comparison = compareRuns(deterministic.run, ai.run);
  const left = stateOf(deterministic);
  const right = stateOf(ai);
  const comparable = Boolean(deterministic.run && ai.run);
  const differences = comparison.differences;

  // Four facts, in the order the composition states them.
  const facts: Array<{ term: string; value: string; warn: boolean }> = [
    {
      term: "contracts",
      value: !comparable
        ? "not comparable"
        : differences.length > 0
          ? "differ"
          : "identical",
      warn: differences.length > 0,
    },
    {
      term: "coverage",
      value: !comparable
        ? "—"
        : comparison.verdict === "agree_but_incomplete"
          ? "incomplete on both"
          : "identical",
      warn: comparison.verdict === "agree_but_incomplete",
    },
    {
      term: "output",
      value: !comparable
        ? "—"
        : comparison.shareOneResult
          ? "identical"
          : "differs",
      warn: comparable && !comparison.shareOneResult,
    },
  ];

  return (
    <div className="compare" data-testid="compare-workspace">
      <p className="report-question">{question}</p>
      <h1 className="display" data-testid="compare-verdict-headline">
        {comparison.headline}
      </h1>
      <p className="context-line">{comparison.detail}</p>

      {comparable && (
        <dl
          className="compare-facts"
          data-testid="compare-facts"
          data-verdict={comparison.verdict}
        >
          {facts.map((fact) => (
            <div key={fact.term} className="compare-fact">
              <dt>{fact.term}</dt>
              <dd className={fact.warn ? "compare-fact--warn" : undefined}>
                {fact.value}
              </dd>
            </div>
          ))}
          <div className="compare-fact">
            <dt>recorded route</dt>
            <dd>
              <PlanningRouteNote
                deterministic={deterministic.run}
                ai={ai.run}
              />
            </dd>
          </div>
        </dl>
      )}

      {/* How each strategy got there: one table, not two stage stacks. */}
      <section className="compare-routes" aria-label="How each strategy got there">
        <h2 className="section-heading">How each strategy got there</h2>
        <div className="scroll-x">
          <table className="data" data-testid="compare-routes">
            <thead>
              <tr>
                <th scope="col">strategy</th>
                <th scope="col">calls</th>
                <th scope="col">cost</th>
                <th scope="col">runtime</th>
                <th scope="col">contract</th>
                <th scope="col">status</th>
              </tr>
            </thead>
            <tbody>
              {[
                { side: deterministic, state: left },
                { side: ai, state: right },
              ].map(({ side, state }) => (
                <tr key={side.title}>
                  <th scope="row">{side.title}</th>
                  <td className="numeric">{calls(side.usage)}</td>
                  <td className="numeric">{money(side.usage)}</td>
                  <td className="numeric">{runtime(side.run)}</td>
                  <td className="mono">{contractHash(side.run)}</td>
                  <td>{state.label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* A side that did not produce a report says why, before anything
          claims the two can be compared. */}
      {!left.showsReport && <RunStateCard state={left} />}
      {!right.showsReport && <RunStateCard state={right} />}

      {comparison.shareOneResult ? (
        <section className="compare-shared" data-testid="shared-result">
          <h2 className="section-heading">
            Both strategies produced the same answer
          </h2>
          <div className="compare-body">{deterministic.children}</div>

          {/*
            The reclaimed space. Agreement that is asserted but not
            specified is a slogan, so this says what it was measured over.
          */}
          <section
            className="compare-agreement"
            data-testid="agreement-basis"
            aria-label="Why this counts as agreement"
          >
            <h3 className="section-heading">Why this counts as agreement</h3>
            <dl className="evidence-list">
              <div className="evidence-row">
                <dt>accepted contract</dt>
                <dd>
                  the same typed plan: same measure, same period field, same
                  filters, same grouping
                </dd>
              </div>
              <div className="evidence-row">
                <dt>executed output</dt>
                <dd>
                  the same values over the same rows. Wording differs between
                  the two narrations and is not compared: it is not part of
                  the contract.
                </dd>
              </div>
              <div className="evidence-row">
                <dt>cost</dt>
                <dd>
                  {deterministic.title} {money(deterministic.usage)} ·{" "}
                  {ai.title} {money(ai.usage)}
                </dd>
              </div>
            </dl>
          </section>
        </section>
      ) : comparable ? (
        <section className="compare-diff-section" data-testid="divergence">
          <h2 className="section-heading">The strategies did not agree</h2>
          <p className="compare-diff-note">
            Neither result is presented as the answer. The difference is shown
            field by field rather than summarised, because a summary would be
            one more interpretation on top of the two already in question.
          </p>

          {differences.length > 0 ? (
            <div className="scroll-x">
              <table className="data contract-diff" data-testid="contract-diff">
                <thead>
                  <tr>
                    <th scope="col">field</th>
                    <th scope="col">{deterministic.title}</th>
                    <th scope="col">{ai.title}</th>
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
            </div>
          ) : (
            <p className="compare-diff-note">
              The differing part of the interpretation is not one this report
              breaks out. Both traces are in the evidence drawer.
            </p>
          )}

          {/* Both results, below the difference rather than beside it: the
              difference is what a reader opened Compare for. */}
          <div className="compare-grid">
            <article className="compare-pane">
              <h3 className="section-heading">{deterministic.title}</h3>
              {deterministic.children}
            </article>
            <article className="compare-pane">
              <h3 className="section-heading">{ai.title}</h3>
              {ai.children}
            </article>
          </div>
        </section>
      ) : null}

      <div className="report-actions">
        <button
          type="button"
          className="btn"
          {...opensSheet(() => setEvidenceOpen(true))}
          data-testid="inspect-both-traces"
        >
          Inspect both traces <span aria-hidden="true">→</span>
        </button>
        <span className="compare-action-note">
          opens one drawer with a tab per strategy
        </span>
      </div>

      {evidenceOpen && (
        <CompareEvidenceDrawer
          deterministic={{ title: deterministic.title, run: deterministic.run }}
          ai={{ title: ai.title, run: ai.run }}
          onClose={() => setEvidenceOpen(false)}
        />
      )}
    </div>
  );
}
