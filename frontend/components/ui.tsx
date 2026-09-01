"use client";

import { ReactNode } from "react";
import { titleCase } from "@/lib/format";

export function Card({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-card border border-line bg-surface ${className}`}
    >
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 border-b border-line px-4 py-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-muted">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

/** Status colours are reserved and always ship with a label, never colour alone. */
const STATUS_TONE: Record<string, string> = {
  RECOVERED: "text-good border-good/40 bg-good/10",
  SUCCESS: "text-good border-good/40 bg-good/10",
  APPROVED: "text-good border-good/40 bg-good/10",
  PROCESSED: "text-good border-good/40 bg-good/10",
  EXECUTING: "text-series-1 border-series-1/40 bg-series-1/10",
  DETECTED: "text-ink-2 border-line-strong bg-surface-2",
  ANALYZING: "text-ink-2 border-line-strong bg-surface-2",
  RECOMMENDED: "text-ink-2 border-line-strong bg-surface-2",
  PENDING: "text-warning border-warning/40 bg-warning/10",
  BLOCKED: "text-serious border-serious/40 bg-serious/10",
  DUPLICATE: "text-serious border-serious/40 bg-serious/10",
  EXHAUSTED: "text-ink-muted border-line-strong bg-surface-2",
  EXPIRED: "text-ink-muted border-line-strong bg-surface-2",
  FAILED: "text-critical border-critical/40 bg-critical/10",
  FAILURE: "text-critical border-critical/40 bg-critical/10",
  INVALID: "text-critical border-critical/40 bg-critical/10",
  NO_RESPONSE: "text-ink-muted border-line-strong bg-surface-2",
};

export function StatusBadge({ status, className = "" }: { status: string; className?: string }) {
  const tone = STATUS_TONE[status] ?? "text-ink-2 border-line-strong bg-surface-2";
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-2xs font-medium ${tone} ${className}`}
    >
      {status === "RECOVERED" && <span aria-hidden>✓</span>}
      {status === "BLOCKED" && <span aria-hidden>⊘</span>}
      {(status === "FAILED" || status === "INVALID") && <span aria-hidden>✕</span>}
      {titleCase(status)}
    </span>
  );
}

export function ScenarioChip({ scenario, label }: { scenario: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-ink-2">
      <span
        aria-hidden
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ background: `var(--series-${scenarioSlot(scenario)})` }}
      />
      {label}
    </span>
  );
}

export function scenarioSlot(scenario: string): 1 | 2 | 3 {
  if (scenario === "CHECKOUT_ABANDONMENT") return 2;
  if (scenario === "FAILED_SUBSCRIPTION") return 3;
  return 1;
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-surface-2 ${className}`} />;
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <p className="text-sm font-medium text-ink-2">{title}</p>
      {hint && <p className="max-w-md text-xs text-ink-muted">{hint}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-card border border-critical/40 bg-critical/10 px-4 py-3">
      <span aria-hidden className="text-critical">✕</span>
      <div>
        <p className="text-sm font-medium text-critical">Something went wrong</p>
        <p className="mt-0.5 text-xs text-ink-2">{message}</p>
      </div>
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  type = "button",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  const styles = {
    primary: "bg-[var(--series-1)] text-white hover:opacity-90 border-transparent",
    secondary: "bg-surface-2 text-ink border-line-strong hover:border-ink-muted",
    ghost: "bg-transparent text-ink-2 border-transparent hover:bg-surface-2",
  }[variant];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-md border px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-2xs text-ink-muted">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "mt-1 w-full rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-sm text-ink outline-none focus:border-[var(--series-1)]";
