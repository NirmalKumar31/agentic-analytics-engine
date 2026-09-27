import type { ExecutionMode, UiMode } from '../lib/types'

/**
 * What the badge can say. A superset of the API's `ExecutionMode`, because
 * the deployment-level fact and the thing on screen are no longer the same
 * question: a deployment that offers AI is reported as `ai_live`, while the
 * run a visitor is looking at may be the deterministic one.
 */
export type BadgeMode = ExecutionMode | 'compare_live'

/**
 * Which badge belongs on screen.
 *
 * A replay is always a replay. Otherwise the selected mode decides, because
 * it is what the next run will be -- deriving this from the deployment's
 * capabilities would label a deterministic run "AI live" on any deployment
 * that merely offers AI.
 */
export function badgeMode(
  replaying: boolean,
  deployment: ExecutionMode,
  selected: UiMode,
): BadgeMode {
  if (replaying || deployment === 'recorded') return 'recorded'
  if (selected === 'compare') return 'compare_live'
  return selected === 'ai' ? 'ai_live' : 'deterministic_live'
}

const LABELS: Record<BadgeMode, { label: string; tone: string; title: string }> = {
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
  compare_live: {
    label: 'Compare both',
    tone: 'live',
    title:
      'Two runs of the same question against the same rows: one with a language ' +
      'model making the agent decisions, one with deterministic rules. Reported ' +
      'separately, with no merged verdict.',
  },
}

/**
 * States which of the three execution modes produced what is on screen.
 *
 * The distinction is load-bearing: a scripted-provider run executes the same
 * analytics and the same verification as a model-driven one, but nothing
 * about it is a language model, and presenting it as one would be false.
 */
export function ModeBadge({ mode }: { mode: BadgeMode }) {
  const { label, tone, title } = LABELS[mode]
  return (
    <span className={`mode-pill ${tone}`} title={title}>
      <span className="dot" />
      {label}
    </span>
  )
}
