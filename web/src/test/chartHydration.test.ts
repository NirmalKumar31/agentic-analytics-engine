import { describe, expect, it } from 'vitest'

import { hydrateChartSpec } from '../lib/chartHydration'
import { checkChartSpec } from '../lib/chartSafety'
import type { ChartSpec, ResultSnapshot } from '../lib/types'

function snapshot(overrides: Partial<ResultSnapshot> = {}): ResultSnapshot {
  return {
    result_id: 'result_01',
    tool_name: 'aggregate_for_question',
    task_id: 'task_01',
    sql: 'select 1',
    columns: ['business_type', 'total_annual_revenue'],
    rows: [
      ['Retail', 83373290.48],
      ['Ecommerce', 75881961.32],
    ],
    row_count: 2,
    truncated: false,
    dataset_fingerprint: 'fp',
    duration_ms: 3,
    parameters: {},
    warnings: [],
    statistical_result: null,
    ...overrides,
  } as ResultSnapshot
}

/** A chart exactly as the engine publishes one: an encoding and a citation. */
function chart(overrides: Partial<ChartSpec> = {}): ChartSpec {
  return {
    chart_id: 'chart_01',
    title: 'total annual revenue by business type',
    result_id: 'result_01',
    finding_ids: ['finding_01'],
    spec: {
      mark: 'bar',
      encoding: {
        x: { field: 'business_type', type: 'nominal' },
        y: {
          field: 'total_annual_revenue',
          type: 'quantitative',
          axis: { format: ',.2f' },
        },
      },
    },
    ...overrides,
  }
}

describe('hydrateChartSpec', () => {
  it('resolves the cited result into inline values', () => {
    const result = hydrateChartSpec(chart(), snapshot())
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.spec.data).toEqual({
      values: [
        { business_type: 'Retail', total_annual_revenue: 83373290.48 },
        { business_type: 'Ecommerce', total_annual_revenue: 75881961.32 },
      ],
    })
  })

  it('produces a spec the safety check then accepts', () => {
    const snap = snapshot()
    const result = hydrateChartSpec(chart(), snap)
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(checkChartSpec(result.spec, snap.columns)).toEqual({ ok: true })
  })

  it('leaves the published specification data-free', () => {
    // The payload is shared with the table and the provenance drawer; a spec
    // that grew a data key in place would leak into both.
    const published = chart()
    hydrateChartSpec(published, snapshot())
    expect(published.spec.data).toBeUndefined()
  })

  it('keeps a raw published specification non-renderable on its own', () => {
    const published = chart()
    const direct = checkChartSpec(published.spec, snapshot().columns)
    expect(direct.ok).toBe(false)
    expect(direct.reason).toBe('chart data must be inline values')
  })

  it('refuses a chart whose cited result is missing', () => {
    const result = hydrateChartSpec(chart(), undefined)
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.reason).toContain('result_01')
  })

  it('refuses a chart cited against the wrong result', () => {
    const result = hydrateChartSpec(chart({ result_id: 'result_09' }), snapshot())
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.reason).toContain('result_09')
  })

  it('refuses an encoded field that is not a column of the result', () => {
    const wrong = chart()
    wrong.spec = {
      mark: 'bar',
      encoding: {
        x: { field: 'not_a_column', type: 'nominal' },
        y: { field: 'total_annual_revenue', type: 'quantitative' },
      },
    }
    const result = hydrateChartSpec(wrong, snapshot())
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.reason).toContain('not_a_column')
  })

  it('refuses a row that does not match the declared columns', () => {
    const result = hydrateChartSpec(chart(), snapshot({ rows: [['Retail']] }))
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.reason).toContain('row 1')
  })

  it('refuses a measure cell that is not a number', () => {
    const result = hydrateChartSpec(chart(), snapshot({ rows: [['Retail', 'lots']] }))
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.reason).toContain('not a number')
  })

  it('refuses a result with no rows rather than drawing an empty plot', () => {
    const result = hydrateChartSpec(chart(), snapshot({ rows: [] }))
    expect(result.ok).toBe(false)
    if (result.ok) return
    expect(result.reason).toContain('no rows')
  })

  it('passes through a specification that already carries inline values', () => {
    // The metric-registry path inlines its values server-side.
    const inline = chart()
    inline.spec = { ...inline.spec, data: { values: [{ business_type: 'Retail' }] } }
    const result = hydrateChartSpec(inline, snapshot())
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.spec).toBe(inline.spec)
  })

  it('refuses data that is present but not inline rows', () => {
    for (const data of [
      { url: 'https://evil.example/rows.json' },
      { name: 'someDataset' },
      { sequence: { start: 0, stop: 10 } },
    ]) {
      const remote = chart()
      remote.spec = { ...remote.spec, data }
      const result = hydrateChartSpec(remote, snapshot())
      expect(result.ok).toBe(false)
      if (result.ok) return
      expect(result.reason).toBe('chart data must be inline values')
    }
  })

  it('copies only the columns the chart encodes', () => {
    const wide = snapshot({
      columns: ['business_type', 'total_annual_revenue', 'contact_email'],
      rows: [['Retail', 83373290.48, 'private@example.com']],
    })
    const result = hydrateChartSpec(chart(), wide)
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(JSON.stringify(result.spec)).not.toContain('private@example.com')
  })

  it('hydrates a tooltip array as well as the positional channels', () => {
    const tipped = chart()
    tipped.spec = {
      mark: 'bar',
      encoding: {
        x: { field: 'business_type', type: 'nominal' },
        y: { field: 'total_annual_revenue', type: 'quantitative' },
        tooltip: [
          { field: 'business_type', type: 'nominal' },
          { field: 'total_annual_revenue', type: 'quantitative', format: ',.2f' },
        ],
      },
    }
    const result = hydrateChartSpec(tipped, snapshot())
    expect(result.ok).toBe(true)
  })

  it('still refuses a hostile spec once hydrated', () => {
    const hostile = chart()
    hostile.spec = {
      mark: 'bar',
      transform: [{ calculate: 'window.top.location', as: 'x' }],
      encoding: { x: { field: 'business_type', type: 'nominal' } },
    }
    const snap = snapshot()
    const result = hydrateChartSpec(hostile, snap)
    // Hydration may succeed; the safety check is what rejects it.
    const reason = result.ok ? checkChartSpec(result.spec, snap.columns).reason : result.reason
    expect(reason).toContain('transform')
  })
})
