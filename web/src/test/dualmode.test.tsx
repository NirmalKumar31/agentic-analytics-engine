import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ComparisonView } from '../components/ComparisonView'
import { ModeSelector } from '../components/ModeSelector'
import type { Capabilities } from '../lib/types'

const BOTH_AVAILABLE: Capabilities = {
  modes: [
    {
      mode: 'deterministic',
      available: true,
      label: 'Deterministic Analytics',
      description: 'Agent decisions come from a scripted provider.',
      reason: '',
      message: '',
    },
    {
      mode: 'ai',
      available: true,
      label: 'AI Analytics',
      description: 'A cloud language model interprets the question.',
      reason: '',
      message: '',
    },
  ],
  compare_available: true,
  ai_limits: { runs_per_session: 3, max_model_calls_per_run: 24, max_runtime_seconds: 180 },
}

const AI_DISABLED: Capabilities = {
  modes: [
    BOTH_AVAILABLE.modes[0]!,
    {
      mode: 'ai',
      available: false,
      label: 'AI Analytics',
      description: 'A cloud language model interprets the question.',
      reason: 'ai_disabled',
      message: 'AI Analytics is turned off on this deployment.',
    },
  ],
  compare_available: false,
  ai_limits: null,
}

describe('ModeSelector', () => {
  it('offers the three choices as one accessible radio group', () => {
    render(
      <ModeSelector capabilities={BOTH_AVAILABLE} value="deterministic" onChange={() => {}} />,
    )
    const group = screen.getByRole('radiogroup', { name: /analysis mode/i })
    expect(within(group).getAllByRole('radio')).toHaveLength(3)
    expect(screen.getByRole('radio', { name: /^Deterministic Analytics/ })).toBeChecked()
  })

  it('disables AI and Compare, and says why, when AI is unavailable', () => {
    render(<ModeSelector capabilities={AI_DISABLED} value="deterministic" onChange={() => {}} />)
    expect(screen.getByRole('radio', { name: /^AI Analytics/ })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /^Compare Both/ })).toBeDisabled()
    // The reason is text, not colour alone -- on both disabled choices.
    expect(screen.getAllByText(/turned off on this deployment/i)).toHaveLength(2)
  })

  it('keeps Deterministic selectable when AI is unavailable', () => {
    render(<ModeSelector capabilities={AI_DISABLED} value="deterministic" onChange={() => {}} />)
    expect(screen.getByRole('radio', { name: /^Deterministic Analytics/ })).toBeEnabled()
  })

  it('reports the chosen mode', async () => {
    const onChange = vi.fn()
    render(<ModeSelector capabilities={BOTH_AVAILABLE} value="deterministic" onChange={onChange} />)
    await userEvent.click(screen.getByRole('radio', { name: /^Compare Both/ }))
    expect(onChange).toHaveBeenCalledWith('compare')
  })

  it('is reachable by keyboard', async () => {
    render(<ModeSelector capabilities={BOTH_AVAILABLE} value="deterministic" onChange={() => {}} />)
    await userEvent.tab()
    expect(screen.getByRole('radio', { name: /^Deterministic Analytics/ })).toHaveFocus()
  })

  it('discloses the AI quota and what is sent', () => {
    render(<ModeSelector capabilities={BOTH_AVAILABLE} value="ai" onChange={() => {}} />)
    expect(screen.getByText(/limited public quota/i)).toBeInTheDocument()
    expect(screen.getByText(/schema, profiles and aggregates/i)).toBeInTheDocument()
    expect(screen.getByText(/3 AI runs per dataset session/i)).toBeInTheDocument()
  })

  it('never calls deterministic mode fake or simulated', () => {
    const { container } = render(
      <ModeSelector capabilities={BOTH_AVAILABLE} value="deterministic" onChange={() => {}} />,
    )
    const text = container.textContent?.toLowerCase() ?? ''
    expect(text).not.toContain('fake')
    expect(text).not.toContain('simulated')
    expect(text).toContain('deterministic analytics')
  })
})

function side(overrides: Partial<Parameters<typeof ComparisonView>[0]['ai']> = {}) {
  return {
    title: 'AI Analytics',
    subtitle: 'A cloud model plans and interprets.',
    run: null,
    error: null,
    pending: false,
    children: null,
    ...overrides,
  }
}

describe('ComparisonView', () => {
  const deterministic = side({ title: 'Deterministic Analytics', children: <p>left result</p> })

  it('shows both panes with independent labels', () => {
    render(
      <ComparisonView
        question="Why did margin fall?"
        deterministic={deterministic}
        ai={side({ children: <p>right result</p> })}
      />,
    )
    expect(screen.getByRole('region', { name: 'Deterministic Analytics' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'AI Analytics' })).toBeInTheDocument()
    expect(screen.getByText('left result')).toBeInTheDocument()
    expect(screen.getByText('right result')).toBeInTheDocument()
  })

  it('keeps the deterministic result when the AI side failed', () => {
    render(
      <ComparisonView
        question="Why did margin fall?"
        deterministic={deterministic}
        ai={side({ error: 'AI Analytics has reached its public demo usage limit.' })}
      />,
    )
    expect(screen.getByText('left result')).toBeInTheDocument()
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(/public demo usage limit/i)
  })

  it('announces each side status politely', () => {
    render(
      <ComparisonView
        question="Q"
        deterministic={deterministic}
        ai={side({ pending: true })}
      />,
    )
    const statuses = document.querySelectorAll('[aria-live="polite"]')
    expect(statuses.length).toBeGreaterThanOrEqual(2)
  })

  it('shows AI usage without inventing one for the deterministic side', () => {
    render(
      <ComparisonView
        question="Q"
        deterministic={deterministic}
        ai={side({
          usage: {
            input_tokens: 4200,
            output_tokens: 900,
            provider_attempts: 6,
            estimated_cost_microdollars: 1234,
          },
        })}
      />,
    )
    expect(screen.getByText('4,200')).toBeInTheDocument()
    expect(screen.getByText('6')).toBeInTheDocument()
  })

  it('never declares a winner or ranks the two sides', () => {
    const { container } = render(
      <ComparisonView
        question="Why did margin fall?"
        deterministic={deterministic}
        ai={side({ children: <p>right result</p> })}
      />,
    )
    const text = container.textContent?.toLowerCase() ?? ''
    for (const banned of ['winner', 'more accurate', 'better', 'best', 'score']) {
      expect(text).not.toContain(banned)
    }
    expect(text).toContain('are not ranked')
  })

  it('explains that only the planning differs', () => {
    const { container } = render(
      <ComparisonView question="Q" deterministic={deterministic} ai={side()} />,
    )
    const text = container.textContent ?? ''
    expect(text).toContain('rule-based planning')
    expect(text).toContain('same analytics engine')
    expect(text).toContain('same publication checks')
  })
})
