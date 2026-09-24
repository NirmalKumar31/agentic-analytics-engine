import { formatDuration } from '../lib/format'
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
            <p className="small dim mono" style={{ margin: 0, overflowWrap: 'anywhere' }}>
              {catalog.dataset_fingerprint}
            </p>
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
            {tasks.map((task) => (
              <div className="rail-row" key={task.task_id}>
                <span className="mono">{task.task_id}</span>
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
                <span className="mono">{result.tool_name}</span>
                <span className="mono">{formatDuration(result.duration_ms)}</span>
              </button>
            ))}
          </div>
        </section>
      )}
    </aside>
  )
}

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric-tile">
      <div className="value">{typeof value === 'number' ? value.toLocaleString() : value}</div>
      <div className="label">{label}</div>
    </div>
  )
}
