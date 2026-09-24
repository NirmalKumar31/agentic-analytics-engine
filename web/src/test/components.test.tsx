import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ActivityLog } from '../components/ActivityLog'
import { ExecutionFlow } from '../components/ExecutionFlow'
import { ProvenanceDrawer } from '../components/ProvenanceDrawer'
import { ResultTable } from '../components/ResultTable'
import { event, finding, hostileSnapshot, planEvents, snapshot, task, trace } from './fixtures'

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

describe('ProvenanceDrawer', () => {
  function open(onClose = vi.fn()) {
    render(
      <ProvenanceDrawer
        finding={finding}
        results={{ [snapshot.result_id]: snapshot }}
        tasks={[task]}
        trace={trace}
        datasetFingerprint="sha256:deadbeefdeadbeefdeadbeefdeadbeef"
        onClose={onClose}
      />,
    )
    return onClose
  }

  it('shows the task, the SQL, the result and the cited cells', () => {
    open()
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('task_01')).toBeInTheDocument()
    expect(within(dialog).getByText(/SELECT period, gross_margin_pct/)).toBeInTheDocument()
    expect(within(dialog).getByText(/gross_margin_pct at 2025-04-01/)).toBeInTheDocument()
    expect(document.querySelectorAll('td.cited')).toHaveLength(2)
  })

  it('shows the recomputed calculation', () => {
    open()
    expect(screen.getByText(/recalculated this from the cells above/)).toBeInTheDocument()
    // The stated change appears twice on purpose: once in the calculation
    // panel and once in the result row it was computed from.
    expect(screen.getAllByText('-7.67').length).toBeGreaterThanOrEqual(2)
  })

  it('shows the dataset fingerprint', () => {
    open()
    expect(screen.getByText('sha256:deadbeefdeadbeefdeadbeefdeadbeef')).toBeInTheDocument()
  })

  it('shows the MCP tool path', () => {
    open()
    expect(screen.getByText('analyze_timeseries')).toBeInTheDocument()
  })

  it('never displays model reasoning', () => {
    open()
    const text = screen.getByRole('dialog').textContent ?? ''
    for (const word of ['reasoning', 'thinking', 'chain of thought', 'scratchpad']) {
      expect(text.toLowerCase()).not.toContain(word)
    }
  })

  it('closes on Escape and on the close button', async () => {
    const onClose = open()
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
    await userEvent.click(screen.getByLabelText('Close'))
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})

describe('ExecutionFlow', () => {
  it('draws one branch per planned task', () => {
    const { container } = render(<ExecutionFlow events={planEvents} />)
    // PLAN + 2 tasks + 2 MCP nodes + VERIFY + REPORT
    expect(container.querySelectorAll('.flow-node')).toHaveLength(7)
  })

  it('marks a completed task done and a started task active', () => {
    const { container } = render(<ExecutionFlow events={planEvents} />)
    expect(container.querySelectorAll('.flow-node.done').length).toBeGreaterThan(0)
    expect(container.querySelectorAll('.flow-node.active').length).toBeGreaterThan(0)
  })

  it('draws no branches before a plan exists', () => {
    const { container } = render(<ExecutionFlow events={[event('run_started', {}, 1)]} />)
    expect(container.querySelectorAll('.flow-node')).toHaveLength(3)
  })

  it('animates only the edges of tasks that are actually running', () => {
    const { container } = render(<ExecutionFlow events={planEvents} />)
    const running = container.querySelectorAll('.flow-edge.running')
    // task_02 started and has not completed; task_01 has.
    expect(running.length).toBeGreaterThan(0)
    expect(running.length).toBeLessThan(container.querySelectorAll('.flow-edge').length)
  })
})

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
    expect(screen.getByText('Analysis Agent')).toBeInTheDocument()
    expect(screen.getByText('compute_metric')).toBeInTheDocument()
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
