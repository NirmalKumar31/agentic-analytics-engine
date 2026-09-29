import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ReportView } from '../components/ReportView'
import type { QueryContract } from '../lib/types'

const contract: QueryContract = {
  operation: 'average',
  table: 'uploaded_data',
  measure: 'annual_revenue',
  dimension: 'region',
  filters: [
    { column: 'age', operator: '>=', value: 30 },
    { column: 'age', operator: '<=', value: 40 },
  ],
  ascending: false,
  confident: true,
  explanation: 'validated',
  interpretation: 'ai-grounded',
  contract_hash: 'contract',
}

function report() {
  return render(
    <ReportView
      question="Average annual revenue for people aged 30 to 40 by region"
      report={null}
      findings={[]}
      rejected={[]}
      charts={[]}
      results={{}}
      queryContract={contract}
      onShowWork={() => {}}
    />,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('ReportView', () => {
  it('shows the operation, population filters and grouping actually executed', () => {
    report()
    const applied = screen.getByTestId('applied-analysis')
    expect(applied).toHaveTextContent('average')
    expect(applied).toHaveTextContent('annual revenue')
    expect(applied).toHaveTextContent('region')
    expect(applied).toHaveTextContent('age >= 30')
    expect(applied).toHaveTextContent('age <= 40')
  })

  it('describes a zero-finding completion without implying a crash', () => {
    report()
    expect(screen.getByText(/no verified finding answered the requested analysis/i)).toBeVisible()
  })

  it('offers the browser print path for saving a PDF', async () => {
    const print = vi.spyOn(window, 'print').mockImplementation(() => {})
    report()
    await userEvent.click(screen.getByRole('button', { name: /print \/ save pdf/i }))
    expect(print).toHaveBeenCalledOnce()
  })
})
