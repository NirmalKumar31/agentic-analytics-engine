import { describe, expect, it } from 'vitest'

import { checkChartSpec } from '../lib/chartSafety'

const columns = ['period', 'revenue']

function spec(overrides: Record<string, unknown> = {}) {
  return {
    data: { values: [{ period: 'a', revenue: 1 }] },
    mark: { type: 'line' },
    encoding: {
      x: { field: 'period', type: 'temporal' },
      y: { field: 'revenue', type: 'quantitative' },
    },
    ...overrides,
  }
}

describe('checkChartSpec', () => {
  it('accepts a well-formed spec', () => {
    expect(checkChartSpec(spec(), columns).ok).toBe(true)
  })

  it('rejects a remote data url', () => {
    const result = checkChartSpec({ ...spec(), data: { url: 'https://evil.example/x.json' } }, columns)
    expect(result.ok).toBe(false)
    expect(result.reason).toContain('url')
  })

  it('rejects transforms', () => {
    const result = checkChartSpec(spec({ transform: [{ calculate: 'alert(1)', as: 'z' }] }), columns)
    expect(result.ok).toBe(false)
  })

  it('rejects signals', () => {
    expect(checkChartSpec(spec({ signals: [{ name: 'x' }] }), columns).ok).toBe(false)
  })

  it('rejects a nested expr', () => {
    const hostile = spec({
      encoding: {
        x: { field: 'period', type: 'temporal', expr: 'alert(1)' },
        y: { field: 'revenue', type: 'quantitative' },
      },
    })
    expect(checkChartSpec(hostile, columns).ok).toBe(false)
  })

  it('rejects a field that is not a column of the result', () => {
    const hostile = spec({
      encoding: {
        x: { field: '<img src=x onerror=alert(1)>', type: 'nominal' },
        y: { field: 'revenue', type: 'quantitative' },
      },
    })
    const result = checkChartSpec(hostile, columns)
    expect(result.ok).toBe(false)
    expect(result.reason).toContain('not a column')
  })

  it('rejects a disallowed mark', () => {
    expect(checkChartSpec(spec({ mark: 'pie' }), columns).ok).toBe(false)
  })

  it('rejects a disallowed encoding type', () => {
    const hostile = spec({
      encoding: { x: { field: 'period', type: 'geojson' }, y: { field: 'revenue', type: 'quantitative' } },
    })
    expect(checkChartSpec(hostile, columns).ok).toBe(false)
  })

  it('rejects a spec with no inline data', () => {
    expect(checkChartSpec({ mark: 'bar', encoding: {} }, columns).ok).toBe(false)
  })
})
