import { kindLabel } from '../lib/format'
import type { ChartSpec, Finding, QueryContract, ResultSnapshot, Verdict } from '../lib/types'
import { Chart } from './Chart'

interface Props {
  question: string
  report: import('../lib/types').Report | null
  findings: Finding[]
  rejected: Verdict[]
  charts: ChartSpec[]
  results: Record<string, ResultSnapshot>
  queryContract?: QueryContract | null
  onShowWork: (findingId: string) => void
}

export function ReportView({
  question,
  report,
  findings,
  rejected,
  charts,
  results,
  queryContract,
  onShowWork,
}: Props) {
  const byId = new Map(findings.map((f) => [f.finding_id, f]))

  return (
    <div className="stack">
      <section className="panel">
        <div className="panel-head">
          <h2>Report</h2>
          <span className="spacer" />
          <span className="small dim">
            {findings.length} published · {rejected.length} withheld
          </span>
          <button className="btn ghost small no-print" onClick={() => window.print()}>
            Print / Save PDF
          </button>
        </div>
        <div className="panel-body stack">
          <p className="small dim" style={{ margin: 0 }}>
            Question
          </p>
          <p style={{ margin: 0, fontSize: 15 }}>{question}</p>

          {queryContract?.confident ? (
            <div className="applied-analysis" data-testid="applied-analysis">
              <h3>Applied analysis</h3>
              <dl className="contract-summary small">
                <div>
                  <dt>Calculation</dt>
                  <dd>{queryContract.operation}</dd>
                </div>
                {queryContract.measure ? (
                  <div>
                    <dt>Measure</dt>
                    <dd>{queryContract.measure.replaceAll('_', ' ')}</dd>
                  </div>
                ) : null}
                {queryContract.dimension ? (
                  <div>
                    <dt>Grouped by</dt>
                    <dd>{queryContract.dimension.replaceAll('_', ' ')}</dd>
                  </div>
                ) : null}
                {queryContract.filters.map((filter, index) => (
                  <div key={`${filter.column}-${filter.operator}-${index}`}>
                    <dt>Filter</dt>
                    <dd>
                      {filter.column.replaceAll('_', ' ')} {filter.operator}
                      {filter.value === null || filter.value === '' ? '' : ` ${filter.value}`}
                    </dd>
                  </div>
                ))}
                {queryContract.period ? (
                  <div>
                    <dt>Period</dt>
                    <dd>
                      {queryContract.period[0]} to {queryContract.period[1]}
                    </dd>
                  </div>
                ) : null}
              </dl>
            </div>
          ) : null}

          {report && (
            <>
              <h3 style={{ margin: '6px 0 0', fontSize: 13, color: 'var(--text-muted)' }}>
                Executive summary
              </h3>
              <p className="muted" style={{ margin: 0 }}>
                {report.executive_summary}
              </p>
            </>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Key findings</h2>
          <span className="spacer" />
          <span className="small dim">click any finding to see how it was derived</span>
        </div>
        <div className="panel-body stack">
          {findings.length === 0 ? (
            <p className="small dim" style={{ margin: 0 }}>
              No verified finding answered the requested analysis.
            </p>
          ) : (
            findings.map((finding) => (
              <article className={`finding ${finding.kind}`} key={finding.finding_id}>
                <p className="finding-text">{finding.text}</p>
                <div className="finding-foot">
                  <span className={`tag ${finding.verification_status}`}>
                    {finding.verification_status === 'supported' ? 'Supported' : 'Held back'}
                  </span>
                  <span className={`tag ${finding.kind}`}>{kindLabel(finding.kind)}</span>
                  <span className="spacer" style={{ flex: 1 }} />
                  <button className="btn ghost small" onClick={() => onShowWork(finding.finding_id)}>
                    Show work →
                  </button>
                </div>
              </article>
            ))
          )}
        </div>
      </section>

      {charts.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <h2>Charts</h2>
            <span className="spacer" />
            <span className="small dim">built from cited query results</span>
          </div>
          <div className="panel-body stack">
            {charts.map((chart) => (
              <Chart
                key={chart.chart_id}
                chart={chart}
                snapshot={results[chart.result_id]}
                onOpenProvenance={onShowWork}
              />
            ))}
          </div>
        </section>
      )}

      {report && report.sections.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <h2>Analysis</h2>
          </div>
          <div className="panel-body stack">
            {report.sections.map((section) => (
              <div key={section.heading} className="stack" style={{ gap: 6 }}>
                <h3 style={{ margin: 0, fontSize: 14 }}>{section.heading}</h3>
                {section.finding_ids.length > 0 && (
                  <div className="chip-row">
                    {section.finding_ids.map((id) => {
                      const finding = byId.get(id)
                      return (
                        <button
                          key={id}
                          className="chip"
                          onClick={() => onShowWork(id)}
                          title={finding?.text}
                          style={{ cursor: 'pointer' }}
                        >
                          {id}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {rejected.length > 0 && (
        <section className="panel">
          <div className="panel-head">
            <h2>Withheld findings</h2>
            <span className="spacer" />
            <span className="small dim">proposed but withheld by publication checks</span>
          </div>
          <div className="panel-body stack">
            {rejected.map((verdict) => (
              <div className="notice" key={verdict.finding_id}>
                <span className={`tag ${verdict.status}`}>{verdict.status.replace('_', ' ')}</span>{' '}
                {verdict.reason}
              </div>
            ))}
          </div>
        </section>
      )}

      {report && (report.limitations.length > 0 || report.next_questions.length > 0) && (
        <section className="panel">
          <div className="panel-head">
            <h2>Limitations and next questions</h2>
          </div>
          <div className="panel-body stack">
            {report.limitations.length > 0 && (
              <div>
                <p className="small dim" style={{ margin: '0 0 6px' }}>
                  Limitations
                </p>
                <ul className="list small">
                  {report.limitations.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
            {report.next_questions.length > 0 && (
              <div>
                <p className="small dim" style={{ margin: '0 0 6px' }}>
                  Worth asking next
                </p>
                <ul className="list small">
                  {report.next_questions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
