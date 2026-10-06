/**
 * The one string a reader sees for one cell.
 *
 * This is the browser half of `presentation/fields.py:display_value`, and
 * the two are held to a single hand-written contract --
 * `test/fixtures/displayValueContract.json`, which both a pytest and a
 * vitest read. Neither side is allowed to be the authority: if they
 * disagree about a case, one of the two suites fails and names it.
 *
 * It exists because they did disagree, silently and in public. The backend
 * resolved a period through `period_label` and the headline said
 * "Revenue peaked at $3,184,322.72 in **Oct 2025**"; the table beside it
 * formatted the same cell with no field metadata at all and said
 * "**2025-01-01T00:00:00**" with "1,050,312.91" next to it, no currency.
 * Neither surface was wrong on its own terms. There was no shared term.
 *
 * Everything it needs is on the `DisplayField`. A missing field means the
 * backend declined to describe that column, and the value is then shown as
 * stored -- which is honest, and is what the old path did for every
 * column.
 */

import type { DisplayField } from "./types";

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

/** Units written before the number. A currency symbol is never a suffix. */
const PREFIX_UNITS = new Set(["$", "£", "€", "¥"]);

/** Decimal places for a fractional measure. `fields.py:MEASURE_PRECISION`. */
const DISPLAY_PLACES = 2;

/**
 * A period a reader can read. Mirrors `period_label`.
 *
 * The midnight suffix carries no information at any grain this product
 * aggregates to, so it is never shown. Anything that does not parse comes
 * back unchanged: a period this does not understand is still the engine's
 * own value, and guessing at it would be worse than printing it.
 */
export function periodLabel(value: unknown, grain?: string | null): string {
  const text = String(value ?? "").trim();
  if (!text) return text;
  const head = text.split("T")[0]!;
  const parts = head.split("-");
  let year: number;
  let month: number;
  let day: number;
  if (parts.length >= 3) {
    [year, month, day] = [Number(parts[0]), Number(parts[1]), Number(parts[2])];
  } else if (parts.length === 2) {
    [year, month, day] = [Number(parts[0]), Number(parts[1]), 1];
  } else {
    return text;
  }
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) {
    return text;
  }
  if (month < 1 || month > 12) return text;
  if (grain === "day" || grain === "week") return `${MONTHS[month - 1]} ${day}, ${year}`;
  if (grain === "year") return String(year);
  // Month and quarter both read as a month: the bucket's first day is an
  // implementation detail of how it is stored.
  return `${MONTHS[month - 1]} ${year}`;
}

/**
 * Thousands separators at the precision the table uses.
 *
 * Mirrors `verification/canonical.py:format_number`, which the backend's
 * headline and highlights already go through. A whole number carries no
 * decimals; a fractional one carries two. Deliberately *not*
 * `format.ts:formatNumber`, which switches to exponential at the extremes
 * -- that rule has no counterpart on the backend, so a figure near it
 * would be written two ways again.
 */
function formatFixed(value: number, places: number): string {
  if (Number.isInteger(value)) {
    return value.toLocaleString("en-US");
  }
  const rounded = Number(value.toFixed(places));
  if (Number.isInteger(rounded)) {
    return rounded.toLocaleString("en-US");
  }
  return rounded.toLocaleString("en-US", {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  });
}

/** A formatted number carrying the unit its field declares. */
export function withUnit(text: string, field?: DisplayField | null): string {
  if (!field?.unit) return text;
  const unit = field.unit;
  if (PREFIX_UNITS.has(unit)) return `${unit}${text}`;
  // `%` sits tight against the number; a named unit -- `pp` -- takes a
  // space, because "0.71pp" reads as a typo and "0.71 pp" reads as a
  // measurement.
  return `${text}${unit === "%" ? "" : " "}${unit}`;
}

/**
 * A flag's state, or the value as stored.
 *
 * A dimension value is the visitor's own data and rewriting it is not the
 * interface's business.
 */
export function labelValue(value: unknown, field?: DisplayField | null): string {
  if (value === null || value === undefined) return "—";
  const text = String(value).trim();
  const labels = field?.boolean_labels;
  if (labels) {
    if (typeof value === "boolean") return labels[value ? "true" : "false"] ?? text;
    // `1.0` and `1` are the same flag.
    const asNumber = Number(text);
    const key = Number.isFinite(asNumber) ? String(Math.trunc(asNumber)) : text.toLowerCase();
    return labels[key] ?? labels[text.toLowerCase()] ?? text;
  }
  return text;
}

export function displayValue(value: unknown, field?: DisplayField | null): string {
  if (value === null || value === undefined) return "—";

  if (field?.boolean_labels) return labelValue(value, field);

  if (field?.semantic_kind === "time") {
    return periodLabel(value, field.time_grain ?? null);
  }

  if (typeof value === "boolean") return labelValue(value, field);

  const number = typeof value === "number" ? value : Number(String(value).trim());
  if (!Number.isFinite(number) || String(value).trim() === "") {
    return labelValue(value, field);
  }

  /*
   * The declared scale, before formatting.
   *
   * A two-proportion test's `rate` is `successes / n` -- stored as
   * 0.5045 and written as 50.46%. The backend applied it and this did
   * not, so one report said "Repeat purchase rate is 50.46% for late" in
   * the headline and "0.50%" in the table two inches below. That is the
   * exact defect the shared contract exists to prevent, and it got
   * through because the contract had no case with a scale in it. It has
   * four now.
   */
  const scaled = field?.scale && field.scale !== 1 ? number * field.scale : number;

  const text =
    field?.precision === 0
      ? Math.trunc(scaled).toLocaleString("en-US")
      : formatFixed(scaled, DISPLAY_PLACES);
  return withUnit(text, field);
}
