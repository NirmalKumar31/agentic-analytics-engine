/**
 * Choose which decision-maker runs the analysis.
 *
 * Availability comes from the server's capability response, never from a
 * build-time flag: a frontend that decides for itself will offer AI on a
 * deployment that cannot serve it, and the visitor learns that only after
 * asking.
 */

import type { Capabilities, UiMode } from '../lib/types'

interface Props {
  capabilities: Capabilities
  value: UiMode
  onChange: (mode: UiMode) => void
  disabled?: boolean
}

interface Choice {
  mode: UiMode
  label: string
  description: string
  available: boolean
  unavailableMessage: string
}

export function ModeSelector({ capabilities, value, onChange, disabled }: Props) {
  const byMode = new Map(capabilities.modes.map((m) => [m.mode, m]))
  const deterministic = byMode.get('deterministic')
  const ai = byMode.get('ai')

  const choices: Choice[] = [
    {
      mode: 'deterministic',
      label: deterministic?.label ?? 'Deterministic Analytics',
      description:
        deterministic?.description ??
        'Agent decisions come from a scripted provider, so the same question produces the same plan every time.',
      available: deterministic?.available ?? false,
      unavailableMessage: deterministic?.message ?? '',
    },
    {
      mode: 'ai',
      label: ai?.label ?? 'AI Analytics',
      description:
        ai?.description ??
        'A cloud language model interprets the question and chooses which analyses to run.',
      available: ai?.available ?? false,
      unavailableMessage: ai?.message ?? '',
    },
    {
      mode: 'compare',
      label: 'Compare Both',
      description:
        'Runs the same question through each decision path and shows the two results side by side.',
      available: capabilities.compare_available,
      unavailableMessage:
        ai?.available === false ? (ai?.message ?? '') : 'Both modes must be available to compare.',
    },
  ]

  return (
    <fieldset className="mode-selector" disabled={disabled}>
      <legend className="small dim">Analysis mode</legend>
      <div className="mode-options" role="radiogroup" aria-label="Analysis mode">
        {choices.map((choice) => {
          const id = `mode-${choice.mode}`
          const describedBy = `${id}-description`
          return (
            <div
              key={choice.mode}
              className={`mode-option${value === choice.mode ? ' selected' : ''}${
                choice.available ? '' : ' unavailable'
              }`}
            >
              <input
                type="radio"
                id={id}
                name="analysis-mode"
                value={choice.mode}
                checked={value === choice.mode}
                disabled={!choice.available || disabled}
                aria-describedby={describedBy}
                onChange={() => onChange(choice.mode)}
              />
              <label htmlFor={id}>
                <span className="mode-option-label">{choice.label}</span>
                <span className="small dim" id={describedBy}>
                  {choice.available ? choice.description : choice.unavailableMessage}
                </span>
              </label>
            </div>
          )
        })}
      </div>

      {value === 'ai' || value === 'compare' ? (
        <p className="small dim mode-note">
          AI Analytics uses a limited public quota and can fail if the provider is
          unavailable. Only governed analytics context — schema, profiles and aggregates —
          is sent. Findings that the publication checks do not accept are withheld.
          {capabilities.ai_limits
            ? ` Up to ${capabilities.ai_limits.runs_per_session} AI runs per dataset session.`
            : ''}
        </p>
      ) : null}
    </fieldset>
  )
}
