# 0001: One service for dual-mode v0.1

Status: accepted
Date: 2026-09-27

## Context

The engine needs to offer two orchestration strategies from one public site:
deterministic analytics, where a scripted provider makes the agent decisions,
and AI analytics, where a cloud model does. A third mode runs both against one
question and shows them side by side.

Three topologies were considered.

**A. One service, provider chosen per run.** A single FastAPI process. The
mode arrives with the analysis request; the provider is constructed for that
run.

**B. Two public services.** A deterministic deployment and an AI deployment,
each with its own configuration and secrets, with the browser talking to both.

**C. Deterministic public service plus a restricted internal AI service.** The
public process proxies AI runs to an internal one that holds the key.

## Decision

**A, for v0.1.**

## Why

Dataset sessions are the deciding constraint. A session is a DuckDB
connection, a capability cookie and an uploaded file living in one process. In
topology B, Compare Both either uploads the dataset twice (two sessions, two
fingerprints, and a comparison that is no longer of the same data) or shares
a session across origins, which means a cross-site credentialed cookie,
`SameSite=None`, a CORS allow-list and CSRF protection that the code does not
have today.

The security work that buys is small. The stated benefit of B is that the
cloud key lives in a process the deterministic traffic cannot reach. But the
public deterministic surface and the public AI surface are the same FastAPI
app, the same session manager and the same MCP server; splitting the process
does not split the attack surface that matters, and it adds a cross-origin
credential path that is a real one.

B and C are also more deployment than can be built and scrutinised carefully
in one pass, and an insecure two-service design is worse than a sound
one-service design.

## Consequences

The cloud credential and the deterministic service share a process. Mitigated
by:

- the provider is constructed per run from the requested mode, so a
  deterministic run never builds a cloud provider;
- the credential exists only in the server environment and is never returned
  by any endpoint, including `/api/config`;
- AI mode is off by default and requires explicit enablement;
- AI-specific budgets and a durable global spend breaker bound cost
  independently of the deterministic limits;
- an external provider-side spend cap is required before anonymous AI is
  exposed.

The completed run records its own mode, provider kind and model id, so a
result is never interpreted through the server's current configuration.

## Later

A two-service split stays open. It becomes worthwhile when AI traffic needs
separate scaling or a separate availability budget, and it should be revisited
then, with a same-origin path for the browser such as a reverse proxy, so
the cookie architecture does not have to change.
