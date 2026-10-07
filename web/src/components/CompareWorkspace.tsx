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
import {
  COMPARED_FIELD_COUNT,
  COMPARISON_LABEL,
  contractComparison,
  type ComparisonState,
} from "../lib/contractDiff";
import { runState } from "../lib/runState";
import { opensSheet } from "./SideSheet";
import { CompareEvidenceDrawer } from "./CompareEvidenceDrawer";
import { EvidenceBody } from "./EvidenceDrawer";
import { PlanningRouteNote } from "./PlanningRouteNote";
import { RunFlowchart } from "./RunFlowchart";
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
  /*
   * Which strategy's full report is shown.
   *
   * Zero, which is the deterministic side: it is the one that always runs,
   * so it is the one that is always there to show. Switching runs nothing
   * -- both payloads are in memory and the tab chooses which finished
   * record is displayed.
   */
  const [shown, setShown] = useState(0);

  const comparison = compareRuns(deterministic.run, ai.run);
  const left = stateOf(deterministic);
  const right = stateOf(ai);
  const comparable = Boolean(deterministic.run && ai.run);
  const differences = comparison.differences;

  /*
   * Four facts, in the order the composition states them.
   *
   * `contracts` and `coverage` each have four states rather than two,
   * because an absence is not an agreement. Both of these used to collapse
   * to "identical" whenever there was nothing to compare: a run where each
   * pane's own appendix read "no contract was accepted" and "coverage not
   * recorded for this run" was summarised above them as
   * `contracts: identical · coverage: identical`. The report contradicted
   * itself on one page, and the summary was the part that was wrong.
   */
  const contractState = contractComparison(
    deterministic.run?.query_contract,
    ai.run?.query_contract,
    { bothRan: comparable },
  );
  const coverageState: ComparisonState = !comparable
    ? "not_comparable"
    : !deterministic.run?.question_coverage || !ai.run?.question_coverage
      ? "not_recorded"
      : comparison.verdict === "agree_but_incomplete"
        ? "different"
        : "identical";

  const facts: Array<{ term: string; value: string; warn: boolean }> = [
    {
      term: "contracts",
      value: COMPARISON_LABEL[contractState],
      // `not_recorded` is flagged too: a comparison nobody can make is a
      // thing the reader has to know, not a quiet dash.
      warn: contractState === "different" || contractState === "not_recorded",
    },
    {
      term: "coverage",
      value:
        coverageState === "different"
          ? "incomplete on both"
          : COMPARISON_LABEL[coverageState],
      warn: coverageState === "different" || coverageState === "not_recorded",
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

  /** The two strategies, in the order they are offered. */
  const sides = [
    { role: "deterministic", side: deterministic },
    { role: "ai", side: ai },
  ];
  const current = sides[shown] ?? sides[0]!;

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

        {/*
          The same diagram a single run carries, once per strategy.

          It was suppressed here, and the reason was that Compare has one
          evidence control for both runs and two stage summaries would be
          two more things to read. The table above was meant to stand in
          for it. Exported to PDF that reasoning does not hold: the only
          labelled picture of either run was in the evidence appendix, and
          a reader who asked how the analysis ran got six unlabelled dots
          per strategy and a sentence.

          It also turns out to be the one place the two strategies visibly
          differ. They agree on the contract, the coverage and the values
          -- that is what the verdict strip says -- so the planning is the
          whole of the difference, and the AI side's extra `interpret`
          stage is that difference, drawn. A table of runtimes cannot show
          it; two spines of five and six boxes show it at a glance.

          Deliberately not merged into one diagram with the odd stage
          marked. A merged spine would be a run that neither strategy
          made, and this section is called "How each strategy got there".
        */}
        {[deterministic, ai].map((side) =>
          // `events` is declared on the payload and still absent from one
          // the server wrote before it carried them, so this reads it the
          // way `AnswerReport` does rather than trusting the type.
          side.run?.events?.length ? (
            <RunFlowchart
              key={side.title}
              events={side.run.events}
              strategy={side.title}
            />
          ) : null,
        )}
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
                {/*
                  The caption's numbers are the table's own: `differences`
                  is what the rows are mapped from, and
                  `COMPARED_FIELD_COUNT` is the length of the list those
                  rows were selected out of. Neither is written here, so
                  neither can drift from what is underneath it.
                */}
                <caption data-testid="contract-diff-caption">
                  {differences.length} of {COMPARED_FIELD_COUNT} compared
                  contract {differences.length === 1 ? "field" : "fields"}{" "}
                  {differences.length === 1 ? "differs" : "differ"}; the rest
                  are identical.
                </caption>
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

          {/*
            Both results, below the difference and **one at a time**.

            They were side by side in a two-column grid above 900px, and
            that is where Compare stopped being readable: two full reports
            -- each with a headline, a chart, highlights and a result table
            -- in half a laptop's width, then stacked into two long
            documents a reader has to scroll between to compare anything.
            Neither arrangement lets you put one number beside another.

            A switcher instead. The difference that a reader opened Compare
            for is the table above, which is already a side-by-side view of
            the part that differs; the full reports are the backing detail,
            and one of them at full width is readable at every viewport in
            the sweep.

            Switching runs nothing. Both payloads are in memory, and the
            tab chooses which finished record is shown -- the same
            arrangement `CompareEvidenceDrawer` uses for the traces.

            Rendered whenever there is not one shared answer -- including
            while the two are *not yet comparable*. An earlier version
            gated this on `comparable`, so a run that had finished was
            hidden because the other side was still going or had failed. A
            result on hand is not withheld because its counterpart is
            missing.
          */}
          <section className="compare-reports" aria-label="Each strategy's report">
            <h3 className="section-heading">Each strategy in full</h3>

            <div
              className="compare-tabs"
              role="tablist"
              aria-label="Which strategy's report to show"
            >
              {sides.map((entry, index) => (
                <button
                  key={entry.role}
                  type="button"
                  role="tab"
                  id={`compare-tab-${entry.role}`}
                  aria-selected={index === shown}
                  aria-controls="compare-report-panel"
                  // Only the selected tab is a tab stop; the arrow keys
                  // move between them. That is the tablist pattern, and it
                  // is what stops a two-tab control costing a keyboard user
                  // two presses to get past.
                  tabIndex={index === shown ? 0 : -1}
                  className={`compare-tab${index === shown ? " is-active" : ""}`}
                  data-testid={`compare-tab-${entry.role}`}
                  onClick={() => setShown(index)}
                  onKeyDown={(event) => {
                    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
                    event.preventDefault();
                    const next =
                      event.key === "ArrowRight"
                        ? (shown + 1) % sides.length
                        : (shown - 1 + sides.length) % sides.length;
                    setShown(next);
                    document.getElementById(`compare-tab-${sides[next]!.role}`)?.focus();
                  }}
                >
                  {entry.side.title}
                </button>
              ))}
            </div>

            <article
              className="compare-report"
              id="compare-report-panel"
              role="tabpanel"
              aria-labelledby={`compare-tab-${current.role}`}
              data-testid="compare-report-panel"
              data-strategy={current.role}
            >
              <p className="compare-pane-sub">{current.side.subtitle}</p>
              {/*
                Inside the strategy's own panel, not above both of them.
                A refusal, a quota stop or a failed start belongs to one
                strategy, and a reader has to be able to attribute it
                without counting which card came first. `RunStateCard`
                renders nothing for a verified answer, so a clean side
                carries no card at all.
              */}
              <RunStateCard state={stateOf(current.side)} />
              {current.side.children ?? (
                <p className="compare-pane-empty" aria-live="polite">
                  {stateOf(current.side).reason || stateOf(current.side).label}
                </p>
              )}
            </article>
          </section>
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
        {[
          { role: "deterministic", side: deterministic },
          { role: "ai", side: ai },
        ].map(({ role, side }) =>
          side.run ? (
            <section
              key={role}
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
