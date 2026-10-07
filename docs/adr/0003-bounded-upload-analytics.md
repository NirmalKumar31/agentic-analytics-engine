# 0003: A bounded analytics engine, not a general question answerer

Status: accepted
Date: 2026-09-30

## Context

Production testing on real uploads found the engine answering questions it
had not been asked. The failures were not arithmetic, since DuckDB computed
correctly every time. They were in deciding *what* to compute and in
describing what had been computed.

Four, from the evidence:

* `total website_visits by age` answered `SUM(age) = 48,195` and presented
  it as an answer about website visits. Inference called `website_visits`
  an identifier because it is ~98% distinct, called `age` a measure, and
  the resolver let the requested grouping into the measure pool; the
  resulting collision was "repaired" by deleting the grouping.
* `total weekly_sales by store over time` returned monthly totals with no
  store. `by store for each month` returned store totals with no month.
  The contract had one singular `dimension` and could not represent the
  question either way.
* `total Weekly_Sales by Store` on 45 stores returned the top 25 and the
  report called it the complete breakdown of every row.
* A suggested question asked which team size "contributes most to age",
  which is arithmetically reproducible and analytically meaningless.

Both modes reached the same wrong contract in the first three, so
Compare Both reported "same governed interpretation", agreement about the
wrong thing, displayed as corroboration.

## Decision

The product is **governed analytics for structured business tables**, and
the promise is bounded accordingly:

> Within the documented question grammar and resource limits, the engine
> either returns a complete, numerically verified, relevant result from the
> requested contract, or refuses with a specific actionable reason. It
> never silently answers a different question.

Four structural consequences.

**One authoritative contract.** `dimensions` is an ordered list of at most
two, and every decision, hash, coverage check and SQL clause reads it. The
singular `dimension` survives one release as a projection that is
populated only when there is exactly one grouping, and null for zero or
two, so a two-cut question read through the old field looks like a
question with no grouping rather than like a one-cut question.

**Explicit roles outrank inferred ones.** A column named after `by` is a
grouping; a numeric column named in measure position may be aggregated
even where inference called it an identifier. A resolved grouping is
excluded from every measure pool. A role conflict refuses; it is never
repaired by dropping a component the question stated.

**Coverage is measured against the question, not the contract.** The first
attempt derived its requirements from the accepted mapping, which made it
a rename of `confident`: a dropped grouping was never "required", so
nothing was ever missing. Requirements now come from the question text.
Planner agreement, question coverage and output equality are three
separate statements, and Compare Both reports them separately.

**The model plans; it does not compute.** One typed planning call. No model
chooses a tool, restates arithmetic, picks a chart or writes a summary
after a governed aggregate exists. That path cost seven model calls per
question and produced a redundant rejected draft each time. Deterministic
mode makes none.

## What is refused rather than approximated

* A complete breakdown larger than the result ceiling. It refuses with the
  group count, the ceiling and concrete ways to narrow, because the groups
  it would omit may hold most of the population.
* Causal conclusions, forecasts, arbitrary joins, and significance claims
  without an implemented test.
* A question whose measure, grouping, filter or period cannot be mapped
  without guessing.
* Grouping by an identifier or near-unique text, whose values would travel
  into a remote prompt as group labels.

## Consequences

**A reasonable question can be unanswerable at current limits.** Store x
month on 45 stores and 33 months is 1,485 groups against a ceiling of 500,
so it refuses. That is the right answer under this decision and a real
limitation of the deployment, not of the question. Raising
`max_result_rows` is a deployment decision with a memory cost.

**Suggestions are narrower than the engine.** Automatic questions are
offered only for strongly additive measures. Summing an age or a rating is
permitted when a visitor asks for it explicitly and never proposed.

**None of this is evidence about a real model.** The corpus drives both
modes with a scripted provider. What is claimed is that the bounds hold
against the plans the tests construct, and that a cloud plan which cannot
be validated either falls back to the engine's own contract, recorded as
a fallback and never as independent agreement, or refuses.
