/**
 * Two results from one question, reported side by side.
 *
 * There is deliberately no winner badge, score or accuracy ranking. The two
 * sides differ in how the analysis was planned; that is not evidence that
 * either is more accurate, and a badge saying otherwise would be a claim
 * the engine cannot support.
 */

import type { ReactNode } from 'react'
import type { RunPayload, RunUsage } from '../lib/types'

interface Side {
  title: string
  subtitle: string
  run: RunPayload | null
  error: string | null
  pending: boolean
  usage?: RunUsage
  children: ReactNode
}

interface Props {
  question: string
  deterministic: Side
  ai: Side
}

function statusLabel(side: Side): string {
  if (side.error) return 'Failed'
  if (side.pending) return 'Running'
  if (side.run) return 'Complete'
  return 'Not started'
}

function Pane({ side }: { side: Side }) {
  const status = statusLabel(side)
  return (
    <section className="compare-pane" aria-label={side.title}>
      <div className="panel-head">
        <h2>{side.title}</h2>
        <span className="spacer" style={{ flex: 1 }} />
        <span className={`tag status-${status.toLowerCase()}`} aria-live="polite">
          {status}
        </span>
      </div>
      <p className="small dim compare-subtitle">{side.subtitle}</p>

      {side.error ? (
        <p className="error" role="alert">
          {side.error}
        </p>
      ) : null}

      {side.usage ? (
        <dl className="usage-summary small dim">
          <div>
            <dt>Model calls</dt>
            <dd>{side.usage.provider_attempts}</dd>
          </div>
          <div>
            <dt>Input tokens</dt>
            <dd>{side.usage.input_tokens.toLocaleString()}</dd>
          </div>
          <div>
            <dt>Output tokens</dt>
            <dd>{side.usage.output_tokens.toLocaleString()}</dd>
          </div>
        </dl>
      ) : null}

      <div className="compare-body">{side.children}</div>
    </section>
  )
}

export function ComparisonView({ question, deterministic, ai }: Props) {
  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-head">
          <h2>Compare Both</h2>
        </div>
        <div className="panel-body stack">
          <p style={{ margin: 0 }}>{question}</p>
          <p className="small dim" style={{ margin: 0 }}>
            The same question, planned two ways. Deterministic Analytics uses rule-based
            planning; AI Analytics uses a cloud model to plan and interpret. Both run the
            same analytics engine, the same SQL guard, the same statistics, the same
            verification and the same publication checks, against the same dataset. The
            results are shown independently and are not ranked.
          </p>
        </div>
      </section>

      <div className="compare-grid">
        <Pane side={deterministic} />
        <Pane side={ai} />
      </div>
    </div>
  )
}
