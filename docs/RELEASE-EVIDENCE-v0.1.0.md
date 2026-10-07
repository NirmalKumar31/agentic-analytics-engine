# Release evidence: v0.1.0

What was measured against the deployed service, by whom, and what it does
not cover. Figures are transcribed from captures held under gitignored
`var/hosted-acceptance/`; their checksums are below so the summary can be
checked against the originals rather than believed.

Accepted SHA: `3384c002fc4faab184ff5da1ed6e0988af9f1a62`
Service: <https://agentic-analytics-engine.onrender.com>
Captured: 2026-09-29

---

## 1. Credential-free hosted acceptance

`scripts/live_acceptance.py` against production: **55 checks, all passed.**

> The earlier handoff recorded 58. Re-running the script reports
> `PASS: all 55 checks`, and 55 is used here because it is the number this
> release actually measured. Every area the handoff listed is covered; the
> count was wrong, not the coverage.

Covered: health and readiness; the application shell; all three committed
recordings; a built-in warehouse session and a deterministic analysis over
it; supported findings with resolved citations; read-only SQL; a Secure,
HttpOnly capability cookie absent from the run payload; CSV upload and
profiling; a deterministic upload analysis; safe refusal of an unmappable
question; session deletion and run cleanup; API documentation and schema
paths returning JSON 404; the MCP endpoint withdrawn with 503; and the
security-header matrix (CSP, HSTS, nosniff, referrer policy, frame denial,
permissions policy, opener isolation).

Reproduce:

```
python scripts/live_acceptance.py https://agentic-analytics-engine.onrender.com
```

It needs no credential, because the deployment needs none for this path.

## 2. Paid hosted acceptance: one Compare Both run

Exactly one request. No retry was attempted or permitted.

A 20-row synthetic invoice table was uploaded to a fresh session. Its total
was computed locally, before the run, as **2,960.00**, so the engine's
answer could be checked against arithmetic rather than against itself.

Question: *"What is the total invoice_amount?"*

| | Deterministic | AI |
|---|---|---|
| Status | `completed` | `completed` |
| Provider | `scripted` | `cloud`, `gpt-6-luna` |
| Published | 1 finding, **2,960** | 1 finding, **2,960.0** |
| Input / output tokens | 6,728 / 693 | 5,895 / 1,087 |
| Provider attempts | 11 (scripted, no network) | 8 |
| Ledger cost | **0 µ$** | **1,160 µ$ = $0.001160** |

Both panes read the same dataset fingerprint
(`sha256:2e43ddecc79e48b4…`), and their result stores were disjoint. The
deterministic half made no remote model call and consumed no AI allowance.
The session was deleted afterwards; re-fetching it returned 404.

**17 of 17 mechanically checkable criteria passed:** both panes completed;
one dataset fingerprint; independent result stores; deterministic used no
remote model and 0 µ$; AI used the governed provider path; exactly one
published finding per pane; both equal to the independently computed
total; zero unsupported publications; zero irrelevant publications; every
cited evidence cell resolved to its exact result; finding identifiers
unique within each run.

A leak scan over the whole capture, both event streams and the full JSON,
found no API key, `Authorization` value, Redis URL, internal hostname,
capability cookie or private filesystem path, and **no uploaded row
reference anywhere in the output**.

### What the cost figure does and does not establish

The $0.001160 ledger amount is consistent with the provider token
categories and conservative per-call rounding. The reduced public usage
payload did not expose the detailed category breakdown, so an exact
external category-by-category reconciliation was not possible.

The public run payload exposes input tokens, output tokens, provider
attempts and an estimated cost in microdollars. Cached, cache-write and
reasoning categories, and the reservation, settlement and retention
counts, are captured internally but are not part of that payload. No new
public accounting fields were added for this release: doing so would
change an already accepted deployment and could not retroactively recover
the deleted run's omitted details.

## 3. Cost controls in force

| Control | Value |
|---|---|
| Per AI run | $0.25 |
| Per day | $0.50 |
| Lifetime (application) | $4.00 |
| Provider project hard limit | $5.00 |
| Model calls per run | 64 |
| AI runtime per run | 180 s |
| Concurrent AI runs | 1 |
| Runs per session / per IP-hour | 3 / 5 |

Admission is taken in a durable Redis ledger **before** any provider
dispatch, and there is no in-memory fallback: if the ledger is unreachable
AI is refused and Deterministic Analytics continues unaffected.

These are **admission controls, not provider billing guarantees.** A
request that leaves the process may be billed whether or not a usable
answer returns, which is why an unreadable usage report retains its whole
reservation rather than settling to zero. The provider's own $5 project
limit is the external backstop. The free Key Value plan can lose its
counters if the datastore itself restarts, which would reset the
application's view of lifetime spend while the provider limit stays in
force.

## 4. Upload coverage

The committed corpus is **230 generated cases across 40 domains, 24
structural families and 10 question kinds**, in CSV and Parquet, run in
both deterministic and AI-stub modes with identical outcomes case by case.
Within that corpus: zero unexpected failures, zero unsupported
publications, zero irrelevant publications, zero silent measure, dimension,
unit or time-field guesses, zero cross-run leaks and zero raw-cell
disclosures.

This is broad tested coverage, **not a guarantee for every possible
dataset.** The corpus is 230 questions its authors chose against data its
authors generated. A question that is ambiguous, unsupported by the data,
or insufficiently evidenced is expected to be refused or reported as a
limitation rather than answered. That behaviour, not universal
correctness, is what the corpus demonstrates.

## 5. What was not exercised

- **Hosted AI-off rollback was not manually exercised.** The documented
  procedure, which is to set `AAE_AI_ANALYTICS_ENABLED=false` and redeploy, is
  covered by automated isolation tests, not by a rehearsal against this
  deployment.
- **Hosted Redis-counter persistence across a web-service restart was not
  directly observed.** The ledger is durable by construction and the
  behaviour is tested locally, but no production restart was performed to
  watch a counter survive one.
- **Browsers:** Chromium only. Firefox and WebKit are **not** tested
  against this deployment and no claim is made about them.
- **A second paid request was not made.** The single Compare Both run
  above is the whole of the hosted paid evidence.

## 6. Checksums

Raw captures are deliberately not committed: they contain full event
streams and result payloads. They are held under gitignored
`var/hosted-acceptance/`.

| File | Bytes | SHA-256 |
|---|---|---|
| `compare_run.json` | 86,936 | `71158850bc4f78f637d18e1e6bc2718c8c058f01bcdf88a2bbc00b03a06912e3` |
| `events_ai.txt` | 5,922 | `a590d0526a064d955c8ce21d211161742de58022761aa9a13b8ab1fe379f16a7` |
| `events_deterministic.txt` | 7,025 | `d7e671e1013065d47ac71a0c2073ddc3a98f0e901c57a1017c54ae859077f8de` |

Verify:

```
shasum -a 256 var/hosted-acceptance/*
```

## 7. Rollback

Documented emergency procedure, not exercised during this release:

1. Set `AAE_AI_ANALYTICS_ENABLED=false`.
2. Redeploy.
3. Verify Deterministic Analytics and the recordings remain available.

Deterministic Analytics never reads the credential or the ledger, so it is
unaffected. The ledger holds counters only; no data migration is involved.
