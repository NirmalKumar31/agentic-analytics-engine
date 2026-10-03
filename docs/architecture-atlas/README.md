# Architecture atlas

Three complementary diagrams for the implemented product. They are deliberately
not literal call graphs: each is a readable map of one boundary that matters
when evaluating or changing the engine.

The SVGs are editable and embed the stack marks they display; PNGs are useful
for README previews and slide decks. Solid arrows are the primary path; dashed
arrows are conditional or deployment-dependent.

## 1. From question to inspectable answer

[Open SVG](01-end-to-end.svg) · [open PNG](01-end-to-end.png)

![End-to-end governed analysis](01-end-to-end.png)

This is the complete reader-facing path: demo or upload, session admission,
local or governed planning, MCP/DuckDB computation, publication gates, and an
answer with tables, charts and provenance. It is a conceptual flow, not a
claim that every question uses every graph node.

## 2. Automatic governed planning

[Open SVG](02-governed-planning.svg) · [open PNG](02-governed-planning.png)

![Automatic governed planning](02-governed-planning.png)

This isolates the default routing policy. An exactly resolved upload question
does not construct a cloud provider or consume AI admission. An eligible
ambiguity may use a typed AI planning proposal, which is locally validated
before a contract reaches execution. An unsafe or unmappable request is
refused with its reason.

Forced AI and the comparison screen are explicit audit paths; they are not
shown as an automatic fallback.

## 3. Runtime and data boundaries

[Open SVG](03-runtime-and-data.svg) · [open PNG](03-runtime-and-data.png)

![Runtime and data boundaries](03-runtime-and-data.png)

This view separates the one Render/Docker application container from optional
external dependencies. The website's MCP client and server run in-process.
Uploaded data is ephemeral session state; the optional key-value store holds
admission and usage accounting, not uploaded files. The model boundary is
conditional and governed.

## Role-confirmation boundary

The three visual plates were audited before session-scoped role confirmation
was added. The current architecture includes this extra effective-schema
boundary, documented here rather than silently implying that the plates
depict it.

1. Raw inferred schema and session-scoped, revisioned confirmations form one
   effective schema.
2. The API schema inspector, planner, contract resolver and MCP tools consume
   that effective schema.
3. Each run pins one confirmation snapshot and carries its role evidence and
   schema revision into the planning audit.
4. A role-confirmation PATCH rejects stale revisions and refuses changes while
   the same session has an active run.

The confirmation changes only an ambiguous field to **quantity** or
**category**. It is validated server-side, scoped to one session, not written
to durable user state, and cannot relabel completed evidence. See
[ADR 0007](../adr/0007-session-scoped-role-confirmation.md).

## Source map

| Diagram claim | Implementation |
| --- | --- |
| React, TypeScript, Vite and Vega-Lite | web/package.json and web/src |
| FastAPI, sessions, uploads and SSE | src/agentic_analytics/api/app.py |
| Workflow, upload fast path and publication | src/agentic_analytics/graph |
| Automatic routing and lazy planner creation | src/agentic_analytics/agents/analyst.py and ADR 0005 |
| Typed contract compiler | src/agentic_analytics/analytics/upload_plan.py |
| Presentation contract and evidence-bound prose | src/agentic_analytics/presentation and ADR 0004 |
| MCP client/server and in-process transport | src/agentic_analytics/mcp_layer |
| Guarded DuckDB execution | analytics/execute.py and warehouse/sqlguard.py |
| Governed cloud accounting | src/agentic_analytics/llm |
| Effective schema and role-confirmation provenance | analytics/semantic.py, warehouse/session.py and ADR 0007 |
| Container and deployment boundary | Dockerfile and render.yaml |

## Provenance and icon attribution

The first three diagrams were audited against source snapshot
5180381eee6ea80919ed521329ccccc1b9adec5e. Their core topology is still the
implementation; this README explicitly adds the later role-confirmation
boundary instead of backdating it into the artwork.

Brand marks in the embedded SVGs are from Simple Icons. Their source URLs and
attribution are recorded in [assets/SOURCES.txt](assets/SOURCES.txt). The
OpenAI model symbol and line icons are original; the diagrams do not claim an
official OpenAI logo or a separate hosted MCP service.

For the detailed architecture and its invariants, read
[ARCHITECTURE.md](../ARCHITECTURE.md). For what has actually been measured,
read the release evidence rather than treating a diagram as proof.

