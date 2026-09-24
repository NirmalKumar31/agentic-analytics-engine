/**
 * Client-side Vega-Lite validation.
 *
 * The server already validates every specification it produces. This repeats
 * the check in the browser because the browser is where a bad spec would
 * actually execute, and because a recording is a file that could be served
 * from anywhere. Defence that only exists on one side of the wire is not
 * defence.
 */

const ALLOWED_MARKS = new Set(['bar', 'line', 'area', 'point', 'scatter'])
const ALLOWED_TYPES = new Set(['quantitative', 'nominal', 'ordinal', 'temporal'])
const FORBIDDEN_KEYS = new Set([
  'signals',
  'expr',
  'datasets',
  'transform',
  'params',
  'selection',
  'url',
  'loader',
  'usermeta',
  'onerror',
])

export interface ChartCheck {
  ok: boolean
  reason?: string
}

function scan(node: unknown, path: string): string | null {
  if (Array.isArray(node)) {
    for (let i = 0; i < node.length; i += 1) {
      const found = scan(node[i], `${path}[${i}]`)
      if (found) return found
    }
    return null
  }
  if (node && typeof node === 'object') {
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      if (FORBIDDEN_KEYS.has(key)) return `specification contains "${key}" at ${path}`
      const found = scan(value, `${path}.${key}`)
      if (found) return found
    }
  }
  return null
}

export function checkChartSpec(spec: Record<string, unknown>, columns: string[]): ChartCheck {
  const forbidden = scan(spec, '$')
  if (forbidden) return { ok: false, reason: forbidden }

  const data = spec.data as { values?: unknown } | undefined
  if (!data || !Array.isArray(data.values)) {
    return { ok: false, reason: 'chart data must be inline values' }
  }

  const mark = spec.mark as string | { type?: string } | undefined
  const markType = typeof mark === 'string' ? mark : mark?.type
  if (!markType || !ALLOWED_MARKS.has(markType)) {
    return { ok: false, reason: `mark "${String(markType)}" is not allowed` }
  }

  const encoding = spec.encoding as Record<string, unknown> | undefined
  if (!encoding || typeof encoding !== 'object') {
    return { ok: false, reason: 'chart has no encoding' }
  }

  const known = new Set(columns)
  for (const [channel, definition] of Object.entries(encoding)) {
    const entries = Array.isArray(definition) ? definition : [definition]
    for (const entry of entries) {
      if (!entry || typeof entry !== 'object') continue
      const { field, type } = entry as { field?: unknown; type?: unknown }
      if (typeof field === 'string' && !known.has(field)) {
        return { ok: false, reason: `${channel} references "${field}", not a column of the result` }
      }
      if (typeof type === 'string' && !ALLOWED_TYPES.has(type)) {
        return { ok: false, reason: `encoding type "${type}" is not allowed` }
      }
    }
  }
  return { ok: true }
}
