/**
 * Resolving a chart's cited result into inline values, in the browser.
 *
 * The engine publishes a chart as an encoding plus the `result_id` of the
 * result it draws. It deliberately does not repeat the rows: they are
 * already in the payload once, under `results`, where verification and the
 * table read them. A chart that carried its own copy could disagree with
 * the table it sits beside, and there would be no way to tell which one was
 * the verified figure.
 *
 * So the join happens here, in memory, immediately before rendering. That
 * keeps one property worth keeping: a specification as it arrives from the
 * API is not renderable on its own. `checkChartSpec` rejects it for having
 * no inline data, and it only becomes renderable after being matched
 * against a snapshot that this client holds. Nothing from the wire reaches
 * Vega without having been resolved against verified columns first.
 *
 * This function never mutates its arguments. The API payload is shared with
 * the table, the provenance drawer and the comparison; a spec that grew a
 * `data` key in place would leak into all of them.
 */

import type { Cell, ChartSpec, ResultSnapshot } from './types'

export type ChartHydration =
  | { ok: true; spec: Record<string, unknown> }
  | { ok: false; reason: string }

/** Encoding channels whose field must name a column of the result. */
interface FieldRef {
  field: string
  type?: string
}

function encodingRefs(spec: Record<string, unknown>): FieldRef[] | null {
  const encoding = spec.encoding
  if (!encoding || typeof encoding !== 'object' || Array.isArray(encoding)) return null
  const refs: FieldRef[] = []
  for (const definition of Object.values(encoding as Record<string, unknown>)) {
    const entries = Array.isArray(definition) ? definition : [definition]
    for (const entry of entries) {
      if (!entry || typeof entry !== 'object') continue
      const { field, type } = entry as { field?: unknown; type?: unknown }
      if (typeof field === 'string') {
        refs.push({ field, type: typeof type === 'string' ? type : undefined })
      }
    }
  }
  return refs
}

/**
 * True when a specification already carries its own inline rows.
 *
 * The metric-registry path builds charts server-side and inlines the values
 * there. Those specifications are complete on arrival and are passed
 * through untouched rather than re-resolved, so that one rendering path
 * serves both.
 */
function hasInlineValues(spec: Record<string, unknown>): boolean {
  const data = spec.data
  if (!data || typeof data !== 'object' || Array.isArray(data)) return false
  return Array.isArray((data as { values?: unknown }).values)
}

function coerceQuantitative(value: Cell): number | null | undefined {
  if (value === null) return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : undefined
  }
  return undefined
}

/**
 * Turn a published chart and its cited result into a renderable spec.
 *
 * Returns a reason rather than throwing: a chart that cannot be resolved is
 * a chart to explain, not a crash that takes the report down with it.
 */
export function hydrateChartSpec(
  chart: ChartSpec,
  snapshot: ResultSnapshot | undefined,
): ChartHydration {
  const spec = chart.spec
  if (!spec || typeof spec !== 'object' || Array.isArray(spec)) {
    return { ok: false, reason: 'the chart specification is not an object' }
  }

  if (hasInlineValues(spec)) return { ok: true, spec }

  if (spec.data !== undefined) {
    // Present but not inline rows: a URL, a named dataset, a generator.
    return { ok: false, reason: 'chart data must be inline values' }
  }

  if (!snapshot) {
    return { ok: false, reason: `the result this chart cites (${chart.result_id}) is not in this run` }
  }
  if (snapshot.result_id !== chart.result_id) {
    return { ok: false, reason: `this chart cites ${chart.result_id}, not the result it was given` }
  }

  const refs = encodingRefs(spec)
  if (refs === null) return { ok: false, reason: 'chart has no encoding' }
  if (refs.length === 0) return { ok: false, reason: 'chart encoding names no columns' }

  const index = new Map<string, number>()
  snapshot.columns.forEach((column, position) => {
    if (!index.has(column)) index.set(column, position)
  })

  const quantitative = new Set<string>()
  for (const ref of refs) {
    if (!index.has(ref.field)) {
      return { ok: false, reason: `the chart plots "${ref.field}", not a column of the cited result` }
    }
    if (ref.type === 'quantitative') quantitative.add(ref.field)
  }

  const fields = [...new Set(refs.map((ref) => ref.field))]
  const values: Record<string, Cell>[] = []
  for (let row = 0; row < snapshot.rows.length; row += 1) {
    const cells = snapshot.rows[row]
    if (!Array.isArray(cells) || cells.length !== snapshot.columns.length) {
      return { ok: false, reason: `row ${row + 1} of the cited result does not match its columns` }
    }
    const record: Record<string, Cell> = {}
    for (const field of fields) {
      const cell = cells[index.get(field)!]
      if (quantitative.has(field)) {
        const numeric = coerceQuantitative(cell ?? null)
        if (numeric === undefined) {
          return {
            ok: false,
            reason: `"${field}" is plotted as a measure but row ${row + 1} is not a number`,
          }
        }
        record[field] = numeric
      } else {
        record[field] = cell ?? null
      }
    }
    values.push(record)
  }

  if (values.length === 0) {
    return { ok: false, reason: 'the cited result has no rows to plot' }
  }

  // A new object every time. The spec that arrived stays data-free.
  return { ok: true, spec: { ...spec, data: { values } } }
}
