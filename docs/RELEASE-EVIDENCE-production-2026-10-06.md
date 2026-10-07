# Production audit: 6 October 2026

This record is intentionally narrow: what was checked, against which commit,
what it cost, and what it does not prove.

## Target

- Service: <https://agentic-analytics-engine.onrender.com>
- Deployed commit: `f98b89093a863c4a4d2a02f26711885313380961`
- `/api/health`: exact commit match, ready warehouse, three recordings
- `/api/config`: live analysis, uploads, AI Analytics and Compare available
- Public Streamable HTTP MCP: withdrawn with 503; the website continues to
  use the same MCP server through its in-process SDK transport

## Evidence

| Check | Result |
|---|---|
| Merge-commit CI | 10/10 jobs passed in run `37474093846`, including Docker and Browser end to end |
| Hosted browser acceptance | 156/156 cells, 156 screenshots |
| Matrix | 6 widths × 2 themes × 13 states |
| Hosted mutations | 0 uploads, 0 analyses, 0 comparisons, 0 provider calls; enforced by request interception |
| API acceptance | 60/60 checks, including a deterministic run, an upload run, safe refusal, security headers and cleanup |
| Paid Compare canary | 41/41 checks, one cloud call, $0.000158 |

The browser sweep waits for Vega's SVG, marks and labels before capture. An
empty chart box therefore cannot satisfy the chart check. The reviewed
screens included desktop and phone reports, both themes, the mode selector,
Compare, evidence, focus restoration, refusal, reduced motion, print and the
AI waiting state.

The paid canary asked `What is the average Weekly_Revenue by Promo_Flag?`
over a generated 200-row fixture. Both strategies completed, accepted the
same typed contract, matched a DuckDB result computed before upload, reported
complete row coverage, resolved their own citations and shared no result or
finding identifiers. The deterministic side used no model allowance. The AI
side reported one provider call, 736 input tokens, 167 output tokens and 158
microdollars. The script did not retry and deleted the session.

An earlier exploratory run costing $0.007378 motivated the report-truth and
presentation fixes but is not counted as acceptance evidence here: its raw
capture is not committed with this record.

## Findings from this audit

The deployed upload screen said nothing reached a model unless the visitor
chose an AI strategy. That was false for the default Governed Analysis mode,
which may consult a model when local rules cannot resolve an eligible
ambiguity. The audit branch corrects the disclosure and pins the wording with
a component test. This finding does not invalidate the execution evidence
above, but the copy fix requires its own merge and deployment before it is
true in production.

The component suite then exposed two rendering defects that did not break the
hosted screenshots: the disclosure's `<details>` element was nested inside a
paragraph, which is invalid HTML, and two Compare lists used display titles as
React keys. Repeated titles produced duplicate-key warnings and could make
React omit or reuse the wrong item. The audit branch uses valid disclosure
markup and stable strategy identifiers.

The repository review also found frozen test counts, one removed deployment
path, one moved test path, an obsolete two-pane Compare instruction and an old
animated-flow description in current documentation. Those statements are
corrected, the design package is labeled as historical input, and a link test
based on Git's tracked files now prevents an uncommitted local file from
satisfying a documentation link.

## Limits

- The 156-cell sweep uses route interception for report states. It verifies
  the deployed frontend and recorded payloads without spending; it is not a
  live-model quality study.
- The paid result is one question, one dataset shape and one model response.
  It proves that path worked once, not that model planning is reliable across
  questions.
- No claim of WCAG conformance, performance under load, high availability or
  causal inference is made.
- AI-off rollback and a production Redis restart were not rehearsed.
