import { createHash } from 'node:crypto'
import { appendFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type APIRequestContext, type Page, type Request } from '@playwright/test'

/**
 * Every upload and every close, written down.
 *
 * CI runs all three engines against one container, and the container holds
 * two ceilings this suite can exhaust: `max_active_upload_sessions` (24 by
 * default) and `uploads_per_ip_per_hour` (200 in CI). Both were exhausted
 * on the first push of the redesign branch, and nothing in the suite could
 * say how many uploads it made -- the number had to be reconstructed from
 * rejection messages in a CI log.
 *
 * So the suite counts itself. `scripts/check-upload-budget.mjs` reads these
 * ledgers after the run and asserts the totals, which makes the budget a
 * thing that fails a build rather than a thing somebody remembers.
 *
 * One line per event, appended. Workers are serialised (`workers: 1`), so
 * appends do not interleave; a `.jsonl` file survives a crashed run in a
 * way a single JSON document would not.
 */
/*
 * Outside `test-results`, deliberately.
 *
 * Playwright clears its output directory at the start of every run, and CI
 * runs the three engines as three separate invocations against one
 * container. A ledger under `test-results` was therefore wiped between
 * engines, so the "job total" it reported was only ever the last engine's
 * -- which is precisely the accounting the shared ceilings require. The
 * three engines share 200 uploads and 200 analyses per hour; the ledger has
 * to outlive each of them to say so.
 */
const LEDGER = join(
  dirname(fileURLToPath(import.meta.url)),
  '..',
  'playwright-ledger',
)

/*
 * Which job wrote this line.
 *
 * The ledger now outlives a Playwright invocation, which is the whole
 * point of it -- and the cost of that is that a file left behind by an
 * earlier job looks exactly like a file written by this one. A stale
 * `chromium.jsonl` from yesterday's run would satisfy "every expected
 * engine is present" while describing nothing that happened today, and a
 * ledger that is half this job and half another one reports totals that
 * belong to neither.
 *
 * So the outer wrapper pins an id -- in CI, the run id and the attempt,
 * so a re-run of the same workflow is a different job -- and every record
 * carries it. `scripts/check-resource-budget.mjs` refuses a ledger that
 * holds more than one, or one that is not the job it was asked about.
 * Unset, it is a fixed string: the three engines still agree with each
 * other, but the guard says out loud that it cannot detect staleness.
 */
const JOB = process.env.AAE_E2E_JOB_ID ?? 'local-unpinned'

/** Events written by this process, for the durable id below. */
let events = 0

export type LedgerEvent =
  /** A `POST /api/datasets/upload` was attempted. */
  | 'upload'
  /** ...and the server returned a session, which is now live. */
  | 'open'
  /** ...and that session was handed back by id. */
  | 'close'
  | 'refused'
  /** A `POST /api/analyses` or `/api/comparisons` left the page. */
  | 'request'
  /** Its response came back, with the source it came from. */
  | 'response'
  /** A real server response was remembered for replay. */
  | 'capture'
  /** A payload committed to the repository was registered for replay. */
  | 'fixture'

export function ledger(
  event: LedgerEvent,
  detail: string,
  /**
   * The engine, when there is no test to ask.
   *
   * A worker-scoped fixture's *teardown* runs after the last test, where
   * `test.info()` throws -- so a session closed there would go unrecorded
   * and the peak-concurrency figure would be computed from opens alone.
   * The fixture captures the project name while a test is still live and
   * hands it back here.
   */
  project?: string,
  /**
   * Structured fields, merged last so they win.
   *
   * The traffic observer records a response that may arrive after the test
   * that caused it has ended, where `test.info()` throws -- so it captures
   * the engine and the test path when the *request* is made and passes
   * them back here. Everything the guard reconciles on (sequence, status,
   * source, capture) is a field rather than a substring of `detail`,
   * because a reconciliation that parses prose is a reconciliation that
   * stops working when somebody improves the wording.
   */
  extra?: Record<string, unknown>,
): void {
  let name = project
  let where = 'worker teardown'
  try {
    name ??= test.info().project.name
    where = test.info().titlePath.join(' \u203a ')
  } catch {
    // Outside a test. `project` is then required, and a missing one is
    // reported by `check-upload-budget.mjs` as an unattributable event.
  }
  events += 1
  try {
    mkdirSync(LEDGER, { recursive: true })
    appendFileSync(
      join(LEDGER, `${name ?? 'unattributed'}.jsonl`),
      JSON.stringify({
        event,
        /*
         * Durable and unique across workers.
         *
         * A worker restart -- which a single failing test causes -- starts
         * a new process with its own counter, so a plain integer would
         * repeat within one engine's ledger and a duplicate-detection rule
         * would either fire on honest data or be too weak to fire at all.
         */
        id: `${JOB}:${name ?? 'unattributed'}:${process.pid}:${events}`,
        job: JOB,
        project: name ?? null,
        detail,
        test: where,
        at: Date.now(),
        ...extra,
      }) + '\n',
    )
  } catch (reason) {
    /*
     * Not swallowed.
     *
     * This used to return quietly, on the reasoning that a ledger failure
     * should not fail the test it is measuring. That is backwards: the
     * budget guard's verdict is only worth something if the ledger is
     * complete, and a run that silently stopped recording is a run that
     * reports a smaller cost than it had. A guard that cannot be wrong
     * about the direction of its own error is worth more than a test that
     * survives a broken disk.
     */
    const message = reason instanceof Error ? reason.message : String(reason)
    console.error(`the resource ledger could not be written: ${message}`)
    throw reason
  }
}

/** A CSV whose columns the deterministic resolver can map to a question. */
export function sampleCsv(rows = 200): string {
  const regions = ['North', 'South', 'East', 'West']
  const lines = ['order_id,order_date,region,revenue']
  for (let i = 0; i < rows; i += 1) {
    const month = String((i % 12) + 1).padStart(2, '0')
    lines.push(`${i},2025-${month}-15,${regions[i % regions.length]},${(10 + ((i * 7) % 490)).toFixed(2)}`)
  }
  return lines.join('\n')
}

/* ------------------------------------------------- the dataset shapes
 *
 * Every shape this suite genuinely needs, in one place.
 *
 * They were spread across the specs that happened to use them, which made
 * "how many distinct datasets does this suite require" a question nobody
 * could answer -- and the answer mattered: the suite uploaded 150 files per
 * engine against a container that allows 24 live sessions. Gathered here,
 * each one is a worker fixture in `fixtures.ts`, uploaded once per engine.
 *
 * A new shape belongs here only when a test needs data the existing ones
 * cannot express. A different filename is not a different shape.
 */

/** 36 categories over 400 rows: a long table and a high-cardinality chart. */
export function wideCsv(rows = 400, groups = 36): string {
  const lines = ["id,order_date,category,revenue"];
  for (let i = 0; i < rows; i += 1) {
    const month = String((i % 12) + 1).padStart(2, "0");
    lines.push(
      `${i},2025-${month}-15,cat_${String(i % groups).padStart(2, "0")},${(10 + ((i * 13) % 900)).toFixed(2)}`,
    );
  }
  return lines.join("\n");
}

/** Two dimensions, so the chart carries a colour encoding. */
export function twoDimensionCsv(rows = 240): string {
  const regions = ["North", "South", "East", "West"];
  const channels = ["web", "retail", "partner"];
  const lines = ["order_id,order_date,region,channel,revenue"];
  for (let i = 0; i < rows; i += 1) {
    const month = String((i % 12) + 1).padStart(2, "0");
    lines.push(
      `${i},2025-${month}-15,${regions[i % 4]},${channels[i % 3]},${(10 + ((i * 7) % 490)).toFixed(2)}`,
    );
  }
  return lines.join("\n");
}

/**
 * The composer, the dataset context strip, and the schema side sheet.
 *
 * Two date columns, so the ambiguity signals have something real to report.
 * Every assertion here is about a property of the composed screen rather
 * than about an element existing -- "the composer is the largest control",
 * "the landing is not also on screen" -- because element-existence is what
 * the previous suite checked and it is what let a panel-in-a-panel layout
 * pass review.
 */
export function twoClockCsv(rows = 240): string {
  const regions = ["North", "South", "East", "West"];
  const lines = ["order_id,order_date,signup_date,region,revenue"];
  for (let i = 0; i < rows; i += 1) {
    const month = String((i % 12) + 1).padStart(2, "0");
    lines.push(
      `${i},2025-${month}-15,2024-${month}-02,${regions[i % 4]},${(10 + ((i * 7) % 490)).toFixed(2)}`,
    );
  }
  return lines.join("\n");
}

/** A close call in the band where a code list and a count are identical. */
export function closeCallCsv(rows = 400): string {
  const sites = ["alpha", "beta", "gamma", "delta"];
  const lines = ["site,dose,reading"];
  for (let i = 0; i < rows; i += 1) {
    lines.push(`${sites[i % 4]},${(2.5 + i * 0.1).toFixed(2)},${18 + (i % 48)}`);
  }
  return lines.join("\n");
}

/** A dataset whose `age` column the engine cannot classify from the data. */
export function ambiguousCsv(): string {
  const regions = ["North", "South", "East", "West"];
  const lines = ["region,revenue,age,visits"];
  for (let i = 0; i < 400; i += 1) {
    lines.push(
      `${regions[i % 4]},${100 + i * 7},${18 + (i % 48)},${2 + (i % 5)}`,
    );
  }
  return lines.join("\n");
}

/** An ordinary dataset with nothing ambiguous in it. */
export function plainCsv(): string {
  const regions = ["North", "South", "East", "West"];
  const lines = ["region,revenue,month"];
  for (let i = 0; i < 200; i += 1) {
    lines.push(`${regions[i % 4]},${100 + i * 7},2025-${(i % 12) + 1}`);
  }
  return lines.join("\n");
}

/**
 * Wait for a run to finish.
 *
 * Keyed on the report heading rather than on a spinner disappearing: the
 * report is the thing a visitor is waiting for, and a test that passes
 * before it renders is not testing the flow.
 */
export async function waitForReport(page: Page): Promise<void> {
  await expect(page.getByTestId('report-panel')).toBeVisible({
    timeout: 90_000,
  })
}

/**
 * Wait for a comparison to render.
 *
 * Compare has no `report-panel`: the two strategies' reports are compact
 * panes, and under agreement there is one shared result rather than a
 * single-run report. The workspace is the anchor, and waiting on the
 * wrong one timed out for 90 seconds on a page that had already rendered.
 */
export async function waitForCompare(page: Page): Promise<void> {
  await expect(page.getByTestId('compare-workspace')).toBeVisible({
    timeout: 90_000,
  })
}

/**
 * The recorded-run buttons, in the landing's prepared-data list.
 *
 * Scoped by test id rather than by a heading. The old selector reached for
 * `section.panel` containing a heading named exactly "Dataset" -- which
 * worked, and was three assumptions deep: that the surface is a panel, that
 * it is headed, and that the heading says that word. All three were true
 * only because the landing had not been designed yet.
 *
 * The demo warehouse is deliberately excluded: it is the first row of the
 * same list and is not a recording, and a helper that returned it would
 * make `recordingButtons().first()` open the demo.
 */
export function recordingButtons(page: Page) {
  return page
    .getByTestId('prepared-data')
    .locator('button.prepared-item')
    .filter({ hasNotText: 'Commerce demo warehouse' })
}

/** The suggested-question buttons, in the composer. */
export function suggestedQuestions(page: Page) {
  // Scoped by test id. The old selector reached for `section.panel`
  // containing a heading named "Ask"; the composer is not a panel and has
  // no such heading, because it is no longer one box among several.
  return page.getByTestId('question-examples').locator('button.suggestion')
}

/**
 * Upload a file and wait for its profile.
 *
 * An acceptance suite must never turn a server-side capacity refusal into a
 * skipped test: skipped is green in Playwright's summary and once concealed
 * a mutation that had not run at all. CI provisions sufficient capacity for
 * this suite; a refusal here is therefore a failure with useful evidence.
 */
export async function uploadFile(
  page: Page,
  name: string,
  contents: string,
): Promise<void> {
  /*
   * The attempt and the session are two events, not one.
   *
   * This used to record `open` before the file was even sent, so a refused
   * upload counted as a live session that nothing would ever close, and a
   * successful one was recorded without the id the server gave it. The
   * attempt is what the hourly ceiling counts; the session is what the pool
   * of 24 counts; and reconciling the pool needs the id, because "19 opens
   * and 19 closes" is also what a suite that closed the same session
   * nineteen times would report.
   */
  ledger('upload', name)
  const upload = page.waitForResponse(
    (response) =>
      response.url().includes('/api/datasets/upload') &&
      response.request().method() === 'POST',
  )
  await page.locator('input[type="file"]').setInputFiles({
    name,
    mimeType: 'text/csv',
    buffer: Buffer.from(contents),
  })
  const response = await upload
  if (response.ok()) {
    const body = (await response.json()) as { session_id?: string }
    ledger('open', name, undefined, { session: body.session_id ?? null })
  }
  // The composer is the signal that profiling finished.
  //
  // This waited for the schema inspector, which was resident on the canvas.
  // The inspector now lives inside a side sheet that opens on request, so
  // it is not in the document until a reader asks for it -- and every
  // upload test timed out waiting for something that was never going to
  // appear. The composer is the better signal anyway: it is what the
  // profiling was *for*, and it is what the reader is waiting to use.
  const ready = page.getByTestId('composer')
  const notice = page.locator('.notice.error')
  await expect(ready.or(notice).first()).toBeVisible({ timeout: 30_000 })
  if (await notice.isVisible()) {
    const text = (await notice.textContent()) ?? ''
    throw new Error(`upload was rejected: ${text.trim()}`)
  }
}

/**
 * A response the suite fulfilled, and where the payload came from.
 *
 * Set by `route.fulfill` and therefore impossible for the server to emit:
 * the application under test has no code that writes it, so a response
 * carrying it was answered by this suite and a response without it reached
 * the container. That is the whole basis of the analysis accounting.
 *
 * A `run_fixture_*` prefix on the run id was the earlier signal. It is
 * weaker -- it is a convention in test code about a value the *product*
 * mints, and a product that one day generates ids with that shape would
 * silently reclassify real runs as free.
 */
export const SOURCE_HEADER = 'x-aae-e2e-analysis-source'
/** The sha of the payload the fulfilled response served. */
export const CAPTURE_HEADER = 'x-aae-e2e-capture'
/**
 * `capture`: a remembered real response. `committed`: a payload in the
 * repository. `harness`: a response the suite synthesised while
 * orchestrating a *real* run -- Compare's `POST /api/comparisons` is
 * answered here while the deterministic analysis behind it is issued to
 * the container -- which costs nothing itself and must be accompanied by
 * the server-backed request it stands for.
 */
export type FixtureSource = 'capture' | 'committed' | 'harness'

/**
 * Choose an analysis mode the way a reader does: by its label.
 *
 * The radio input is covered by its own `<label>`, which is how the
 * control is built -- the label carries the visible name and the
 * screen-reader description, and clicking it is what selects the mode.
 * Playwright's `.check()` clicks the *input*, and Firefox's hit-testing
 * reports the label as intercepting those pointer events, so the click is
 * retried until the test times out. In CI that took out twenty Firefox
 * tests in one run: every one of them went through `resetToComposer`,
 * which put the mode back to Deterministic, and the call log read
 * `<label for="mode-deterministic">…</label> intercepts pointer events`
 * over and over for two minutes.
 *
 * It did not reproduce locally, because the local server advertises AI and
 * the container does not: with AI and Compare unavailable both options
 * carry the `.unavailable` class, and the label's box sits differently.
 * That is a configuration difference exposing a wrong interaction, not a
 * product defect -- a reader clicking the label has always worked.
 *
 * The selection is asserted rather than assumed, which `.check()` only did
 * implicitly.
 */
export async function selectMode(
  page: Page,
  mode: 'auto' | 'deterministic' | 'ai' | 'compare',
): Promise<void> {
  const input = page.locator(`#mode-${mode}`)
  if ((await input.count()) === 0) return
  if (await input.isChecked()) return
  await page.locator(`label[for="mode-${mode}"]`).click()
  await expect(input, `the ${mode} mode did not become selected`).toBeChecked()
}

/**
 * Answer `/api/config` with the server's own response, modified.
 *
 * Three specs had their own copy of this, and all three were fragile in
 * the same way: the application asks for its configuration on mount and
 * can ask again -- after a reset, or while a test is being torn down --
 * and `route.fetch()` hands back an `APIResponse` owned by a request
 * context that teardown disposes. A late request then failed with
 * `apiResponse.json: Response has been disposed`, which Playwright
 * reported as the *test* failing. It took out Chromium's `compareSetup`
 * once and WebKit's once, hours apart, for a request nothing was waiting
 * on.
 *
 * So there is one handler, it tolerates its own context going away, and a
 * page that is closing gets the real configuration rather than an error.
 * Nothing is weakened by that: a closing page has no selector left to
 * assert on.
 */
export async function routeConfig(
  page: Page,
  mutate: (body: Record<string, any>) => void,
): Promise<void> {
  await page.route('**/api/config', async (route) => {
    try {
      const response = await route.fetch()
      const body = (await response.json()) as Record<string, any>
      mutate(body)
      await route.fulfill({ response, json: body })
    } catch {
      await route.fallback().catch(() => {})
    }
  })
}

/**
 * Headers for a response this suite is answering, with the payload behind
 * it registered in the same breath.
 *
 * A spec that fulfils a billed route by hand -- `dualmode.spec.ts`'s quota
 * 429, `accessibility.spec.ts`'s refused AI run -- must say so, or the
 * accounting counts it as the container answering and the build fails on a
 * ceiling nobody reached. Registering and declaring in one call is the
 * only way to make the two impossible to get out of step.
 */
export function fixtureHeaders(
  payload: unknown,
  from: FixtureSource = 'committed',
): Record<string, string> {
  return { [SOURCE_HEADER]: from, [CAPTURE_HEADER]: registerPayload(payload, from) }
}

/** `POST /api/analyses` and `POST /api/comparisons`, the two that cost. */
const BILLED = /\/api\/(analyses|comparisons)$/

/**
 * One sequence per billed request, unique across worker processes.
 *
 * A plain counter was not: Playwright starts a fresh worker process
 * whenever the worker fixtures a file needs change, and each one began
 * again at 1 -- so three processes produced three requests numbered 1 and
 * the guard reported them as duplicates of each other. The pid makes the
 * identity the thing it has to be, which is unique within the engine's
 * ledger rather than within one process's memory.
 */
let counter = 0
const nextSequence = () => `${process.pid}-${(counter += 1)}`

/** The sha of a payload, as both the ledger and the headers name it. */
export function payloadSha(payload: unknown): string {
  const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonical)
    if (value && typeof value === 'object') {
      return Object.fromEntries(
        Object.keys(value as Record<string, unknown>)
          .sort()
          .map((key) => [key, canonical((value as Record<string, unknown>)[key])]),
      )
    }
    return value
  }
  return createHash('sha256').update(JSON.stringify(canonical(payload))).digest('hex').slice(0, 12)
}

/**
 * Register a payload the suite is about to serve, so a replay of it can be
 * reconciled against the thing it replays.
 *
 * `capture` means a real server response was remembered; `committed` means
 * a payload that lives in the repository. The guard refuses a fulfilled
 * response whose sha was never registered, which is what makes "every
 * replay references something real" a property of the artifact rather than
 * a claim in a comment.
 */
export function registerPayload(payload: unknown, from: FixtureSource): string {
  const sha = payloadSha(payload)
  ledger(from === 'capture' ? 'capture' : 'fixture', sha, undefined, { sha, from })
  return sha
}

/**
 * A billed request the harness issued itself, rather than the page.
 *
 * Compare's route handler fulfils `POST /api/comparisons` and issues the
 * deterministic `POST /api/analyses` through `route.fetch`, which does not
 * surface as page traffic -- so `watchTraffic` cannot see it and the one
 * analysis a Compare scenario really costs would go unrecorded. Recorded
 * here, at the only point where the request exists, with the same
 * sequence/response shape everything else reconciles on.
 */
export function ledgerHarnessRequest(path: string, status: number, detail: string): void {
  const seq = nextSequence()
  let project: string | undefined
  let where: string | undefined
  try {
    project = test.info().project.name
    where = test.info().titlePath.join(' \u203a ')
  } catch {
    // Reported by the guard as unattributable.
  }
  ledger('request', `${seq} POST ${path}`, project, { seq, path, test: where })
  ledger('response', `${seq} ${status} server ${path}`, project, {
    seq,
    status,
    source: 'server',
    capture: null,
    path,
    test: where,
    detail,
  })
}

/**
 * Count what the page actually sent, not what the test meant to send.
 *
 * The first version of this derived "real run" from a `Set` of pages that
 * had a fixture route installed, cleared by the `release()` the route
 * handler returned. `terminalStates.spec.ts` unrouted by pattern instead of
 * calling `release()`, so the route went away and the marker did not -- and
 * every later real analysis in that worker was recorded as a free replay.
 * Two of them were: `visualReview`'s "a real report holds at every approved
 * width" and `timeline`'s refused run -- enough to put the job over its
 * analysis budget while the guard reported it passing. The suite lied to
 * its own accounting and the accounting believed it. (The figures are in
 * `e2e/UPLOADS.md`, beside the runs they were taken from; they are not
 * repeated here, because a number in a comment is a number nothing
 * re-measures.)
 *
 * So accounting is a property of the network boundary. Every billed POST
 * gets a sequence number on its way out and exactly one response entry on
 * its way back, classified by a header the server cannot send. Nothing a
 * test does -- forgetting a cleanup, clicking "Run analysis" instead of
 * calling `ask()`, installing a route in a `beforeAll` -- can change the
 * classification, because none of it is consulted.
 */
export function watchTraffic(page: Page): void {
  const seen = new WeakMap<Request, { seq: string; project: string; test: string }>()

  page.on('request', (request) => {
    if (request.method() !== 'POST') return
    const path = new URL(request.url()).pathname
    if (!BILLED.test(path)) return
    const seq = nextSequence()
    let project = 'unattributed'
    let where = 'outside a test'
    try {
      project = test.info().project.name
      where = test.info().titlePath.join(' \u203a ')
    } catch {
      // Recorded as unattributable, which the guard fails on.
    }
    seen.set(request, { seq, project, test: where })
    ledger('request', `${seq} POST ${path}`, project, { seq, path, test: where })
  })

  page.on('response', (response) => {
    const request = response.request()
    const path = new URL(response.url()).pathname

    /*
     * A 429 the *container* issued. `dualmode.spec.ts` fulfils one itself
     * to assert how a quota refusal reads, and counting that as a ceiling
     * being reached would fail the build on a test doing its job.
     */
    if (response.status() === 429 && response.headers()[SOURCE_HEADER] === undefined) {
      ledger('refused', `429 ${request.method()} ${path}`, undefined, { path })
    }

    if (request.method() !== 'POST' || !BILLED.test(path)) return
    const origin = seen.get(request)
    const headers = response.headers()
    const declared = headers[SOURCE_HEADER]
    /*
     * Absent means the container answered. Present means this suite did,
     * and the value says with what. An unrecognised value is neither, and
     * the guard refuses it rather than guessing which side of the budget
     * it belongs on.
     */
    const source = declared === undefined ? 'server' : declared
    ledger(
      'response',
      `${origin?.seq ?? '?'} ${response.status()} ${source} ${path}`,
      origin?.project,
      {
        seq: origin?.seq ?? null,
        status: response.status(),
        source,
        capture: headers[CAPTURE_HEADER] ?? null,
        path,
        test: origin?.test,
      },
    )
  })
}

/**
 * Kept as the name the fixtures used to call, now that it watches
 * everything rather than only the refusals.
 */
export const watchForRefusals = watchTraffic

/**
 * Render a finished run from a committed payload, with no analysis at all.
 *
 * A terminal state -- refused, no findings, withheld, quota-stopped,
 * failed, cancelled -- is produced by *answering* a finished run with a
 * fixture. The earlier version still started a real analysis to obtain a
 * run id and then threw its result away, which cost one of the container's
 * 200 analyses per IP per hour for every cell of a 24-cell matrix.
 *
 * So the POST is answered too. The application gets a run id, polls it, and
 * receives the fixture; the engine is never asked to compute anything. That
 * is honest about what these tests are: assertions about how a state
 * *renders*, not about how it is reached. The tests that are about reaching
 * one -- `app.spec.ts`'s refusal, `timeline.spec.ts`'s stopped stage --
 * still drive the real engine.
 *
 * Returns a function that removes both routes. A route left installed would
 * answer the next test on a shared session with this test's payload.
 */
export async function answerRunWith(
  page: Page,
  payload: Record<string, unknown>,
  /**
   * Where the payload came from. `committed` is a fixture in the
   * repository; `capture` is a real response this run remembered. The
   * guard holds both to the same rule -- a replay must name a payload that
   * was registered -- and keeps them apart in the accounting, because a
   * committed fixture is evidence about *rendering* and a capture is
   * evidence about rendering something the engine really produced.
   */
  from: FixtureSource = 'committed',
): Promise<() => Promise<void>> {
  const runId = `run_fixture_${Math.random().toString(36).slice(2, 10)}`
  const sha = registerPayload(payload, from)
  const headers = { [SOURCE_HEADER]: from, [CAPTURE_HEADER]: sha }

  await page.route('**/api/analyses', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback()
      return
    }
    const body = route.request().postDataJSON() as {
      session_id?: string
      question?: string
    }
    await route.fulfill({
      status: 202,
      headers,
      json: {
        run_id: runId,
        session_id: body.session_id ?? 'session_fixture',
        question: body.question ?? '',
      },
    })
  })

  await page.route('**/api/analyses/*', async (route) => {
    await route.fulfill({ status: 200, headers, json: { ...payload, run_id: runId } })
  })

  return async () => {
    await page.unroute('**/api/analyses/*')
    await page.unroute('**/api/analyses')
  }
}

/**
 * Put a live page into `theme`, through the control a reader would use.
 *
 * Seeding `localStorage` before load was the earlier approach and it cannot
 * work on a shared session: the dataset lives in React state, so reloading
 * to pick up the seed would throw it away and force another upload. The
 * toggle is the product's own affordance and needs no reload.
 */
export async function setTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
  const html = page.locator('html')
  if ((await html.getAttribute('data-theme')) === theme) return
  await page
    .getByRole('button', { name: new RegExp(`Switch to ${theme} theme`, 'i') })
    .click()
  await expect(html).toHaveAttribute('data-theme', theme)
}

/**
 * Give the server its upload session back.
 *
 * A browser context closing does **not** free a session: the server holds
 * it until the capability is used to delete it or the TTL expires, and the
 * TTL outlives a CI run by a wide margin. Every Playwright test gets a
 * fresh context and therefore a fresh capability, so without this each
 * upload leaves one more session resident, and the twenty-fifth is refused
 * with "the demo is at capacity for uploaded datasets". That is exactly how
 * Chromium failed on PR #46.
 *
 * "End session" is the product's own control -- it calls
 * `DELETE /api/datasets/{id}` with the capability cookie. No test-only
 * endpoint is involved, and a suite that frees what it takes is behaving
 * like the client it is standing in for.
 *
 * Tolerant of a page with no session: callers put this in `afterAll`, where
 * an earlier failure may have left nothing to close.
 */
export async function endSession(page: Page, project?: string): Promise<void> {
  if (page.isClosed()) return
  const control = page.getByRole('button', { name: 'End session' })
  try {
    // Whatever the last test left open. A side sheet puts a scrim over the
    // header, so the control is in the document but not clickable -- and
    // Playwright waits for actionability, which in a fixture teardown is a
    // 120-second hang with no test to attribute it to. This happened: the
    // last terminal-state test leaves the evidence drawer open.
    await page.keyboard.press('Escape').catch(() => {})
    if ((await control.count()) === 0) return
    const deleted = page.waitForResponse(
      (response) =>
        /\/api\/datasets\/[^/]+$/.test(new URL(response.url()).pathname) &&
        response.request().method() === 'DELETE',
      { timeout: 15_000 },
    )
    await control.first().click({ timeout: 10_000 })
    // The control disappears with the session it ended, which is the
    // application's own signal that the DELETE resolved.
    await expect(control).toHaveCount(0, { timeout: 15_000 })
    // By id, so the guard reconciles *which* session came back rather than
    // only how many did.
    const session = new URL((await deleted).url()).pathname.split('/').pop() ?? null
    ledger('close', 'end session', project, { session })
  } catch {
    // A session that cannot be closed is a capacity problem for the *next*
    // test, and `check-upload-budget.mjs` is what reports it: failing here
    // would blame teardown for a defect somewhere else.
  }
}

/**
 * Start an analysis from the composer.
 *
 * The run button is disabled while the app is busy or the field is empty,
 * so a plain `click()` waits the full test timeout and then reports only
 * "element is not enabled" -- which says nothing about *why*.
 *
 * This has happened twice on Firefox, deep in a full suite run, in the
 * role-confirmation describe, and it does not reproduce in isolation (six
 * consecutive clean attempts against the same server). Rather than leave a
 * 120-second timeout with no evidence, this fails in ten seconds and
 * reports the state that would explain it: what the field actually holds,
 * whether a modal sheet is still open over the page, and which phase the
 * app thinks it is in.
 */
export async function ask(page: Page, question: string): Promise<void> {
  /*
   * This does not count the run.
   *
   * It used to, by reading a `Set` of pages with a fixture route
   * installed -- and a spec that unrouted by pattern instead of calling the
   * `release()` it was handed left the marker set, so every later real
   * analysis in that worker was recorded as free. `watchTraffic` counts the
   * request at the network boundary instead, which also means a test that
   * clicks "Run analysis" without coming through here is counted anyway.
   */
  const field = page.getByLabel('Business question')
  /*
   * Bounded, and with a sentence attached.
   *
   * While a run is in flight the question field is not in the document at
   * all, so `fill()` -- which has no timeout of its own and falls back to
   * the test's -- waits two minutes and is then reported as "Target page,
   * context or browser has been closed", because that is what the timeout
   * teardown does to the context. The name of the test that actually
   * caused it does not appear anywhere in that message. A bounded wait
   * turns the same condition into a named failure in fifteen seconds.
   */
  await expect(
    field,
    'the composer was not on the page: something left a run in flight on ' +
      'this session, or the reset did not return it to the composer',
  ).toBeVisible({ timeout: 15_000 })
  await field.fill(question)
  /*
   * And the value stuck.
   *
   * The composer is a controlled textarea, so a render landing just after
   * the fill can revert it to whatever the component's state still holds.
   * Closing a side sheet is one way to produce that render -- focus
   * restoration happens asynchronously -- and the result was an empty
   * field, a disabled run button, and a diagnostic that correctly reported
   * both without saying why.
   *
   * Asserted and refilled rather than waited on: the question is either in
   * the field or it is not, and that is a state to check rather than a
   * duration to guess at.
   */
  await expect
    .poll(
      async () => {
        if ((await field.inputValue()) !== question) await field.fill(question)
        return field.inputValue()
      },
      {
        timeout: 10_000,
        message: 'the composer did not keep the question it was given',
      },
    )
    .toBe(question)

  /*
   * By test id, not by label.
   *
   * The action is named per mode now -- "Run governed analysis",
   * "Run deterministic analysis", "Run with AI", "Compare both
   * planners" -- so a name regex here would have to be kept in step with
   * four strings in a component, in every spec that drives a run.
   * `composer.spec.ts` asserts the accessible name per mode, which is the
   * assertion that belongs to a test rather than to navigation.
   */
  const run = page.getByTestId('run')
  try {
    await expect(run).toBeEnabled({ timeout: 10_000 })
  } catch {
    const state = await page.evaluate(() => ({
      field: (document.querySelector('#composer-field') as HTMLTextAreaElement | null)?.value ?? null,
      phase: document.body.dataset.phase ?? null,
      sheetOpen: Boolean(
        document.querySelector('[data-testid="schema-sheet"], [data-testid="evidence-drawer"], [data-testid="compare-evidence-drawer"]'),
      ),
      notice: document.querySelector('.notice.error')?.textContent?.trim() ?? null,
    }))
    throw new Error(
      `the run button stayed disabled: ${JSON.stringify(state)}`,
    )
  }

  await run.click()
}

/** Everything a page could be storing client-side, as one string. */
export async function clientSideState(page: Page): Promise<string> {
  const storage = await page.evaluate(() => {
    const dump = (s: Storage) =>
      Object.keys(s)
        .map((k) => `${k}=${s.getItem(k)}`)
        .join('|')
    return {
      local: dump(window.localStorage),
      session: dump(window.sessionStorage),
      cookie: document.cookie,
      url: window.location.href,
    }
  })
  const html = await page.content()
  return [storage.local, storage.session, storage.cookie, storage.url, html].join('\n')
}

/** The session capability, read from the browser context's cookie jar. */
export async function capability(page: Page): Promise<string> {
  const cookies = await page.context().cookies()
  const session = cookies.find((c) => c.name === 'aae_session')
  expect(session, 'no aae_session cookie was set').toBeTruthy()
  return session!.value
}

/** Open the demo warehouse through the API, returning its session id. */
export async function openDemoViaApi(request: APIRequestContext): Promise<string> {
  const response = await request.post('/api/datasets/demo')
  expect(response.status()).toBe(200)
  return (await response.json()).session_id as string
}

/**
 * Open the application.
 *
 * Every test navigated with `page.goto("/")`, whose default `waitUntil` is
 * `load` -- and on Firefox that intermittently never resolves. Twice in
 * consecutive CI runs a test burned its entire 120s budget inside
 * `page.goto`, "waiting until load", on two *different* tests: once in
 * `foundation.spec.ts`, once in `informationArchitecture.spec.ts`. The
 * trace from the second one is unambiguous about what had happened by
 * then: the document, its script, its stylesheet and the app's own
 * `/api/config` had all returned 200, and the page snapshot shows the
 * banner, the theme toggle and all four progress steps rendered. The
 * application was up and interactive; only `load` was outstanding. Which
 * request held it cannot be named, because a trace has no entry for a
 * request that never received a response.
 *
 * So no test waits on `load` any more. `load` means "every subresource
 * settled", which is not what any of these tests assert about, and it put
 * 63 call sites one stalled request away from a two-minute hang. The
 * deterministic ready state is the application's own: the shell mounted.
 * That is both narrower and a stronger signal -- `load` can fire before
 * React has rendered anything.
 */
export async function openApp(page: Page): Promise<void> {
  // `commit`, which resolves as soon as the response for the navigation is
  // received. Not `load`, and not `domcontentloaded` either: both were
  // tried and both hung on Firefox.
  //
  // The second trace is what settles it. With `domcontentloaded` the
  // navigation still timed out -- and the report shows the document, the
  // stylesheet, the bundle and the app's own `/api/config` all returned
  // **200**, with the page snapshot and a 128KB screenshot showing the
  // banner, the theme toggle and all four progress steps rendered. The
  // server served everything and the application was running. What never
  // arrived was Playwright's lifecycle event for the navigation.
  //
  // So neither a browser lifecycle *completion* event nor `commit` is a
  // reliable gate here. Trigger navigation inside the page instead of with
  // `page.goto`: Playwright makes locator assertions wait for an in-flight
  // `page.goto`, which quietly turned the earlier Promise.race back into a
  // lifecycle wait. A browser that reports navigation late still reaches the
  // same app shell; a dead server cannot, so it fails through the bounded
  // shell wait instead of being silently accepted.
  const baseUrl = process.env.AAE_E2E_BASE_URL ?? 'http://127.0.0.1:8000';
  await page.evaluate((url) => window.location.assign(url), new URL('/', baseUrl).href);
  // `app-shell` is on the shell root, which React renders unconditionally,
  // so its presence means the bundle parsed, executed and mounted -- not
  // merely that bytes arrived. `index.html` contains only `<div id="root">`
  // and the module script, so the marker cannot exist before mount.
  //
  // Not `getByRole("banner")`: the provenance drawer also renders a
  // `<header>`, so that locator can match twice and fail strict mode for a
  // reason unrelated to readiness. Not `<body>` or a piece of copy either
  // -- one exists before React runs and the other moves when wording does.
  //
  // Bounded, so a server that never answers fails the test instead of
  // hanging it: 30s on the navigation and 20s on the app-shell wait. The
  // shell renders even when `/api/config` fails,
  // because the error state is drawn inside it, so an API failure reaches
  // the test's own assertions rather than stalling here.
  const shell = page.getByTestId('app-shell');
  await expect(shell).toBeVisible({ timeout: 20_000 });
}

/**
 * A locator for everything matching `selector` *on screen*.
 *
 * Step H gave the report a print appendix: `AnswerReport` renders the
 * evidence drawer's contents a second time inside a `hidden` section that
 * only `@media print` reveals, so that a reader who prints a report is not
 * given less than a reader who clicks through it.
 *
 * The consequence is that the run timeline, the planning audit, the
 * activity trace and the accepted contract each exist twice in the
 * document. A bare `getByTestId` for one of them now matches two elements
 * and fails Playwright's strict mode -- correctly, because the question
 * "is this on the canvas?" has stopped being the same question as "is this
 * in the DOM?".
 *
 * This is the first one. Where the drawer is the subject, scope to
 * `evidence-drawer` instead; the appendix is outside it.
 */
export function onCanvas(page: Page, selector: string) {
  return page.locator(`${selector}:not([data-print-appendix] *)`);
}

/** The same, by test id. */
export function canvasTestId(page: Page, testId: string) {
  return onCanvas(page, `[data-testid="${testId}"]`);
}

/** Inside the open evidence drawer, which the print appendix is not. */
export function inDrawer(page: Page, testId: string) {
  return page.getByTestId("evidence-drawer").getByTestId(testId);
}
