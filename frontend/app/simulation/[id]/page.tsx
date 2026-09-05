"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { SimulationReport } from "@/lib/types";
import { SCENARIO_LABEL, dateTime, percent } from "@/lib/format";
import { Card, ErrorState, Skeleton, StatusBadge } from "@/components/ui";
import { BaselineBars, BaselineDatum, PipelineFunnel } from "@/components/charts";
import { KpiTile, MoneyDistinction } from "@/components/kpi";

const STRATEGY_LABEL: Record<string, string> = {
  NO_RECOVERY: "No recovery",
  ALWAYS_RETRY: "Always retry",
  FIXED_RETRY: "Fixed retry",
  RECOVERAI: "RecoverAI",
};

export default function SimulationReportPage() {
  const params = useParams<{ id: string }>();
  const query = useQuery({
    queryKey: ["simulation-report", params.id],
    queryFn: () => api.get<SimulationReport>(`/api/simulation/${params.id}/report`),
  });

  if (query.isError) return <ErrorState message={(query.error as Error).message} />;
  if (query.isLoading || !query.data) return <Skeleton className="h-[600px] w-full" />;

  const { run, reproducibility: repro, totals, scenarios, funnel, ai_performance: ai, baselines, uplift } =
    query.data;
  const counters = (run.progress?.counters ?? {}) as Record<string, number>;

  const chartData: BaselineDatum[] = baselines.map((b) => ({
    strategy: b.strategy,
    label: STRATEGY_LABEL[b.strategy] ?? b.strategy,
    recovered: b.recovered_revenue.minor,
    display: b.recovered_revenue.display,
    rate: b.recovery_rate,
    attempts: b.attempts,
    efficiency: b.recovery_efficiency.display,
  }));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/simulation" className="text-xs text-ink-muted hover:text-ink">
            ← All runs
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="font-mono text-lg font-semibold tracking-tight text-ink">{run.run_ref}</h1>
            <StatusBadge status={run.status} />
            {run.label && <span className="text-xs text-ink-2">{run.label}</span>}
          </div>
          <p className="mt-0.5 text-xs text-ink-muted">
            seed {run.seed} · {run.ai_mode} engine ·{" "}
            {run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : "—"} ·{" "}
            {dateTime(run.started_at)}
          </p>
        </div>
        <Link href={`/opportunities?run_id=${run.id}`}>
          <span className="rounded-md border border-line-strong px-3 py-1.5 text-xs text-ink-2 hover:text-ink">
            View this run&rsquo;s opportunities →
          </span>
        </Link>
      </div>

      {run.error && <ErrorState message={run.error} />}

      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <KpiTile label="Revenue at risk" value={totals.revenue_at_risk.compact}
          sub={`${totals.opportunities.toLocaleString("en-IN")} opportunities`} />
        <KpiTile label="Recovered revenue" value={totals.recovered_revenue.compact} tone="good"
          sub={`${totals.opportunities_recovered.toLocaleString("en-IN")} settled in the ledger`} />
        <KpiTile label="Recovery rate" value={percent(totals.recovery_rate)} tone="accent"
          sub="Recovered ÷ at risk" />
        <KpiTile label="Uplift over best baseline"
          value={uplift.uplift_percent != null ? `+${uplift.uplift_percent}%` : "—"}
          tone="accent"
          sub={uplift.best_baseline ? `vs ${STRATEGY_LABEL[uplift.best_baseline]}` : undefined} />
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title="What was generated" subtitle="The synthetic merchant this run created">
          <dl className="space-y-1.5 text-xs">
            <Row label="Customers" value={counters.customers} />
            <Row label="Payments" value={counters.payments_total}
              note={`${counters.payments_succeeded ?? 0} succeeded · ${counters.payments_failed ?? 0} failed`} />
            <Row label="Checkouts" value={counters.checkouts_total}
              note={`${counters.checkouts_completed ?? 0} completed · ${counters.checkouts_abandoned ?? 0} abandoned`} />
            <Row label="Subscriptions" value={counters.subscriptions_total}
              note={`${counters.renewals_succeeded ?? 0} renewed · ${counters.renewals_failed ?? 0} failed`} />
            <Row label="Opportunities detected" value={counters.opportunities} />
          </dl>
        </Card>

        <Card title="What counts as recovered" subtitle="Three quantities, never added together">
          <MoneyDistinction
            recovered={totals.recovered_revenue}
            expected={totals.expected_recovery_value}
            projected={totals.projected_retention_value}
          />
        </Card>

        <Card title="Reproducibility" subtitle="Everything needed to re-run this experiment">
          <dl className="space-y-1.5 text-xs">
            <Row label="Seed" value={repro.seed} mono />
            <Row label="Decision engine" value={repro.ai_mode} />
            {repro.ai_model && <Row label="Model" value={repro.ai_model} />}
            <Row label="Engine version" value={repro.engine_version} mono />
            <Row label="Data version" value={repro.data_version} mono />
            <Row label="Max attempts" value={(repro.policy_snapshot as Record<string, number>)?.max_attempts} />
            <Row label="Max messages" value={(repro.policy_snapshot as Record<string, number>)?.max_notifications} />
          </dl>
          <p
            className={`mt-3 rounded border px-2 py-1.5 text-2xs leading-snug ${
              repro.deterministic
                ? "border-good/40 bg-good/10 text-good"
                : "border-line-strong bg-surface-2 text-ink-2"
            }`}
          >
            {repro.deterministic
              ? "Fully reproducible — re-running this seed reproduces every number on this page."
              : "A live model made the decisions, so re-running this seed reproduces the dataset and hidden truth, but not necessarily the decisions."}
          </p>
        </Card>
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title="Recovery pipeline" subtitle="Every opportunity took this path">
          <PipelineFunnel
            stages={[
              { label: "Opportunities detected", value: funnel.opportunities_detected },
              { label: "AI recommendations", value: funnel.ai_decisions },
              {
                label: "Policy approved",
                value: funnel.policy_approved,
                note: `${funnel.policy_blocked.toLocaleString("en-IN")} blocked by guardrails`,
              },
              { label: "Attempts executed", value: funnel.recovery_attempts },
              {
                label: "Revenue recovered",
                value: funnel.recovered,
                note: "Settled in the ledger",
              },
            ]}
          />
        </Card>

        <Card title="By scenario" subtitle="Revenue at risk against what was recovered" className="lg:col-span-2">
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="border-b border-line text-left">
                  {["Scenario", "At risk", "Recovered", "Rate", "Opportunities", "Recovered"].map((h, i) => (
                    <th key={i} className="px-3 py-2 text-2xs font-semibold uppercase tracking-wide text-ink-muted">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {scenarios.map((s) => (
                  <tr key={s.scenario} className="border-b border-line last:border-0">
                    <td className="px-3 py-2 text-xs text-ink">{SCENARIO_LABEL[s.scenario] ?? s.scenario}</td>
                    <td className="tabular px-3 py-2 text-xs text-ink-2">{s.revenue_at_risk.display}</td>
                    <td className="tabular px-3 py-2 text-xs font-medium text-good">{s.recovered_revenue.display}</td>
                    <td className="tabular px-3 py-2 text-xs text-ink-2">{percent(s.recovery_rate)}</td>
                    <td className="tabular px-3 py-2 text-xs text-ink-2">{s.opportunities}</td>
                    <td className="tabular px-3 py-2 text-xs text-ink-2">{s.opportunities_recovered}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Baseline comparison" subtitle="Same opportunities, same hidden truth, same dice">
          {baselines.length ? (
            <>
              <BaselineBars data={chartData} />
              <div className="-mx-4 mt-3 overflow-x-auto">
                <table className="w-full min-w-[520px] text-sm">
                  <thead>
                    <tr className="border-b border-line text-left">
                      {["Strategy", "Recovered", "Rate", "Attempts", "Wasted", "₹/attempt"].map((h) => (
                        <th key={h} className="px-3 py-2 text-2xs font-semibold uppercase tracking-wide text-ink-muted">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {baselines.map((b) => {
                      const us = b.strategy === "RECOVERAI";
                      return (
                        <tr key={b.strategy} className={`border-b border-line last:border-0 ${us ? "bg-[var(--series-1)]/5" : ""}`}>
                          <td className={`px-3 py-2 text-xs ${us ? "font-semibold text-ink" : "text-ink-2"}`}>
                            {STRATEGY_LABEL[b.strategy] ?? b.strategy}
                          </td>
                          <td className={`tabular px-3 py-2 text-xs ${us ? "font-semibold text-good" : "text-ink-2"}`}>
                            {b.recovered_revenue.display}
                          </td>
                          <td className="tabular px-3 py-2 text-xs text-ink-2">{percent(b.recovery_rate)}</td>
                          <td className="tabular px-3 py-2 text-xs text-ink-2">{b.attempts}</td>
                          <td className="tabular px-3 py-2 text-xs text-ink-2">{b.unnecessary_attempts}</td>
                          <td className="tabular px-3 py-2 text-xs text-ink-2">{b.recovery_efficiency.display}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="py-8 text-center">
              <p className="text-sm text-ink-2">Baselines were not computed for this run</p>
              <p className="mt-1 text-2xs text-ink-muted">
                The run was started with baseline comparison disabled. Re-run with it enabled to
                compare against no-recovery, always-retry and fixed-retry.
              </p>
            </div>
          )}
        </Card>

        <Card title="AI decision quality" subtitle="Measured against outcomes it never saw">
          <dl className="space-y-2 text-xs">
            <Metric label="Decisions made" value={ai.decisions.toLocaleString("en-IN")} />
            <Metric label="Chose the hindsight-optimal action"
              value={ai.action_accuracy != null ? percent(ai.action_accuracy) : "—"}
              note="The best action is partly unobservable, so this ceiling is below 100%." />
            <Metric label="Predicted vs actual"
              value={`${percent(ai.average_predicted_probability ?? 0)} vs ${percent(ai.actual_recovery_rate ?? 0)}`} />
            <Metric label="Expected calibration error"
              value={ai.calibration_error != null ? percent(ai.calibration_error) : "—"}
              note="Lower is better; 0% means predictions match reality." />
            <Metric label="Brier score" value={ai.brier_score != null ? ai.brier_score.toFixed(3) : "—"} />
            <Metric label="Actions on unrecoverable revenue"
              value={ai.unnecessary_action_rate != null ? percent(ai.unnecessary_action_rate) : "—"} />
            <Metric label="Deliberate stops" value={String(ai.stop_decisions ?? 0)} />
            <div className="border-t border-line pt-2">
              <dt className="text-ink-muted">Decision sources</dt>
              <dd className="mt-1 flex flex-wrap gap-2">
                {Object.entries(ai.decision_sources).map(([src, n]) => (
                  <span key={src} className="rounded border border-line-strong px-1.5 py-0.5 text-2xs text-ink-2">
                    {src} · {n.toLocaleString("en-IN")}
                  </span>
                ))}
              </dd>
            </div>
          </dl>
        </Card>
      </section>
    </div>
  );
}

function Row({ label, value, note, mono }: { label: string; value: unknown; note?: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <div className="min-w-0">
        <dt className="text-ink-muted">{label}</dt>
        {note && <p className="text-2xs leading-snug text-ink-muted">{note}</p>}
      </div>
      <dd className={`tabular shrink-0 font-medium text-ink ${mono ? "font-mono text-2xs" : ""}`}>
        {value === undefined || value === null ? "—" : String(value)}
      </dd>
    </div>
  );
}

function Metric({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <div className="min-w-0">
        <dt className="text-ink-2">{label}</dt>
        {note && <p className="text-2xs leading-snug text-ink-muted">{note}</p>}
      </div>
      <dd className="tabular shrink-0 text-sm font-semibold text-ink">{value}</dd>
    </div>
  );
}
