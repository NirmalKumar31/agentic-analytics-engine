/**
 * An edge sheet: a disclosure over the page, not a separate destination.
 *
 * Extracted from `ProvenanceDrawer`, which had solved the focus problem
 * properly and privately. Two more surfaces need the same behaviour -- the
 * schema inspector and the evidence drawer -- and the half that is easy to
 * get wrong is the half that is invisible.
 *
 * What it guarantees:
 *
 * - Focus moves into the sheet on open, and back to whatever opened it on
 *   close. Closing used to leave focus on `<body>`, which drops a keyboard
 *   user at the top of the document and makes them tab the whole page again
 *   to return to the statement they were reading about.
 * - Escape closes it, from anywhere, including from inside a nested control.
 * - Focus is contained while it is open, so Tab cannot walk out of the sheet
 *   and into a page the reader cannot see.
 *
 * The page behind stays rendered and is not re-laid-out. A sheet that
 * reflowed the document would make the thing it is explaining move.
 */

import { useCallback, useEffect, useRef, type ReactNode } from "react";

/** Everything that can take focus, in document order. */
const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])';

export function SideSheet({
  title,
  onClose,
  testId,
  children,
}: {
  title: string;
  onClose: () => void;
  testId?: string;
  children: ReactNode;
}) {
  const sheetRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  /** Whatever had focus when the sheet opened, so it can be given back. */
  const openerRef = useRef<Element | null>(null);

  // `onClose` is a fresh closure on every render of the parent, so an
  // effect that depends on it re-runs on every render. That is not a
  // style point: the effect moves focus to the Close button, so the sheet
  // stole focus back from whatever the reader was using every time
  // anything re-rendered. Confirming a column role moves focus to the
  // control that replaces it, and the sheet yanked it straight back.
  //
  // The handler reads the latest `onClose` through a ref, and the effect
  // runs exactly once, on mount.
  const closeHandler = useRef(onClose);
  closeHandler.current = onClose;
  const close = useCallback(() => closeHandler.current(), []);

  useEffect(() => {
    openerRef.current = document.activeElement;
    closeRef.current?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close();
        return;
      }
      if (event.key !== "Tab") return;

      // Containment. Without it, Tab from the last control in the sheet
      // moves to the page underneath, which is covered by a scrim and
      // cannot be seen -- focus simply disappears.
      const sheet = sheetRef.current;
      if (!sheet) return;
      const stops = [...sheet.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );
      if (stops.length === 0) return;
      const first = stops[0]!;
      const last = stops[stops.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      // Give focus back to the control that opened the sheet.
      //
      // Deferred by a frame rather than restored synchronously. Cleanup
      // runs *before* React removes the sheet from the DOM, and WebKit then
      // moves focus to `<body>` as the focused element inside it
      // disappears -- undoing a synchronous restore. Chromium and Firefox
      // happen not to, which is why extracting this component from
      // `ProvenanceDrawer` and dropping the deferral passed on two engines
      // and failed on the third.
      //
      // `isConnected` because the opener can itself be unmounted while the
      // sheet is open -- a new run replaces the strip it lives in. Focusing
      // a detached node silently does nothing, which looks exactly like the
      // bug this is fixing.
      const opener = openerRef.current as HTMLElement | null;
      requestAnimationFrame(() => {
        if (opener && opener.isConnected) opener.focus();
      });
    };
  }, [close]);

  return (
    <>
      <div className="scrim" onClick={close} aria-hidden="true" />
      <div
        className="side-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid={testId}
        ref={sheetRef}
      >
        <div className="side-sheet-head">
          <h2 className="section-heading">{title}</h2>
          <button
            type="button"
            className="btn small"
            onClick={close}
            ref={closeRef}
          >
            Close
          </button>
        </div>
        <div className="side-sheet-body">{children}</div>
      </div>
    </>
  );
}
