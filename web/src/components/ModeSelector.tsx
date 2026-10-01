/**
 * Choose which decision-maker runs the analysis.
 *
 * Availability comes from the server's capability response, never from a
 * build-time flag: a frontend that decides for itself will offer AI on a
 * deployment that cannot serve it, and the visitor learns that only after
 * asking.
 */

import type { Capabilities, UiMode } from "../lib/types";

interface Props {
  capabilities: Capabilities;
  value: UiMode;
  onChange: (mode: UiMode) => void;
  disabled?: boolean;
}

interface Choice {
  mode: UiMode;
  label: string;
  description: string;
  available: boolean;
  unavailableMessage: string;
}

export function ModeSelector({
  capabilities,
  value,
  onChange,
  disabled,
}: Props) {
  const byMode = new Map(capabilities.modes.map((m) => [m.mode, m]));
  const auto = byMode.get("auto");
  const deterministic = byMode.get("deterministic");
  const ai = byMode.get("ai");

  const primary: Choice = {
    mode: "auto",
    label: auto?.label ?? "Governed Analysis",
    description:
      auto?.description ??
      "Rules resolve clear questions locally. AI-assisted planning is used only when the question is genuinely ambiguous.",
    available: auto?.available ?? false,
    unavailableMessage:
      auto?.message ?? "Governed Analysis is unavailable on this deployment.",
  };
  const auditChoices: Choice[] = [
    {
      mode: "deterministic",
      label: deterministic?.label ?? "Deterministic Analytics",
      description:
        deterministic?.description ??
        "Agent decisions come from a scripted provider, so the same question produces the same plan every time.",
      available: deterministic?.available ?? false,
      unavailableMessage: deterministic?.message ?? "",
    },
    {
      mode: "ai",
      label: ai?.label ?? "AI Analytics",
      description:
        ai?.description ??
        "A cloud language model interprets the question and chooses which analyses to run.",
      available: ai?.available ?? false,
      unavailableMessage: ai?.message ?? "",
    },
    {
      mode: "compare",
      label: "Compare planning strategies",
      description:
        "Runs the same question through each decision path and shows the two results side by side.",
      available: capabilities.compare_available,
      unavailableMessage:
        ai?.available === false
          ? (ai?.message ?? "")
          : "Rule-based and AI-assisted planning must both be available to compare them.",
    },
  ];

  return (
    <fieldset className="mode-selector" disabled={disabled}>
      <legend className="small dim">Planning method</legend>
      <div
        className="mode-options"
        role="radiogroup"
        aria-label="Analysis mode"
      >
        {[primary, ...auditChoices].map((choice) => {
          const id = `mode-${choice.mode}`;
          const describedBy = `${id}-description`;
          return (
            <div
              key={choice.mode}
              className={`mode-option${value === choice.mode ? " selected" : ""}${
                choice.available ? "" : " unavailable"
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
                  {choice.available
                    ? choice.description
                    : choice.unavailableMessage}
                </span>
              </label>
            </div>
          );
        })}
      </div>

      <details className="disclosure planning-audit">
        <summary>Planning audit</summary>
        <p className="small dim">
          Rules only, AI-assisted planning, and comparing the two are diagnostic
          paths. Governed Analysis is the default product path.
        </p>
      </details>

      {value === "ai" || value === "compare" ? (
        <p className="small dim mode-note">
          AI Analytics uses a limited public quota and can fail if the provider
          is unavailable. Only governed analytics context — schema, profiles and
          aggregates — is sent. Findings that the publication checks do not
          accept are withheld.
          {capabilities.ai_limits
            ? ` Up to ${capabilities.ai_limits.runs_per_session} AI runs per dataset session.`
            : ""}
        </p>
      ) : null}
    </fieldset>
  );
}
