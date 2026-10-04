import { expect, type APIRequestContext, type Page } from '@playwright/test'

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
  await page.locator('input[type="file"]').setInputFiles({
    name,
    mimeType: 'text/csv',
    buffer: Buffer.from(contents),
  })
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
  const field = page.getByLabel('Business question')
  await field.fill(question)

  const run = page.getByRole('button', { name: /^(Run analysis|Run with AI)/ })
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
