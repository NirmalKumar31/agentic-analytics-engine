import { describe, expect, it } from 'vitest'

import { formatCell, formatDuration, formatNumber, formatPValue, kindLabel } from '../lib/format'

describe('formatNumber', () => {
  it('renders integers with separators', () => {
    expect(formatNumber(1234567)).toBe('1,234,567')
  })
  it('renders floats to two decimals', () => {
    expect(formatNumber(40.936876)).toBe('40.94')
  })
  it('uses exponential notation for tiny values', () => {
    expect(formatNumber(2.15e-122)).toBe('2.150e-122')
  })
  it('handles null and booleans', () => {
    expect(formatNumber(null)).toBe('—')
    expect(formatNumber(true)).toBe('true')
  })
})

describe('formatCell', () => {
  it('truncates very long values so a table stays readable', () => {
    const long = 'x'.repeat(400)
    expect(formatCell(long).length).toBeLessThanOrEqual(120)
  })
  it('returns markup as text, never as markup', () => {
    expect(formatCell('<script>alert(1)</script>')).toBe('<script>alert(1)</script>')
  })
})

describe('misc formatters', () => {
  it('formats durations', () => {
    expect(formatDuration(0.4)).toBe('<1 ms')
    expect(formatDuration(120)).toBe('120 ms')
    expect(formatDuration(2500)).toBe('2.50 s')
  })
  it('formats p-values', () => {
    expect(formatPValue(0.0004)).toContain('e-')
    expect(formatPValue(0.0421)).toBe('0.0421')
  })
  it('labels evidence kinds', () => {
    expect(kindLabel('statistical_result')).toBe('Statistical')
    expect(kindLabel('unknown_kind')).toBe('unknown_kind')
  })
})
