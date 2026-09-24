import { useEffect, useRef } from 'react'

import { agentLabel, formatDuration, formatNumber, formatPValue, kindLabel } from '../lib/format'
import type { Finding, ResultSnapshot, TaskOutcome, TraceCall } from '../lib/types'
import { ResultTable } from './ResultTable'

interface Props {
  finding: Finding
  results: Record<string, ResultSnapshot>
  tasks: TaskOutcome[]
  trace: TraceCall[]
  datasetFingerprint: string
  onClose: () => void
}

/**
 * How a published number was derived.
 *
 * Everything shown here is read out of the run artefact: the task, the SQL
 * that ran, the rows that came back, the specific cells the claim cites, the
 * arithmetic the engine recomputed, and the MCP calls involved. No model
 * reasoning is displayed, because none is recorded.
 */
export function ProvenanceDrawer({
  finding,
  results,
  tasks,
  trace,
  datasetFingerprint,
  onClose,
}: Props) {
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const cited = finding.result_ids.map((id) => results[id]).filter(Boolean) as ResultSnapshot[]
  const task = tasks.find((t) => t.task_id === finding.task_id)
  const calls = trace.filter((c) => c.result_id && finding.result_ids.includes(c.result_id))
  const change = finding.claimed_change

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="How this was derived">
        <header className="drawer-head">
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2>How this was derived</h2>
            <p className="small muted" style={{ margin: 0 }}>
              {finding.text}
            </p>
          </div>
          <button ref={closeRef} className="btn ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <div className="drawer-body">
          <section className="prov-section">
            <h3>Finding</h3>
            <div className="row">
              <span className={`tag ${finding.kind}`}>{kindLabel(finding.kind)}</span>
              <span className={`tag ${finding.verification_status}`}>
                {finding.verification_status.replace('_', ' ')}
              </span>
              <span className="mono small dim">{finding.finding_id}</span>
            </div>
            <p className="small muted" style={{ margin: 0 }}>
              <strong className="dim">Verifier: </strong>
              {finding.verifier_reason}
            </p>
          </section>

          {task && (
            <section className="prov-section">
              <h3>Analytical task</h3>
              <dl className="kv">
                <dt>Task</dt>
                <dd className="mono">{task.task_id}</dd>
                <dt>Status</dt>
                <dd>{task.status}</dd>
                <dt>Tool calls</dt>
                <dd>{task.tool_calls}</dd>
              </dl>
            </section>
          )}

          {finding.metric_ids.length > 0 && (
            <section className="prov-section">
              <h3>Metrics</h3>
              <div className="chip-row">
                {finding.metric_ids.map((metric) => (
                  <span key={metric} className="chip">
                    {metric}
                  </span>
                ))}
              </div>
            </section>
          )}

          {cited.map((snapshot) => (
            <section className="prov-section" key={snapshot.result_id}>
              <h3>
                Query · {snapshot.tool_name} · {snapshot.result_id}
              </h3>
              {snapshot.sql ? (
                <pre className="sql">{snapshot.sql}</pre>
              ) : (
                <p className="small dim" style={{ margin: 0 }}>
                  This tool did not execute SQL.
                </p>
              )}
              <ResultTable
                snapshot={snapshot}
                highlight={finding.evidence_cells.filter(
                  (c) => c.result_id === snapshot.result_id,
                )}
              />
              {snapshot.warnings.length > 0 && (
                <ul className="list small">
                  {snapshot.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              )}
              {snapshot.statistical_result && (
                <StatisticalPanel result={snapshot.statistical_result} />
              )}
            </section>
          ))}

          {finding.evidence_cells.length > 0 && (
            <section className="prov-section">
              <h3>Referenced cells</h3>
              <div className="cell-ref">
                {finding.evidence_cells.map((cell, index) => (
                  <span className="cell-chip" key={`${cell.result_id}-${index}`}>
                    {cell.label ?? `${cell.column}[${cell.row}]`} = {formatNumber(cell.value)}
                  </span>
                ))}
              </div>
            </section>
          )}

          {change && (
            <section className="prov-section">
              <h3>Calculation</h3>
              <div className="calc">
                <div className="row">
                  <span>from</span>
                  <span>{formatNumber(change.from)}</span>
                </div>
                <div className="row">
                  <span>to</span>
                  <span>{formatNumber(change.to)}</span>
                </div>
                <div className="row">
                  <span>{change.type.replace('_', ' ')}</span>
                  <span>{formatNumber(change.stated)}</span>
                </div>
                {finding.numeric_check?.ok && (
                  <div className="row">
                    <span>recomputed</span>
                    <span className="ok">
                      matches — the engine recalculated this from the cells above
                    </span>
                  </div>
                )}
              </div>
            </section>
          )}

          {finding.numeric_check && finding.numeric_check.checks.length > 0 && (
            <section className="prov-section">
              <h3>Numeric checks</h3>
              <div className="calc">
                {finding.numeric_check.checks.map((check, index) => (
                  <div className="row" key={index}>
                    <span>{formatNumber(check.stated)}</span>
                    <span className={check.matched ? 'ok' : undefined}>
                      {check.matched ? `✓ ${check.source}` : '✗ not found in the cited results'}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {calls.length > 0 && (
            <section className="prov-section">
              <h3>Agent and tool path</h3>
              <div className="activity">
                {calls.map((call, index) => (
                  <div className="activity-row tool" key={index}>
                    <span className="activity-icon" />
                    <span className="activity-label">
                      <b>{agentLabel(call.agent)}</b> → MCP:{' '}
                      <span className="mono">{call.tool_name}</span>
                      {call.result_id ? <span className="dim"> → {call.result_id}</span> : null}
                    </span>
                    <span className="activity-meta">{formatDuration(call.duration_ms)}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="prov-section">
            <h3>Dataset fingerprint</h3>
            <p className="mono small dim" style={{ margin: 0, overflowWrap: 'anywhere' }}>
              {datasetFingerprint}
            </p>
          </section>
        </div>
      </aside>
    </>
  )
}

function StatisticalPanel({
  result,
}: {
  result: NonNullable<ResultSnapshot['statistical_result']>
}) {
  return (
    <div className="stack">
      <dl className="kv">
        <dt>Test</dt>
        <dd>{result.test_name}</dd>
        <dt>Statistic</dt>
        <dd className="mono">{formatNumber(result.statistic)}</dd>
        <dt>p-value</dt>
        <dd className="mono">{formatPValue(result.p_value)}</dd>
        {result.effect_size != null && (
          <>
            <dt>{result.effect_size_name ?? 'Effect size'}</dt>
            <dd className="mono">{formatNumber(result.effect_size)}</dd>
          </>
        )}
        {result.confidence_interval && (
          <>
            <dt>{Math.round(result.confidence_level * 100)}% interval</dt>
            <dd className="mono">
              [{formatNumber(result.confidence_interval[0])},{' '}
              {formatNumber(result.confidence_interval[1])}]
            </dd>
          </>
        )}
        <dt>Sample sizes</dt>
        <dd className="mono">
          {Object.entries(result.sample_sizes)
            .map(([name, size]) => `${name}=${size.toLocaleString()}`)
            .join(', ')}
        </dd>
      </dl>
      {result.assumptions.length > 0 && (
        <div>
          <p className="small dim" style={{ margin: '0 0 4px' }}>
            Assumptions
          </p>
          <ul className="list small">
            {result.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      )}
      {result.warnings.length > 0 && (
        <ul className="list small">
          {result.warnings.map((w) => (
            <li key={w} style={{ color: 'var(--warning)' }}>
              {w}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
