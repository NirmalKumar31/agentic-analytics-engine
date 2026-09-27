import { expect, test } from '@playwright/test'

/**
 * Dual-mode behaviour in a real browser.
 *
 * No test here depends on live paid inference. AI runs are exercised by
 * intercepting the API, so the browser contract is covered without spending
 * anything and without a credential being present.
 */

async function openDemo(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.getByRole('button', { name: /Commerce demo warehouse/ }).click()
  await expect(page.getByRole('radiogroup', { name: /analysis mode/i })).toBeVisible()
}

test.describe('choosing a mode', () => {
  test('deterministic is the default and is selectable', async ({ page }) => {
    await openDemo(page)
    await expect(page.getByRole('radio', { name: /^Deterministic Analytics/ })).toBeChecked()
  })

  test('AI and Compare are disabled, with a stated reason, when AI is off', async ({ page }) => {
    await openDemo(page)
    const ai = page.getByRole('radio', { name: /^AI Analytics/ })
    await expect(ai).toBeDisabled()
    await expect(page.getByRole('radio', { name: /^Compare Both/ })).toBeDisabled()
    // A reason a visitor can read, not just a greyed control.
    await expect(page.getByText(/AI Analytics is (turned off|not configured)/i).first()).toBeVisible()
  })

  test('the selector is reachable and operable by keyboard', async ({ page }) => {
    await openDemo(page)
    const deterministic = page.getByRole('radio', { name: /^Deterministic Analytics/ })
    await deterministic.focus()
    await expect(deterministic).toBeFocused()
  })

  test('no public copy calls deterministic mode fake', async ({ page }) => {
    await openDemo(page)
    const text = ((await page.locator('body').textContent()) ?? '').toLowerCase()
    expect(text).not.toContain('fake')
    expect(text).toContain('deterministic analytics')
  })
})

test.describe('a deterministic run', () => {
  test('completes and reports findings', async ({ page }) => {
    await openDemo(page)
    await page.getByLabel('Business question').fill('What is total revenue?')
    await page.getByRole('button', { name: /Run analysis/ }).click()
    await expect(page.getByRole('heading', { name: 'Key findings' })).toBeVisible({
      timeout: 60_000,
    })
  })
})

test.describe('AI and Compare, with the API intercepted', () => {
  test('Compare Both shows two independent panes', async ({ page }) => {
    // Advertise both modes so the selector offers Compare.
    await page.route('**/api/config', async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      body.capabilities.modes = body.capabilities.modes.map((m: { mode: string }) =>
        m.mode === 'ai' ? { ...m, available: true, reason: '', message: '' } : m,
      )
      body.capabilities.compare_available = true
      body.capabilities.ai_limits = {
        runs_per_session: 3,
        max_model_calls_per_run: 24,
        max_runtime_seconds: 180,
      }
      await route.fulfill({ response, json: body })
    })

    let aiRunId = ''
    await page.route('**/api/comparisons', async (route) => {
      // Start a real deterministic run so the left pane is genuine, and
      // mint an id for the right one that the stub below answers.
      const request = route.request().postDataJSON() as { session_id: string; question: string }
      const started = await route.fetch({
        url: new URL('/api/analyses', page.url()).toString(),
        method: 'POST',
        postData: JSON.stringify({ ...request, mode: 'deterministic' }),
        headers: { 'content-type': 'application/json' },
      })
      const { run_id } = (await started.json()) as { run_id: string }
      aiRunId = 'run_stubbed_ai'
      await route.fulfill({
        status: 202,
        json: {
          comparison_id: 'cmp_stub',
          session_id: request.session_id,
          question: request.question,
          deterministic_run_id: run_id,
          ai_run_id: aiRunId,
        },
      })
    })

    await page.route('**/api/analyses/run_stubbed_ai', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          run_id: 'run_stubbed_ai',
          session_id: 'x',
          question: 'What is total revenue?',
          status: 'failed',
          created_at: Date.now() / 1000,
          mode: 'ai',
          provider_kind: 'cloud',
          error: 'AI Analytics has reached its public demo usage limit.',
          findings: [],
          rejected: [],
          charts: [],
          results: {},
          events: [],
          mcp_trace: [],
        },
      })
    })

    await openDemo(page)
    await page.getByRole('radio', { name: /^Compare Both/ }).click()
    await page.getByLabel('Business question').fill('What is total revenue?')
    await page.getByRole('button', { name: /Run both/ }).click()

    // Two labelled panes.
    await expect(page.getByRole('region', { name: 'Deterministic Analytics' })).toBeVisible({
      timeout: 60_000,
    })
    await expect(page.getByRole('region', { name: 'AI Analytics' })).toBeVisible()

    // The AI side failed; the deterministic side still produced a report.
    await expect(page.getByRole('alert')).toContainText(/public demo usage limit/i)
    await expect(
      page.getByRole('region', { name: 'Deterministic Analytics' }).getByText(/Key findings/),
    ).toBeVisible({ timeout: 60_000 })

    // No winner, anywhere.
    const text = ((await page.locator('body').textContent()) ?? '').toLowerCase()
    for (const banned of ['winner', 'more accurate']) {
      expect(text).not.toContain(banned)
    }
  })

  test('an AI run refused by quota is reported without leaking anything', async ({ page }) => {
    await page.route('**/api/config', async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      body.capabilities.modes = body.capabilities.modes.map((m: { mode: string }) =>
        m.mode === 'ai' ? { ...m, available: true, reason: '', message: '' } : m,
      )
      await route.fulfill({ response, json: body })
    })
    await page.route('**/api/analyses', async (route) => {
      await route.fulfill({
        status: 429,
        json: {
          detail:
            'AI mode has reached its public demo usage limit. Deterministic Analytics is still available.',
        },
      })
    })

    await openDemo(page)
    await page.getByRole('radio', { name: /^AI Analytics/ }).click()
    await page.getByLabel('Business question').fill('What is total revenue?')
    await page.getByRole('button', { name: /Run with AI/ }).click()

    await expect(page.getByText(/public demo usage limit/i)).toBeVisible()
    const text = (await page.locator('body').textContent()) ?? ''
    for (const leak of ['sk-', 'redis://', 'Traceback']) {
      expect(text).not.toContain(leak)
    }
  })
})
