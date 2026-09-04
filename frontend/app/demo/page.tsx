"use client";

import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { DemoResult } from "@/lib/types";
import { SCENARIO_LABEL, percent, time } from "@/lib/format";
import { Button, Card, StatusBadge, Skeleton } from "@/components/ui";

const SCENARIOS = [
  {
    key: "failed-payment",
    title: "Failed payment",
    blurb: "₹5,000 UPI payment fails on a bank timeout.",
    scenario: "FAILED_PAYMENT",
  },
  {
    key: "checkout-abandonment",
    title: "Abandoned checkout",
    blurb: "₹7,000 cart abandoned at the payment step.",
    scenario: "CHECKOUT_ABANDONMENT",
  },
  {
    key: "subscription-failure",
    title: "Failed subscription",
    blurb: "₹999 renewal fails on an expired card.",
    scenario: "FAILED_SUBSCRIPTION",
  },
];

export default function DemoPage() {
  const [result, setResult] = useState<DemoResult | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [revealedCount, setRevealedCount] = useState<number>(0);

  const run = useMutation({
    mutationFn: (key: string) => api.post<DemoResult>(`/api/demo/${key}`, {}),
    onSuccess: (data) => {
      setResult(data);
      setRevealedCount(0);
    },
  });

  useEffect(() => {
    if (!result) return;
    if (revealedCount < result.narrative.length) {
      const timer = setTimeout(() => {
        setRevealedCount((c) => c + 1);
      }, 800);
      return () => clearTimeout(timer);
    }
  }, [result, revealedCount]);

  const isComplete = result && revealedCount === result.narrative.length;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-ink">Demo Mode</h1>
        <p className="mt-0.5 text-xs text-ink-2">
          One opportunity, start to finish, through the real pipeline — nothing is stubbed
          except the simulated payment outcome.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {SCENARIOS.map((s) => (
          <div key={s.key} className="rounded-card border border-line bg-surface p-4">
            <h2 className="text-sm font-semibold text-ink">{s.title}</h2>
            <p className="mt-1 text-xs text-ink-2">{s.blurb}</p>
            <Button
              className="mt-3 w-full"
              disabled={run.isPending}
              onClick={() => {
                setActive(s.key);
                run.mutate(s.key);
              }}
            >
              {run.isPending && active === s.key ? "Running…" : `Run ${s.title.toLowerCase()}`}
            </Button>
          </div>
        ))}
      </div>

      {run.isError && (
        <Card>
          <p className="text-xs text-critical">{(run.error as Error).message}</p>
        </Card>
      )}

      {result && (
        <>
          <div
            className="rounded-card border px-5 py-4 transition-all duration-500"
            style={{
              borderColor: isComplete
                ? result.opportunity.recovered_amount.minor > 0
                  ? "color-mix(in srgb, var(--status-good) 40%, transparent)"
                  : "var(--line)"
                : "transparent",
              background: isComplete
                ? result.opportunity.recovered_amount.minor > 0
                  ? "color-mix(in srgb, var(--status-good) 8%, transparent)"
                  : "var(--surface)"
                : "transparent",
            }}
          >
            {isComplete ? (
              <>
                <p className="text-2xs font-medium uppercase tracking-wide text-ink-muted">Result</p>
                <p
                  className={`tabular mt-1 text-3xl font-semibold ${
                    result.opportunity.recovered_amount.minor > 0 ? "text-good" : "text-ink-2"
                  }`}
                >
                  {result.headline}
                </p>
                <p className="mt-1 text-xs text-ink-2">
                  {SCENARIO_LABEL[result.opportunity.scenario]} ·{" "}
                  {result.opportunity.amount_at_risk.display} at risk ·{" "}
                  <Link
                    href={`/opportunities/${result.opportunity.opportunity_ref}`}
                    className="font-mono text-[var(--series-1)] hover:underline"
                  >
                    {result.opportunity.opportunity_ref}
                  </Link>
                </p>
              </>
            ) : (
              <div className="flex h-[80px] items-center justify-center">
                <p className="text-sm text-ink-muted animate-pulse">Running simulation pipeline...</p>
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card title="What RecoverAI did" subtitle="Each step, in order">
              <ol className="space-y-2">
                {result.narrative.slice(0, revealedCount).map((step) => (
                  <li key={step.step} className="rounded-md border border-line bg-surface-2/40 p-3 animate-in fade-in slide-in-from-bottom-2 duration-300">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-surface px-1.5 py-0.5 text-2xs text-ink-2">
                        Step {step.step}
                      </span>
                      <span className="text-sm font-semibold text-ink">{step.action}</span>
                      {step.policy_verdict && <StatusBadge status={step.policy_verdict} />}
                      {step.outcome && <StatusBadge status={step.outcome} />}
                      {step.recovered.minor > 0 && (
                        <span className="tabular text-sm font-semibold text-good">
                          {step.recovered.display}
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-ink-2">{step.reason}</p>
                    {step.blocked_by_rule && (
                      <p className="mt-1 font-mono text-2xs text-serious">{step.blocked_by_rule}</p>
                    )}
                  </li>
                ))}
                {!isComplete && (
                  <li className="flex items-center gap-2 p-3">
                    <Skeleton className="h-4 w-4 rounded-full animate-pulse" />
                    <Skeleton className="h-4 w-32 animate-pulse" />
                  </li>
                )}
              </ol>

              {isComplete && result.opportunity.ai_decisions[0] && (
                <div className="mt-3 border-t border-line pt-3 animate-in fade-in duration-500">
                  <p className="text-2xs font-medium uppercase tracking-wide text-ink-muted">
                    The point
                  </p>
                  <p className="mt-1 text-xs leading-relaxed text-ink-2">
                    The AI estimated{" "}
                    <strong className="text-ink">
                      {percent(result.opportunity.ai_decisions[0].recovery_probability, 0)}
                    </strong>{" "}
                    likelihood. That number never touched the ledger. The{" "}
                    <strong className="text-good">
                      {result.opportunity.recovered_amount.display}
                    </strong>{" "}
                    was recorded because a payment actually succeeded — not because the model
                    was confident.
                  </p>
                </div>
              )}
            </Card>

            <Card title="Event timeline" subtitle="Detection through settlement">
              {isComplete ? (
                <>
                  <ol className="relative space-y-3 pl-4 animate-in fade-in duration-500">
                    <span aria-hidden className="absolute bottom-1 left-[3px] top-1 w-px bg-line" />
                    {result.opportunity.timeline.map((event, i) => (
                      <li key={i} className="relative">
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

                  {result.opportunity.ledger[0] && (
                    <div className="mt-3 rounded-md border border-good/40 bg-good/5 p-3 animate-in fade-in slide-in-from-bottom-2 duration-500">
                      <p className="text-xs font-semibold text-good">Ledger entry</p>
                      <p className="mt-1 font-mono text-2xs text-ink-2">
                        {result.opportunity.ledger[0].settlement_key}
                      </p>
                      <p className="mt-1 text-2xs text-ink-muted">
                        Unique per opportunity — this revenue cannot be counted a second time.
                      </p>
                    </div>
                  )}
                </>
              ) : (
                <div className="space-y-4 pt-2">
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-4 w-1/2" />
                  <Skeleton className="h-4 w-5/6" />
                </div>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
