# Local Stage-1 evaluation — qwen2.5:7b-instruct

A seven-question run of the real-model harness against a local model. It is
behavioural evidence that the governed engine works with a non-scripted
planner: real planning, real structured output, real MCP tool selection,
real DuckDB execution, real verification and real failure modes.

It is **not** a benchmark. There is no answer key and no pass mark.

## Reproduce

```bash
make data
AAE_PROVIDER_MODE=local \
AAE_OLLAMA_MODEL=qwen2.5:7b-instruct \
AAE_OLLAMA_TIMEOUT_SECONDS=600 \
AAE_BUDGETS__MAX_LLM_CALLS=64 \
AAE_BUDGETS__MAX_RUNTIME_SECONDS=1800 \
python -m agentic_analytics.cli evaluate-real-model \
  --selection stage1 --question-timeout-seconds 2400 \
  --checkpoint-dir var/stage1 --out var/stage1/report.json
```

Needs a running Ollama with that model pulled. Expect roughly 80 minutes.

## Configuration

| setting | value |
|---|---|
| engine SHA | `492f16ed05193acb9de70525adec127934df4483` |
| harness schema | 2 |
| model | `qwen2.5:7b-instruct` |
| model digest | `845dbda0ea48ed74` |
| quantization | Q4_K_M (7.6B) |
| Ollama | 0.32.5 |
| temperature / think | 0.0 / False |
| dataset seed | 4242 |
| selection | stage1 |
| max_llm_calls | 64 |
| max_analysis_tasks | 6 |
| max_tool_calls_per_task | 6 |
| max_total_tool_calls | 48 |
| max_followup_rounds | 1 |
| max_runtime_seconds | 1800.0 |
| question_timeout_seconds | 2400.0 |
| ollama_timeout_seconds | 600.0 |

`max_llm_calls` and `max_runtime_seconds` are evaluation overrides, not
product defaults, which remain 40 and 300. A 40-call arm on the warehouse
question produced seven candidate findings and published none: every critic
call was refused before dispatch, so verification was starved and no report
was written.

## Outcomes

| dataset | kind | terminated | timeout | tool calls | succeeded | preflight rejections | published | withheld | seconds |
|---|---|---|---|---|---|---|---|---|---|
| warehouse | grouped | yes | no | 27 | 27 | 8 | 0 | 9 | 2108 |
| sales | aggregate | yes | no | 19 | 12 | 2 | 9 | 6 | 986 |
| marketing | grouped | no | no | 2 | 1 | 1 | 0 | 0 | 96 |
| sales | ranking | yes | no | 18 | 18 | 2 | 10 | 0 | 657 |
| marketing | trend | yes | no | 1 | 1 | 4 | 12 | 0 | 569 |
| retention | statistical | no | no | 3 | 1 | 1 | 0 | 0 | 156 |
| sales | unsupported | no | no | 6 | 5 | 6 | 0 | 0 | 201 |

Totals: 46 candidate findings,
31 published, 15 withheld
({"critic": 2, "numeric_mismatch": 4, "verification_budget_exhausted": 9}), 1 exact
duplicate removed. 199 provider request attempts,
195 successful. 217,392 input
and 17,570 output tokens. Zero schema, JSON and transport failures;
zero whole-question timeouts.

Three questions did not complete. That is a model and utility outcome, not an
engine failure: a question may legitimately publish nothing.

## Manual review

Every published finding was checked against its resolved evidence cells.
`manual-review.json` carries the per-finding record and these categories:

- **A** — evidence, arithmetic, units, comparison scope and wording all supported.
- **B** — numerically supported, but the wording or citation carries a semantic
  limitation the deterministic gates do not enforce.
- **C** — unsupported: a number, entity or direction the cited results do not carry.

**A: 24. B: 7. C: 0.**

The seven B findings are worth reading, because they show exactly what the
gates do and do not cover:

- three are worded as a range whose endpoints are the same number
  ("ranges from 43985.05 to 43985.05") — accurate, and the range framing
  implies a comparison that was not made;
- two cite a cell that does not itself contain the claimed number, though
  the number is present elsewhere in the cited result;
- one is a universal claim ("each product family") resting on a single cell;
- one maps five values to five families while citing two cells.

None is wrong. Each is a reminder that arithmetic verification is not a proof
of semantic correctness, which is why published findings are described as
*numerically verified against cited results* and never as true.

## What is not here

This run predates the relevance gate, so some published findings are accurate
without answering the question that was asked. The gate landed later; a run
carrying it would withhold those under `irrelevant_to_question`.

No raw dataset rows, prompts, credentials, session capabilities or local
paths are stored. The evaluation datasets are generated from the recorded
seed.

`checksums.json` covers the other files in this directory.
