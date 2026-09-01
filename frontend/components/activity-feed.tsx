"use client";

import { ActivityEntry } from "@/lib/types";
import { time, titleCase } from "@/lib/format";

const ACTOR_COLOR: Record<string, string> = {
  AI: "var(--series-1)",
  POLICY: "var(--series-2)",
  EXECUTOR: "var(--series-3)",
  LEDGER: "var(--status-good)",
  SYSTEM: "var(--ink-muted)",
  WEBHOOK: "var(--series-1)",
  SIMULATOR: "var(--ink-muted)",
};

function describe(entry: ActivityEntry): string {
  const d = entry.detail ?? {};
  switch (entry.action) {
    case "OPPORTUNITY_DETECTED":
      return `${titleCase(String(d.scenario ?? ""))} · ${inr(d.amount_at_risk_minor)} at risk`;
    case "AI_RECOMMENDED":
      return `${d.action} · ${Math.round(Number(d.recovery_probability ?? 0) * 100)}% likely`;
    case "POLICY_APPROVED":
      return `${d.requested_action} approved`;
    case "POLICY_BLOCKED":
      return `${d.requested_action} blocked · ${d.blocked_by_rule}`;
    case "RECOVERY_EXECUTED":
      return `${d.action} · ${d.execution_status}`;
    case "RECOVERY_SETTLED":
      return `${inr(d.recovered_amount_minor)} recovered via ${d.action}`;
    default:
      return titleCase(entry.action);
  }
}

function inr(minor: unknown): string {
  const rupees = Number(minor ?? 0) / 100;
  return `₹${rupees.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

export function ActivityFeed({ entries }: { entries: ActivityEntry[] }) {
  if (!entries.length) {
    return <p className="py-6 text-center text-xs text-ink-muted">No activity yet.</p>;
  }
  return (
    <ol className="max-h-[420px] space-y-0 overflow-y-auto">
      {entries.map((entry) => (
        <li key={entry.id} className="flex gap-3 border-b border-line py-2 last:border-0">
          <span className="tabular w-[62px] shrink-0 pt-0.5 text-2xs text-ink-muted">
            {time(entry.occurred_at)}
          </span>
          <span
            aria-hidden
            className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ background: ACTOR_COLOR[entry.actor] ?? "var(--ink-muted)" }}
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-ink">
              {entry.entity_id ?? ""} {describe(entry)}
            </p>
            <p className="text-2xs text-ink-muted">{entry.actor}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}
