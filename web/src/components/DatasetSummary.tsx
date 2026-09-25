import type { DatasetSummary as Summary } from '../lib/types'

const ROLE_ORDER = ['time', 'measure', 'dimension', 'identifier', 'ignored'] as const

const ROLE_LABEL: Record<string, string> = {
  time: 'time',
  measure: 'measure',
  dimension: 'dimension',
  identifier: 'identifier',
  ignored: 'not used',
}

/**
 * What the engine worked out about an uploaded file, before any question.
 *
 * Every role here is inferred from column types and cardinality, which the
 * panel says plainly — an inferred measure is not a governed metric, and
 * conflating the two would be inventing business semantics.
 */
export function DatasetSummary({
  summary,
  onAsk,
}: {
  summary: Summary
  onAsk?: (question: string) => void
}) {
  const shown = [...summary.fields].sort(
    (a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role),
  )

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Dataset understanding</h2>
        <span className="spacer" />
        <span className="tag">inferred</span>
      </div>
      <div className="panel-body stack">
        <p className="muted" style={{ margin: 0 }}>
          {summary.headline}
        </p>

        <div className="table-wrap" style={{ maxHeight: 280 }}>
          <table className="data">
            <thead>
              <tr>
                <th scope="col">column</th>
                <th scope="col">type</th>
                <th scope="col">role</th>
                <th scope="col">distinct</th>
                <th scope="col">null %</th>
                <th scope="col">why</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((field) => (
                <tr key={field.name}>
                  <td>{field.name}</td>
                  <td className="dim">{field.data_type}</td>
                  <td>
                    <span className={`tag role-${field.role}`}>{ROLE_LABEL[field.role]}</span>
                  </td>
                  <td>{field.distinct_count.toLocaleString()}</td>
                  <td>{field.null_pct.toFixed(1)}</td>
                  <td className="dim">{field.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="small dim" style={{ margin: 0 }}>
          Roles are inferred from column types and cardinality. They are not governed
          metric definitions — the engine does not know what your columns mean.
        </p>

        {summary.ambiguities.length > 0 && (
          <div className="notice warn">
            <strong>One thing to confirm</strong>
            <ul className="list small" style={{ marginTop: 6 }}>
              {summary.ambiguities.map((item) => (
                <li key={item.concept}>{item.question}</li>
              ))}
            </ul>
          </div>
        )}

        {onAsk && summary.measures.length > 0 && (
          <div className="example-list">
            {suggestions(summary).map((question) => (
              <button className="example" key={question} onClick={() => onAsk(question)}>
                {question}
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

/** Questions this dataset can actually answer, from its inferred shape. */
function suggestions(summary: Summary): string[] {
  const out: string[] = []
  const measure = summary.measures[0]
  const dimension = summary.dimensions[0]
  const time = summary.time_fields[0]
  if (measure && dimension) out.push(`What is total ${measure} by ${dimension}?`)
  if (measure && time) out.push(`How did ${measure} change over time?`)
  if (measure && dimension) out.push(`Which ${dimension} contributes most to ${measure}?`)
  return out.slice(0, 3)
}
