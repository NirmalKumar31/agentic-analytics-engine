/**
 * The report as a reader sees it on screen.
 *
 * `AnswerReport` renders the evidence drawer's contents a second time, as a
 * `hidden` appendix that only `@media print` reveals. That is deliberate --
 * a reader who prints a report must not get less than a reader who clicks
 * through it -- but it means the report element now contains two copies of
 * every datum the drawer holds: one behind a control, one behind `hidden`.
 *
 * A test about what is *on the canvas* has to exclude the appendix, or it
 * is asserting against a copy that no reader can see. These helpers are
 * what "on the canvas" means from here on.
 */

/** Is this node outside the print appendix -- that is, on screen? */
export function onCanvas(node: Element): boolean {
  return node.closest("[data-print-appendix]") === null;
}

/** The text a reader actually sees in `el`, with the appendix removed. */
export function canvasText(el: HTMLElement): string {
  const copy = el.cloneNode(true) as HTMLElement;
  for (const appendix of copy.querySelectorAll("[data-print-appendix]")) {
    appendix.remove();
  }
  return copy.textContent ?? "";
}

/**
 * Assert that nothing matching `selector` is on the canvas.
 *
 * The weaker claim -- "it is not in the DOM" -- is no longer the right one:
 * the appendix legitimately holds a copy. What has to stay true is that the
 * reader is not shown it, and that the appendix carrying it is hidden.
 */
export function expectOffCanvas(
  root: ParentNode,
  selector: string,
): Element[] {
  const all = [...root.querySelectorAll(selector)];
  const visible = all.filter(onCanvas);
  if (visible.length > 0) {
    throw new Error(`${selector} is resident on the report canvas`);
  }
  for (const node of all) {
    const appendix = node.closest("[data-print-appendix]")!;
    if (!appendix.hasAttribute("hidden")) {
      throw new Error(`${selector} is in a print appendix that is not hidden`);
    }
  }
  return all;
}
