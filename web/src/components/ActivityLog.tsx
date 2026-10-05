import { agentLabel, formatDuration, toolLabel } from '../lib/format'
import type { RunEvent, TraceCall } from '../lib/types'

interface Props {
  events: RunEvent[]
  trace: TraceCall[]
  showTrace: boolean
  onToggleTrace: () => void
  running: boolean
}

interface Line {
  key: string
  tone: 'tool' | 'verified' | 'rejected' | 'failed' | 'plain'
  agent: string
  detail: React.ReactNode
  meta?: string
}

/**
 * The clean activity line, with a technical MCP trace behind a toggle.
 *
 * The default view names the agent and the tool it reached for. The toggle
 * adds the arguments, the duration and the result id -- never a credential,
 * never a raw provider error, never model reasoning.
 */
export function ActivityLog({ events, trace, showTrace, onToggleTrace, running }: Props) {
  const lines: Line[] = []
  let traceIndex = 0

  for (const event of events) {
    const data = event.data
    switch (event.type) {
      case 'question_analyzed':
        lines.push({
          key: event.event_id,
          tone: 'plain',
          agent: 'Question Analyst',
          detail: (
            <>
              → {String(data.analysis_type)} ·{' '}
              <span className="mono">{(data.target_metrics as string[])?.join(', ')}</span>
            </>
          ),
        })
        break
      case 'plan_generated':
        lines.push({
          key: event.event_id,
          tone: 'plain',
          agent: 'Analysis Planner',
          detail: <>→ {String(data.task_count)} analytical tasks dispatched</>,
        })
        break
      case 'mcp_tool_called': {
        const call = trace[traceIndex]
        traceIndex += 1
        lines.push({
          key: event.event_id,
          tone: 'tool',
          agent: agentLabel(String(data.agent ?? 'Analysis Agent')),
          detail: (
            <>
              → {toolLabel(String(data.tool_name))}
              {showTrace && (
                <span className="dim mono"> {summariseArgs(data.arguments)}</span>
              )}
              {!showTrace && <span className="dim"> · {describe(data.arguments)}</span>}
            </>
          ),
          meta: showTrace && call ? formatDuration(call.duration_ms) : undefined,
        })
        break
      }
      case 'mcp_tool_completed':
        if (showTrace) {
          lines.push({
            key: event.event_id,
            tone: 'tool',
            agent: '',
            detail: (
              <span className="dim">
                ← {String(data.row_count ?? 0)} rows returned
              </span>
            ),
            meta: formatDuration(Number(data.duration_ms ?? 0)),
          })
        }
        break
      case 'mcp_tool_failed':
        /*
         * A call the engine refused is not a tool that failed.
         *
         * `preflight` checks a proposed call against the tool's real
         * signature before anything crosses MCP, and refuses it there --
         * so an unsupported argument never reaches the tool at all. The
         * trace said "Analyze Timeseries failed: analyze_timeseries does
         * not accept 'dimensions'", which reads as the analysis breaking
         * when what happened is the engine declining to make a call it
         * knew was wrong. The event already carries `preflight`; only the
         * wording ignored it.
         *
         * The distinction matters to a reader deciding whether to trust
         * the run: a refused call is a guardrail working, and a tool
         * failure is not.
         */
        lines.push({
          key: event.event_id,
          tone: 'failed',
          agent: agentLabel(String(data.agent ?? 'Analysis Agent')),
          detail: data.preflight ? (
            <>
              → {toolLabel(String(data.tool_name))} was not called:{' '}
              {String(data.error ?? '').slice(0, 120)}
            </>
          ) : (
            <>
              → {toolLabel(String(data.tool_name))} failed:{' '}
              {String(data.error ?? '').slice(0, 120)}
            </>
          ),
        })
        break
      case 'finding_verified':
        lines.push({
          key: event.event_id,
          tone: 'verified',
          agent: 'Verifier',
          // The event carries `status` and `rule`, not the finding's text,
          // so this rendered "supported:" followed by nothing.
          detail: (
            <>
              → {String(data.status ?? 'supported')}
              {data.rule ? (
                <>
                  {' · '}
                  <span className="mono">{String(data.rule)}</span>
                </>
              ) : null}
            </>
          ),
        })
        break
      case 'finding_rejected':
        lines.push({
          key: event.event_id,
          tone: 'rejected',
          agent: 'Verifier',
          detail: (
            <>
              → withheld: {truncate(String(data.text ?? ''))}
              <span className="dim"> — {String(data.reason ?? '')}</span>
            </>
          ),
        })
        break
      case 'analysis_task_failed':
        lines.push({
          key: event.event_id,
          tone: 'failed',
          agent: 'Analysis Worker',
          detail: <>
            → {data.task_id ? 'an analytical task failed' : 'analysis was stopped'}:{' '}
            {String(data.error ?? data.reason ?? '')}
          </>,
        })
        break
      case 'followup_round_started':
        lines.push({
          key: event.event_id,
          tone: 'plain',
          agent: 'Orchestrator',
          detail: <>→ follow-up round {String(data.round)}: {String(data.reason ?? '')}</>,
        })
        break
      case 'chart_created':
        lines.push({
          key: event.event_id,
          tone: 'plain',
          agent: 'Visualisation Agent',
          // `chart_kind` and `result_id`, which is what the engine emits.
          // This read `data.mark` and `data.title`; neither exists on the
          // event, so every chart in every trace read
          // "undefined chart · undefined". Found by reading a captured
          // payload rather than by looking at the code.
          detail: (
            <>
              → {String(data.chart_kind ?? 'chart')} chart ·{' '}
              <span className="mono">{String(data.result_id ?? '')}</span>
            </>
          ),
        })
        break
      case 'chart_rejected':
        lines.push({
          key: event.event_id,
          tone: 'rejected',
          agent: 'Visualisation Agent',
          detail: <>→ chart rejected: {String(data.reason ?? '')}</>,
        })
        break
      case 'budget_exceeded':
        lines.push({
          key: event.event_id,
          tone: 'failed',
          agent: 'Budget',
          detail: <>→ {String(data.detail ?? '')}</>,
        })
        break
      case 'report_completed':
        lines.push({
          key: event.event_id,
          tone: 'plain',
          agent: 'Report Agent',
          detail: (
            <>
              → {String(data.finding_count)} published, {String(data.rejected_count)} withheld
            </>
          ),
        })
        break
      default:
        break
    }
  }

  return (
    <section className="panel" data-testid="activity" data-busy={running ? 'true' : 'false'}>
      <div className="panel-head">
        <h2 className="section-heading">Activity</h2>
        {running && <span className="tag pulse">running</span>}
        <span className="spacer" />
        <button className="btn ghost small" onClick={onToggleTrace} aria-pressed={showTrace}>
          {showTrace ? 'Hide MCP trace' : 'Show MCP trace'}
        </button>
      </div>
      <div className="panel-body">
        {lines.length === 0 ? (
          <p className="small dim" style={{ margin: 0 }}>
            No activity yet.
          </p>
        ) : (
          <div className="activity">
            {lines.map((line) => (
              <div className={`activity-row ${line.tone}`} key={line.key}>
                <span className="activity-icon" />
                <span className="activity-label">
                  {line.agent && <b>{line.agent}</b>} {line.detail}
                </span>
                <span className="activity-meta">{line.meta ?? ''}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

function truncate(text: string, length = 92): string {
  return text.length > length ? `${text.slice(0, length - 1)}…` : text
}

/** A short, human reading of what a tool call was asked for. */
function describe(args: unknown): string {
  if (!args || typeof args !== 'object') return ''
  const a = args as Record<string, unknown>
  if (typeof a.metric === 'string') {
    if (typeof a.dimension === 'string') return `${a.metric} by ${a.dimension}`
    if (Array.isArray(a.dimensions) && a.dimensions.length > 0) {
      return `${a.metric} by ${(a.dimensions as string[]).join(', ')}`
    }
    if (typeof a.grain === 'string') return `${a.metric} by ${a.grain}`
    return String(a.metric)
  }
  if (typeof a.test_type === 'string') return String(a.test_type).replace(/_/g, ' ')
  if (typeof a.table === 'string') return String(a.table)
  return ''
}

/** The safe argument view shown behind the technical toggle. */
function summariseArgs(args: unknown): string {
  if (!args || typeof args !== 'object') return ''
  const entries = Object.entries(args as Record<string, unknown>)
    .filter(([key, value]) => key !== 'session_id' && value !== null && value !== undefined)
    .map(([key, value]) => `${key}=${compact(value)}`)
  return entries.length ? `{ ${entries.join(', ')} }` : ''
}

function compact(value: unknown): string {
  if (Array.isArray(value)) {
    if (value.length === 0) return '[]'
    return `[${value.map((v) => compact(v)).join(',')}]`
  }
  if (value && typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>)
    return `{${keys.slice(0, 3).join(',')}${keys.length > 3 ? ',…' : ''}}`
  }
  const text = String(value)
  return text.length > 28 ? `${text.slice(0, 27)}…` : text
}
