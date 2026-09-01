/**
 * Display helpers.
 *
 * The API sends money as both integer paise and a preformatted string; the UI uses the
 * preformatted value and never does currency arithmetic. These helpers exist for the
 * cases where a chart axis or a derived label needs a shorter form.
 */

export type Money = {
  minor: number;
  major: number;
  display: string;
  compact: string;
};

export const ZERO_MONEY: Money = { minor: 0, major: 0, display: "₹0", compact: "₹0" };

export function money(value: Money | null | undefined): Money {
  return value ?? ZERO_MONEY;
}

/** Indian compact notation for chart axes: 1250000 paise -> "₹12.5K". */
export function compactInr(minor: number): string {
  const rupees = Math.abs(minor) / 100;
  const sign = minor < 0 ? "-" : "";
  if (rupees >= 1e7) return `${sign}₹${(rupees / 1e7).toFixed(2)}Cr`;
  if (rupees >= 1e5) return `${sign}₹${(rupees / 1e5).toFixed(1)}L`;
  if (rupees >= 1e3) return `${sign}₹${(rupees / 1e3).toFixed(1)}K`;
  return `${sign}₹${Math.round(rupees)}`;
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function number(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-IN");
}

export function titleCase(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function time(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export const SCENARIO_LABEL: Record<string, string> = {
  FAILED_PAYMENT: "Failed Payments",
  CHECKOUT_ABANDONMENT: "Checkout Abandonment",
  FAILED_SUBSCRIPTION: "Failed Subscriptions",
};

export const SCENARIO_SHORT: Record<string, string> = {
  FAILED_PAYMENT: "Payments",
  CHECKOUT_ABANDONMENT: "Checkouts",
  FAILED_SUBSCRIPTION: "Subscriptions",
};

/** Scenario -> categorical slot. Fixed order, never cycled. */
export const SCENARIO_VAR: Record<string, string> = {
  FAILED_PAYMENT: "var(--series-1)",
  CHECKOUT_ABANDONMENT: "var(--series-2)",
  FAILED_SUBSCRIPTION: "var(--series-3)",
};
