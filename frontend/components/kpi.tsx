"use client";

import { ReactNode } from "react";
import { Money, percent } from "@/lib/format";
import { Skeleton } from "./ui";

/**
 * A stat tile, not a chart: one number that needs no plot to be read (§27).
 * The value wears text ink; any colour beside it is a status cue with its own label.
 */
export function KpiTile({
  label,
  value,
  sub,
  tone = "neutral",
  hint,
  loading,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "neutral" | "good" | "critical" | "accent";
  hint?: string;
  loading?: boolean;
}) {
  const accent = {
    neutral: "var(--line-strong)",
    good: "var(--status-good)",
    critical: "var(--status-critical)",
    accent: "var(--series-1)",
  }[tone];

  return (
    <div className="relative overflow-hidden rounded-card border border-line bg-surface px-4 py-3">
      <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: accent }} />
      <p className="text-2xs font-medium uppercase tracking-wide text-ink-muted">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-28" />
      ) : (
        <p className="tabular mt-1 text-2xl font-semibold leading-tight text-ink">{value}</p>
      )}
      {sub && <div className="mt-1 text-xs text-ink-2">{sub}</div>}
      {hint && <p className="mt-1 text-2xs leading-snug text-ink-muted">{hint}</p>}
    </div>
  );
}

/**
 * The §39 distinction, made visual: actual recovered money, the AI's expected value,
 * and projected retention are three different quantities and are never added together.
 */
export function MoneyDistinction({
  recovered,
  expected,
  projected,
}: {
  recovered: Money;
  expected: Money;
  projected: Money;
}) {
  const rows = [
    {
      label: "Actual recovered revenue",
      value: recovered.display,
      note: "Settled in the ledger — a payment actually succeeded",
      tone: "text-good",
      weight: "text-lg font-semibold",
    },
    {
      label: "Expected recovery value",
      value: expected.display,
      note: "Amount at risk × AI probability. A forecast, not money.",
      tone: "text-ink-2",
      weight: "text-sm",
    },
    {
      label: "Projected retention value",
      value: projected.display,
      note: "Future subscription cycles. Never counted as recovered.",
      tone: "text-ink-2",
      weight: "text-sm",
    },
  ];
  return (
    <div className="space-y-2.5">
      {rows.map((row) => (
        <div key={row.label} className="flex items-baseline justify-between gap-4">
          <div className="min-w-0">
            <p className="text-xs font-medium text-ink">{row.label}</p>
            <p className="text-2xs leading-snug text-ink-muted">{row.note}</p>
          </div>
          <p className={`tabular shrink-0 ${row.weight} ${row.tone}`}>{row.value}</p>
        </div>
      ))}
    </div>
  );
}

export function RateBar({ rate, colorVar }: { rate: number; colorVar: string }) {
  const pct = Math.max(0, Math.min(1, rate)) * 100;
  return (
    <div className="mt-2">
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
        {/* 4px rounded data-end, anchored to the baseline */}
        <div
          className="h-full rounded-r-[4px]"
          style={{ width: `${pct}%`, background: colorVar }}
          role="img"
          aria-label={`Recovery rate ${percent(rate)}`}
        />
      </div>
    </div>
  );
}
