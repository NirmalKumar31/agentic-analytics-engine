/**
 * Settling a role the data cannot decide.
 *
 * Inference reports a close call when a numeric column sits in the band
 * where a code list and a genuine count look the same. The engine has been
 * wrong about that in both directions, and nothing in the values settles
 * it, because the person who uploaded the file is the only one who knows.
 *
 * Three things this deliberately does not do:
 *
 *   - It does not submit on selection. Choosing a radio is considering an
 *     option; the confirmation is a separate, deliberate act, because this
 *     changes how the engine aggregates the column.
 *   - It does not relabel the field locally. The server's response replaces
 *     the session payload, so what is on screen is what the engine will
 *     use. A UI that reported a role the backend had not accepted would be
 *     the exact defect this feature exists to prevent.
 *   - It does not describe the result as governed, verified or correct. It
 *     is one person's statement about one session.
 *
 * Confirming replaces the control with the settled card, which removes the
 * button that was just pressed. Left alone that drops focus to <body> --
 * a keyboard user loses their place in the schema table, and a screen
 * reader says nothing at all, because a removed element announces no
 * result. So the outcome goes to a live region and focus moves to the
 * control that replaced it.
 */

import { useEffect, useId, useRef, useState } from "react";

import type { ConfirmableRole, InferredField, RoleChange } from "../lib/types";

/** Plain language. "Dimension" is the engine's word, not a reader's. */
const CHOICES: Array<{ role: ConfirmableRole; label: string; effect: string }> = [
  {
    role: "measure",
    label: "Quantity",
    effect: "can be averaged or totalled",
  },
  {
    role: "dimension",
    label: "Category",
    effect: "used to split results into groups",
  },
];

interface Props {
  field: InferredField;
  /** Applies the change and resolves once the server has accepted it. */
  onApply: (changes: RoleChange[]) => Promise<void>;
  disabled?: boolean;
}

export function RoleConfirmation({ field, onApply, disabled = false }: Props) {
  const groupName = useId();
  const confirmed = field.role_source === "user_confirmed";
  const [choice, setChoice] = useState<ConfirmableRole>(
    (field.role as ConfirmableRole) ?? "measure",
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");

  // Whether this component caused the change it is now rendering. Without
  // it, focus would be seized on first paint from whatever the reader was
  // doing, on every confirmed column on the page.
  const acted = useRef(false);
  const anchor = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    // `pending` is in the deps because the replacement button is disabled
    // while the request is in flight, and focusing a disabled element does
    // nothing, in jsdom and in every browser. Waiting for the request to
    // settle is what makes the focus actually land.
    if (!acted.current || pending) return;
    acted.current = false;
    anchor.current?.focus();
  }, [confirmed, pending]);

  async function run(changes: RoleChange[]) {
    setPending(true);
    setError(null);
    setStatus("");
    // Raised before awaiting. `await` yields, so React can flush the
    // parent's state update, and run the effect below, before the
    // continuation here would reach this line. The effect also runs when
    // `pending` settles, so a late flag would still be seen; raising it
    // here means the first run is the one that acts, rather than relying
    // on a second.
    acted.current = true;
    try {
      await onApply(changes);
      const wasReset = changes.some((change) => change.action === "reset");
      setStatus(
        wasReset
          ? `${field.name} is back to the role the engine inferred.`
          : `${field.name} is confirmed for this session.`,
      );
    } catch (reason) {
      // Nothing replaced the button, so there is nothing to move focus to.
      acted.current = false;
      // The prior server state stays on screen. Showing the attempted role
      // after a refusal would claim something the engine did not accept.
      setError(reason instanceof Error ? reason.message : "the change was not saved");
    } finally {
      setPending(false);
    }
  }

  // One region per control, announced politely so it does not interrupt.
  // It carries the outcome, not the error: a refusal is already an alert.
  const announcement = (
    <p className="sr-only" role="status" aria-live="polite">
      {status}
    </p>
  );

  if (confirmed) {
    return (
      <div className="role-confirmation" data-testid="role-confirmed">
        <p className="small">
          <strong>Confirmed for this session.</strong>{" "}
          Read as <strong>{labelFor(field.role as ConfirmableRole)}</strong>; the
          engine had inferred{" "}
          <strong>{labelFor(field.inferred_role as ConfirmableRole)}</strong>.
        </p>
        <button
          // Distinct keys across the two branches. Without them React
          // reconciles these into one DOM node and focus survives by
          // accident; the guarantee would then rest on reconciliation
          // rather than on the effect above, and would break silently the
          // first time either branch gained a sibling.
          key="reset"
          ref={anchor}
          type="button"
          className="btn ghost small"
          disabled={disabled || pending}
          onClick={() => void run([{ column: field.name, action: "reset" }])}
        >
          {pending ? "Resetting…" : "Reset to inferred"}
        </button>
        {announcement}
        {error && (
          <p className="notice error small" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="role-confirmation" data-testid="role-confirmation">
      <fieldset disabled={disabled || pending}>
        <legend className="small dim">
          The data cannot settle this one. Which is it?
        </legend>
        {CHOICES.map((option) => (
          <label className="role-choice small" key={option.role}>
            <input
              type="radio"
              name={groupName}
              value={option.role}
              checked={choice === option.role}
              onChange={() => setChoice(option.role)}
            />
            <span>
              <strong>{option.label}</strong> — {option.effect}
            </span>
          </label>
        ))}
      </fieldset>
      <button
        key="confirm"
        ref={anchor}
        type="button"
        className="btn small"
        disabled={disabled || pending}
        onClick={() =>
          void run([{ column: field.name, action: "confirm", role: choice }])
        }
      >
        {pending ? "Confirming…" : "Confirm for this session"}
      </button>
      {announcement}
      {error && (
        <p className="notice error small" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function labelFor(role: ConfirmableRole | undefined): string {
  return CHOICES.find((option) => option.role === role)?.label ?? String(role ?? "");
}
