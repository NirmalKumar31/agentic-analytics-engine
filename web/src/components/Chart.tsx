import { useEffect, useLayoutEffect, useRef, useState } from 'react'

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
  const [width, setWidth] = useState(0)
  const [themeVersion, setThemeVersion] = useState(0)

  useLayoutEffect(() => {
    const element = host.current
    if (!element) return
    const update = () => setWidth(Math.floor(element.getBoundingClientRect().width))
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const root = document.documentElement
    const observer = new MutationObserver(() => setThemeVersion((value) => value + 1))
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])

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
    const css = getComputedStyle(document.documentElement)
    const token = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback
    const plotWidth = Math.max(240, width - 32)

    void import('vega-embed')
      .then(({ default: embed }) =>
        // The card renders the title above the plot, so the spec's own title
        // is suppressed rather than drawn twice.
        embed(element, {
          ...hydrated.spec,
          title: undefined,
          width: plotWidth,
          autosize: { type: 'fit', contains: 'padding' },
        } as never, {
          actions: false,
          // SVG, not canvas: a printed report is the artefact people keep,
          // and a canvas goes onto the page as a screen-resolution raster
          // that print styles cannot reach. The axis labels are pale
          // because the screen is dark; on white paper they have to be
          // restated in ink, and only SVG text can be.
          renderer: 'svg',
          config: {
            background: 'transparent',
            axis: {
              labelColor: token('--ink-secondary', '#46515c'),
              titleColor: token('--ink-secondary', '#46515c'),
              gridColor: token('--rule-hairline', '#d7dce0'),
            },
            legend: { labelColor: token('--ink-secondary', '#46515c'), titleColor: token('--ink-secondary', '#46515c') },
            range: { category: [token('--signal', '#126c72'), token('--action', '#c64b25'), '#5a6f95', '#8c6b38', '#5d7883'] },
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
  }, [chart, snapshot, width, themeVersion])

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
