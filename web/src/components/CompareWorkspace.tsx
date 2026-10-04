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
import { EvidenceBody } from "./EvidenceDrawer";
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

function tokens(usage: RunUsage | undefined, side: "input" | "output"): string {
  if (!usage) return "—";
  const value = side === "input" ? usage.input_tokens : usage.output_tokens;
  return value.toLocaleString();
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

      {/*
        The verdict block.

        `contract-comparison` names the whole thing -- the headline, the
        sentence under it and the four facts -- rather than a notice box,
        because the comparison *is* all of that. `data-verdict` stays on it
        as the stable machine-readable signal: the prose is written for a
        reader and may change, the verdict may not.
      */}
      <section
        className="compare-verdict"
        data-testid="contract-comparison"
        data-verdict={comparison.verdict}
      >
        <h1 className="display" data-testid="compare-verdict-headline">
          {comparison.headline}
        </h1>
        <p className="context-line">{comparison.detail}</p>

      {/*
        The substantive claim, and the reason there is no winner badge.
        Both strategies run the same engine over the same dataset with the
        same coverage checks, the same verification and the same publication
        checks; only the *planning* differs, and the model never calculates
        a result. Without this a reader can only guess what "AI Analytics"
        did, and the obvious guess -- that a model produced the numbers --
        is the one thing that is not true.
      */}
      <p className="compare-scope-note">
        Both strategies run the same analytics engine over the same dataset,
        with the same coverage checks, the same verification and the same
        publication checks. Only the planning differs: rule-based planning on
        one side, a cloud model on the other. The model never calculates a
        result. The two are shown independently and are not ranked.
      </p>

      {comparable && (
        <dl className="compare-facts">
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
              {/* A policy decision, not a third execution lane. Automatic
                  routing chooses *which* of the two strategies runs; it is
                  not a strategy of its own, and presenting it as a third
                  column would invent a run that never happened. */}
              <PlanningRouteNote
                deterministic={deterministic.run}
                ai={ai.run}
              />
            </dd>
          </div>
        </dl>
      )}
      </section>

      {/* How each strategy got there: one table, not two stage stacks. */}
      <section className="compare-routes" aria-label="How each strategy got there">
        <h2 className="section-heading">How each strategy got there</h2>
        <div className="scroll-x">
          <table className="data" data-testid="compare-routes">
            <thead>
              <tr>
                <th scope="col">strategy</th>
                <th scope="col">calls</th>
                <th scope="col">input</th>
                <th scope="col">output</th>
                <th scope="col">cost</th>
                <th scope="col">runtime</th>
                <th scope="col">contract</th>
                <th scope="col">status</th>
              </tr>
            </thead>
            <tbody>
              {[
                { side: deterministic, state: left, role: "deterministic" },
                { side: ai, state: right, role: "ai" },
              ].map(({ side, state, role }) => (
                <tr key={role}>
                  <th scope="row">{side.title}</th>
                  <td className="numeric">{calls(side.usage)}</td>
                  {/* Token counts in their own cells. A deterministic run
                      has no usage at all, and printing a zero would invent
                      one -- an em dash says "there was none", which is a
                      different statement. */}
                  <td className="numeric">{tokens(side.usage, "input")}</td>
                  <td className="numeric">{tokens(side.usage, "output")}</td>
                  <td className="numeric">{money(side.usage)}</td>
                  <td className="numeric">{runtime(side.run)}</td>
                  <td className="mono">{contractHash(side.run)}</td>
                  {/* Each side's own terminal state, machine-readable.
                      Compare must never collapse two outcomes into one: a
                      run that refused and a run that completed are not "the
                      comparison". The id is the one the suite has always
                      used; it moved from a pane header to the row of the
                      table that compares the two. */}
                  <td
                    data-testid="pane-status"
                    data-state={state.state}
                    // Polite, per side. A reader using a screen reader has
                    // to learn that *this* strategy finished, not that
                    // "the comparison" changed: the two sides reach their
                    // terminal states independently and at different times.
                    aria-live="polite"
                  >
                    {state.label}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

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
              {/*
                Cost and tokens stay visible even when the result is shared:
                when two strategies produce the same numbers, what they
                spent getting there is the substantive difference between
                them, and it is the only thing a reader can act on.
              */}
              {[
                { side: deterministic, role: "deterministic" },
                { side: ai, role: "ai" },
              ].map(({ side, role }) => (
                <div className="evidence-row" key={role}>
                  <dt>{side.title}</dt>
                  <dd>
                    {/* A deterministic run has no usage record at all.
                        "— · — calls · — in · — out" is four dashes where
                        one fact belongs; "no provider call" says the thing
                        a reader wants to know, which is that this side
                        cost nothing and contacted nothing. */}
                    {side.usage
                      ? `${money(side.usage)} · ${calls(side.usage)} calls · ${tokens(
                          side.usage,
                          "input",
                        )} in · ${tokens(side.usage, "output")} out`
                      : "no provider call, no cost"}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        </section>
      ) : (
        <section className="compare-diff-section" data-testid="divergence">
          {comparable && (
            <>
              <h2 className="section-heading">The strategies did not agree</h2>
              <p className="compare-diff-note">
                Neither result is presented as the answer. The difference is
                shown field by field rather than summarised, because a
                summary would be one more interpretation on top of the two
                already in question.
              </p>
            </>
          )}

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
          ) : comparable ? (
            <p className="compare-diff-note">
              The differing part of the interpretation is not one this report
              breaks out. Both traces are in the evidence drawer.
            </p>
          ) : null}

          {/* Both results, below the difference rather than beside it: the
              difference is what a reader opened Compare for. */}
          {/*
            Both sides, below the difference rather than beside it: the
            difference is what a reader opened Compare for.

            Rendered whenever there is not one shared answer -- including
            while the two are *not yet comparable*. An earlier version gated
            this on `comparable`, so a run that had finished was hidden
            because the other side was still going or had failed. A result
            on hand is not withheld because its counterpart is missing.
          */}
          <div className="compare-grid">
            {[
              { side: deterministic, role: "deterministic" },
              { side: ai, role: "ai" },
            ].map(({ side, role }) => (
              <article
                key={role}
                className="compare-pane"
                // A labelled region: each side is independently identifiable
                // to a screen reader, which is what stops two panes of
                // similar numbers becoming one undifferentiated block.
                role="region"
                aria-label={side.title}
              >
                <h3 className="section-heading">{side.title}</h3>
                <p className="compare-pane-sub">{side.subtitle}</p>
                {/*
                  Inside the side's own block, not above both of them.
                  A refusal, a quota stop or a failed start belongs to one
                  strategy, and a reader has to be able to attribute it
                  without counting which card came first. `RunStateCard`
                  renders nothing for a verified answer, so a clean side
                  carries no card at all.
                */}
                <RunStateCard state={stateOf(side)} />
                {side.children ?? (
                  <p className="compare-pane-empty" aria-live="polite">
                    {stateOf(side).reason || stateOf(side).label}
                  </p>
                )}
              </article>
            ))}
          </div>
        </section>
      )}

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

      {/*
        The same appendix `AnswerReport` renders, with one section per
        strategy. Compare's evidence is behind a tabbed drawer, and a tab is
        a screen device: on paper there is nothing to switch, so both traces
        are laid out one after the other. Without this, printing a Compare
        reaches the reader as two answers and no record of how either was
        produced -- which is the one thing a comparison is for.
      */}
      <section
        className="print-appendix"
        data-print-appendix=""
        data-testid="compare-print-appendix"
        hidden
      >
        <h2 className="section-heading">Appendix: evidence</h2>
        {[deterministic, ai].map((side) =>
          side.run ? (
            <section
              key={side.title}
              className="print-appendix-side"
              data-strategy={side.title}
            >
              <h3 className="section-heading">{side.title}</h3>
              <EvidenceBody run={side.run} expanded />
            </section>
          ) : null,
        )}
      </section>

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
