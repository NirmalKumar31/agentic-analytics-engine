import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

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
  await expect(page.getByRole('heading', { name: 'Key findings' })).toBeVisible({
    timeout: 90_000,
  })
}

/**
 * The recorded-run buttons, in the Dataset panel.
 *
 * Both panels render `.example-list`, so an unscoped selector picks the
 * wrong one once a dataset is open -- and silently opens a recording
 * instead of asking a question.
 */
export function recordingButtons(page: Page) {
  return page
    .locator('section.panel', {
      // Exact: "Dataset" is a prefix of "Dataset understanding".
      has: page.getByRole('heading', { name: 'Dataset', exact: true }),
    })
    .locator('.example-list button.example')
}

/** The suggested-question buttons, in the Ask panel. */
export function suggestedQuestions(page: Page) {
  return page
    .locator('section.panel', { has: page.getByRole('heading', { name: 'Ask' }) })
    .locator('.example-list button.example')
}

/**
 * Upload a file and wait for the profile, or skip if the server is
 * rate-limiting.
 *
 * The public deployment allows a handful of uploads per address per hour.
 * A suite that uploads several times per browser will legitimately be
 * turned away, and a refused upload is the abuse control working -- so the
 * test skips with the reason rather than reporting a product failure.
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
  const understanding = page.getByRole('heading', { name: 'Dataset understanding' })
  const notice = page.locator('.notice.error')
  await expect(understanding.or(notice).first()).toBeVisible({ timeout: 30_000 })
  if (await notice.isVisible()) {
    const text = (await notice.textContent()) ?? ''
    test.skip(
      /too many uploads|at capacity/i.test(text),
      `server is rate-limiting uploads: ${text.trim()}`,
    )
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
