"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { api } from "@/lib/api";
import { AIPerformance, BaselineStrategy, CalibrationPoint, Uplift } from "@/lib/types";
import { compactInr, percent, titleCase } from "@/lib/format";
import { Card, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { BaselineBars, BaselineDatum, CalibrationChart } from "@/components/charts";
import { KpiTile } from "@/components/kpi";

const STRATEGY_LABEL: Record<string, string> = {
  NO_RECOVERY: "No recovery",
  ALWAYS_RETRY: "Always retry",
  FIXED_RETRY: "Fixed retry",
  RECOVERAI: "RecoverAI",
};

export default function AnalyticsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-[500px] w-full" />}>
      <AnalyticsContent />
    </Suspense>
  );
}

function AnalyticsContent() {
  const params = useSearchParams();
  const runId = params.get("run") ?? undefined;

  const baselines = useQuery({
    queryKey: ["baselines", runId],
    queryFn: () =>
      api.get<{ run_id: string | null; strategies: BaselineStrategy[]; uplift: Uplift }>(
        "/api/analytics/baselines",
        { run_id: runId },
      ),
  });

  const calibration = useQuery({
    queryKey: ["calibration", runId],
    queryFn: () =>
      api.get<{
        curve: CalibrationPoint[];
        expected_calibration_error: number | null;
        brier_score: number | null;
        average_predicted_probability: number | null;
        actual_recovery_rate: number | null;
      }>("/api/analytics/calibration", { run_id: runId }),
  });

  const metrics = useQuery({
    queryKey: ["recovery-metrics", runId],
    queryFn: () =>
      api.get<{ business: Record<string, any>; ai: AIPerformance; policy: Record<string, number> }>(
        "/api/recovery/metrics",
        { run_id: runId },
      ),
  });

  if (baselines.isError) return <ErrorState message={(baselines.error as Error).message} />;

  const strategies = baselines.data?.strategies ?? [];
  const uplift = baselines.data?.uplift ?? {};
  const ai = metrics.data?.ai;

  const chartData: BaselineDatum[] = strategies.map((s) => ({
    strategy: s.strategy,
    label: STRATEGY_LABEL[s.strategy] ?? s.strategy,
    recovered: s.recovered_revenue.minor,
    display: s.recovered_revenue.display,
    rate: s.recovery_rate,
    attempts: s.attempts,
    efficiency: s.recovery_efficiency.display,
  }));

  const curve = (calibration.data?.curve ?? [])
    .filter((p) => p.count > 0 && p.actual !== null)
    .map((p) => ({ predicted: p.predicted, actual: p.actual as number, count: p.count, bucket: p.bucket }));

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-ink">Analytics</h1>
        <p className="mt-0.5 text-xs text-ink-2">
          How RecoverAI compares to naive strategies, and how honest its predictions are.
        </p>
      </div>

      {baselines.isLoading ? (
        <Skeleton className="h-[300px] w-full" />
      ) : !strategies.length ? (
        <Card>
          <EmptyState
            title="No baseline comparison yet"
            hint="Baselines are computed at the end of a simulation run. Run one to compare RecoverAI against no-recovery, always-retry and fixed-retry strategies."
          />
        </Card>
      ) : (
        <>
          <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <KpiTile
              label="RecoverAI recovered"
              value={compactInr(uplift.recoverai_recovered_minor ?? 0)}
              tone="good"
              sub="Settled in the ledger"
            />
            <KpiTile
              label={`Best baseline (${STRATEGY_LABEL[uplift.best_baseline ?? ""] ?? "—"})`}
              value={compactInr(uplift.best_baseline_recovered_minor ?? 0)}
              sub="Same opportunities, same hidden truth"
            />
            <KpiTile
              label="Additional revenue recovered"
              value={compactInr(uplift.uplift_minor ?? 0)}
              tone="accent"
              sub={uplift.uplift_percent != null ? `+${uplift.uplift_percent}% over best baseline` : undefined}
            />
            <KpiTile
              label="Revenue per attempt"
              value={compactInr(Math.round(uplift.efficiency_minor ?? 0))}
              tone="accent"
              sub={
                uplift.efficiency_uplift_percent != null
                  ? `+${uplift.efficiency_uplift_percent}% vs baseline`
                  : undefined
              }
              hint="RecoverAI often spends more touches — each one is worth more."
            />
          </section>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card
              title="Baseline comparison"
              subtitle="Replayed over the same opportunities and the same hidden ground truth"
            >
              <BaselineBars data={chartData} />
              <p className="mt-2 text-2xs leading-snug text-ink-muted">
                Each strategy faces identical dice rolls per opportunity and attempt, so the
                difference reflects decision quality rather than luck.
              </p>
            </Card>

            <Card title="Strategy detail" subtitle="Cost as well as outcome">
              <div className="-mx-4 overflow-x-auto">
                <table className="w-full min-w-[520px] text-sm">
                  <thead>
                    <tr className="border-b border-line text-left">
                      {["Strategy", "Recovered", "Rate", "Attempts", "Wasted", "Messages", "₹/attempt"].map((h) => (
                        <th key={h} className="px-3 py-2 text-2xs font-semibold uppercase tracking-wide text-ink-muted">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {strategies.map((s) => {
                      const isUs = s.strategy === "RECOVERAI";
                      return (
                        <tr
                          key={s.strategy}
                          className={`border-b border-line last:border-0 ${isUs ? "bg-[var(--series-1)]/5" : ""}`}
                        >
                          <td className={`px-3 py-2 text-xs ${isUs ? "font-semibold text-ink" : "text-ink-2"}`}>
                            {STRATEGY_LABEL[s.strategy] ?? s.strategy}
                          </td>
                          <td className={`tabular px-3 py-2 text-xs ${isUs ? "font-semibold text-good" : "text-ink-2"}`}>
                            {s.recovered_revenue.display}
                          </td>
                          <td className="tabular px-3 py-2 text-xs text-ink-2">{percent(s.recovery_rate)}</td>
                          <td className="tabular px-3 py-2 text-xs text-ink-2">{s.attempts.toLocaleString("en-IN")}</td>
                          <td className="tabular px-3 py-2 text-xs text-ink-2">
                            {s.unnecessary_attempts.toLocaleString("en-IN")}
                          </td>
                          <td className="tabular px-3 py-2 text-xs text-ink-2">
                            {s.customer_notifications.toLocaleString("en-IN")}
                          </td>
                          <td className="tabular px-3 py-2 text-xs text-ink-2">
                            {s.recovery_efficiency.display}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-2xs leading-snug text-ink-muted">
                “Wasted” counts attempts spent on opportunities that were never recoverable —
                the cost a context-blind strategy cannot see.
              </p>
            </Card>
          </div>
        </>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card
          title="Prediction calibration"
          subtitle="When the AI says 40%, does 40% actually happen?"
        >
          {calibration.isLoading ? (
            <Skeleton className="h-[240px] w-full" />
          ) : curve.length === 0 ? (
            <EmptyState title="No resolved predictions yet" />
          ) : (
            <>
              <CalibrationChart data={curve} />
              <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-line pt-3 text-xs">
                <div>
                  <dt className="text-ink-muted">Expected calibration error</dt>
                  <dd className="tabular text-sm font-semibold text-ink">
                    {calibration.data?.expected_calibration_error != null
                      ? percent(calibration.data.expected_calibration_error)
                      : "—"}
                  </dd>
                  <p className="text-2xs text-ink-muted">Lower is better; 0% is perfect.</p>
                </div>
                <div>
                  <dt className="text-ink-muted">Average predicted vs actual</dt>
                  <dd className="tabular text-sm font-semibold text-ink">
                    {percent(calibration.data?.average_predicted_probability ?? 0)} vs{" "}
                    {percent(calibration.data?.actual_recovery_rate ?? 0)}
                  </dd>
                  <p className="text-2xs text-ink-muted">Across all resolved attempts.</p>
                </div>
              </dl>
            </>
          )}
        </Card>

        <Card title="AI decision quality" subtitle="Measured against outcomes it never saw">
          {metrics.isLoading || !ai ? (
            <Skeleton className="h-[240px] w-full" />
          ) : (
            <dl className="space-y-2 text-xs">
              <Metric label="Decisions made" value={ai.decisions.toLocaleString("en-IN")} />
              <Metric
                label="Chose the hindsight-optimal action"
                value={ai.action_accuracy != null ? percent(ai.action_accuracy) : "—"}
                note="The best action is partly unobservable, so this ceiling is below 100%."
              />
              <Metric
                label="Actual recovery rate"
                value={ai.actual_recovery_rate != null ? percent(ai.actual_recovery_rate) : "—"}
              />
              <Metric
                label="Brier score"
                value={ai.brier_score != null ? ai.brier_score.toFixed(3) : "—"}
                note="Mean squared forecast error. Lower is better."
              />
              <Metric
                label="Actions on unrecoverable revenue"
                value={ai.unnecessary_action_rate != null ? percent(ai.unnecessary_action_rate) : "—"}
                note="Effort spent where nothing could have worked."
              />
              <Metric label="Deliberate stops" value={String(ai.stop_decisions ?? 0)} />
              <div className="border-t border-line pt-2">
                <dt className="text-ink-muted">Decision sources</dt>
                <dd className="mt-1 flex flex-wrap gap-2">
                  {Object.entries(ai.decision_sources).map(([source, count]) => (
                    <span
                      key={source}
                      className="rounded border border-line-strong px-1.5 py-0.5 text-2xs text-ink-2"
                    >
                      {titleCase(source)} · {count.toLocaleString("en-IN")}
                    </span>
                  ))}
                </dd>
                <p className="mt-1 text-2xs text-ink-muted">
                  Reported honestly: cached and fallback decisions are never counted as fresh
                  model calls.
                </p>
              </div>
              {metrics.data?.policy && (
                <div className="border-t border-line pt-2">
                  <Metric
                    label="Policy block rate"
                    value={percent(metrics.data.policy.block_rate ?? 0)}
                    note={`${(metrics.data.policy.blocked_actions ?? 0).toLocaleString("en-IN")} recommendations refused by guardrails`}
                  />
                </div>
              )}
            </dl>
          )}
        </Card>
      </div>
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
