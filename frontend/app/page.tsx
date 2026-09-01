"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import { ActivityEntry, DashboardSummary } from "@/lib/types";
import { SCENARIO_LABEL, SCENARIO_SHORT, SCENARIO_VAR, percent } from "@/lib/format";
import { Card, EmptyState, ErrorState, Skeleton, Button } from "@/components/ui";
import { KpiTile, MoneyDistinction, RateBar } from "@/components/kpi";
import { PipelineFunnel, ScenarioBars, ScenarioDatum } from "@/components/charts";
import { ActivityFeed } from "@/components/activity-feed";

export default function DashboardPage() {
  const summary = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: () => api.get<DashboardSummary>("/api/dashboard/summary"),
    refetchInterval: 8000,
  });

  const activity = useQuery({
    queryKey: ["dashboard-activity"],
    queryFn: () => api.get<ActivityEntry[]>("/api/dashboard/activity", { limit: 30 }),
    refetchInterval: 5000,
  });

  if (summary.isError) {
    return <ErrorState message={(summary.error as Error).message} />;
  }

  const data = summary.data;
  const totals = data?.totals;
  const funnel = data?.funnel;
  const loading = summary.isLoading;

  const hasData = (totals?.opportunities ?? 0) > 0;

  const scenarioData: ScenarioDatum[] =
    data?.scenarios.map((s) => ({
      scenario: s.scenario,
      label: SCENARIO_SHORT[s.scenario] ?? s.scenario,
      atRisk: s.revenue_at_risk.minor,
      recovered: s.recovered_revenue.minor,
      unrecovered: Math.max(0, s.revenue_at_risk.minor - s.recovered_revenue.minor),
      atRiskDisplay: s.revenue_at_risk.display,
      recoveredDisplay: s.recovered_revenue.display,
      rate: s.recovery_rate,
    })) ?? [];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Revenue Recovery</h1>
          <p className="mt-0.5 text-xs text-ink-2">
            Failed payments, abandoned checkouts and failed subscriptions — one pipeline.
          </p>
        </div>
        <Link href="/simulation">
          <Button variant="secondary">Run a simulation →</Button>
        </Link>
      </div>

      {!loading && !hasData ? (
        <Card>
          <EmptyState
            title="No recovery data yet"
            hint="Run a simulation to generate customers, payments, checkouts and subscriptions, then watch RecoverAI work them through the pipeline."
            action={
              <Link href="/simulation" className="mt-2">
                <Button>Open Simulation Control Centre</Button>
              </Link>
            }
          />
        </Card>
      ) : (
        <>
          <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <KpiTile
              label="Total revenue at risk"
              value={totals?.revenue_at_risk.compact ?? "—"}
              sub={totals ? `${totals.opportunities.toLocaleString("en-IN")} opportunities` : undefined}
              loading={loading}
            />
            <KpiTile
              label="Total recovered revenue"
              value={totals?.recovered_revenue.compact ?? "—"}
              tone="good"
              sub={
                totals
                  ? `${totals.opportunities_recovered.toLocaleString("en-IN")} settled in the ledger`
                  : undefined
              }
              loading={loading}
            />
            <KpiTile
              label="Overall recovery rate"
              value={totals ? percent(totals.recovery_rate) : "—"}
              tone="accent"
              sub="Recovered ÷ at risk"
              loading={loading}
            />
            <KpiTile
              label="Successful recoveries"
              value={funnel ? funnel.successful_recoveries.toLocaleString("en-IN") : "—"}
              sub={
                funnel
                  ? `${funnel.failed_attempts.toLocaleString("en-IN")} failed attempts · ${funnel.policy_blocked.toLocaleString("en-IN")} blocked`
                  : undefined
              }
              loading={loading}
            />
          </section>

          <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card
              title="Recovery by scenario"
              subtitle="Each bar is the revenue at risk; the filled part was actually recovered"
              className="lg:col-span-2"
            >
              {loading ? <Skeleton className="h-[230px] w-full" /> : <ScenarioBars data={scenarioData} />}
            </Card>

            <Card
              title="What counts as recovered"
              subtitle="Three different quantities, never added together"
            >
              {loading || !totals ? (
                <Skeleton className="h-[140px] w-full" />
              ) : (
                <MoneyDistinction
                  recovered={totals.recovered_revenue}
                  expected={totals.expected_recovery_value}
                  projected={totals.projected_retention_value}
                />
              )}
            </Card>
          </section>

          <section className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {data?.scenarios.map((s) => (
              <div key={s.scenario} className="rounded-card border border-line bg-surface p-4">
                <div className="flex items-center gap-2">
                  <span
                    aria-hidden
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ background: SCENARIO_VAR[s.scenario] }}
                  />
                  <h3 className="text-sm font-semibold text-ink">
                    {SCENARIO_LABEL[s.scenario] ?? s.scenario}
                  </h3>
                </div>
                <dl className="mt-3 space-y-1.5">
                  <div className="flex items-baseline justify-between">
                    <dt className="text-xs text-ink-2">Revenue at risk</dt>
                    <dd className="tabular text-sm font-medium text-ink">
                      {s.revenue_at_risk.compact}
                    </dd>
                  </div>
                  <div className="flex items-baseline justify-between">
                    <dt className="text-xs text-ink-2">Recovered</dt>
                    <dd className="tabular text-sm font-semibold text-good">
                      {s.recovered_revenue.compact}
                    </dd>
                  </div>
                  <div className="flex items-baseline justify-between">
                    <dt className="text-xs text-ink-2">Recovery rate</dt>
                    <dd className="tabular text-sm font-medium text-ink">{percent(s.recovery_rate)}</dd>
                  </div>
                </dl>
                <RateBar rate={s.recovery_rate} colorVar={SCENARIO_VAR[s.scenario]} />
                <p className="mt-2 text-2xs text-ink-muted">
                  {s.opportunities_recovered.toLocaleString("en-IN")} of{" "}
                  {s.opportunities.toLocaleString("en-IN")} opportunities recovered
                </p>
              </div>
            ))}
          </section>

          <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card title="Recovery pipeline" subtitle="Every opportunity takes this path">
              {loading || !funnel ? (
                <Skeleton className="h-[200px] w-full" />
              ) : (
                <PipelineFunnel
                  stages={[
                    { label: "Opportunities detected", value: funnel.opportunities_detected },
                    { label: "AI recommendations", value: funnel.ai_decisions },
                    {
                      label: "Policy approved",
                      value: funnel.policy_approved,
                      note: `${funnel.policy_blocked.toLocaleString("en-IN")} blocked by guardrails`,
                    },
                    { label: "Recovery attempts executed", value: funnel.recovery_attempts },
                    {
                      label: "Revenue recovered",
                      value: funnel.recovered,
                      note: "Settled in the ledger — money actually moved",
                    },
                  ]}
                />
              )}
            </Card>

            <Card
              title="Live activity"
              subtitle="Detection → AI → policy → execution → settlement"
              className="lg:col-span-2"
              action={
                <span className="inline-flex items-center gap-1.5 text-2xs text-ink-muted">
                  <span aria-hidden className="live-dot h-1.5 w-1.5 rounded-full bg-good" />
                  Live
                </span>
              }
            >
              {activity.isLoading ? (
                <Skeleton className="h-[300px] w-full" />
              ) : (
                <ActivityFeed entries={activity.data ?? []} />
              )}
            </Card>
          </section>
        </>
      )}
    </div>
  );
}
