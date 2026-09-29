import { useEffect, useRef, useState } from 'react'
import { formatDuration, toolLabel } from '../lib/format'
import type {
  DatasetCatalog,
  MetricInfo,
  ResultSnapshot,
  RunMetrics,
  TaskOutcome,
} from '../lib/types'

interface Props {
  catalog: DatasetCatalog | null
  metrics: MetricInfo[]
  usedMetrics: string[]
  results: Record<string, ResultSnapshot>
  tasks: TaskOutcome[]
  runMetrics: RunMetrics | null
  onOpenResult: (resultId: string) => void
}

export function RightRail({
  catalog,
  metrics,
  usedMetrics,
  results,
  tasks,
  runMetrics,
  onOpenResult,
}: Props) {
  const metricsById = new Map(metrics.map((m) => [m.name, m]))
  const queries = Object.values(results).filter((r) => r.sql)

  return (
    <aside className="rail">
      {runMetrics && (
        <section className="panel">
          <div className="panel-head">
            <h2>Run</h2>
          </div>
          <div className="panel-body">
            <div className="metric-grid">
              <Tile label="tasks" value={runMetrics.analysis_tasks} />
              <Tile label="MCP calls" value={runMetrics.mcp_tool_calls} />
              <Tile label="published" value={runMetrics.findings_published} />
              <Tile label="withheld" value={runMetrics.findings_rejected} />
              <Tile label="LLM calls" value={runMetrics.llm_calls} />
              <Tile label="seconds" value={runMetrics.runtime_seconds} />
            </div>
          </div>
        </section>
      )}

      {catalog && (
        <section className="panel">
          <div className="panel-head">
            <h2>Dataset</h2>
          </div>
          <div className="panel-body stack" style={{ gap: 10 }}>
            <p className="small muted" style={{ margin: 0 }}>
              {catalog.source}
            </p>
            <div className="rail-list">
              {catalog.tables.map((table) => (
                <div className="rail-row" key={table.name}>
                  <span className="mono">{table.name}</span>
                  <span className="mono">{table.row_count.toLocaleString()}</span>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {usedMetrics.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <h2>Metrics used</h2>
          </div>
          <div className="panel-body stack" style={{ gap: 8 }}>
            {usedMetrics.map((name) => (
              <div key={name}>
                <div className="mono small">{name}</div>
                <div className="small dim">{metricsById.get(name)?.description ?? ''}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {tasks.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <h2>Analysis tasks</h2>
          </div>
          <div className="panel-body rail-list">
            {tasks.map((task, index) => (
              <div className="rail-row" key={task.task_id}>
                <span>Task {index + 1}</span>
                <span className={`tag ${task.status === 'succeeded' ? 'supported' : task.status === 'failed' ? 'rejected' : 'partially_supported'}`}>
                  {task.status}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {queries.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <h2>Queries</h2>
          </div>
          <div className="panel-body rail-list">
            {queries.map((result) => (
              <button
                className="rail-row"
                key={result.result_id}
                onClick={() => onOpenResult(result.result_id)}
                style={{ background: 'none', border: 'none', padding: '2px 0', textAlign: 'left' }}
              >
                <span>{toolLabel(result.tool_name)}</span>
                <span className="mono">{formatDuration(result.duration_ms)}</span>
              </button>
            ))}
          </div>
        </section>
      )}
    </aside>
  )
}

/**
 * Counts from the previous value to the new one.
 *
 * These are measurements of a run in progress, and a number that jumps
 * reads as a different number rather than the same one having moved. The
 * animation is skipped entirely for anyone who has asked for less motion,
 * and for the first paint, where there is nothing to count from.
 */
function useCountUp(value: number, duration = 520): number {
  const [shown, setShown] = useState(value)
  const from = useRef(value)

  useEffect(() => {
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const start = from.current
    if (reduced || start === value || !Number.isFinite(value)) {
      from.current = value
      setShown(value)
      return
    }
    let frame = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / duration)
      // Ease out: fast to most of the value, then settle.
      const eased = 1 - (1 - p) ** 3
      setShown(start + (value - start) * eased)
      if (p < 1) frame = requestAnimationFrame(tick)
      else from.current = value
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [value, duration])

  return shown
}

function Tile({ label, value }: { label: string; value: number }) {
  const shown = useCountUp(value)
  // Fractional inputs (seconds) keep their precision; counts stay integers
  // while they climb so a tally never shows a fraction of a tool call.
  const decimals = Number.isInteger(value) ? 0 : 3
  const text = Number.isFinite(shown)
    ? shown.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })
    : String(value)
  return (
    <div className="metric-tile">
      <div className="value">{text}</div>
      <div className="label">{label}</div>
    </div>
  )
}
