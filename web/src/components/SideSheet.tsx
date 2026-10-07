/**
 * An edge sheet: a disclosure over the page, not a separate destination.
 *
 * Extracted from `ProvenanceDrawer`, which had solved the focus problem
 * properly and privately. Two more surfaces need the same behaviour: the
 * schema inspector and the evidence drawer, and the half that is easy to
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

/**
 * Click handler for a control that opens a sheet.
 *
 * WebKit does not focus a `<button>` when it is clicked, following Safari's
 * long-standing behaviour, not a Playwright artefact, so
 * `document.activeElement` is `<body>` at the moment the sheet mounts, and
 * the sheet dutifully restores focus to `<body>` on close. A
 * keyboard-and-mouse user is dropped at the top of the document.
 *
 * It is a shared helper rather than a line at each call site because this
 * was fixed once for the schema inspector and then reintroduced verbatim by
 * the evidence drawer. A control that opens a dialog should hold focus
 * anyway: it is where the reader is, and where they expect to be put back.
 */
export function opensSheet(open: () => void) {
  return {
    onClick: (event: React.MouseEvent<HTMLElement>) => {
      event.currentTarget.focus();
      open();
    },
  };
}

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
    };

    /*
     * Containment, as a backstop rather than as manual Tab cycling.
     *
     * The first version computed the sheet's first and last focusable
     * elements and wrapped Tab between them. That is wrong on WebKit:
     * Safari leaves buttons out of the tab order by default, so the
     * computed `last` was an element Tab would never reach, the wrap never
     * fired, and focus walked out of the sheet after two presses onto a
     * page covered by a scrim, where it simply disappears.
     *
     * Listening for focus *arriving* outside the sheet needs no model of
     * which elements a given engine considers tabbable. Whatever the
     * browser's natural order inside the sheet is, it is already correct;
     * the only thing to prevent is leaving.
     */
    const pullBack = () => {
      const sheet = sheetRef.current;
      if (!sheet) return;
      if (sheet.contains(document.activeElement)) return;
      closeRef.current?.focus();
    };

    const onFocusIn = (event: FocusEvent) => {
      const sheet = sheetRef.current;
      if (!sheet) return;
      const target = event.target as Node | null;
      if (target && sheet.contains(target)) return;
      closeRef.current?.focus();
    };

    /*
     * `focusin` alone is not enough on WebKit.
     *
     * Safari leaves buttons out of the tab order, so Tab from inside the
     * sheet can move focus out of the *document* (to the browser chrome
     *) rather than to another element. Nothing receives focus, so
     * `focusin` never fires, `document.activeElement` falls back to
     * `<body>`, and the next Tab re-enters the page at the top: on the
     * surface behind the scrim.
     *
     * `focusout` fires in that case. The check is deferred a tick because
     * at `focusout` time the new target has not been focused yet, so
     * reading `activeElement` immediately would always see the old one.
     */
    const onFocusOut = () => {
      window.setTimeout(pullBack, 0);
    };

    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("focusout", onFocusOut);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("focusout", onFocusOut);
      // Give focus back to the control that opened the sheet.
      //
      // Deferred by a frame rather than restored synchronously. Cleanup
      // runs *before* React removes the sheet from the DOM, and WebKit then
      // moves focus to `<body>` as the focused element inside it
      // disappears, undoing a synchronous restore. Chromium and Firefox
      // happen not to, which is why extracting this component from
      // `ProvenanceDrawer` and dropping the deferral passed on two engines
      // and failed on the third.
      //
      // `isConnected` because the opener can itself be unmounted while the
      // sheet is open, because a new run replaces the strip it lives in. Focusing
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
