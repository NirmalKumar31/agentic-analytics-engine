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
    // The test must not inherit a developer's local cloud configuration.
    // It exercises the unavailable contract, so make that server response
    // explicit rather than relying on the process environment.
    await page.route('**/api/config', async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      body.capabilities.modes = body.capabilities.modes.map((mode: { mode: string }) =>
        mode.mode === 'ai'
          ? {
              ...mode,
              available: false,
              reason: 'ai_disabled',
              message: 'AI Analytics is turned off for this deployment.',
            }
          : mode,
      )
      body.capabilities.compare_available = false
      await route.fulfill({ response, json: body })
    })
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

  test('Show work resolves evidence from the pane the visitor clicked', async ({ page }) => {
    await page.route('**/api/config', async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      body.capabilities.modes = body.capabilities.modes.map((mode: { mode: string }) =>
        mode.mode === 'ai' ? { ...mode, available: true, reason: '', message: '' } : mode,
      )
      body.capabilities.compare_available = true
      body.capabilities.ai_limits = {
        runs_per_session: 3,
        max_model_calls_per_run: 24,
        max_runtime_seconds: 180,
      }
      await route.fulfill({ response, json: body })
    })

    await page.route('**/api/comparisons', async (route) => {
      const request = route.request().postDataJSON() as { session_id: string; question: string }
      const started = await route.fetch({
        url: new URL('/api/analyses', page.url()).toString(),
        method: 'POST',
        postData: JSON.stringify({ ...request, mode: 'deterministic' }),
        headers: { 'content-type': 'application/json' },
      })
      const { run_id } = (await started.json()) as { run_id: string }
      await route.fulfill({
        status: 202,
        json: {
          comparison_id: 'cmp_provenance',
          session_id: request.session_id,
          question: request.question,
          deterministic_run_id: run_id,
          ai_run_id: 'run_ai_provenance',
        },
      })
    })

    await page.route('**/api/analyses/run_ai_provenance', async (route) => {
      await route.fulfill({
        status: 200,
        json: {
          run_id: 'run_ai_provenance',
          session_id: 'session_ai',
          question: 'What is total revenue?',
          status: 'completed',
          created_at: Date.now() / 1000,
          mode: 'ai',
          provider_kind: 'cloud',
          dataset: {
            dataset_kind: 'demo',
            source: 'test',
            dataset_fingerprint: 'sha256:ai-pane-only',
            tables: [],
            metrics_available: [],
          },
          report: null,
          findings: [
            {
              finding_id: 'f1',
              text: 'AI PANE ONLY: total revenue is 123.',
              kind: 'calculated_fact',
              task_id: 'task_ai',
              result_ids: ['res_ai'],
              evidence_cells: [
                { result_id: 'res_ai', row: 0, column: 'total_revenue', value: 123, label: 'AI total' },
              ],
              metric_ids: [],
              verification_status: 'supported',
              verifier_reason: 'Test fixture.',
              numeric_check: null,
              claimed_change: null,
            },
          ],
          rejected: [],
          charts: [],
          tasks: [
            {
              task_id: 'task_ai',
              status: 'succeeded',
              findings: [],
              result_ids: ['res_ai'],
              tool_calls: 1,
              error: null,
              notes: [],
            },
          ],
          results: {
            res_ai: {
              result_id: 'res_ai',
              tool_name: 'aggregate_for_question',
              task_id: 'task_ai',
              sql: 'SELECT 123 AS total_revenue',
              columns: ['total_revenue'],
              rows: [[123]],
              row_count: 1,
              truncated: false,
              dataset_fingerprint: 'sha256:ai-pane-only',
              duration_ms: 1,
              parameters: {},
              warnings: [],
              statistical_result: null,
            },
          },
          mcp_trace: [],
          events: [],
          metrics: {},
          stopped_reason: '',
        },
      })
    })

    await openDemo(page)
    await page.getByRole('radio', { name: /^Compare Both/ }).click()
    await page.getByLabel('Business question').fill('What is total revenue?')
    await page.getByRole('button', { name: /Run both/ }).click()

    const aiPane = page.getByRole('region', { name: 'AI Analytics' })
    await expect(aiPane.getByText('AI PANE ONLY: total revenue is 123.')).toBeVisible()
    await aiPane.getByRole('button', { name: 'Show work →' }).click()

    const drawer = page.getByRole('dialog', { name: 'How this was derived' })
    await expect(drawer).toContainText('AI PANE ONLY: total revenue is 123.')
    // The pane owns the evidence even though opaque result ids are no longer
    // shown to a visitor. The deterministic result would have a different
    // total, so this proves resolution from the clicked pane.
    await expect(drawer).toContainText('AI total = 123')
    await expect(drawer).not.toContainText('Deterministic finding')
  })
})
