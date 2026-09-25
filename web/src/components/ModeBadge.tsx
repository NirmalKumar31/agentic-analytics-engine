import type { ExecutionMode } from '../lib/types'

const LABELS: Record<ExecutionMode, { label: string; tone: string; title: string }> = {
  recorded: {
    label: 'Recorded',
    tone: 'replay',
    title:
      'Replaying a committed run. The queries, results and verification decisions are ' +
      'exactly those the recorded run produced.',
  },
  deterministic_live: {
    label: 'Deterministic live',
    tone: 'deterministic',
    title:
      'The graph, MCP tools, SQL and verification execute now. Agent decisions come ' +
      'from a scripted deterministic provider, not a language model.',
  },
  ai_live: {
    label: 'AI live',
    tone: 'live',
    title: 'A language model is making the agent decisions.',
  },
}

/**
 * States which of the three execution modes produced what is on screen.
 *
 * The distinction is load-bearing: a scripted-provider run executes the same
 * analytics and the same verification as a model-driven one, but nothing
 * about it is a language model, and presenting it as one would be false.
 */
export function ModeBadge({ mode }: { mode: ExecutionMode }) {
  const { label, tone, title } = LABELS[mode]
  return (
    <span className={`mode-pill ${tone}`} title={title}>
      <span className="dot" />
      {label}
    </span>
  )
}
