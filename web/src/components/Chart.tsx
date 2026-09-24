import { useEffect, useRef, useState } from 'react'

import { checkChartSpec } from '../lib/chartSafety'
import type { ChartSpec, ResultSnapshot } from '../lib/types'

interface Props {
  chart: ChartSpec
  snapshot: ResultSnapshot | undefined
  onOpenProvenance?: (findingId: string) => void
}

/**
 * Renders one validated Vega-Lite chart.
 *
 * The specification is re-checked here before it reaches vega-embed: the
 * browser is where a hostile spec would actually run.
 */
export function Chart({ chart, snapshot, onOpenProvenance }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!host.current) return
    const columns = snapshot?.columns ?? []
    const check = checkChartSpec(chart.spec, columns)
    if (!check.ok) {
      setError(check.reason ?? 'the chart specification was rejected')
      return
    }

    let disposed = false
    let view: { finalize: () => void } | null = null
    const element = host.current

    void import('vega-embed')
      .then(({ default: embed }) =>
        // The card renders the title above the plot, so the spec's own title
        // is suppressed rather than drawn twice.
        embed(element, { ...chart.spec, title: undefined } as never, {
          actions: false,
          renderer: 'canvas',
          theme: 'dark',
          config: {
            background: 'transparent',
            axis: { labelColor: '#9aa5b8', titleColor: '#6b7688', gridColor: '#222a36' },
            legend: { labelColor: '#9aa5b8', titleColor: '#6b7688' },
            range: { category: ['#5b9cff', '#3fbf8f', '#a78bfa', '#e5a44c', '#4cc2d6'] },
          },
        }),
      )
      .then((result) => {
        if (disposed) {
          result.view.finalize()
          return
        }
        view = result.view
        setError(null)
      })
      .catch(() => setError('the chart could not be rendered'))

    return () => {
      disposed = true
      view?.finalize()
      element.replaceChildren()
    }
  }, [chart, snapshot])

  return (
    <figure className="chart-card" style={{ margin: 0 }}>
      <h4>{chart.title}</h4>
      {error ? (
        <div className="notice warn">{error}</div>
      ) : (
        <div className="chart-host" ref={host} role="img" aria-label={chart.title} />
      )}
      {chart.finding_ids.length > 0 && onOpenProvenance && (
        <div className="row" style={{ marginTop: 8 }}>
          <button
            className="btn ghost small"
            onClick={() => onOpenProvenance(chart.finding_ids[0]!)}
          >
            Show work
          </button>
          <span className="small dim mono">{chart.result_id}</span>
        </div>
      )}
    </figure>
  )
}
