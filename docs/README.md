# Documentation map

Start with the root [README](../README.md) for setup and a short product
overview. The files below separate current behaviour from historical evidence.

## Current system

- [Architecture](ARCHITECTURE.md): runtime boundaries and data flow
- [Deployment](DEPLOYMENT.md): configuration, release and rollback procedure
- [Evaluation](EVALUATION.md): deterministic and model-assisted evaluation
- [Limitations](LIMITATIONS.md): measured limits, and claims this project does
  not make
- [Performance budget](PERFORMANCE-BUDGET.md): enforced frontend limits
- [Production audit, 6 October 2026](RELEASE-EVIDENCE-production-2026-10-06.md):
  initial evidence for `f98b890` and the zero-spend follow-up for the current
  deployment
- [Production capacity rehearsal, 7 October 2026](RELEASE-EVIDENCE-capacity-2026-10-07.md):
  a bounded concurrency and isolation check against the current deployment;
  not a throughput result
- [Architecture decision records](adr/): decisions that still constrain the
  implementation

## Historical material

Files named `RELEASE-EVIDENCE-*` record the checks made for one commit or
release. They describe the current deployment only where they say so.

The [design package](design/REDESIGN-BRIEF.md) records the approved input to
the redesign. The [architecture atlas](architecture-atlas/README.md) carries
three diagrams with the commit each was audited against in its footer.

Keep both for traceability rather than as current operating instructions.
