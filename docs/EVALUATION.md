# Evaluation

The demo warehouse contains six deliberately injected phenomena. The agents
never see them. After a run finishes, the harness scores what it published
against what the generator actually did.

Reproduce with:

```bash
make data
make evaluate     # writes var/evaluation/report.json
```

---

## Why this is worth measuring

An analytics agent can look impressive and be wrong. The two failure modes
that matter are publishing a number that nothing supports, and missing the
thing the question was about. Both are invisible without ground truth.

Because the dataset is generated rather than found, the ground truth is exact.
`data/ground_truth.py` is the answer key; `tests/unit/test_ground_truth_isolation.py`
walks the import graph of every agent-facing package and fails if that module
is reachable from any of them, and separately asserts no injected entity name
appears in a prompt template. Without that test the whole exercise would be
circular.

---

## The injected patterns

| id | What the generator does |
|---|---|
| `q3_margin_compression` | Raises order discount rates and shifts unit volume toward low-margin Electronics during 2025-07-01..2025-09-30. Revenue rises because volume grows; gross margin falls because both discount and mix move against it. |
| `home_kitchen_returns` | Home & Kitchen carries a return rate several times the warehouse average, amplified for customers in the `new` segment, dominated by `damaged_in_transit`. |
| `northeast_carrier_delay` | From 2025-05-01 the carrier `RapidPost` shipping to `Northeast` has its delivery-time distribution shifted later. |
| `delay_suppresses_repeat` | A late first delivery multiplies the repeat-purchase draw by 0.42. The association is real in the data — and region confounds it, so a two-proportion test alone does not support a causal claim. |
| `affiliate_weak_contribution` | `affiliate` gets larger baskets (strong revenue) plus the highest discount rate, highest spend per acquisition and an above-average return rate (worst contribution). |
| `q4_seasonality` | November and December carry ~1.6× baseline order volume, with a smaller July bump. |

Each is asserted directly against the generated data in
`tests/unit/test_data_generator.py`, independently of any agent. If a pattern
stopped being present, those tests fail before the evaluation does — which
distinguishes "the agents got worse" from "the data changed".

---

## The benchmark

Eight questions. Each declares the metrics that must appear, the entity that
must be named, the direction that must be stated, and whether a real
statistical test or a rejection is expected.

| | Question | Pattern |
|---|---|---|
| Q1 | Did gross margin decline in Q3 2025, and did revenue rise? | `q3_margin_compression` |
| Q2 | Revenue increased in Q3 2025, but gross margin fell. What caused it? | `q3_margin_compression` |
| Q3 | Which product category has the highest return rate? | `home_kitchen_returns` |
| Q4 | Which customer segments are driving the increase in return rate? | `home_kitchen_returns` |
| Q5 | Which acquisition channel has the weakest contribution margin? | `affiliate_weak_contribution` |
| Q6 | Do shipping delays appear to affect repeat purchasing? | `delay_suppresses_repeat` |
| Q7 | Which shipping carrier has the highest late delivery rate? | `northeast_carrier_delay` |
| Q8 | How did monthly revenue change over 2025? | `q4_seasonality` |

Q1 and Q2 share a pattern on purpose: Q1 asks whether both directions are
recovered, Q2 asks whether the *cause* is identified. Q8 is a plain trend
question, present so the suite also covers the easy case.

---

## What each metric measures

| Metric | Definition |
|---|---|
| **pattern found** | The published findings name the expected metric, the expected entity, and the expected direction. All three, or the case does not count. |
| **numeric accuracy** | Every published number re-verified from scratch against the cells it cites, using the same deterministic checker the run used. |
| **SQL validity** | Share of executed statements that are `SELECT` or `WITH`. |
| **tool-call validity** | Share of MCP calls that returned a result rather than an error. |
| **published support rate** | published / (published + withheld). Deliberately **not** a target to maximise: a run that withholds nothing is not verifying anything. |
| **provenance completeness** | Share of published findings that cite at least one result, whose results all exist, and that carry a verifier reason. |
| **chart field validity** | Share of chart encoding fields that are real columns of the chart's result. |
| **unsupported findings published** | Count of published findings asserting causation. Must be zero. |

Direction is checked by word family (`fell`/`declin`/`lower`/`weakest` versus
`rose`/`increas`/`higher`/`strongest`) rather than by sign, because the finding
text is what a reader sees.

---

## Results

Measured on the full warehouse (seed `20260924`, fingerprint
`sha256:8e9ad9348f7dc18660d78ed3cd4d4b32`) with the scripted provider:

```
cases                              8
cases passed                       8
patterns expected                  6
patterns found                     6
patterns missed                    []

task completion                    1.00
metric correctness                 1.00
directional correctness            1.00
numeric accuracy                   1.00
SQL validity                       1.00
tool-call validity                 1.00
provenance completeness            1.00
chart field validity               1.00
published support rate             0.95

total findings published          35
total findings withheld            2
unsupported findings published     0
total MCP tool calls              35
total provider calls             193
mean runtime per question       0.09 s
wall clock for the suite         1.8 s
```

`tests/evaluation/test_benchmark.py` runs the same harness on a reduced
warehouse in CI and fails the build if any pattern is missed, if numeric
accuracy is below 1.0, if any unsupported finding is published, or if
**nothing is ever withheld** — a suite in which the verifier never fires is
not testing the verifier.

---

## What these numbers do and do not show

**They show** that the deterministic layers work: the metric layer computes
what it claims, the statistics are real SciPy output, every published number
traces to a cited cell, and the verification gates fire on genuine mistakes.
They also show the injected patterns are findable through the tool surface the
agents actually have.

**They do not show** that a language model would plan as well. The scripted
provider maps question keywords to metrics; it is a fixed heuristic, not a
reasoner. Live mode drives the same graph, the same MCP tools and the same
verification, and the provenance and safety guarantees hold identically — but
plan quality under a real model is not measured here, because measuring it
would require paid calls.

The honest reading: this measures the *engine*, and the engine is the part
that decides whether a wrong number can reach a report.

---

## Re-running against a live provider

```bash
AAE_PROVIDER_MODE=local AAE_OLLAMA_MODEL=qwen2.5:7b-instruct make evaluate
```

Needs a running Ollama server and no credential. Expect lower task-completion
and support rates and longer runtimes; the verification metrics are the
interesting ones, since they measure how often the engine catches a real model
overstating its results.
