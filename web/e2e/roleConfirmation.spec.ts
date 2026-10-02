import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { ask, uploadFile, waitForReport } from "./helpers";

/**
 * Settling a role in a real browser.
 *
 * The jsdom suite proves the component's logic. What it cannot prove is
 * that a keyboard reaches the control, that focus lands somewhere after
 * the control replaces itself, that the announcement is in the accessible
 * tree, or that the thing fits on a phone. Each of those failed at least
 * once during development in a way jsdom reported as green -- the focus
 * restoration was focusing a disabled button, which jsdom and every
 * browser both treat as a no-op, and only an assertion on the real
 * `activeElement` caught it.
 *
 * Uploads are the scarce resource here: the server admits a limited pool
 * of sessions and an exhausted pool shows up as a skip, which reads as a
 * pass. So this file uploads once per test and no more, and the spec is
 * written so that no test needs two datasets.
 */

/** A close call in the band where a code list and a count are identical. */
function closeCallCsv(rows = 400): string {
  const sites = ["alpha", "beta", "gamma", "delta"];
  const lines = ["site,dose,reading"];
  for (let i = 0; i < rows; i += 1) {
    lines.push(`${sites[i % 4]},${(2.5 + i * 0.1).toFixed(2)},${18 + (i % 48)}`);
  }
  return lines.join("\n");
}

/** The control, once the inspector is open. */
async function openInspector(page: Page) {
  const inspector = page.getByTestId("schema-inspector");
  await expect(inspector).toBeVisible();
  // It is a closed `<details>`; its body is hidden from the a11y tree and
  // from the keyboard until it is opened.
  const summary = inspector.locator("summary").first();
  if (!(await inspector.evaluate((node: HTMLDetailsElement) => node.open))) {
    await summary.click();
  }
  return inspector;
}

async function uploadCloseCall(page: Page) {
  await page.goto("/");
  await uploadFile(page, "clinical.csv", closeCallCsv());
  return openInspector(page);
}

test.describe("confirming a role", () => {
  test("a keyboard alone can choose and confirm", async ({ page }) => {
    await uploadCloseCall(page);

    const control = page.getByTestId("role-confirmation");
    await expect(control).toBeVisible();

    // Reach the radio by keyboard rather than clicking it: a control that
    // can only be operated with a pointer is not operable.
    const category = control.getByRole("radio", { name: /Category/ });
    await category.focus();
    await expect(category).toBeFocused();
    await page.keyboard.press("Space");
    await expect(category).toBeChecked();

    // Selecting is not confirming.
    await expect(page.getByTestId("role-confirmed")).toHaveCount(0);

    const confirm = control.getByRole("button", { name: /Confirm for this session/ });
    await confirm.focus();
    await page.keyboard.press("Enter");

    await expect(page.getByTestId("role-confirmed")).toBeVisible();
  });

  test("focus lands on the control that replaced the button", async ({ page }) => {
    await uploadCloseCall(page);
    const control = page.getByTestId("role-confirmation");
    await control.getByRole("button", { name: /Confirm for this session/ }).click();

    const reset = page
      .getByTestId("role-confirmed")
      .getByRole("button", { name: /Reset to inferred/ });
    await expect(reset).toBeVisible();

    // The real activeElement, not a React-internal belief about it. The
    // first implementation focused the replacement while it was still
    // disabled, which is a no-op, and jsdom agreed it had worked.
    await expect(reset).toBeFocused();
  });

  test("the outcome is announced, not just drawn", async ({ page }) => {
    await uploadCloseCall(page);
    await page
      .getByTestId("role-confirmation")
      .getByRole("button", { name: /Confirm for this session/ })
      .click();

    const status = page.getByRole("status");
    await expect(status.first()).toContainText(/reading is confirmed for this session/);
  });

  test("a reset returns the engine to its own reading", async ({ page }) => {
    await uploadCloseCall(page);
    await page
      .getByTestId("role-confirmation")
      .getByRole("button", { name: /Confirm for this session/ })
      .click();

    const settled = page.getByTestId("role-confirmed");
    await expect(settled).toBeVisible();
    await settled.getByRole("button", { name: /Reset to inferred/ }).click();

    // Back to the offer, and the announcement says which way it went.
    await expect(page.getByTestId("role-confirmation")).toBeVisible();
    await expect(page.getByRole("status").first()).toContainText(
      /reading is back to the role the engine inferred/,
    );
  });

  test("the audit names the confirmation in the finished report", async ({ page }) => {
    await uploadCloseCall(page);
    const control = page.getByTestId("role-confirmation");
    await control.getByRole("radio", { name: /Category/ }).check();
    await control.getByRole("button", { name: /Confirm for this session/ }).click();
    await expect(page.getByTestId("role-confirmed")).toBeVisible();

    await ask(page, "What is total dose by reading?");
    await waitForReport(page);

    const evidence = page.getByTestId("role-evidence");
    await expect(evidence).toBeVisible();
    await expect(evidence).toContainText(/reading/);
    await expect(evidence).toContainText(/confirmed for this dataset session/);
    // It is one person's statement about one session, and must not read as
    // anything stronger.
    await expect(evidence).not.toContainText(/governed/i);
    await expect(evidence).not.toContainText(/verified/i);
  });
});

test.describe("the control at every width", () => {
  const widths = [
    { label: "phone", width: 360, height: 740 },
    { label: "tablet", width: 768, height: 1024 },
    { label: "desktop", width: 1280, height: 900 },
  ];

  // One upload, three widths. A test per width would be clearer to read
  // and would triple this file's share of the hourly upload allowance;
  // resizing does not need a new dataset, so it does not get one.
  test("fits and stays operable on a phone, a tablet and a desktop", async ({ page }) => {
    await uploadCloseCall(page);
    const control = page.getByTestId("role-confirmation");

    for (const { label, width, height } of widths) {
      await page.setViewportSize({ width, height });
      await expect(control, `${label}: the control is not visible`).toBeVisible();

      // Nothing may push the page into a horizontal scroll: on a phone that
      // is how a control becomes unreachable rather than merely cramped.
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(
        overflow,
        `${label}: the page scrolls sideways by ${overflow}px`,
      ).toBeLessThanOrEqual(1);

      // The confirm button has to be a real target, not a sliver.
      const box = await control
        .getByRole("button", { name: /Confirm for this session/ })
        .boundingBox();
      expect(box, `${label}: the confirm button has no box`).not.toBeNull();
      expect(
        box!.height,
        `${label}: confirm button is ${box!.height}px tall`,
      ).toBeGreaterThanOrEqual(24);
      expect(box!.width, `${label}: confirm button has no width`).toBeGreaterThan(0);

      // Each radio must still be reachable and hittable at this width.
      for (const name of [/Quantity/, /Category/]) {
        const radio = control.getByRole("radio", { name });
        const radioBox = await radio.boundingBox();
        expect(radioBox, `${label}: a radio has no box`).not.toBeNull();
        expect(radioBox!.height, `${label}: radio is ${radioBox!.height}px`).toBeGreaterThanOrEqual(12);
      }
    }
  });
});

test.describe("accessibility of the control", () => {
  test("no serious or critical violations, offered or settled", async ({ page }) => {
    await uploadCloseCall(page);

    const scan = async (label: string) => {
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
        .analyze();
      const blocking = results.violations.filter((violation) =>
        ["serious", "critical"].includes(violation.impact ?? ""),
      );
      const detail = blocking
        .map((v) => `  [${v.impact}] ${v.id}: ${v.help} at ${v.nodes[0]?.target.join(" ")}`)
        .join("\n");
      expect(blocking.length, `${label}:\n${detail}`).toBe(0);
    };

    // Both states, because the settled card is a different subtree and a
    // scan of only the offer would never have seen it.
    await scan("the control as offered");
    await page
      .getByTestId("role-confirmation")
      .getByRole("button", { name: /Confirm for this session/ })
      .click();
    await expect(page.getByTestId("role-confirmed")).toBeVisible();
    await scan("the control once settled");
  });
});
