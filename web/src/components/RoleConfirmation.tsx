/**
 * Settling a role the data cannot decide.
 *
 * Inference reports a close call when a numeric column sits in the band
 * where a code list and a genuine count look the same. The engine has been
 * wrong about that in both directions, and nothing in the values settles
 * it -- the person who uploaded the file is the only one who knows.
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
 */

import { useId, useState } from "react";

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

  async function run(changes: RoleChange[]) {
    setPending(true);
    setError(null);
    try {
      await onApply(changes);
    } catch (reason) {
      // The prior server state stays on screen. Showing the attempted role
      // after a refusal would claim something the engine did not accept.
      setError(reason instanceof Error ? reason.message : "the change was not saved");
    } finally {
      setPending(false);
    }
  }

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
          type="button"
          className="btn ghost small"
          disabled={disabled || pending}
          onClick={() => void run([{ column: field.name, action: "reset" }])}
        >
          {pending ? "Resetting…" : "Reset to inferred"}
        </button>
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
        type="button"
        className="btn small"
        disabled={disabled || pending}
        onClick={() =>
          void run([{ column: field.name, action: "confirm", role: choice }])
        }
      >
        {pending ? "Confirming…" : "Confirm for this session"}
      </button>
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
