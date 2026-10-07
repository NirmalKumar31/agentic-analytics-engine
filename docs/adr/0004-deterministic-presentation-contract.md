# 0004: A deterministic presentation contract

Status: accepted
Date: 2026-10-01

## Context

The frontend built reports by arranging `finding.text`. That made the
browser responsible for recovering analytical structure from prose it could
only guess at, and every guess it had to make was wrong somewhere:

- **Which number is the answer.** A grouped result arrived as one sentence
  enumerating its rows, so the report showed
  `Total Weekly Sales by Store: 1: 222,402,808.85; 2: …; and further groups
  in the cited result.` The reader got the table twice, once badly.
- **What a value means.** `blue_light_filter_active` holds `0` and `1`, and
  the report printed `0` and `1`. The browser had no way to know the column
  was a flag, or what its states were called.
- **Whether an axis is ordered.** 48 ages were plotted as unordered labels,
  because nothing told the chart that ages form a sequence.
- **Whether a breakdown is complete.** Completeness was being inferred, and
  every available signal inferred it wrongly: a top-25 of 45 stores,
  covering 3,575 of 6,435 rows, read as the complete breakdown.
- **What a unit is.** Nothing distinguished a percentage from a ratio, or
  said that "revenue" implies no currency.

Each of these was fixed once, locally, in whichever component noticed. The
defect was structural: the engine knew all five answers and did not say
them.

## Decision

The engine returns a typed `AnalysisPresentation` alongside the existing
payload. It is derived only from things the run already proved: the
accepted `QueryContract`, the verified `ResultSnapshot`, `QuestionCoverage`,
`GroupCoverage`, the published findings, the chart decision and the
provenance cells. It is built by a pure function with no model call.

Four properties make it a contract rather than a convenience.

**It carries no figure it cannot point at.** Every highlight cites at least
one `EvidenceCell`; the schema rejects one that does not. Prose is checked
rather than trusted: `numbers_resolve` requires each number in the headline
and secondary summary to be a cell of the cited result, a recorded coverage
count, or a declared difference recomputed from two cells. Nothing else can
be stated. This matters because the failure mode was never invented numbers
It was *real* cells in a false sentence, such as calling the tenth row of
a top-ten list "the lowest".

**It never claims more than the analysis supports.** "Highest" is said only
when coverage records that every group is present. A two-group difference is
"descriptive", because no test was run. An ordered dimension gets a numeric
axis and an explicit statement that no trend was tested.

**Its display metadata requires converging evidence.** A column becomes a
boolean only when every value present is `0` or `1`, or the type is
declared boolean, not merely when it has two values, which would relabel a
two-store table "Off" and "On". A percentage needs both a name token and a
compatible observed range, so a `rate` holding `0.03` is not formatted as
`3%`. Currency is never inferred from a name.

**It is additive.** `report`, `findings`, `rejected`, `charts`, `results`
and `query_contract` are unchanged. A consumer that predates this field
reads exactly what it read before, and a failure to build a presentation
degrades the report to the older rendering path rather than failing a run
whose numbers are already computed and verified.

## What this does not decide

The contract says what a result *means* for presentation. It carries no
colours, no layout and no component names, and it does not choose the
chart, because `analytics.charts.chart_for` already does that, and a second
selector would let the report and the chart disagree about what was drawn.

## A limit worth recording

The profiler cannot distinguish an ordered quantity from a numeric entity
key. `age` (48 values, 18–65) and `Store` (45 values, 1–45) receive
identical classifications from `infer_schema`, down to the same reason
string, because they are structurally identical: dense integer ranges whose
values each recur. Any rule separating them would be a guess about naming
presented as inference.

So the prose is uniform, and both get the same "highest … lowest" sentence,
and the only consequence of the ordered flag is a numeric chart axis, which
is a true statement about position and not about meaning. If the two should
read differently, that requires a signal the engine does not currently
have, and it is a product decision rather than an inference.

## Consequences

- One source of truth for how a figure reads. `format_number` is shared by
  the answer prose, the table and the chart axis, which is why a revenue
  total no longer appears as `12,296,516.7` in a sentence and
  `12,296,516.70` in the table beside it.
- Prose failures are rejected at the boundary. A headline containing more
  than one semicolon, or trailing off in "and further groups", fails schema
  validation rather than reaching a reviewer.
- Archived recordings render without being rewritten. The compatibility
  adapter repeats what a run published verbatim, sets
  `compatibility_derived`, and leaves unrecorded coverage as `None` rather
  than guessing, because "not recorded" and "complete" were conflated
  once already.
- The frontend still renders from `finding.text` until a later change
  consumes this field. Shipping the contract first means the renderer can
  be built against a stable, tested schema.

## Alternatives considered

**Keep fixing the frontend.** Rejected: five separate guesses in the
browser cannot be made correct, because the information needed to decide
them is on the server.

**Have a model write the report prose.** Rejected outright. The figures
would stop being verifiable, and the engine's whole claim is that every
published number is traceable to a cell.

**Put the chart's rows in the presentation.** Rejected: the rows already
exist once, under `results`, where the table and verification read them. A
second copy could disagree with the table beside it, with no way to tell
which was the verified figure.
