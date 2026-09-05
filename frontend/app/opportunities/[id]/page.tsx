"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { OpportunityDetail } from "@/lib/types";
import { SCENARIO_LABEL, dateTime, percent, time, titleCase } from "@/lib/format";
import { Button, Card, ErrorState, ScenarioChip, Skeleton, StatusBadge } from "@/components/ui";

export default function OpportunityDetailPage() {
  const params = useParams<{ id: string }>();
  const queryClient = useQueryClient();

  const query = useQuery({
    queryKey: ["opportunity", params.id],
    queryFn: () => api.get<OpportunityDetail>(`/api/opportunities/${params.id}`),
  });

  const recover = useMutation({
    mutationFn: () => api.post(`/api/opportunities/${params.id}/recover`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["opportunity", params.id] }),
  });

  if (query.isError) return <ErrorState message={(query.error as Error).message} />;
  if (query.isLoading || !query.data) return <Skeleton className="h-[600px] w-full" />;

  const o = query.data;
  const closed = ["RECOVERED", "EXHAUSTED", "EXPIRED"].includes(o.status);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/opportunities" className="text-xs text-ink-muted hover:text-ink">
            ← All opportunities
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="font-mono text-lg font-semibold tracking-tight text-ink">
              {o.opportunity_ref}
            </h1>
            <StatusBadge status={o.status} />
            <ScenarioChip scenario={o.scenario} label={SCENARIO_LABEL[o.scenario] ?? o.scenario} />
            <span className="rounded border border-line-strong px-1.5 py-0.5 text-2xs text-ink-muted">
              {o.source}
            </span>
          </div>
        </div>
        {!closed && (
          <Button onClick={() => recover.mutate()} disabled={recover.isPending}>
            {recover.isPending ? "Running…" : "Run recovery cycle"}
          </Button>
        )}
      </div>

      {/* Headline money row — the §39 distinction, again, in place. */}
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Stat label="Revenue at risk" value={o.amount_at_risk.display} />
        <Stat
          label="Recovered revenue"
          value={o.recovered_amount.display}
          tone={o.recovered_amount.minor > 0 ? "good" : "muted"}
          note={
            o.recovered_amount.minor > 0
              ? `Settled ${dateTime(o.recovered_at)}`
              : "Nothing settled for this opportunity"
          }
        />
        <Stat
          label="Projected retention"
          value={o.projected_retention.display}
          note="Future cycles — never counted as recovered revenue"
        />
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title="Customer context" subtitle="What the AI could observe">
          <dl className="space-y-1.5 text-xs">
            <Row label="Customer" value={`${o.customer.name ?? "—"} (${o.customer.customer_ref})`} />
            <Row label="Segment" value={titleCase(o.customer.segment)} />
            <Row label="Account age" value={`${o.customer.account_age_days} days`} />
            <Row
              label="Historical success rate"
              value={percent(o.customer.historical_success_rate)}
              emphasise
            />
            <Row label="Previous transactions" value={String(o.customer.previous_transaction_count)} />
            <Row label="Average order value" value={o.customer.average_order_value.display} />
            <Row label="Lifetime value" value={o.customer.lifetime_value.display} />
            <Row label="Preferred method" value={o.customer.preferred_payment_method ?? "—"} />
            <Row label="Previous recoveries" value={String(o.customer.previous_recoveries)} />
          </dl>
        </Card>

        <Card title="Failure" subtitle="Why the revenue is at risk" className="lg:col-span-2">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs">
            <Row label="Category" value={o.failure_category ?? "—"} emphasise />
            <Row label="Gateway code" value={o.failure_code ?? "—"} />
            <Row label="Reason" value={o.reason_code ?? "—"} />
            <Row label="Detected" value={dateTime(o.detected_at)} />
            <Row label="Attempts made" value={String(o.attempt_count)} />
            <Row label="Customer messages sent" value={String(o.notification_count)} />
          </dl>

          {o.ai_decisions.length > 0 && (
            <div className="mt-4 border-t border-line pt-3">
              <h3 className="text-xs font-semibold text-ink">Why the AI chose what it chose</h3>
              <p className="mt-1 text-xs leading-relaxed text-ink-2">
                {o.ai_decisions[o.ai_decisions.length - 1].reason}
              </p>
            </div>
          )}
        </Card>
      </section>

      <Card title="Decision trail" subtitle="Every recommendation, every guardrail, every outcome">
        <div className="space-y-3">
          {o.ai_decisions.map((decision) => {
            // Join on identity, never on array position: a blocked recommendation
            // consumes a decision without producing an attempt, so the arrays differ.
            const policy = o.policy_decisions.find((p) => p.ai_decision_id === decision.id);
            const attempt = o.attempts.find((a) => a.ai_decision_id === decision.id);
            return (
              <div key={decision.id} className="rounded-md border border-line bg-surface-2/40 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-surface px-1.5 py-0.5 text-2xs font-medium text-ink-2">
                    Decision {decision.sequence}
                  </span>
                  <span className="text-sm font-semibold text-ink">{decision.action}</span>
                  <span className="tabular text-xs text-ink-2">
                    {percent(decision.recovery_probability, 0)} likely · {percent(decision.confidence, 0)} confident
                  </span>
                  <span className="rounded border border-line-strong px-1.5 py-0.5 text-2xs text-ink-muted">
                    {decision.risk_level} risk
                  </span>
                  <span className="rounded border border-line-strong px-1.5 py-0.5 text-2xs text-ink-muted">
                    {decision.decision_source}
                    {decision.model ? ` · ${decision.model}` : ""}
                  </span>
                </div>

                <p className="mt-1.5 text-xs leading-relaxed text-ink-2">{decision.reason}</p>

                {decision.validation_errors && decision.validation_errors.length > 0 && (
                  <p className="mt-1.5 rounded border border-serious/40 bg-serious/10 px-2 py-1 text-2xs text-serious">
                    Model output rejected ({decision.validation_errors.join("; ")}) — deterministic
                    fallback used instead.
                  </p>
                )}

                {policy && (
                  <div className="mt-2 border-t border-line pt-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge status={policy.verdict} />
                      <span className="text-xs text-ink-2">{policy.reason}</span>
                    </div>
                    <details className="mt-1.5">
                      <summary className="cursor-pointer text-2xs text-ink-muted hover:text-ink-2">
                        {policy.rules_evaluated.length} policy rules evaluated
                      </summary>
                      <ul className="mt-1.5 space-y-0.5">
                        {policy.rules_evaluated.map((rule) => (
                          <li key={rule.rule_id} className="flex items-start gap-2 text-2xs">
                            <span
                              aria-hidden
                              className={rule.passed ? "text-good" : "text-critical"}
                            >
                              {rule.passed ? "✓" : "✕"}
                            </span>
                            <span className="font-mono text-ink-muted">{rule.rule_id}</span>
                            <span className="text-ink-2">{rule.message}</span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  </div>
                )}

                {attempt && (
                  <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-line pt-2">
                    <span className="font-mono text-2xs text-ink-muted">{attempt.attempt_ref}</span>
                    <span className="text-xs text-ink-2">
                      executed via {attempt.executor}
                    </span>
                    {attempt.outcome ? (
                      <>
                        <StatusBadge status={attempt.outcome.outcome} />
                        {attempt.outcome.outcome === "SUCCESS" && (
                          <span className="tabular text-xs font-semibold text-good">
                            {attempt.outcome.realized_amount.display}
                          </span>
                        )}
                        <span className="text-2xs text-ink-muted">
                          evidence: {attempt.outcome.evidence_ref ?? "—"} ({attempt.outcome.evidence_type})
                        </span>
                      </>
                    ) : (
                      <StatusBadge status="PENDING" />
                    )}
                  </div>
                )}
              </div>
            );
          })}
          {o.ai_decisions.length === 0 && (
            <p className="py-4 text-center text-xs text-ink-muted">
              No decisions yet — this opportunity has not entered the pipeline.
            </p>
          )}
        </div>
      </Card>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Event timeline" subtitle="What happened, and when">
          <ol className="relative space-y-3 pl-4">
            <span aria-hidden className="absolute bottom-1 left-[3px] top-1 w-px bg-line" />
            {o.timeline.map((event, i) => (
              <li key={`${event.at}-${i}`} className="relative">
                <span
                  aria-hidden
                  className="absolute -left-4 top-1 h-1.5 w-1.5 rounded-full bg-[var(--series-1)] ring-2 ring-[var(--surface)]"
                />
                <div className="flex items-baseline gap-2">
                  <span className="tabular text-2xs text-ink-muted">{time(event.at)}</span>
                  <span className="text-xs font-medium text-ink">{event.title}</span>
                  <span className="text-2xs text-ink-muted">{event.actor}</span>
                </div>
              </li>
            ))}
          </ol>
        </Card>

        <Card
          title="Customer messages"
          subtitle="Sent when only the customer can fix the payment method"
        >
          {o.messages.length === 0 ? (
            <p className="py-6 text-center text-xs text-ink-muted">
              No message was sent for this opportunity.
            </p>
          ) : (
            <ul className="space-y-2">
              {o.messages.map((m) => (
                <li key={m.id} className="rounded-md border border-line p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded border border-line-strong px-1.5 py-0.5 text-2xs font-medium text-ink-2">
                      {m.channel}
                    </span>
                    <StatusBadge status={m.status} />
                    <span className="font-mono text-2xs text-ink-muted">{m.recipient ?? "—"}</span>
                    {m.delivered_externally ? (
                      <span className="text-2xs text-good">delivered via {m.provider}</span>
                    ) : (
                      <span className="text-2xs text-ink-muted">
                        {m.status === "SIMULATED" ? "composed, not delivered" : m.reason ?? ""}
                      </span>
                    )}
                  </div>
                  {m.body && (
                    <pre className="mt-2 whitespace-pre-wrap font-sans text-xs leading-relaxed text-ink-2">
                      {m.body}
                    </pre>
                  )}
                  {m.error && <p className="mt-1 text-2xs text-critical">{m.error}</p>}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 border-t border-line pt-2 text-2xs leading-snug text-ink-muted">
            A delivered message is a recovery action, not recovered revenue — it only counts
            once the customer actually pays.
          </p>
        </Card>

        <Card title="Recovery ledger" subtitle="The only record that counts as money">
          {o.ledger.length === 0 ? (
            <p className="py-6 text-center text-xs text-ink-muted">
              No ledger entry — no revenue was recovered for this opportunity.
            </p>
          ) : (
            o.ledger.map((entry) => (
              <div key={entry.id} className="rounded-md border border-good/40 bg-good/5 p-3">
                <div className="flex items-baseline justify-between">
                  <span className="text-xs font-semibold text-good">Revenue recovered</span>
                  <span className="tabular text-lg font-semibold text-good">
                    {entry.recovered_amount.display}
                  </span>
                </div>
                <dl className="mt-2 space-y-1 text-2xs">
                  <Row label="Original amount at risk" value={entry.original_amount.display} />
                  <Row label="Recovered by" value={entry.action ?? "—"} />
                  <Row label="On attempt" value={String(entry.attempt_number ?? "—")} />
                  <Row label="Source" value={entry.source} />
                  <Row label="Settled at" value={dateTime(entry.settled_at)} />
                  <Row label="Settlement key" value={entry.settlement_key} mono />
                </dl>
                <p className="mt-2 text-2xs leading-snug text-ink-muted">
                  This key is unique — replaying the same evidence, or a second successful
                  payment, cannot add to this figure.
                </p>
              </div>
            ))
          )}
        </Card>
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  note,
  tone = "neutral",
}: {
  label: string;
  value: string;
  note?: string;
  tone?: "neutral" | "good" | "muted";
}) {
  const color = tone === "good" ? "text-good" : tone === "muted" ? "text-ink-muted" : "text-ink";
  return (
    <div className="rounded-card border border-line bg-surface px-4 py-3">
      <p className="text-2xs font-medium uppercase tracking-wide text-ink-muted">{label}</p>
      <p className={`tabular mt-1 text-xl font-semibold ${color}`}>{value}</p>
      {note && <p className="mt-0.5 text-2xs text-ink-muted">{note}</p>}
    </div>
  );
}

function Row({
  label,
  value,
  emphasise,
  mono,
}: {
  label: string;
  value: string;
  emphasise?: boolean;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-ink-muted">{label}</dt>
      <dd
        className={`text-right ${mono ? "font-mono text-2xs" : ""} ${
          emphasise ? "font-semibold text-ink" : "text-ink-2"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}
