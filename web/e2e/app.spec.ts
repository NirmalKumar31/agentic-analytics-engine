import { expect, test } from '@playwright/test'

import {
  ask,
  capability,
  clientSideState,
  recordingButtons,
  sampleCsv,
  uploadFile,
  suggestedQuestions,
  waitForReport,
} from './helpers'

/**
 * The flows a visitor actually performs, in a real browser, against a real
 * server. Unit tests render components against fixtures; these are the only
 * checks that the assembled thing works.
 */

test.describe('the page a visitor lands on', () => {
  test('loads and states which execution mode produced what is on screen', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByRole('button', { name: /Commerce demo warehouse/ })).toBeVisible()
    // One of the three modes, named. Not a generic "ready".
    await expect(page.locator('.mode-pill')).toHaveText(
      /Recorded|Deterministic live|AI live/,
    )
  })

  test('offers the three committed recordings', async ({ page }) => {
    await page.goto('/')
    const recordings = recordingButtons(page)
    await expect(recordings).toHaveCount(3)
    await expect(page.getByText(/not a language model/i)).toBeVisible()
  })
})

test.describe('a recorded run', () => {
  test('renders a report, provenance and the MCP trace', async ({ page }) => {
    await page.goto('/')
    await recordingButtons(page).first().click()

    await waitForReport(page)
    await expect(page.locator('.mode-pill')).toHaveText('Recorded')

    const findings = page.locator('article.finding')
    expect(await findings.count()).toBeGreaterThan(0)

    // Provenance: finding -> task -> MCP call -> result cells.
    await page.getByRole('button', { name: 'Show work →' }).first().click()
    const drawer = page.getByRole('dialog', { name: 'How this was derived' })
    await expect(drawer).toBeVisible()
    await expect(drawer.getByRole('heading', { name: 'Finding' })).toBeVisible()
    await expect(drawer.getByRole('heading', { name: 'Analytical task' })).toBeVisible()
    await expect(drawer.getByRole('heading', { name: 'Agent and tool path' })).toBeVisible()
    await expect(drawer.getByRole('heading', { name: 'Referenced cells' })).toBeVisible()
    await expect(drawer.getByRole('heading', { name: 'Dataset fingerprint' })).toBeVisible()
    await expect(drawer.getByText(/MCP:/).first()).toBeVisible()

    await drawer.getByRole('button', { name: 'Close' }).click()
    await expect(drawer).toBeHidden()
  })
})

test.describe('the demo warehouse', () => {
  test('answers a built-in question with supported findings', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: /Commerce demo warehouse/ }).click()
    await expect(page.getByRole('heading', { name: 'Ask' })).toBeVisible()

    // Deterministic mode must say that interpretation is rule-based.
    const badge = await page.locator('.mode-pill').textContent()
    if (badge?.includes('Deterministic live')) {
      await expect(page.getByTestId('interpretation-notice')).toContainText(
        /rule-based/i,
      )
    }

    await suggestedQuestions(page).first().click()
    await page.getByRole('button', { name: 'Run analysis' }).click()

    // Progress is visible while it runs.
    await expect(page.getByRole('heading', { name: 'Analysis' }).first()).toBeVisible()
    await waitForReport(page)

    const findings = page.locator('article.finding')
    expect(await findings.count()).toBeGreaterThan(0)
    // Every finding on screen carries its verification verdict.
    const supported = page.locator('article.finding .tag.supported')
    expect(await supported.count()).toBeGreaterThan(0)

    await page.getByRole('button', { name: 'Show work →' }).first().click()
    await expect(page.getByRole('dialog', { name: 'How this was derived' })).toBeVisible()
  })
})

test.describe('uploading a file', () => {
  test('profiles it, answers a mappable question, and refuses an unmappable one', async ({
    page,
  }) => {
    await page.goto('/')
    await uploadFile(page, 'e2e-sales.csv', sampleCsv())

    // The dataset understanding step, marked inferred rather than governed.
    await expect(page.getByText('Roles are inferred from column types')).toBeVisible()

    await ask(page, 'What is the total revenue by region?')
    await waitForReport(page)
    const answered = page.locator('article.finding')
    expect(await answered.count()).toBeGreaterThan(0)
    await expect(page.locator('article.finding').first()).toContainText(/region|revenue/i)

    // A question the rules cannot map must not be answered anyway.
    await page.getByRole('button', { name: 'Start over' }).click()
    await ask(page, 'Explain the root cause of customer churn in this file')
    await waitForReport(page)
    // Stated in more than one place -- the activity log and the report's
    // limitations -- so this asserts presence, not uniqueness.
    await expect(page.getByText(/could not be mapped/i).first()).toBeVisible()
    await expect(
      page.locator('li', { hasText: /could not be mapped/i }).first(),
      'the report must record the refusal, not only the activity log',
    ).toBeVisible()
  })

  test('refuses an invalid file with a readable message', async ({ page }) => {
    await page.goto('/')
    await page.locator('input[type="file"]').setInputFiles({
      name: 'not-data.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('\x89PNG\r\n\x1a\n binary rubbish, definitely not a table'),
    })
    const notice = page.locator('.notice.error')
    await expect(notice).toBeVisible()
    // A message for a person, not a stack trace or a host path.
    await expect(notice).not.toContainText('Traceback')
    await expect(notice).not.toContainText('/app/')
  })
})

test.describe('the session boundary', () => {
  // `page.request` rather than the `request` fixture: the fixture has its
  // own cookie jar, so it would 404 for want of the capability and the test
  // would pass without proving anything.
  test('ending a session makes its dataset and run unreachable', async ({ page }) => {
    await page.goto('/')
    await uploadFile(page, 'ending.csv', sampleCsv(80))

    // The public handle, which the shell exposes for exactly this check.
    const handle = await page.locator('.shell').getAttribute('data-session-id')
    expect(handle).toBeTruthy()
    const before = await page.request.get(`/api/datasets/${handle}`)
    expect(before.status(), 'the dataset should be reachable before deletion').toBe(200)

    await page.getByRole('button', { name: 'End session' }).click()
    // The dataset chooser, not a heading: "Dataset" is a prefix of "Dataset
    // understanding", which is still on screen for a frame after the click.
    await expect(page.getByRole('button', { name: /Commerce demo warehouse/ })).toBeVisible()
    await expect(page.getByRole('button', { name: 'End session' })).toBeHidden()

    const after = await page.request.get(`/api/datasets/${handle}`)
    expect(after.status(), 'the dataset should be gone after deletion').toBe(404)
  })

  test('one browser cannot reach another browser dataset', async ({ browser }) => {
    const alice = await browser.newContext()
    const bob = await browser.newContext()
    try {
      const alicePage = await alice.newPage()
      await alicePage.goto('/')
      await uploadFile(
        alicePage,
        'alice.csv',
        'region,revenue\nALICEZONE,100\nALICEZONE,200\n',
      )

      const aliceCookie = (await alice.cookies()).find((c) => c.name === 'aae_session')
      expect(aliceCookie).toBeTruthy()

      // Bob has his own capability and must not be able to use Alice's handle
      // even if he learns it.
      const bobPage = await bob.newPage()
      await bobPage.goto('/')
      const aliceHandle = await alicePage.locator('.shell').getAttribute('data-session-id')
      expect(aliceHandle).toBeTruthy()
      const response = await bob.request.get(`/api/datasets/${aliceHandle}`)
      expect(response.status(), "Bob reached Alice's dataset").toBe(404)
      const bobCookie = (await bob.cookies()).find((c) => c.name === 'aae_session')
      expect(bobCookie?.value).not.toBe(aliceCookie?.value)
    } finally {
      await alice.close()
      await bob.close()
    }
  })
})

test.describe('what the browser can see', () => {
  test('the session capability is not readable from the page', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: /Commerce demo warehouse/ }).click()
    await expect(page.getByRole('heading', { name: 'Ask' })).toBeVisible()

    const secret = await capability(page)
    expect(secret.length).toBeGreaterThan(20)

    const visible = await clientSideState(page)
    expect(visible, 'the capability leaked into the page').not.toContain(secret)
  })

  test('the session cookie is HttpOnly, and Secure when served over TLS', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('button', { name: /Commerce demo warehouse/ }).click()
    await expect(page.getByRole('heading', { name: 'Ask' })).toBeVisible()

    const cookie = (await page.context().cookies()).find((c) => c.name === 'aae_session')
    expect(cookie).toBeTruthy()
    expect(cookie!.httpOnly).toBe(true)
    expect(cookie!.path).toBe('/')
    if (new URL(page.url()).protocol === 'https:') {
      expect(cookie!.secure, 'a hosted deployment must set Secure').toBe(true)
    }
  })

  test('private API responses are not cacheable', async ({ page, request }) => {
    await page.goto('/')
    const response = await request.get('/api/config')
    expect(response.headers()['cache-control']).toContain('no-store')
    expect(response.headers()['x-content-type-options']).toBe('nosniff')
  })
})

test.describe('the public MCP endpoint', () => {
  test('matches the deployment policy while the website keeps working', async ({
    page,
    request,
    baseURL,
  }) => {
    const response = await request.post('/mcp', {
      data: { jsonrpc: '2.0', id: 1, method: 'initialize', params: {} },
      headers: { 'Content-Type': 'application/json' },
      failOnStatusCode: false,
    })
    const loopback = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])/.test(baseURL ?? '')
    if (loopback) {
      // A loopback binding is local development; the SDK's own
      // localhost-only protection applies and the endpoint is served.
      expect(response.status()).not.toBe(503)
    } else {
      // A network binding with no Host allow-list withdraws the transport.
      expect(response.status()).toBe(503)
    }

    // Either way the site itself analyses, because its agents reach the
    // same MCP server over the in-process transport.
    await page.goto('/')
    await page.getByRole('button', { name: /Commerce demo warehouse/ }).click()
    await suggestedQuestions(page).first().click()
    await page.getByRole('button', { name: 'Run analysis' }).click()
    await waitForReport(page)
    expect(await page.locator('article.finding').count()).toBeGreaterThan(0)
  })
})
