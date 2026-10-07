# Evaluation

The demo warehouse contains six deliberately injected phenomena. The agents
never see them. After a run finishes, the harness scores what it published
against what the generator actually did.

```bash
make data
make evaluate     # writes var/evaluation/report.json
```

---

## What this benchmark is, and is not

**It is a deterministic end-to-end engine benchmark.** It runs with the
scripted provider, so the agent decisions are rules rather than a model.

It measures:

- graph execution and parallel dispatch
- MCP tool execution over a real client/server pair
- SQL and statistical correctness
- provenance completeness
- deterministic verification
- publication behaviour, specifically whether anything unsupported escapes

It does **not** measure:

- language-model question understanding
- planning quality
- tool-selection reliability

Those depend entirely on the provider and are not exercised here. The run
artefact says so itself: every report carries `benchmark_kind`,
`provider_mode`, `measures` and `does_not_measure`, so the numbers cannot be
quoted out of context without the context travelling with them.

The honest one-line reading: **this measures the engine, and the engine is
the part that decides whether a wrong number can reach a report.**

---

## Why ground truth is exact

Because the dataset is generated rather than found, the answer is known
precisely. `data/ground_truth.py` holds it. Two tests keep it away from the
agents:

1. **Import graph.** No agent-facing package may import the module.
2. **Prompt capture.** A full benchmark runs behind a recording provider that
   keeps every request. The test then asserts that no pattern id, no
   answer-key sentence, and no expected entity appears in any system prompt,
   user prompt, context object or schema, and separately that no expected
   entity is written into a prompt template.

An entity such as `Home & Kitchen` legitimately appears in a query *result*,
because a worker has to read it to state a finding. What must never exist is
an instruction naming it. Without that distinction the audit would either
miss real leaks or reject correct behaviour.

---

## The injected patterns

| id | What the generator does |
|---|---|
| `q3_margin_compression` | Raises discount rates and shifts unit volume toward low-margin Electronics during 2025-07-01..2025-09-30. Revenue rises on volume; gross margin falls. |
| `home_kitchen_returns` | Home & Kitchen carries a return rate several times the average, amplified for `new` customers, dominated by `damaged_in_transit`. |
| `northeast_carrier_delay` | From 2025-05-01, `RapidPost` shipments to `Northeast` have their delivery-time distribution shifted later. |
| `late_delivery_repeat_association` | A late first delivery multiplies the repeat-purchase draw by 0.42. Region also predicts lateness, so region confounds the comparison and a two-proportion test cannot separate them. |
| `affiliate_weak_contribution` | `affiliate` gets larger baskets (strong revenue) plus the highest discount rate, highest spend per acquisition and above-average returns (worst contribution). |
| `q4_seasonality` | November and December carry ~1.6x baseline order volume, with a smaller July bump. |

Each is asserted directly against the generated data in
`tests/unit/test_data_generator.py`, independently of any agent. If a pattern
stopped being present those tests fail first, which distinguishes "the agents
got worse" from "the data changed".

**On causal wording.** The generator causes these patterns, because it writes the
rows. The analytics system only sees the finished table, where nothing
identifies a causal effect. So the expectations are phrased as associations,
and a run that concludes causation has its claim withheld. That withholding
is itself a scored expectation for Q6.

---

## The benchmark cases

| | Question | Pattern |
|---|---|---|
| Q1 | Did gross margin decline in Q3 2025, and did revenue rise? | `q3_margin_compression` |
| Q2 | Revenue increased in Q3 2025, but gross margin fell. What caused it? | `q3_margin_compression` |
| Q3 | Which product category has the highest return rate? | `home_kitchen_returns` |
| Q4 | Which customer segments are driving the increase in return rate? | `home_kitchen_returns` |
| Q5 | Which acquisition channel has the weakest contribution margin? | `affiliate_weak_contribution` |
| Q6 | Do shipping delays appear to affect repeat purchasing? | `late_delivery_repeat_association` |
| Q7 | Which shipping carrier has the highest late delivery rate? | `northeast_carrier_delay` |
| Q8 | How did monthly revenue change over 2025? | `q4_seasonality` |

Q1 and Q2 share a pattern deliberately: Q1 asks whether both directions are
recovered, Q2 whether the *cause* is attributed. Q8 is a plain trend question,
present so the suite covers the easy case too.

---

## Metric definitions, with denominators

Every rate below is pooled, total over total, rather than a mean of per-case rates.
Averaging ratios would weight a case with two findings the same as one with
ten.

| Metric | Numerator / denominator |
|---|---|
| **candidate support rate** | findings that survived verification / findings a worker proposed |
| **publication-gate integrity** | published findings with a `supported` verdict / published findings |
| **published-finding numeric verification rate** | published findings whose every number re-verifies / published findings |
| **SQL validity** | statements SQLGuard accepts / statements executed |
| **tool-call validity** | MCP calls returning a result / MCP calls made |
| **provenance completeness** | published findings whose every evidence cell resolves to a real row, column and matching value / published findings |
| **chart field validity** | encoding fields that are columns of their result / encoding fields |
| **resolved without generated SQL** | calls to a governed tool / all tool calls |

**Publication-gate integrity is not an accuracy score**, and it was called
"published support rate" until that wording invited the reading. It asks one
narrow question: did the publication gate emit any finding that its own
verification pipeline had rejected? It is 1.0 by construction unless the gate
leaks, which is worth watching and is *not* independent evidence that the
findings are semantically right. The injected-pattern checks are what provide
that, because the generator knows the answer and the agents cannot see it.

**The numeric rate counts findings, not numeric literals.** `verify_numbers`
checks every figure in a finding and returns one verdict, so 30/30 means 30
of 30 published findings had all their numbers re-verify, not that 30
individual numbers were checked. The name says so now.

Candidate support is *expected* below 1.0, because a run that withholds nothing is
not verifying anything. Published support must be exactly 1.0, because
publishing an unsupported finding is the failure this system exists to
prevent. Reporting one number for both, as an earlier version did, described
neither.

---

## Results

Measured on the full warehouse (seed `20260924`, fingerprint
`sha256:8e9ad9348f7dc18660d78ed3cd4d4b32`) with the scripted provider:

```
cases passed                      8 / 8
injected patterns recovered       6 / 6
patterns missed                   []

candidate findings                38
  supported                       30
  withheld                         8
candidate support rate            30/38 = 0.789

published findings                30
  unsupported published            0
publication-gate integrity       30/30 = 1.000

findings numerically verified    30/30  = 1.000
SQL statements read-only         32/32  = 1.000
tool calls succeeded             34/34  = 1.000
provenance complete              30/30  = 1.000
chart fields valid             192/192  = 1.000

deterministic tool calls          34
generated SQL calls                0
resolved without generated SQL   34/34  = 1.000
statistical tool calls             4
decomposition tool calls           2

provider calls                   187
engine runtime per question    0.109 s   (excludes model inference)
```

The runtime figure is hardware-dependent. The block above is a local run; the
same command on a GitHub-hosted runner reports about 0.15 s. Every other
number in the block is identical on both, because the scripted provider makes
the run deterministic.

`tests/evaluation/test_benchmark.py` runs the same harness on a reduced
warehouse in CI and fails the build if any pattern is missed, if numeric
accuracy is below 1.0, if any unsupported finding is published, if the two
support rates collapse to the same number, or if **nothing is ever
withheld**.

---

## Tool dependence

`resolved_without_generated_sql` exists to answer one question: is this an
analytics engine or a text-to-SQL wrapper?

In the benchmark every call went to a governed tool and none to
`run_readonly_sql`. That is not a target, since whatever the implementation
does gets reported, but the test asserts it stays above 0.5, so a regression
toward generated SQL becomes visible rather than silent.

---

## Running against a real model

```bash
AAE_PROVIDER_MODE=local AAE_OLLAMA_MODEL=qwen2.5:7b-instruct make evaluate
```

Needs a running Ollama server and no credential. Expect lower task completion
and candidate support, and much longer runtimes. The interesting numbers there
are the verification ones: they measure how often the engine catches a real
model overstating its results.

### Resuming, and the one rule that matters

The real-model harness checkpoints after every question, so an interrupted
sweep continues rather than restarting. A checkpoint is only reused when the
model, the provider, the harness schema and the **git SHA** all match what
produced it.

`--resume-incompatible` overrides that check. It exists because interrupted
*debugging* runs are real and re-running twenty minutes of questions to reach
the one that crashed is wasted time. It must never produce a number anyone
reports.

The reason is not bookkeeping fussiness. The engine is the thing under
evaluation. Resuming across SHAs puts questions answered by two different
builds into one report and silently attributes all of them to the second, so
a fix that improved question 30 appears to have improved questions 1 through
29 as well. A sweep whose outcomes came from more than one build measures
nothing, and nothing in the report would show it.

A publishable evaluation therefore starts from zero in a fresh checkpoint
directory on a single SHA. `tests/evaluation/test_real_model_harness.py`
pins the defaults that enforce this.

## Probing one agent role

The full sweep tells you a run published nothing. It does not tell you which
layer failed, and the layers fail for opposite reasons: a model that cannot
meet the evidence contract needs a different schema, a model obstructed by
the request needs a different prompt, and tool results that deserved no
conclusion need neither.

```bash
AAE_PROVIDER_MODE=local AAE_OLLAMA_MODEL=qwen2.5:7b-instruct \
  aae probe-worker-findings --out probe.json
```

The probe hands the `worker_findings` role five fixed results built in
`evaluation/worker_probe.py`: a simple aggregate, a grouped ranking, a
trend, a real statistical test, and one thin result that supports no
conclusion at all. Because the results are known-good, anything that goes
wrong afterwards belongs to the role. For every claim it records reference
integrity (does the result exist, the row exist, the column exist, and does
a copied value match), then runs the three real gates unmodified.

`--schema-variant` and `--prompt-variant` vary one thing at a time, so
"the model cannot do this" and "we asked badly" can be told apart rather
than guessed between. It is a diagnostic: no pass mark, no score, and never
part of CI.

## The 34-question local sweep is optional, not a gate

`aae evaluate-real-model` without `--selection` runs all 34 questions against
whichever local model is configured. It is an **extended behavioural
evaluation, not a release gate**, and it has not been run to completion.
Nothing in this repository reports a result from it, and no claim here rests
on one.

The reason is scope. A 34-question sweep of `qwen2.5:7b-instruct` mostly
measures that model's limits, and that model is not the intended production
reasoner. The seven-question Stage 1 already demonstrates what the
architecture needed to show: real planning, real structured output, real
tool selection, real DuckDB execution, real findings, real verification,
real publication, and real failure modes including contention and budget
exhaustion.

Run it overnight if you want the longer record. Do not treat its absence as
a missing result.

## Local evaluation budgets are not product budgets

The evaluation overrides two ceilings, and neither changes what a deployment
does:

    max_llm_calls        64    (product default stays 40)
    max_runtime_seconds  1800  (product default stays 300)

Measured, on the demo warehouse question. At 40 calls the run produced seven
candidate findings and published **none**: all twelve critic calls were
refused before dispatch, so verification was entirely starved and no report
was written. At 64 the same question produced sixteen candidates, published
eight, ran every critic call and wrote a report. The extra calls were spent
on verification and reporting, not on more tool flailing.

That is evaluation sizing for a slow local model. Product defaults are sized
for a hosted model and a public service, and are unchanged.

## Relevance is checked separately from arithmetic

A finding passes four gates, not three. Claim shape, deterministic arithmetic
and evidence support all ask whether a claim is *right*. None asks whether it
answers the question.

That gap was visible in the committed demonstrations: a revenue ranking by
acquisition channel under "why did gross margin fall", a month-over-month
refund total under "which customer segments drive returns". Every number
verified. Neither answered what was asked.

The critic now returns two judgements. `status` is whether the cited results
support the wording. `answers_question` is whether the finding addresses the
question or a sub-question the task objective names. They are recorded
separately because they fail for different reasons: a claim can be perfectly
evidenced and still be off-topic, and a reader needs to tell those apart. An
accurate, off-topic claim is withheld under `irrelevant_to_question` rather
than called unsupported.

The gate fails closed. A critic that omits `answers_question` has not said
the finding is relevant, and an unavailable critic withholds.

## What publication-gate integrity does and does not mean

`publication_gate_integrity` is an internal consistency metric: the fraction
of published findings that carry a supporting verdict from the pipeline. A
value of 1.000 means the gate emitted nothing it had not verified.

It is not independent evidence that a published finding is true. Arithmetic
verification proves a number appears in a cited result. It does not prove the
sentence around that number is a correct reading of the data, that the units
are named properly, or that a comparison the wording implies was actually
made. The manual review in
`examples/evaluations/local-qwen2.5-7b-instruct-stage1/manual-review.json`
records seven findings that are numerically supported and semantically loose
for exactly these reasons.

Published findings are described as *numerically verified against cited
results*. Never as true.
