# Documentation map

Start with the root [README](../README.md) for setup and a short product
overview. The files below separate current behavior from historical evidence.

## Current system

- [Architecture](ARCHITECTURE.md) — runtime boundaries and data flow
- [Deployment](DEPLOYMENT.md) — configuration, release and rollback procedure
- [Evaluation](EVALUATION.md) — deterministic and model-assisted evaluation
- [Limitations](LIMITATIONS.md) — measured limits and claims the project does
  not make
- [Performance budget](PERFORMANCE-BUDGET.md) — enforced frontend limits
- [Production audit, 6 October 2026](RELEASE-EVIDENCE-production-2026-10-06.md)
  — evidence for deployed commit `f98b890`
- [Architecture decision records](adr/) — decisions that still constrain the
  implementation

## Historical material

Files named `RELEASE-EVIDENCE-*` record checks made for a particular commit or
release; they are not statements about the current deployment unless they say
so. The [design package](design/REDESIGN-BRIEF.md) records the approved input to
the redesign, and the [architecture atlas](architecture-atlas/README.md)
records an earlier architecture snapshot. Keep these for traceability, not as
current operating instructions.
