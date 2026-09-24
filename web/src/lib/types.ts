/** Shapes the API returns. Kept narrow: only what the UI actually reads. */

export type EvidenceKind = 'calculated_fact' | 'statistical_result' | 'interpretation'
export type VerificationStatus = 'supported' | 'partially_supported' | 'unsupported'
export type TaskStatus = 'succeeded' | 'warning' | 'failed'

export interface EvidenceCell {
  result_id: string
  row: number
  column: string
  value: string | number | boolean | null
  label: string | null
}

export interface Finding {
  finding_id: string
  text: string
  kind: EvidenceKind
  task_id: string | null
  result_ids: string[]
  evidence_cells: EvidenceCell[]
  metric_ids: string[]
  verification_status: VerificationStatus
  verifier_reason: string
  numeric_check: NumericCheck | null
  claimed_change: ClaimedChange | null
}

export interface ClaimedChange {
  type: string
  from: number
  to: number
  stated: number
}

export interface NumericCheck {
  ok: boolean
  reason: string
  checks: { stated: number; matched: boolean; source: string; computed: number | null }[]
}

export interface Verdict {
  finding_id: string
  status: VerificationStatus
  reason: string
}

export interface StatisticalResult {
  test_name: string
  statistic: number
  p_value: number
  sample_sizes: Record<string, number>
  effect_size?: number | null
  effect_size_name?: string | null
  confidence_interval?: [number, number] | null
  confidence_level: number
  assumptions: string[]
  warnings: string[]
}

export type Cell = string | number | boolean | null

export interface ResultSnapshot {
  result_id: string
  tool_name: string
  task_id: string | null
  sql: string | null
  columns: string[]
  rows: Cell[][]
  row_count: number
  truncated: boolean
  dataset_fingerprint: string
  duration_ms: number
  parameters: Record<string, unknown>
  warnings: string[]
  statistical_result: StatisticalResult | null
}

export interface TaskOutcome {
  task_id: string
  status: TaskStatus
  findings: unknown[]
  result_ids: string[]
  tool_calls: number
  error: string | null
  notes: string[]
}

export interface ChartSpec {
  chart_id: string
  title: string
  result_id: string
  finding_ids: string[]
  spec: Record<string, unknown>
}

export interface ReportSection {
  heading: string
  body: string
  finding_ids: string[]
}

export interface Report {
  question: string
  executive_summary: string
  key_findings: string[]
  sections: ReportSection[]
  limitations: string[]
  next_questions: string[]
}

export interface TraceCall {
  tool_name: string
  agent: string
  task_id: string | null
  arguments: Record<string, unknown>
  duration_ms: number
  result_id: string | null
  row_count: number | null
  ok: boolean
  error: string | null
}

export interface DatasetCatalog {
  dataset_kind: string
  source: string
  dataset_fingerprint: string
  tables: { name: string; row_count: number; columns: { name: string; type: string }[] }[]
  metrics_available: string[]
}

export interface RunMetrics {
  runtime_seconds: number
  provider: string
  analysis_tasks: number
  tasks_succeeded: number
  tasks_warned: number
  tasks_failed: number
  mcp_tool_calls: number
  findings_published: number
  findings_rejected: number
  charts: number
  llm_calls: number
  [key: string]: unknown
}

export interface RunPayload {
  run_id: string
  question: string
  dataset: DatasetCatalog
  report: Report | null
  findings: Finding[]
  rejected: Verdict[]
  charts: ChartSpec[]
  tasks: TaskOutcome[]
  results: Record<string, ResultSnapshot>
  mcp_trace: TraceCall[]
  events: RunEvent[]
  metrics: RunMetrics
  stopped_reason: string
  status?: string
  title?: string
  demonstrates?: string
}

export type EventType =
  | 'run_started'
  | 'dataset_loaded'
  | 'question_analyzed'
  | 'plan_generated'
  | 'analysis_task_started'
  | 'mcp_tool_called'
  | 'mcp_tool_completed'
  | 'mcp_tool_failed'
  | 'analysis_task_completed'
  | 'analysis_task_failed'
  | 'finding_proposed'
  | 'finding_verified'
  | 'finding_rejected'
  | 'followup_round_started'
  | 'chart_created'
  | 'chart_rejected'
  | 'report_started'
  | 'report_completed'
  | 'budget_exceeded'
  | 'run_completed'
  | 'run_failed'

export interface RunEvent {
  event_id: string
  seq: number
  type: EventType
  at: number
  data: Record<string, unknown>
}

export interface ServerConfig {
  version: string
  provider_mode: string
  live_analytics_enabled: boolean
  uploads_enabled: boolean
  demo_warehouse_ready: boolean
  max_upload_mb: number
  budgets: Record<string, number>
  demo_questions: { id: string; question: string; why: string }[]
  recordings: RecordingSummary[]
}

export interface RecordingSummary {
  recording_id: string
  title: string
  question: string
  recorded_at: string
  provider: string
  findings: number
  rejected: number
  charts: number
  tasks: number
  mcp_tool_calls: number
  dataset_fingerprint: string
  demonstrates: string
}

export interface SessionPayload {
  session_id: string
  catalog: DatasetCatalog
  metrics: MetricInfo[]
}

export interface MetricInfo {
  name: string
  description: string
  expression: string
  valid_dimensions: string[]
  time_field: string
  format: string
}
