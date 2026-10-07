# 0002: The cloud planner interprets language; the engine keeps authority

Status: accepted
Date: 2026-09-30

## Context

AI Analytics asks a cloud model to map a question onto an uploaded table's
inferred schema. It returns a typed `UploadQueryPlan` under strict Structured
Outputs, and `mapping_from_plan` validates it before any SQL is composed.

The validation started from the obvious threats: a fabricated column, an
unsupported operation, an invented source excerpt, a dropped row restriction.
Those are the failures a malformed or hallucinating plan produces, and they
were caught.

Probing the validator with plans a *capable* model plausibly returns found
four that were executed. None of them were malformed. Each validated against
the response schema, named only real columns, and grounded every excerpt in
the question:

1. **Group by `customer_name`.** Asked "average revenue by customer name",
   the plan grouped by an identifier. The validator checked the column
   existed; it did not check the engine would offer it as a grouping. The
   deterministic planner refuses this outright, because an identifier's values
   become group labels in the result and travel from there into the next
   prompt, which is the leak the schema classifier exists to prevent. The
   privacy boundary was holding in one mode.
2. **Add a period.** Asked "what is the average annual revenue", the plan
   restricted to 2024 and the engine executed it. Dropping a stated period is
   caught downstream as a missing restriction; adding one was caught nowhere,
   and the published answer was to a narrower question than the one asked.
3. **Shift a stated period.** The same gap, other direction.
4. **Reverse a ranking.** Asked which region was highest, the plan returned
   `ascending: true`. The operation, measure, grouping, filters and period all
   matched, so every downstream gate passed and the bottom of the table was
   published as the top.

The common shape: the plan did not *break* a rule the engine could see, it
exceeded what the question supported in a way no single field revealed.

## Decision

The plan may decide only what the engine cannot decide for itself, and the
engine re-derives everything it can.

* A grouping must be in `_groupable(schema)`, the same list the rule planner
  uses. One function, read by both planners.
* A period must equal the period the rules parse from the question. Date
  arithmetic is rule-owned in both modes.
* A ranking's direction must match the rules where the rules are confident.
* An explicitly named operation, measure or grouping is not reinterpretable.

A plan breaching any of these is **refused, not corrected**.

## Amended 2026-09-30: a usable contract falls back rather than refusing

The decision below was made before the behaviour was observed on real data,
and it was wrong for one case. Asked `total Weekly_Sales by Store`, a cloud
planner returned `profile` with no measure and no grouping. Refusing there
fails a routine business question because a model chose a different
operation, and the visitor sees an empty pane beside a deterministic answer
that worked.

So: **where the deterministic resolution is confident, its contract
executes.** The reasoning below still holds for everything else, and the
distinction is what the fallback protects rather than whether it exists:

* the arithmetic was never the model's to own, so substituting the rules'
  contract takes nothing from it;
* `interpretation` still says the rules decided and `planner_note` records
  why, so the mode's provenance stays true;
* the canonical contract is byte-identical to the deterministic one, so
  Compare Both reports the same governed interpretation, which it is,
  rather than a false claim of independent agreement;
* where the rules are *not* confident, a refusal is still the answer,
  because there is no contract to fall back to and the model's stated
  reason is the only information available.

The original reasoning follows, because the argument against a *silent*
substitution is the reason `planner_note` exists.

## Why refuse rather than fall back to the rule interpretation

Silently substituting the rule contract would produce an answer, and a
correct one. It would also mean AI Analytics sometimes reports a result its
planner did not choose, with no way for the visitor to know. The visitor
selected AI planning; a refusal tells them the two disagreed, which is
information. A substitution would make the mode's provenance label false.

This also keeps Compare Both honest. Its value is that the two panes either
executed the same canonical contract or did not, and the page says which. A
mode that quietly converges on the rule answer when it disagrees would make
every comparison read as agreement.

## Consequences

**AI mode can refuse where deterministic mode answers.** This is intended,
and the refusal names the disagreement, but it caps how much language coverage
the cloud planner can add. A period the rules cannot parse is now a refusal in
both modes rather than an answer in one, even where the model read it
correctly. Widening this means teaching the rule parser, not loosening the
check.

**The bound is on what the engine can re-derive, which is not everything.** A
plan remains free to choose an operation, measure or grouping where the rules
abstain, as long as every excerpt is grounded and every identifier is real.
That is the coverage AI mode exists for, and it is not verified against a
second opinion, only against the schema and the question.

**No claim is made about real-model behaviour.** The corpus drives both modes
with the scripted provider, so it proves the bounds hold against the plans the
tests construct, not that a cloud model stays inside them. The adversarial
plans in `tests/unit/test_typed_ai_plan_authority.py` were written by hand for
this reason.

## Related

A fifth defect surfaced from the same probe and was not a plan problem at all:
`time_field` (a trend's axis) and `period_field` (the column a period filters)
were folded into one field during contract revalidation. Every non-trend
mapping came back carrying a trend axis it never had, which changed its
canonical hash without changing its SQL, so the MCP boundary refused the
engine's own contract, and every question naming a period published nothing.
It was also the sole cause of the five cross-mode corpus divergences, now
zero. See ARCHITECTURE §14.
