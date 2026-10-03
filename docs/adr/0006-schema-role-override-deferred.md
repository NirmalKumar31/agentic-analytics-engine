# 0006 — A schema-role override needs the backend, so the redesign only reports ambiguity

Status: superseded by [0007](0007-session-scoped-role-confirmation.md)
Date: 2026-10-01

> This record deferred the override and gave the condition for
> shipping one: the engine must honour the role and the audit must
> record it. [0007](0007-session-scoped-role-confirmation.md) meets
> that condition and implements it. The reasoning below is kept as
> written rather than revised, because the deferral was correct at
> the time and the condition it set is what the implementation had
> to satisfy.

## Context

`infer_schema()` assigns each uploaded column a role — measure, dimension,
time, identifier — from its type, its null fraction and how many distinct
values it holds. For most columns that is unambiguous. For some it cannot
be decided from the data at all.

The example that keeps recurring is a numeric column of small integers.
`age` and `Store` are indistinguishable: both are integers in a narrow
band with a modest distinct count and no nulls. One is a measure a reader
might reasonably average; the other is a label whose mean is meaningless.
Nothing in the column's values separates them. Averaging a store number
has already happened once.

The inference already admits this rather than hiding it. `InferredField`
carries `ambiguous: bool`, set when a numeric column sits in the band
where a code list and a genuine count are indistinguishable, and
`InferredSchema` carries an `ambiguities` list for the case where two
columns could plausibly be the same concept and the choice changes the
answer. The information exists and is already computed.

The owner has ruled twice on how not to resolve it: not by column-name
special cases, not by a density heuristic, not by a list of names that
look like identifiers. A dataset-specific rule is a rule that is wrong on
the next dataset, and the engine's whole claim is that it does not guess
about numbers.

That leaves one correct answer: the person who uploaded the file says
which it is. The question this record settles is *where that lands*, not
whether it should exist.

## Decision

The information-architecture and visual redesign (PR I) will **surface
ambiguity and will not offer a control to resolve it.** The schema
inspector marks ambiguous and low-confidence columns visibly and distinctly
from confidently-inferred ones, says in words why the column is a close
call, and states plainly that the role cannot be confirmed yet.

A role override is deferred to its own change, spanning backend and
frontend together.

## Why not do it in the redesign

Because the honest version of this feature is not a frontend feature, and
the frontend-only version is a lie.

A label the engine does not honour is worse than no label. If the
inspector lets someone mark `Store` as a dimension and the resolver still
treats it as a measure, the interface has invited a correction and then
ignored it — and the resulting answer carries a user-confirmed role in the
UI and an inferred role in the computation. That is a provenance defect,
not a cosmetic one.

Doing it properly requires, at minimum:

- **Server-side validation.** The override arrives from a client and
  decides how a column is aggregated. It cannot be trusted on arrival: a
  role that permits `SUM` on a text column, or that contradicts the
  column's type, has to be rejected by the backend rather than by the form
  that produced it.
- **Query-contract invalidation.** A contract compiled under the inferred
  role is stale the moment the role changes. Reusing a cached result after
  an override would answer the old question with the new label on it.
- **Provenance and Planning Audit.** `user-confirmed` has to be
  distinguishable from `inferred` everywhere a role is reported, including
  in the audit trail, because the two have different warrants.
- **Session scope.** The override belongs to one upload session and must
  not leak into another, which touches the bounded session pool.
- **An end-to-end test through real `infer_schema()`.** Hand-writing a
  schema in the test would prove only that the UI can render a role it was
  handed, which is not the thing at risk.

None of that is redesign work, and bundling it into a change whose purpose
is information architecture would make both halves harder to review. PR I
is already restructuring every section of the interface.

## Consequences

- The ambiguity remains **visible but unresolvable in-product** until the
  deferred change lands. A reader is told the column is a close call and is
  not given a way to settle it. That is a real limitation and is recorded
  in `LIMITATIONS.md` §11a rather than left to be discovered.
- PR I must not compensate with a frontend-only label, a column-name
  special case, or a default that quietly picks one reading. Marking a
  column ambiguous is the whole of the behaviour.
- The deferred change has a clear boundary and can be scheduled on its
  own: it is a backend contract change with a small amount of UI attached,
  not a redesign increment.
- Nothing in this decision blocks the redesign. The data it needs —
  `ambiguous` per field, `ambiguities` per schema — is already computed and
  already crosses the API.
