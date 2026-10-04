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

/** The suggested-question buttons, in the Ask panel. */
export function suggestedQuestions(page: Page) {
  return page
    .locator('section.panel', { has: page.getByRole('heading', { name: 'Ask' }) })
    .locator('.example-list button.example')
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
  // The schema inspector is the signal that profiling finished. It used to
  // be a panel with a "Dataset understanding" heading and is now a closed
  // `<details>`, so the heading no longer exists -- waiting for it timed out
  // on every upload test. The test id is a stronger target than the heading
  // was: it identifies the inspector itself rather than a string that two
  // panels could both contain.
  const understanding = page.getByTestId('schema-inspector')
  const notice = page.locator('.notice.error')
  await expect(understanding.or(notice).first()).toBeVisible({ timeout: 30_000 })
  if (await notice.isVisible()) {
    const text = (await notice.textContent()) ?? ''
    throw new Error(`upload was rejected: ${text.trim()}`)
  }
}

/** Start an analysis from the Ask panel. */
export async function ask(page: Page, question: string): Promise<void> {
  await page.getByLabel('Business question').fill(question)
  await page.getByRole('button', { name: 'Run analysis' }).click()
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
