"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import { RazorpayStatus } from "@/lib/types";
import { SCENARIO_SHORT, dateTime, relativeTime } from "@/lib/format";
import { Button, Card, EmptyState, ErrorState, ScenarioChip, Skeleton, StatusBadge } from "@/components/ui";

export default function RazorpayPage() {
  const query = useQuery({
    queryKey: ["razorpay-status"],
    queryFn: () => api.get<RazorpayStatus>("/api/razorpay/status"),
    refetchInterval: 5000,
  });

  const paymentMutation = useMutation({
    mutationFn: () =>
      api.post<{ payment_link_id: string; short_url: string; amount: string; status: string; mode: string }>(
        "/api/razorpay/test-payment",
        { amount_minor: 500000, description: "RecoverAI Test Mode payment", customer_ref: "CUST-DEMO" }
      ),
  });

  if (query.isError) return <ErrorState message={(query.error as Error).message} />;
  if (query.isLoading || !query.data) return <Skeleton className="h-[400px] w-full" />;

  const { integration, webhook, recent_events, live_opportunities } = query.data;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Razorpay Test Mode</h1>
          <p className="mt-0.5 text-xs text-ink-2">
            Live gateway events enter the same pipeline as simulated ones.
          </p>
        </div>
        <Button 
          onClick={() => paymentMutation.mutate()} 
          disabled={paymentMutation.isPending || !integration.configured}
        >
          {paymentMutation.isPending ? "Creating..." : "Create Test Payment"}
        </Button>
      </div>

      {paymentMutation.isSuccess && paymentMutation.data && (
        <div className="rounded-md border border-[var(--series-1)]/40 bg-[var(--series-1)]/10 px-4 py-3">
          <p className="text-sm font-medium text-[var(--series-1)]">Test Payment Link Created!</p>
          <p className="mt-1 text-xs text-ink-2">
            Click here to open it:{" "}
            <a href={paymentMutation.data.short_url} target="_blank" rel="noreferrer" className="font-mono text-[var(--series-1)] hover:underline">
              {paymentMutation.data.short_url}
            </a>
          </p>
          <p className="mt-1 text-2xs text-ink-muted">To simulate a failure, use Razorpay test card: <strong className="font-mono">4111 1111 1111 1112</strong></p>
        </div>
      )}


      <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <ConnectionTile
          label="API credentials"
          connected={integration.configured}
          detail={integration.configured ? (integration.key_id ?? "configured") : "RAZORPAY_KEY_ID not set"}
        />
        <ConnectionTile
          label="Webhook secret"
          connected={integration.webhook_configured}
          detail={
            integration.webhook_configured
              ? "Signature verification enabled"
              : "RAZORPAY_WEBHOOK_SECRET not set"
          }
        />
        <ConnectionTile
          label="Gateway reachable"
          connected={integration.reachable === true}
          detail={
            integration.reachable === null
              ? "Not checked — credentials missing"
              : integration.reachable
                ? "Test Mode responding"
                : (integration.error ?? "Unreachable")
          }
        />
      </section>

      {!integration.configured && (
        <Card title="Connect Razorpay Test Mode">
          <ol className="space-y-2 text-xs text-ink-2">
            <li>
              1. Put your Test Mode keys in <code className="font-mono text-ink">.env</code>:{" "}
              <code className="font-mono text-ink">RAZORPAY_KEY_ID</code>,{" "}
              <code className="font-mono text-ink">RAZORPAY_KEY_SECRET</code>.
            </li>
            <li>
              2. Add a webhook in the Razorpay dashboard pointing at{" "}
              <code className="font-mono text-ink">{webhook.endpoint}</code> and set{" "}
              <code className="font-mono text-ink">RAZORPAY_WEBHOOK_SECRET</code> to match.
            </li>
            <li>
              3. Subscribe to <code className="font-mono text-ink">payment.failed</code>,{" "}
              <code className="font-mono text-ink">payment.captured</code>,{" "}
              <code className="font-mono text-ink">order.paid</code>.
            </li>
            <li>4. Restart the API. Events will appear below as they arrive.</li>
          </ol>
          <p className="mt-3 text-2xs text-ink-muted">
            Credentials are read from the environment only and are never sent to the browser —
            this page receives booleans and a masked key id.
          </p>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card
          title="Recent webhook events"
          subtitle={`${webhook.events_received} received · endpoint ${webhook.endpoint}`}
        >
          {recent_events.length === 0 ? (
            <EmptyState
              title="No webhook events yet"
              hint="Trigger a Test Mode payment in Razorpay and it will appear here within seconds."
            />
          ) : (
            <ul className="max-h-[360px] space-y-0 overflow-y-auto">
              {recent_events.map((event) => (
                <li key={event.id} className="flex items-start gap-3 border-b border-line py-2 last:border-0">
                  <span className="tabular w-[72px] shrink-0 pt-0.5 text-2xs text-ink-muted">
                    {relativeTime(event.received_at)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-ink">{event.event_type ?? "unknown"}</span>
                      <StatusBadge status={event.status} />
                      {!event.signature_valid && (
                        <span className="rounded border border-critical/40 bg-critical/10 px-1.5 py-0.5 text-2xs text-critical">
                          ✕ Bad signature
                        </span>
                      )}
                    </div>
                    <p className="truncate font-mono text-2xs text-ink-muted">{event.event_id}</p>
                    {event.error && <p className="text-2xs text-critical">{event.error}</p>}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 border-t border-line pt-2 text-2xs leading-snug text-ink-muted">
            Events marked <strong className="text-ink-2">Duplicate</strong> were redeliveries.
            They are acknowledged and dropped — a replayed success cannot recover the same
            money twice.
          </p>
        </Card>

        <Card title="Opportunities from live events" subtitle="Same pipeline, same guardrails, same ledger">
          {live_opportunities.length === 0 ? (
            <EmptyState
              title="No live opportunities yet"
              hint="A payment.failed webhook creates a recovery opportunity here."
            />
          ) : (
            <ul className="space-y-2">
              {live_opportunities.map((o) => (
                <li key={o.id} className="rounded-md border border-line p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Link
                      href={`/opportunities/${o.opportunity_ref}`}
                      className="font-mono text-xs font-medium text-[var(--series-1)] hover:underline"
                    >
                      {o.opportunity_ref}
                    </Link>
                    <StatusBadge status={o.status} />
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-3">
                    <ScenarioChip scenario={o.scenario} label={SCENARIO_SHORT[o.scenario] ?? o.scenario} />
                    <span className="tabular text-xs text-ink-2">{o.amount_at_risk.display} at risk</span>
                    {o.recovered_amount.minor > 0 && (
                      <span className="tabular text-xs font-semibold text-good">
                        {o.recovered_amount.display} recovered
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-2xs text-ink-muted">
                    {o.failure_code ?? "—"} · detected {dateTime(o.detected_at)}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function ConnectionTile({
  label,
  connected,
  detail,
}: {
  label: string;
  connected: boolean;
  detail: string;
}) {
  return (
    <div className="rounded-card border border-line bg-surface px-4 py-3">
      <p className="text-2xs font-medium uppercase tracking-wide text-ink-muted">{label}</p>
      <p className={`mt-1 flex items-center gap-1.5 text-sm font-semibold ${connected ? "text-good" : "text-ink-muted"}`}>
        <span aria-hidden>{connected ? "●" : "○"}</span>
        {connected ? "Connected" : "Not connected"}
      </p>
      <p className="mt-0.5 truncate font-mono text-2xs text-ink-muted">{detail}</p>
    </div>
  );
}
