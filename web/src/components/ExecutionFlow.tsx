import type { RunEvent } from '../lib/types'

interface PlanTask {
  task_id: string
  objective: string
  preferred_tool: string
  analysis_type?: string
  metrics?: string[]
  dimensions?: string[]
}

type NodeState = 'idle' | 'active' | 'done' | 'failed'

interface Props {
  events: RunEvent[]
}

/**
 * The plan becoming analytical branches.
 *
 * Every node and edge state is derived from run events. A branch only exists
 * because the planner emitted that task, and only lights up because that task
 * actually started. There is no timed animation standing in for progress.
 */
export function ExecutionFlow({ events }: Props) {
  const byType = (type: string) => events.filter((e) => e.type === type)

  const planEvent = byType('plan_generated')[0]
  const tasks = ((planEvent?.data.tasks as PlanTask[] | undefined) ?? []).slice(0, 6)

  const started = new Set(byType('analysis_task_started').map((e) => e.data.task_id as string))
  const completed = new Map(
    byType('analysis_task_completed').map((e) => [e.data.task_id as string, e.data.status as string]),
  )
  const failed = new Set(byType('analysis_task_failed').map((e) => e.data.task_id as string))

  const planState: NodeState = planEvent ? 'done' : events.length ? 'active' : 'idle'
  const verifyState: NodeState = byType('report_started').length
    ? 'done'
    : byType('finding_verified').length || byType('finding_rejected').length
      ? 'active'
      : 'idle'
  const reportState: NodeState = byType('report_completed').length
    ? 'done'
    : byType('report_started').length
      ? 'active'
      : 'idle'

  const taskState = (id: string): NodeState => {
    if (failed.has(id)) return 'failed'
    const status = completed.get(id)
    if (status) return status === 'failed' ? 'failed' : 'done'
    return started.has(id) ? 'active' : 'idle'
  }

  // Node boxes are 148 wide; the lane pitch and the outer margin both have
  // to clear that, or the first and last branches render past the viewBox.
  const nodeWidth = 148
  const margin = nodeWidth / 2 + 10
  const width = Math.max(700, tasks.length * (nodeWidth + 18) + margin * 2)
  const branchY = 96
  const mcpY = 168
  const verifyY = 240
  const reportY = 312
  const centre = width / 2
  const columns = tasks.map((_, index) =>
    tasks.length === 1
      ? centre
      : margin + (index * (width - margin * 2)) / Math.max(tasks.length - 1, 1),
  )

  return (
    <div className="flow">
      <svg viewBox={`0 0 ${width} 360`} preserveAspectRatio="xMidYMin meet">
        <FlowNode x={centre - 52} y={16} w={104} h={34} label="PLAN" state={planState} />

        {tasks.map((task, index) => {
          const x = columns[index]!
          const state = taskState(task.task_id)
          const running = state === 'active'
          return (
            <g key={task.task_id}>
              <path
                className={`flow-edge ${running ? 'running' : state !== 'idle' ? 'lit' : ''}`}
                d={`M ${centre} 50 C ${centre} ${branchY - 20}, ${x} ${branchY - 34}, ${x} ${branchY}`}
              />
              <FlowNode
                x={x - nodeWidth / 2}
                y={branchY}
                w={nodeWidth}
                h={42}
                label={metricLabel(task)}
                sublabel={cutLabel(task)}
                state={state}
                title={task.objective}
              />
              <path
                className={`flow-edge ${state === 'done' ? 'lit' : running ? 'running' : ''}`}
                d={`M ${x} ${branchY + 42} L ${x} ${mcpY}`}
              />
              <FlowNode
                x={x - nodeWidth / 2}
                y={mcpY}
                w={nodeWidth}
                h={28}
                label={`MCP · ${toolLabel(task.preferred_tool)}`}
                state={state}
                mono
              />
              <path
                className={`flow-edge ${state === 'done' ? 'lit' : ''}`}
                d={`M ${x} ${mcpY + 28} C ${x} ${verifyY - 24}, ${centre} ${verifyY - 34}, ${centre} ${verifyY}`}
              />
            </g>
          )
        })}

        {tasks.length === 0 && (
          <path className="flow-edge" d={`M ${centre} 50 L ${centre} ${verifyY}`} />
        )}

        <FlowNode x={centre - 56} y={verifyY} w={112} h={34} label="VERIFY" state={verifyState} />
        <path
          className={`flow-edge ${reportState !== 'idle' ? 'lit' : ''}`}
          d={`M ${centre} ${verifyY + 34} L ${centre} ${reportY}`}
        />
        <FlowNode x={centre - 56} y={reportY} w={112} h={34} label="REPORT" state={reportState} />
      </svg>
    </div>
  )
}

function FlowNode({
  x,
  y,
  w,
  h,
  label,
  sublabel,
  state,
  mono,
  title,
}: {
  x: number
  y: number
  w: number
  h: number
  label: string
  sublabel?: string
  state: NodeState
  mono?: boolean
  title?: string
}) {
  const centreY = sublabel ? y + h / 2 - 3 : y + h / 2 + 3.5
  return (
    <g className={`flow-node ${state}`}>
      {title && <title>{title}</title>}
      <rect x={x} y={y} width={w} height={h} rx={6} />
      <text
        x={x + w / 2}
        y={centreY}
        textAnchor="middle"
        style={mono ? { fontFamily: 'var(--mono)', fontSize: 10 } : undefined}
      >
        {label}
      </text>
      {sublabel && (
        <text
          x={x + w / 2}
          y={y + h / 2 + 11}
          textAnchor="middle"
          style={{ fontSize: 9.5, opacity: 0.68 }}
        >
          {sublabel}
        </text>
      )}
    </g>
  )
}

/** The metric a branch measures. */
function metricLabel(task: PlanTask): string {
  const metric = task.metrics?.[0] ?? firstWords(task.objective)
  return truncate(metric, 20)
}

/**
 * What distinguishes this branch from its siblings.
 *
 * Several tasks often share a metric, so labelling by metric alone renders a
 * row of identical boxes. The cut -- the dimension, the grain, or the test --
 * is the part worth showing.
 */
function cutLabel(task: PlanTask): string {
  if (task.dimensions && task.dimensions.length > 0) {
    return truncate(`by ${task.dimensions.join(', ')}`, 22)
  }
  if (task.preferred_tool === 'analyze_timeseries') return 'over time'
  if (task.preferred_tool === 'statistical_test') return 'significance test'
  if (task.analysis_type) return task.analysis_type.replace(/_/g, ' ')
  return ''
}

function firstWords(objective: string): string {
  const cleaned = objective.replace(/^(Track|Compare|Measure|Check|Test|Compute)\s+/i, '')
  return cleaned.split(/\s+/).slice(0, 2).join(' ')
}

function truncate(text: string, length: number): string {
  return text.length > length ? `${text.slice(0, length - 1)}…` : text
}

/** Tool names are long enough to overflow the node at mono 10px. */
function toolLabel(tool: string): string {
  const short: Record<string, string> = {
    analyze_timeseries: 'timeseries',
    compare_segments: 'segments',
    compute_metric: 'metric',
    correlation_matrix: 'correlation',
    statistical_test: 'stat test',
    run_readonly_sql: 'sql',
    profile_table: 'profile',
  }
  return short[tool] ?? tool
}
