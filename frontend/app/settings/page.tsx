"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, ErrorState, Skeleton } from "@/components/ui";
import { compactInr } from "@/lib/format";

type PolicySettings = {
  policy: {
    max_attempts: number;
    max_notifications: number;
    cooldown_minutes: number;
    max_discount_minor: number;
    opportunity_ttl_hours: number;
  };
  ai: {
    mode: string;
    model: string | null;
    llm_available: boolean;
    llm_budget_per_run: number;
    max_concurrency: number;
    timeout_seconds: number;
  };
  rules: { id: string; description: string }[];
};

export default function SettingsPage() {
  const query = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<PolicySettings>("/api/settings/policy"),
  });

  if (query.isError) return <ErrorState message={(query.error as Error).message} />;
  if (query.isLoading || !query.data) return <Skeleton className="h-[400px] w-full" />;

  const { policy, ai, rules } = query.data;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-ink">Settings</h1>
        <p className="mt-0.5 text-xs text-ink-2">
          The guardrails every AI recommendation must pass, and how the decision engine is configured.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Policy limits" subtitle="Stamped into every simulation run's config">
          <dl className="space-y-2 text-xs">
            <Row label="Maximum recovery attempts" value={String(policy.max_attempts)} />
            <Row label="Maximum customer messages" value={String(policy.max_notifications)} />
            <Row label="Cooldown between attempts" value={`${policy.cooldown_minutes} min`} />
            <Row
              label="Auto-approved discount ceiling"
              value={compactInr(policy.max_discount_minor)}
              note="Carts above this need manual approval before a discount"
            />
            <Row label="Opportunity time-to-live" value={`${policy.opportunity_ttl_hours} h`} />
          </dl>
        </Card>

        <Card title="Decision engine" subtitle="How recommendations are produced">
          <dl className="space-y-2 text-xs">
            <Row label="Mode" value={ai.mode} />
            <Row
              label="Model"
              value={ai.model ?? "not configured"}
              note={
                ai.llm_available
                  ? "Structured JSON output, validated before policy sees it"
                  : "No API key set — the deterministic engine runs instead"
              }
            />
            <Row label="LLM calls per run" value={String(ai.llm_budget_per_run)} />
            <Row label="Max concurrency" value={String(ai.max_concurrency)} />
            <Row label="Request timeout" value={`${ai.timeout_seconds}s`} />
          </dl>
          <p className="mt-3 border-t border-line pt-2 text-2xs leading-snug text-ink-muted">
            If the model is unavailable, times out, or returns output that fails validation,
            a deterministic fallback produces the decision instead. The platform never stops
            because the AI did.
          </p>
        </Card>
      </div>

      <Card title="Policy rules" subtitle="Evaluated on every recommendation, in order">
        <ul className="space-y-1.5">
          {rules.map((rule) => (
            <li key={rule.id} className="flex items-start gap-3 border-b border-line py-1.5 last:border-0">
              <span className="font-mono text-2xs text-ink-muted">{rule.id}</span>
              <span className="text-xs text-ink-2">{rule.description}</span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-2xs leading-snug text-ink-muted">
          The AI cannot bypass any of these, and high confidence never unlocks a blocked
          action — confidence is only ever used to make a rule stricter.
        </p>
      </Card>
    </div>
  );
}

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <div className="min-w-0">
        <dt className="text-ink-2">{label}</dt>
        {note && <p className="text-2xs leading-snug text-ink-muted">{note}</p>}
      </div>
      <dd className="tabular shrink-0 text-sm font-medium text-ink">{value}</dd>
    </div>
  );
}
