# 0007 — The uploader supplies the missing fact: session-scoped role confirmation

Status: accepted
Date: 2026-10-02

Supersedes [0006](0006-schema-role-override-deferred.md), which deferred
this control and recorded why a frontend-only version would have been
worse than none. That record stands as written; what it deferred is what
this record implements.

## Context

0006 settled the hard part: a numeric column of small integers is
genuinely undecidable from its values. `age` and `Store` are the same
shape — integers in a narrow band, a modest distinct count, no nulls — and
one is a quantity worth averaging while the other is a label whose mean is
meaningless. The owner ruled out every way of guessing: no column-name
special cases, no density heuristic, no list of names that look like
identifiers. A dataset-specific rule is a rule that is wrong on the next
dataset.

That left the only correct answer: the person who uploaded the file says
which it is. 0006 deferred *where that lands*, and gave the reason — a
role the engine does not honour is worse than no role at all, because a
label the planner ignores is a lie told in the interface. So the control
could not ship until the engine would act on it and the audit would
record it.

This record is that change.

## Decision

### Raw inference and effective schema are different things

`infer_schema()` keeps returning what the data alone supports. It is not
given access to anyone's opinion, and `as_dict()` stays pure — a
serializer that reached into session state would make the raw inference
unobservable, and the question "what did the engine think before anyone
intervened" has to stay answerable.

Confirmations are applied by `apply_role_confirmations()`, a pure function
over a copied `InferredSchema`, and the two are combined in exactly one
place: `effective_schema(session, table, *, confirmation_snapshot=None)`.
Every consumer — the API summary, the planner, MCP, the SQL builder —
reads the effective schema. Nothing reads the raw inference and then
patches it locally, because two places that combine the same two inputs
are two places that can disagree.

### The confirmation is session state and nothing else

It lives on the `AnalysisSession` object, which the registry drops. It is
not written to disk, not keyed to a user, not shared between sessions, and
not inherited by the next upload of the same file. A confirmation is one
person's statement about one upload, and it ends with the session.

This is a privacy boundary as much as a scope decision: the engine learns
nothing durable about anyone's data from it.

### Only measure and dimension can be confirmed

`CONFIRMABLE_ROLES` is `("measure", "dimension")` — in the interface,
"quantity" and "category". Those are the two readings the planner can act
on for an ambiguous numeric column, and they are the two the ambiguity is
actually between.

`time`, `identifier` and `ignored` remain unsupported. Offering them would
mean claiming the planner honours a choice it does not, which is precisely
the failure 0006 refused. A column confirmed as a quantity has its
additivity guess cleared to `unknown` rather than assumed: asserting "this
is a quantity" says nothing about whether totalling it means anything, so
the interface offers an average and never a sum.

Confirmation is only offered where the field is ambiguous. A role the data
already settles is not a question, and a control on every numeric column
would train readers to override inference as a matter of course.

### A revision, and stale writes are refused

The session carries `schema_revision`, which increments once per batch
that changes meaning. A batch of five columns is one increment; repeating
a confirmation that is already in force is idempotent and increments
nothing; resetting a column that was never confirmed is a no-op. A
revision that moved on a no-op would invalidate work that was still valid.

`PATCH /api/datasets/{id}/schema/roles` carries `expected_revision`. A
mismatch raises `StaleSchemaRevision` and returns **409** with an
`X-Refusal-Reason` header, applying nothing. Last-write-wins would let a
second tab silently overwrite a choice made in the first, against a column
list it is no longer looking at.

The revision uses its own lock, not the DuckDB connection lock. Overloading
the query lock would make a schema read wait behind a long query and let a
confirmation deadlock against a run holding it.

### A run pins one schema, and an active run blocks the write

`RunContext` pins a `RoleConfirmationSnapshot` — a frozen
`(revision, mapping)` — at admission, and every schema read inside the run
goes through it. A run that saw a confirmation land halfway through would
publish evidence describing a schema that never existed as a whole.

While a run is in flight the confirmation endpoint refuses with 409. The
check is **per session**, via `has_active_run_for_session()`: a global
active-run count would let one person's analysis block an unrelated
dataset's owner from settling their own column.

Compare runs both branches against **one** snapshot, captured once before
either admission. Two captures could straddle a confirmation and produce a
comparison whose two halves disagree about what a column meant — the one
thing a comparison must never do.

### Provenance is required, and the canonical hash excludes it

`InferredField` carries `role_source` (`inferred` | `user_confirmed`) and
the `inferred_role` it would have had. `RunResult` carries the
`schema_revision` it executed at and `role_evidence` for every column the
accepted contract used, each with how it was used.

The planning audit names the confirmation per entry, in the reader's own
vocabulary, and says it is confirmed for this dataset session. It does not
say governed, verified, or correct.

The canonical contract hash **excludes** revision, `role_source` and
reason. Those are provenance, not contract: two runs that compute the same
thing must hash the same, or caching and comparison break on a field that
does not change the arithmetic.

A completed run keeps its own evidence. A later confirmation does not
relabel history; an audit that described the present would not be a record
of the past.

### Reset returns the engine to its own reading

Reset removes the confirmation and the effective role returns to the
inferred one. The column is marked a close call again, because it is one
again. `ambiguous` stays `true` through all of this: confirming does not
make a column decidable, it records that someone decided.

## Consequences

- The uploader can supply the one fact the data does not contain, for
  supported ambiguous numeric fields. `LIMITATIONS.md` §11a changes from
  "visible but unresolvable" to this narrower, truthful statement.
- **A confirmation is user-supplied meaning, not inferred truth.** The
  engine does not validate that a reader is right about their own column
  and cannot. It validates that the choice is one it can act on, acts on
  it, and says whose choice it was. Nothing here makes the engine better at
  guessing; the ambiguity detection is unchanged.
- **Unsupported roles stay unsupported.** Confirming a column as a time
  field or an identifier is still impossible, and still for 0006's reason.
- Values alone still cannot distinguish an ordered quantity from a numeric
  category. That limitation is permanent. What changed is that there is now
  somewhere for the missing fact to come from.
- The browser suite refuses to run against a paid provider. That gate was
  not planned here; it was found while verifying this change, when a
  cloud-mode server owned by another process was discovered listening on
  the suite's default port. See `web/src/test/preflight.ts`, which the browser
  suite's global setup imports.
