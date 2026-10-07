import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ActivityLog } from '../components/ActivityLog'
import { ResultTable } from '../components/ResultTable'
import { event, finding, hostileSnapshot, snapshot, trace } from './fixtures'

describe('ResultTable', () => {
  it('marks the cells a finding cites', () => {
    render(<ResultTable snapshot={snapshot} highlight={finding.evidence_cells} />)
    const cited = document.querySelectorAll('td.cited')
    expect(cited).toHaveLength(2)
    expect(cited[0]?.textContent).toBe('40.94')
    expect(cited[1]?.textContent).toBe('33.27')
  })

  it('renders dataset values as text, never as markup', () => {
    render(<ResultTable snapshot={hostileSnapshot} />)
    // The script tag is displayed as a string and never parsed.
    expect(screen.getByText('<script>window.__pwned = true</script>')).toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
    expect((globalThis as Record<string, unknown>).__pwned).toBeUndefined()
  })

  it('reports truncation', () => {
    render(<ResultTable snapshot={{ ...snapshot, truncated: true }} />)
    expect(screen.getByText(/truncated by the result limit/)).toBeInTheDocument()
  })
})

/*
 * The `ProvenanceDrawer` suite stood here and went with the component.
 *
 * It was a per-finding drawer, opened from a "Show work" on every published
 * finding card, and six identical cards meant six triggers for six drawers
 * describing one run. The evidence drawer carries the contract,
 * verification outcomes, cited cells, timings and the trace once, for the
 * report, and `evidence.spec.ts` asserts every one of those is present.
 *
 * Its focus behaviour (in on open, contained, back to the trigger on
 * close) moved into `SideSheet` and is asserted there, on both sheets,
 * including the two WebKit-only failures the original never caught.
 */


describe('ActivityLog', () => {
  const events = [
    event('question_analyzed', { analysis_type: 'timeseries', target_metrics: ['revenue'] }, 1),
    event('plan_generated', { task_count: 2, tasks: [] }, 2),
    event(
      'mcp_tool_called',
      {
        tool_name: 'compute_metric',
        agent: 'analysis_worker',
        arguments: { metric: 'revenue', dimension: 'region' },
      },
      3,
    ),
    event('finding_rejected', { text: 'X causes Y.', reason: 'asserts causation' }, 4),
  ]

  it('shows a clean agent-and-tool line by default', () => {
    render(
      <ActivityLog events={events} trace={trace} showTrace={false} onToggleTrace={vi.fn()} running={false} />,
    )
    const agent = screen.getByText('Analysis Agent')
    expect(agent).toBeInTheDocument()
    expect(agent.parentElement).toHaveTextContent('Compute Metric')
    expect(screen.getByText(/revenue by region/)).toBeInTheDocument()
  })

  it('shows arguments and durations only when the trace is toggled on', async () => {
    const onToggle = vi.fn()
    const { rerender } = render(
      <ActivityLog events={events} trace={trace} showTrace={false} onToggleTrace={onToggle} running={false} />,
    )
    expect(screen.queryByText(/metric=revenue/)).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /show mcp trace/i }))
    expect(onToggle).toHaveBeenCalled()
    rerender(
      <ActivityLog events={events} trace={trace} showTrace onToggleTrace={onToggle} running={false} />,
    )
    expect(screen.getByText(/metric=revenue/)).toBeInTheDocument()
  })

  it('never shows a session id even with the trace on', () => {
    const withSession = [
      event(
        'mcp_tool_called',
        {
          tool_name: 'compute_metric',
          agent: 'analysis_worker',
          arguments: { metric: 'revenue', session_id: 'ses_secret' },
        },
        1,
      ),
    ]
    render(
      <ActivityLog events={withSession} trace={trace} showTrace onToggleTrace={vi.fn()} running={false} />,
    )
    expect(document.body.textContent).not.toContain('ses_secret')
  })

  it('shows a withheld finding with its reason', () => {
    render(
      <ActivityLog events={events} trace={trace} showTrace={false} onToggleTrace={vi.fn()} running={false} />,
    )
    expect(screen.getByText(/withheld/)).toBeInTheDocument()
    expect(screen.getByText(/asserts causation/)).toBeInTheDocument()
  })
})
