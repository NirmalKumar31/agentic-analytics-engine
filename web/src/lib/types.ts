/** Shapes the API returns. Kept narrow: only what the UI actually reads. */

export type EvidenceKind =
  "calculated_fact" | "statistical_result" | "interpretation";
export type VerificationStatus =
  "supported" | "partially_supported" | "unsupported";
export type TaskStatus = "succeeded" | "warning" | "failed";

export interface EvidenceCell {
  result_id: string;
  row: number;
  column: string;
  value: string | number | boolean | null;
  label: string | null;
}

export interface Finding {
  finding_id: string;
  text: string;
  kind: EvidenceKind;
  task_id: string | null;
  result_ids: string[];
  evidence_cells: EvidenceCell[];
  metric_ids: string[];
  verification_status: VerificationStatus;
  verifier_reason: string;
  numeric_check: NumericCheck | null;
  claimed_change: ClaimedChange | null;
}

export interface ClaimedChange {
  type: string;
  from: number;
  to: number;
  stated: number;
}

export interface NumericCheck {
  ok: boolean;
  reason: string;
  checks: {
    stated: number;
    matched: boolean;
    source: string;
    computed: number | null;
  }[];
}

export interface Verdict {
  finding_id: string;
  status: VerificationStatus;
  reason: string;
}

export interface StatisticalResult {
  test_name: string;
  statistic: number;
  p_value: number;
  sample_sizes: Record<string, number>;
  effect_size?: number | null;
  effect_size_name?: string | null;
  confidence_interval?: [number, number] | null;
  confidence_level: number;
  assumptions: string[];
  warnings: string[];
}

export type Cell = string | number | boolean | null;

/**
 * How much of a grouped answer a result carries.
 *
 * Absent means "not a grouped answer", never "complete": completeness was
 * previously inferred, and every signal available inferred it wrongly.
 */
export interface GroupCoverage {
  complete: boolean;
  groups_returned: number;
  groups_total?: number | null;
  rows_total?: number | null;
  rows_matching?: number | null;
  rows_represented?: number | null;
  query_limit?: number | null;
  ordering: "dimension" | "measure" | "period";
  ranked_by_request: boolean;
}

export interface ResultSnapshot {
  result_id: string;
  tool_name: string;
  task_id: string | null;
  sql: string | null;
  columns: string[];
  rows: Cell[][];
  row_count: number;
  truncated: boolean;
  dataset_fingerprint: string;
  duration_ms: number;
  parameters: Record<string, unknown>;
  warnings: string[];
  statistical_result: StatisticalResult | null;
  group_coverage?: GroupCoverage | null;
}

export interface TaskOutcome {
  task_id: string;
  status: TaskStatus;
  findings: unknown[];
  result_ids: string[];
  tool_calls: number;
  error: string | null;
  notes: string[];
}

export interface ChartSpec {
  chart_id: string;
  title: string;
  result_id: string;
  finding_ids: string[];
  spec: Record<string, unknown>;
}

export interface ReportSection {
  heading: string;
  finding_ids: string[];
}

export interface Report {
  question: string;
  executive_summary: string;
  key_findings: string[];
  sections: ReportSection[];
  limitations: string[];
  next_questions: string[];
}

export interface TraceCall {
  tool_name: string;
  agent: string;
  task_id: string | null;
  arguments: Record<string, unknown>;
  duration_ms: number;
  result_id: string | null;
  row_count: number | null;
  ok: boolean;
  error: string | null;
}

export interface DatasetCatalog {
  dataset_kind: string;
  source: string;
  dataset_fingerprint: string;
  tables: {
    name: string;
    row_count: number;
    columns: { name: string; type: string }[];
  }[];
  metrics_available: string[];
}

export interface RunMetrics {
  runtime_seconds: number;
  provider: string;
  analysis_tasks: number;
  tasks_succeeded: number;
  tasks_warned: number;
  tasks_failed: number;
  mcp_tool_calls: number;
  findings_published: number;
  findings_rejected: number;
  charts: number;
  llm_calls: number;
  [key: string]: unknown;
}

export interface RunPayload {
  /** Present when the run failed or was cancelled. */
  error?: string;
  /** How the run was executed, recorded on the run itself. */
  mode?: RunMode;
  provider_kind?: string;
  comparison_id?: string;
  requested_model?: string;
  resolved_model?: string;
  engine_version?: string;
  dataset_fingerprint?: string;
  usage?: RunUsage;
  query_contract?: QueryContract | null;

  run_id: string;
  question: string;
  dataset: DatasetCatalog;
  report: Report | null;
  findings: Finding[];
  rejected: Verdict[];
  charts: ChartSpec[];
  tasks: TaskOutcome[];
  results: Record<string, ResultSnapshot>;
  mcp_trace: TraceCall[];
  events: RunEvent[];
  metrics: RunMetrics;
  stopped_reason: string;
  status?: string;
  question_coverage?: QuestionCoverage | null;
  chart_decision?: ChartDecision | null;
  timings?: RunTimings | null;
  /**
   * True when the cloud planner returned nothing usable and the engine's
   * own contract executed instead. Compare Both must not present that as
   * the model independently agreeing.
   */
  planner_fallback?: boolean;
  title?: string;
  demonstrates?: string;
}

export interface QueryFilter {
  column: string;
  operator: string;
  value: string | number | null;
  source_text?: string;
}

/**
 * The semantic contract, stable across planner implementations.
 *
 * Deliberately excludes `explanation` and `interpretation`: those record who
 * read the wording, which differs between the two modes by construction and
 * would make every comparison read as a disagreement.
 */
export interface CanonicalContract {
  operation: string;
  table: string;
  measure?: string | null;
  /**
   * Authoritative, ordered, at most two. Read this, never `dimension`.
   *
   * `dimension` is a compatibility projection kept for one release: it is
   * populated only when there is exactly one grouping, and is null for
   * zero or two. A two-cut question read through the singular field looks
   * like a question with no grouping at all, which is how a correct
   * two-dimensional result came to publish an empty report.
   */
  dimensions: string[];
  dimension?: string | null;
  time_field?: string | null;
  /** Only ever set from explicit analytical language, never a column name. */
  time_grain?: "day" | "week" | "month" | "quarter" | "year" | null;
  period?: [string, string] | null;
  period_field?: string | null;
  filters: QueryFilter[];
  ascending: boolean;
}

/** Components a question can fix, and the gate can therefore require. */
export type CoverageComponent =
  | "operation"
  | "measure"
  | "dimensions"
  | "time_grain"
  | "period"
  | "filters"
  | "ranking_direction";

export type CoverageRejectionCode =
  | "unresolved_question"
  | "missing_requested_measure"
  | "missing_requested_grouping"
  | "missing_requested_time_grain"
  | "missing_requested_filter"
  | "changed_requested_operation"
  | "changed_ranking_direction"
  | "result_shape_too_large";

/**
 * Whether the executed contract covers what the question fixed.
 *
 * Distinct from three things it is easy to conflate it with: the two
 * planners agreeing (contract equality), the result holding every group
 * (`GroupCoverage`), and a claim being supported by its cells. Two
 * planners can agree on a contract that answers a different question, so
 * agreement must never be displayed as coverage.
 */
export interface QuestionCoverage {
  complete: boolean;
  required_components: CoverageComponent[];
  applied_components: CoverageComponent[];
  missing_components: CoverageComponent[];
  rejection_codes: CoverageRejectionCode[];
  details: string[];
}

export type ChartKind =
  "bar" | "line" | "grouped_bar" | "ranked_bar" | "kpi" | "none";

/**
 * How the chart was chosen. A pure function of contract and result shape,
 * computed in the engine -- not a model call, which is why identical
 * contracts over identical results now produce identical charts.
 */
export interface ChartDecision {
  kind: ChartKind;
  title?: string;
  spec?: Record<string, unknown>;
  /** Present when `kind` is `none`: why a chart would not help. */
  no_chart_reason?: string;
}

/** Stage durations in milliseconds. */
export interface RunTimings {
  planning_ms?: number;
  execution_ms?: number;
  verification_ms?: number;
  total_ms?: number;
}

export interface QueryContract extends CanonicalContract {
  confident: boolean;
  explanation: string;
  interpretation: string;
  contract_hash: string;
  canonical_contract?: CanonicalContract | null;
}

export type EventType =
  | "run_started"
  | "dataset_loaded"
  | "question_analyzed"
  | "plan_generated"
  | "analysis_task_started"
  | "mcp_tool_called"
  | "mcp_tool_completed"
  | "mcp_tool_failed"
  | "analysis_task_completed"
  | "analysis_task_failed"
  | "finding_proposed"
  | "finding_verified"
  | "finding_rejected"
  | "followup_round_started"
  | "chart_created"
  | "chart_rejected"
  | "report_started"
  | "report_completed"
  | "budget_exceeded"
  | "run_completed"
  | "run_failed"
  | "run_cancelled";

export interface RunEvent {
  event_id: string;
  seq: number;
  type: EventType;
  at: number;
  data: Record<string, unknown>;
}

export type ExecutionMode = "recorded" | "deterministic_live" | "ai_live";

/** Which decision-maker drives a run. The server validates this too. */
export type RunMode = "deterministic" | "ai";

/** A user choice. `compare` runs both and is not a provider mode. */
export type UiMode = RunMode | "compare";

export interface ModeCapability {
  mode: RunMode;
  available: boolean;
  label: string;
  description: string;
  reason: string;
  message: string;
}

export interface AILimits {
  runs_per_session: number;
  max_model_calls_per_run: number;
  max_runtime_seconds: number;
}

export interface Capabilities {
  modes: ModeCapability[];
  compare_available: boolean;
  ai_limits: AILimits | null;
}

export interface RunUsage {
  input_tokens: number;
  output_tokens: number;
  provider_attempts: number;
  estimated_cost_microdollars: number;
}

export interface ComparisonStarted {
  comparison_id: string;
  session_id: string;
  question: string;
  deterministic_run_id: string;
  ai_run_id: string;
}

export interface ServerConfig {
  version: string;
  provider_mode: string;
  execution_mode: ExecutionMode;
  model_inference_remote: boolean;
  live_analytics_enabled: boolean;
  uploads_enabled: boolean;
  demo_warehouse_ready: boolean;
  max_upload_mb: number;
  max_upload_columns: number;
  session_ttl_minutes: number;
  budgets: Record<string, number>;
  demo_questions: { id: string; question: string; why: string }[];
  recordings: RecordingSummary[];
  capabilities: Capabilities;
}

export interface RecordingSummary {
  recording_id: string;
  title: string;
  question: string;
  recorded_at: string;
  provider: string;
  provider_kind?: string;
  run_kind?: string;
  findings: number;
  rejected: number;
  charts: number;
  tasks: number;
  mcp_tool_calls: number;
  dataset_fingerprint: string;
  demonstrates: string;
}

export interface SessionPayload {
  session_id: string;
  catalog: DatasetCatalog;
  metrics: MetricInfo[];
  summary: DatasetSummary | null;
  expires_in_seconds: number;
}

/** Deterministic profile shown before the first question is asked. */
export interface DatasetSummary {
  table: string;
  row_count: number;
  /** Always "inferred": these roles come from types and cardinality, not
   *  from a governed definition anyone wrote down. */
  status: string;
  headline: string;
  fields: InferredField[];
  time_fields: string[];
  dimensions: string[];
  measures: string[];
  identifiers: string[];
  ambiguities: { concept: string; candidates: string[]; question: string }[];
}

export interface InferredField {
  name: string;
  data_type: string;
  role: "time" | "dimension" | "measure" | "identifier" | "ignored";
  null_pct: number;
  distinct_count: number;
  reason: string;
  /**
   * How safe it is to *suggest* summing this column, as distinct from
   * whether it may be summed when asked. The engine totals any numeric
   * column a visitor names; nothing proposes the total of a column whose
   * sum means nothing.
   */
  additive?: "strong" | "weak" | "unknown";
  /** Whether the role was a close call. Ambiguous columns are marked. */
  ambiguous?: boolean;
  min_value: string | null;
  max_value: string | null;
}

export interface MetricInfo {
  name: string;
  description: string;
  expression: string;
  valid_dimensions: string[];
  time_field: string;
  format: string;
}
