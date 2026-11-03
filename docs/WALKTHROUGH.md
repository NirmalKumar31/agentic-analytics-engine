# Follow one question through the code

## Start with the problem

A language model can describe an analysis fluently while selecting the wrong
column, ignoring a filter or inventing a number. This project separates
interpretation, calculation and verification. Each stage has a bounded job.

## Example: total sales by region

Imagine an uploaded CSV with `region` as a category and `sales` as an additive
numeric measure. With those roles established, the request is:

> What are total sales by region?

| region | sales |
| --- | ---: |
| East | 100 |
| West | 80 |
| East | 50 |

1. **Load and profile the data.** `warehouse/upload.py` loads supported files;
   `analytics/semantic.py` infers conservative column roles. An ambiguous role
   is not automatically a business fact.
2. **Interpret the request.** `analytics/upload_plan.py` resolves the requested
   operation, measure, dimension, filters and period. `analytics/resolution.py`
   names the outcome: exact, ambiguous, unresolved, unsupported or unsafe.
3. **Use AI only where allowed.** For eligible ambiguities, `llm/governed.py`
   requests a typed proposal. Local validation must accept it. The model cannot
   supply arbitrary executable SQL or authorize its own proposal.
4. **Compute the contract.** `analytics/compute.py` constructs supported
   operations. `analytics/filters.py` handles allowlisted columns and bound
   parameter values. `analytics/row_filters.py` interprets restrictions in
   uploaded-data questions; it serves a different role from SQL filter rendering.
5. **Run SQL.** `analytics/execute.py` calls the parsed-SQL guard in
   `warehouse/sqlguard.py`, executes over the session's restricted DuckDB
   connection and records a `ResultSnapshot` from `analytics/results.py`.
6. **Verify and publish.** `verification/canonical.py` checks the accepted
   contract against the evidence. The `presentation/` package builds the
   user-facing result. The answer must retain the applicable limitations.

Conceptually, this example computes:

```sql
SELECT region, SUM(sales) AS total_sales
FROM uploaded_data
GROUP BY region;
```

| region | total_sales |
| --- | ---: |
| East | 150 |
| West | 80 |

This is illustrative SQL, not a promise about the generated alias or row order.
The actual response contains the executed SQL and snapshot. If `sales` is an
annual customer attribute rather than an additive transaction value, summing it
is not justified simply because it is numeric. The semantic checks matter.

## The two paths

**Uploaded data:** one dataset, conservative schema interpretation, a typed
contract and canonical verification. Start here when explaining the project.

**Built-in warehouse:** governed metrics are defined in `warehouse/metrics.yml`.
`analytics/metric_plan.py` resolves simple metric questions directly. Richer
analyses use the workflow in `graph/build.py` and bounded roles in `agents/`.
The warehouse path can propose several findings and verify each. It is not
identical to the upload path and does not use exactly one model call.

## What each folder is for

| Folder under `src/agentic_analytics` | Purpose |
| --- | --- |
| `analytics/` | Typed analytical operations, schema interpretation, SQL execution and statistics |
| `warehouse/` | Dataset loading, sessions, metric definitions and SQL guard |
| `verification/` | Contract, coverage, arithmetic, claim and chart checks |
| `presentation/` | Construct explanations and chart/report data from verified results |
| `graph/` | Workflow state and orchestration |
| `agents/` | Bounded warehouse planning, execution and review roles |
| `llm/` | Fake, local and cloud adapters plus cloud usage accounting |
| `mcp_layer/` | Analytical tool contracts and client/server transport |
| `api/` | HTTP endpoints, session access, event streaming and run admission |
| `data/` | Reproducible synthetic demo data and known ground truth |
| `recordings/`, `evaluation/` | Recorded results and evaluation harnesses |

At the root, `web/` is the React client, `tests/` checks behavior and safety,
`scripts/` contains operational checks, and `examples/` holds sample/evaluation
material. These support the application; they are not separate analytics projects.

## Explain the database work

“I separated data interpretation from SQL execution. The accepted contract
identifies a supported measure, grouping and restrictions. Python constructs the
query using governed definitions, validates it and executes it over DuckDB.
The result snapshot retains the SQL and returned values, so the answer can be
checked against the calculation.”

For joins, distinguish the fixed warehouse model from arbitrary uploaded-table
joins, which are unsupported. For filtering, explain the column allowlist and
parameter binding. For verification, explain one actual cited result before
claiming the system guarantees correctness for every possible business question.

## Test the claim, not just the screen

```bash
make test
make verify
```

The test suite includes contracts, filters, SQL restrictions, numerical evidence,
API behavior and refusal cases. Tests use a scripted provider by default. This
proves code behavior on those fixtures; it does not establish real-model accuracy.
Model evaluation and deployment evidence have their own scope in
[EVALUATION.md](EVALUATION.md) and [HISTORY.md](HISTORY.md).

## Cleanup scope

Historical design sheets, generators and release captures are available at the
pinned pre-cleanup commit linked in [History](HISTORY.md). They are no longer
shipped as active project files. Numeric type interpretation lives with arithmetic
verification in `verification/numeric.py`, reducing a module boundary. The active
application and its verification gates remain in place.
