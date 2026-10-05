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

  const choices = [primary, ...auditChoices];
  const selected = choices.find((choice) => choice.mode === value) ?? primary;

  /*
   * A compact control, not four cards.
   *
   * Every choice used to be a bordered card carrying its full description,
   * in a bordered fieldset, below the composer: four paragraphs of
   * explanatory prose, occupying more of the screen than the question field
   * they modify. The strategy is a *qualifier* on the run button -- most
   * readers will never change it from Governed -- so it is sized like one,
   * and only the chosen strategy explains itself.
   *
   * The radiogroup, the per-option `aria-describedby`, the capability
   * gating and the disabled-with-reason behaviour are unchanged: what a
   * screen reader is told is the same, and an unavailable mode is still
   * unselectable with its reason attached rather than silently missing.
   */
  return (
    <div className="mode-selector" data-testid="mode-selector">
      <div
        className="mode-options"
        role="radiogroup"
        aria-label="Analysis mode"
      >
        {choices.map((choice) => {
          const id = `mode-${choice.mode}`;
          const describedBy = `${id}-description`;
          return (
            <span
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
                {/* Visually hidden, not removed: the description is what
                    tells a screen-reader user what they are choosing, and
                    it must not depend on which option happens to be
                    selected. */}
                <span className="sr-only" id={describedBy}>
                  {choice.available
                    ? choice.description
                    : choice.unavailableMessage}
                </span>
              </label>
            </span>
          );
        })}
      </div>

      <p className="mode-description" data-testid="mode-description">
        {selected.available ? selected.description : selected.unavailableMessage}
      </p>

      {/*
        What the four choices are, in one sentence.

        A reader asked why Compare shows two panes when the selector offers
        three modes, and the answer is not discoverable from the selector:
        Governed Analysis is a *router* that picks one of the two planners,
        so comparing it against them would duplicate whichever it chose.
        Said once, here, rather than left to be inferred.
      */}
      <p className="mode-taxonomy small dim" data-testid="mode-taxonomy">
        Governed Analysis chooses between two planners: Deterministic
        Analytics plans by rule, AI Analytics plans with a cloud model.
        Compare runs both and shows them side by side, which is why it has
        two panes and not three.
      </p>

      {value === "ai" || value === "compare" ? (
        <p className="mode-note">
          AI Analytics uses a limited public quota and can fail if the provider
          is unavailable. Only governed analytics context — schema, profiles and
          aggregates — is sent. Findings that the publication checks do not
          accept are withheld.
          {capabilities.ai_limits
            ? ` Up to ${capabilities.ai_limits.runs_per_session} AI runs per dataset session.`
            : ""}
        </p>
      ) : null}

      <details className="disclosure planning-audit">
        <summary>Planning audit</summary>
        <p className="small dim">
          Rules only, AI-assisted planning, and comparing the two are diagnostic
          paths. Governed Analysis is the default product path.
        </p>
      </details>
    </div>
  );
}
