/** Display helpers. Every one of these returns a string, never markup. */

export function formatNumber(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value)
  const magnitude = Math.abs(value)
  if (magnitude !== 0 && (magnitude < 0.001 || magnitude >= 1e12)) {
    return value.toExponential(3)
  }
  if (Number.isInteger(value)) return value.toLocaleString('en-US')
  return value.toLocaleString('en-US', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
}

export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return formatNumber(value)
  const text = String(value)
  // Cell values come from the dataset and may be long or hostile. React
  // escapes them; this only keeps the table readable.
  return text.length > 120 ? `${text.slice(0, 117)}…` : text
}

export function formatDuration(ms: number): string {
  if (ms < 1) return '<1 ms'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

export function formatPeriod(value: unknown): string {
  const text = String(value ?? '')
  return text.includes('T') ? text.split('T')[0]! : text
}

export function formatPValue(p: number): string {
  if (p < 0.001) return p.toExponential(2)
  return p.toFixed(4)
}

const KIND_LABELS: Record<string, string> = {
  calculated_fact: 'Calculated',
  statistical_result: 'Statistical',
  interpretation: 'Interpretation',
}

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

/** Display name for an agent role, used in both the activity log and the drawer. */
export function agentLabel(raw: string): string {
  const named: Record<string, string> = {
    analysis_worker: 'Analysis Agent',
    question_analyst: 'Question Analyst',
    planner: 'Analysis Planner',
    critic: 'Verifier',
    visualizer: 'Visualisation Agent',
    reporter: 'Report Agent',
  }
  return named[raw] ?? raw.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
