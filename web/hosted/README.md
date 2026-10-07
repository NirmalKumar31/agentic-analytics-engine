# Hosted visual acceptance

A browser pass over a **deployment**, at six widths in both themes, that
costs the deployment nothing.

```bash
cd web
AAE_HOSTED_BASE_URL=https://your-service.example.com \
AAE_HOSTED_SHA=<the 40-character commit it should be serving> \
npm run test:hosted

node scripts/check-hosted-acceptance.mjs
```

The second command is not optional. Playwright exits 0 when nothing fails,
including when nothing ran; the checker is what decides whether the sweep
covered the matrix, and it writes `hosted-results/acceptance.json`, which is
the artefact worth filing.

## It refuses before it runs

`preflight.ts` reads `/api/health` and stops unless:

- `build_sha` equals `AAE_HOSTED_SHA` exactly
- `provider_mode` is `fake`
- `status` is `ok` and at least one recording is advertised

There is no bypass flag. A hosted acceptance result is evidence about one
commit; a sweep that ran against whatever happened to be serving proves
nothing about the SHA it is filed under, and Render redeploys on its own
schedule.

## It spends nothing, and that is enforced

The public ceilings are a few uploads an hour. This sweep makes **no
upload, analysis, comparison or provider call**, and the enforcement is not
a convention:

`fixtures.ts` routes every `/api/**` request. A staged fixture answers what
the case needs; otherwise only `GET /api/health`, `GET /api/config` and
`GET /api/recordings[/id]` are allowed through. Everything else is recorded
and **aborted**, and the test then fails naming what it tried to send.

Where the content comes from:

| surface | source | cost |
| --- | --- | --- |
| successful report | the deployment's own recordings, over `GET /api/recordings/{id}` | free read |
| Compare | a recording, with the right side perturbed so the diff has something to show | none |
| terminal states | the committed fixtures in `src/test/runs/states` | none |
| the session | a committed capture of `POST /api/datasets/demo`, taken from a local server | none |

## The matrix

Six widths (360, 390, 768, 1024, 1440, 1920) in light and dark make twelve
projects. Each covers thirteen states: landing, composer, layout,
focus-visible, report, execution-graph, evidence-drawer, focus-restoration,
compare, AI-in-progress, terminal-refused, reduced-motion and print. That is
156 cells, each with a screenshot.

The matrix is declared in `matrix.ts` and again in
`scripts/check-hosted-acceptance.mjs`, which is plain Node and cannot import
the TypeScript. `scripts/hostedGuard.check.mjs` reconciles the two on every
push, and drives the checker against synthetic reports to prove it refuses
an empty run, a missing cell, a retried cell and a test renamed off the
matrix.

## Not in CI

CI cannot know which commit a deployment is serving, and a sweep filed
against the wrong SHA is worse than no sweep. Run it after a deploy, with
the SHA you deployed.

Running it against a local build of a branch is the other normal use, and
is how a change to any of this is demonstrated before it ships:

```bash
AAE_BUILD_SHA=<a sha> AAE_PROVIDER_MODE=fake \
  .venv/bin/uvicorn agentic_analytics.api.app:app --port 8131
```

Use `AAE_HOSTED_REPORT` to keep the two runs' evidence apart.
