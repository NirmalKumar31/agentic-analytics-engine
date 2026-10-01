import { useEffect, useRef, useState } from 'react'

import { hydrateChartSpec } from '../lib/chartHydration'
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
 * Two steps, in this order. The published specification cites the result it
 * draws rather than carrying a second copy of the rows, so it is first
 * resolved against that snapshot in memory; then the resolved specification
 * is re-checked before it reaches vega-embed, because the browser is where
 * a hostile spec would actually run. Checking only what arrived from the
 * wire would validate something other than what renders.
 */
export function Chart({ chart, snapshot, onOpenProvenance }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!host.current) return
    const hydrated = hydrateChartSpec(chart, snapshot)
    if (!hydrated.ok) {
      setError(hydrated.reason)
      return
    }
    const columns = snapshot?.columns ?? []
    const check = checkChartSpec(hydrated.spec, columns)
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
        embed(element, { ...hydrated.spec, title: undefined } as never, {
          actions: false,
          // SVG, not canvas: a printed report is the artefact people keep,
          // and a canvas goes onto the page as a screen-resolution raster
          // that print styles cannot reach. The axis labels are pale
          // because the screen is dark; on white paper they have to be
          // restated in ink, and only SVG text can be.
          renderer: 'svg',
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
        </div>
      )}
    </figure>
  )
}
