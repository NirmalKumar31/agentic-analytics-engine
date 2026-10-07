# 0005: Automatic governed planning

Status: accepted
Date: 2026-10-01

## Context

A visitor had to choose an implementation strategy before asking a
question. "Deterministic Analytics", "AI Analytics" and "Compare Both" are
useful engineering distinctions and bad product defaults: they ask someone
who wants to know their revenue by region to first decide how the question
should be interpreted.

The obstacle to a sensible default was not the UI. It was that the resolver
had one bit to report with. Every unresolvable question came back
`confident=False` with a sentence, so the only policies available were
*never ask a model* and *always ask a model*. The first refuses questions
that a model could read correctly; the second spends a billable request on
every question whose column does not exist.

The resolver already knew the difference. `_pick` has always said either

    the table has no column that looks like a measure

or

    the question does not name which measure to use, and the table has
    3 to choose from

Those are different facts with different remedies, and both were being
flattened into the same non-decision.

## Decision

Add `auto` as a routing policy over the existing planners, and make the
resolver's reasons typed so the policy has something to branch on.

`ResolutionIssue` names twelve specific reasons; `ResolutionState`
classifies them into `exact`, `ambiguous`, `unresolved`, `unsupported` and
`unsafe`. A planning request is issued for exactly one state:

| state | example | planner consulted |
|---|---|---|
| `exact` | every column named | **no** |
| `ambiguous` | "the average by chronotype", several measures | **yes, once** |
| `unresolved` | a column the file does not have | no |
| `unsupported` | no operation this engine implements | no |
| `unsafe` | one column read as both value and grouping | no |

Ambiguity is the weakest claim, so a question that is *also* unresolved,
unsupported or unsafe takes the stronger reading. A missing column refuses
whatever the wording turns out to mean, and spending a request to be told
what the rules already knew is the failure that makes automatic routing
look expensive.

### The provider is built lazily, and that is the whole cost argument

`open_governed_cloud_provider` takes the durable ledger slot at
construction, deliberately, so a run that cannot be priced is refused
before its first billable request. That makes construction, not the
request, the moment quota is spent.

So automatic routing receives a **factory**, not a provider. `RunContext`
carries `open_planner`, and the graph calls it only once ambiguity is
established. An exact question therefore makes no provider call, reads no
credential and consults no ledger. The tests count *constructions* rather
than requests for exactly this reason.

This also preserves a property the deterministic mode already had and must
keep: a deployment with a missing or broken credential still serves
analytics normally. Under `auto`, every question the rules can resolve
still executes; an ambiguous one is refused with a precise reason and the
reformulations that would work. That is a weaker product, not a broken
one, which is why the mode is offered wherever live analysis is.

### What does not change

The model's authority is unchanged, because it was already correct. A plan
still cannot alter an operation or measure the question named, add or shift
a period, reverse a ranking, group by an identifier, or supply SQL,
`mapping_from_plan` enforces every one of those, and `auto` routes through
it rather than around it. Arithmetic, verification, coverage and
provenance are untouched.

`deterministic`, `ai` and `compare` remain selectable and behave exactly as
before. Passing no factory produces the old code path; automatic routing is
additive, and a Compare Both pane or an audit run does not acquire it by
accident.

`provider_kind` reports `governed` for an automatic run rather than
`cloud`. The record is created before the question is resolved, so claiming
a billable path for a run that may make no request would be a false cost
record. The route event says what actually happened.

## Two defects this exposed

Routing made the resolver's conclusions legible, and two were wrong.

**A dropped restriction.** `average total_sleep_hours where mood is good`
resolved as `exact` and was answered for every participant. An equality
clause whose column did not resolve was skipped rather than recorded, so
the restriction vanished, while the sibling branch, for an *ambiguous*
column, refused correctly. The module's own docstring states the policy it
was violating: "a false detection costs a refusal with a reason, a missed
one costs a wrong answer presented as the right one." It now refuses, and
classifies as ambiguous, because a planner reading the sentence may bind
the column.

**A test that could not see it.** Mutation B7, reporting "no candidates"
as "several candidates", survived because the fixture always had
measures and `_pick`'s empty branch was never reached. A text-only upload
now covers it.

## Consequences

- Most questions cost nothing to plan, which is what makes a governed
  default affordable rather than a rebranding of the AI mode.
- A refusal now carries typed reasons and concrete reformulations, so
  "could not be resolved" becomes "name which of these three columns you
  meant".
- The planning audit can state the route, the state, the issues and the
  calls, none of which existed as data before.
- `QuestionMapping.issues` is diagnostics, not semantics: it is excluded
  from `canonical_dict`, so two planners reaching the same contract still
  hash identically however much they struggled to get there.

## Alternatives considered

**Classify question difficulty with a model.** Rejected. It spends a
request to decide whether to spend a request, and it would make routing
unexplainable, which is the one thing a governed engine cannot afford.

**Infer the route from the refusal sentence.** Rejected: string sniffing on
prose that exists to be read by people, which breaks the first time the
wording improves.

**Build the cloud provider eagerly for every automatic run.** Rejected.
Construction is ledger admission, so this charges quota against questions
that never consult a model, and it reads the credential on a path that does
not need it.
