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
  /** The full explanation. Lives in the disclosure and in `aria-describedby`. */
  description: string;
  available: boolean;
  unavailableMessage: string;
}

/**
 * The purpose a card shows, which is the description's first sentence.
 *
 * Derived rather than written separately on purpose. A second, shorter
 * blurb beside the server's own description is two statements about one
 * mode that can disagree, and the server is the authority on what a mode
 * does. Taking the first sentence keeps the card to one line and keeps the
 * full text one disclosure away.
 */
function purposeOf(description: string): string {
  const trimmed = description.trim();
  const end = trimmed.search(/\.\s|\.$/);
  return end === -1 ? trimmed : trimmed.slice(0, end + 1);
}

/**
 * What pressing the button will do, per mode.
 *
 * The action used to read "Run analysis" for both Governed and
 * Deterministic, so the one control that commits a reader to a planner did
 * not say which planner it would use. Exported so the composer and its
 * tests name the same thing.
 */
export const RUN_LABELS: Record<UiMode, string> = {
  auto: "Run governed analysis",
  deterministic: "Run deterministic analysis",
  ai: "Run with AI",
  compare: "Compare planning strategies",
};

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
        "Runs the same question through each planner and shows one full report at a time, with a switcher between them. Narrow columns are not used: a comparison of two reports is unreadable at half a phone's width.",
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
   * Four cards, each selectable and each saying what it is for.
   *
   * This was a compact segmented control: four labels in a pill strip,
   * with only the selected one explaining itself. It read as a row of
   * headings rather than as a choice. Nothing about it said "pick one",
   * the selected state was a background tint, and three of the four modes
   * were unlabelled as to purpose until you selected them.
   *
   * The earlier objection to cards was real and is answered rather than
   * reversed: the version before the pill strip put four full paragraphs
   * on screen, occupying more room than the question field they modify.
   * A card now carries **one sentence** -- the description's first, so it
   * cannot disagree with the full text, and the full explanations, the
   * taxonomy and the AI quota note all sit behind one disclosure.
   *
   * The radiogroup, the per-option `aria-describedby`, the capability
   * gating and the disabled-with-reason behaviour are unchanged: what a
   * screen reader is told is the same, and an unavailable mode is still
   * unselectable with its reason attached rather than silently missing.
   */
  return (
    <div className="mode-selector" data-testid="mode-selector">
      <div
        className="mode-cards"
        role="radiogroup"
        aria-label="Analysis mode"
      >
        {choices.map((choice) => {
          const id = `mode-${choice.mode}`;
          const describedBy = `${id}-description`;
          const chosen = value === choice.mode;
          return (
            <span
              key={choice.mode}
              className={`mode-option${chosen ? " selected" : ""}${
                choice.available ? "" : " unavailable"
              }`}
            >
              <input
                type="radio"
                id={id}
                name="analysis-mode"
                value={choice.mode}
                checked={chosen}
                disabled={!choice.available || disabled}
                aria-describedby={describedBy}
                onChange={() => onChange(choice.mode)}
              />
              <label htmlFor={id}>
                {/* The selected state in a mark, not in colour alone: a
                    reader who cannot separate the hues still has to be
                    able to see which one is chosen. */}
                <span className="mode-option-mark" aria-hidden="true">
                  {chosen ? "\u25cf" : "\u25cb"}
                </span>
                <span className="mode-option-label">{choice.label}</span>
                {/*
                  One sentence, visible. When the mode cannot run, this is
                  the reason instead -- and it carries `describedBy` itself,
                  so a disabled card states its reason exactly once. The
                  earlier markup put the same message in a visible span and
                  again in a hidden one, which a screen reader reads twice.
                */}
                <span
                  className="mode-option-purpose"
                  id={choice.available ? undefined : describedBy}
                >
                  {choice.available
                    ? purposeOf(choice.description)
                    : choice.unavailableMessage}
                </span>
              </label>
              {/*
                The full text, outside the label on purpose.
                
                Inside it, it joined the radio's accessible *name*, so the
                name repeated the first sentence that is already visible on
                the card. As a sibling it is the accessible *description*,
                which is what `aria-describedby` is for.
              */}
              {choice.available && (
                <span className="sr-only" id={describedBy}>
                  {choice.description}
                </span>
              )}
            </span>
          );
        })}
      </div>

      {/*
        Everything that is not the choice itself, behind one control.

        Three paragraphs used to be resident here: the selected mode's full
        description, a taxonomy explaining why Compare has two panes and
        not three, and an AI quota note that appeared on two of the four
        modes. Together they were taller than the cards they explained.
      */}
      <details className="mode-explainer" data-testid="mode-explainer">
        <summary>How these differ, and what AI costs</summary>

        <p className="mode-description" data-testid="mode-description">
          {selected.available
            ? selected.description
            : selected.unavailableMessage}
        </p>

        {/*
          What the four choices are, in one sentence.

          A reader asked why Compare shows two reports when the selector
          offers three modes, and the answer is not discoverable from the
          cards: Governed Analysis is a *router* that picks one of the two
          planners, so comparing it against them would duplicate whichever
          it chose.
        */}
        <p className="mode-taxonomy small dim" data-testid="mode-taxonomy">
          Governed Analysis chooses between two planners: Deterministic
          Analytics plans by rule, AI Analytics plans with a cloud model.
          Compare runs both, which is why it has two reports and not three.
        </p>

        <p className="mode-note">
          AI Analytics uses a limited public quota and can fail if the
          provider is unavailable. The question, column names, inferred column
          types, summary profiles and aggregate results are sent. Findings
          that the publication checks do not accept are withheld.
          {capabilities.ai_limits
            ? ` Up to ${capabilities.ai_limits.runs_per_session} AI runs per dataset session.`
            : ""}
        </p>

        <p className="small dim">
          Rules only, AI-assisted planning, and comparing the two are
          diagnostic paths. Governed Analysis is the default product path.
        </p>
      </details>
    </div>
  );
}
