/**
 * Compare Both could say *whether* two interpretations agreed. This pins
 * that it also says *where* they differ, because a reader told only
 * "different governed interpretations" cannot tell a swapped measure from
 * a dropped row restriction, and those are very different things.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ComparisonView } from '../components/ComparisonView'
import { canonicalOf, contractDifferences } from '../lib/contractDiff'
import type { CanonicalContract, QueryContract, RunPayload } from '../lib/types'

const canonical: CanonicalContract = {
  operation: 'average',
  table: 'uploaded_data',
  measure: 'annual_revenue',
  dimension: 'region',
  time_field: null,
  period: null,
  period_field: null,
  filters: [
    { column: 'age', operator: '>=', value: 30 },
    { column: 'age', operator: '<=', value: 40 },
  ],
  ascending: false,
}

const contract = (over: Partial<QueryContract> = {}): QueryContract => ({
  ...canonical,
  confident: true,
  explanation: 'validated',
  interpretation: 'rule-based',
  contract_hash: 'hash-a',
  canonical_contract: canonical,
  ...over,
})

const withCanonical = (over: Partial<CanonicalContract>, hash = 'hash-b'): QueryContract =>
  contract({ ...over, contract_hash: hash, canonical_contract: { ...canonical, ...over } })

describe('contractDifferences', () => {
  it('reports nothing for two identical canonical contracts', () => {
    expect(contractDifferences(contract(), contract())).toEqual([])
  })

  it('ignores who read the wording', () => {
    const ai = contract({ interpretation: 'ai-grounded', explanation: 'AI plan' })
    expect(contractDifferences(contract(), ai)).toEqual([])
  })

  it('names a swapped measure', () => {
    const diff = contractDifferences(contract(), withCanonical({ measure: 'headcount' }))
    expect(diff).toEqual([
      { label: 'Measure', deterministic: 'annual_revenue', ai: 'headcount' },
    ])
  })

  it('names a dropped row restriction and says what is missing', () => {
    const diff = contractDifferences(contract(), withCanonical({ filters: [] }))
    expect(diff).toHaveLength(1)
    expect(diff[0].label).toBe('Row filters')
    expect(diff[0].deterministic).toContain('age >= 30')
    expect(diff[0].deterministic).toContain('age <= 40')
    expect(diff[0].ai).toBe('none')
  })

  it('does not report a difference for filters stated in another order', () => {
    const reordered = withCanonical({ filters: [...canonical.filters].reverse() })
    expect(contractDifferences(contract(), reordered)).toEqual([])
  })

  it('names an added time period', () => {
    const diff = contractDifferences(
      contract(),
      withCanonical({ period: ['2024-01-01', '2024-12-31'], period_field: 'signup_date' }),
    )
    expect(diff.map((d) => d.label)).toEqual(['Time period', 'Period column'])
    expect(diff[0]).toEqual({
      label: 'Time period',
      deterministic: 'none',
      ai: '2024-01-01 to 2024-12-31',
    })
  })

  it('names a reversed sort order', () => {
    const diff = contractDifferences(contract(), withCanonical({ ascending: true }))
    expect(diff).toEqual([
      { label: 'Sort order', deterministic: 'descending', ai: 'ascending' },
    ])
  })

  it('reports nothing when either side has no contract', () => {
    expect(contractDifferences(contract(), null)).toEqual([])
    expect(contractDifferences(null, contract())).toEqual([])
  })

  it('falls back to the full contract when no canonical block is present', () => {
    const older = contract({ canonical_contract: undefined })
    expect(canonicalOf(older)?.measure).toBe('annual_revenue')
    expect(contractDifferences(older, older)).toEqual([])
  })
})

describe('ComparisonView contract diff', () => {
  const side = (run: RunPayload | null) => ({
    title: 'T',
    subtitle: 'S',
    run,
    error: null,
    pending: false,
    children: null,
  })
  const runWith = (queryContract: QueryContract): RunPayload =>
    ({ query_contract: queryContract }) as RunPayload

  it('shows the differing rows when the two panes disagree', () => {
    render(
      <ComparisonView
        question="Q"
        deterministic={side(runWith(contract()))}
        ai={side(runWith(withCanonical({ dimension: 'store_id' })))}
      />,
    )
    const table = screen.getByTestId('contract-diff')
    expect(table).toHaveTextContent('Grouping')
    expect(table).toHaveTextContent('region')
    expect(table).toHaveTextContent('store_id')
    expect(table).not.toHaveTextContent('Measure')
  })

  it('shows no diff table when the panes agree', () => {
    render(
      <ComparisonView
        question="Q"
        deterministic={side(runWith(contract()))}
        ai={side(runWith(contract({ interpretation: 'ai-grounded' })))}
      />,
    )
    expect(screen.queryByTestId('contract-diff')).toBeNull()
    expect(screen.getByTestId('contract-comparison')).toHaveTextContent(
      /same governed interpretation/i,
    )
  })

  it('stays readable when the hashes differ but no listed field does', () => {
    render(
      <ComparisonView
        question="Q"
        deterministic={side(runWith(contract()))}
        ai={side(runWith(contract({ contract_hash: 'hash-z' })))}
      />,
    )
    expect(screen.queryByTestId('contract-diff')).toBeNull()
    expect(screen.getByTestId('contract-comparison')).toHaveTextContent(
      /not one this report breaks out/i,
    )
  })
})
