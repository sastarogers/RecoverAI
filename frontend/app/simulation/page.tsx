"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { SimulationRun } from "@/lib/types";
import { compactInr, dateTime, percent } from "@/lib/format";
import { Button, Card, ErrorState, Field, Skeleton, StatusBadge, inputClass } from "@/components/ui";

type ConfigState = {
  seed: number;
  label: string;
  num_customers: number;
  num_payments: number;
  num_checkouts: number;
  num_subscriptions: number;
  payment_success_rate: number;
  checkout_completion_rate: number;
  subscription_renewal_success_rate: number;
  unrecoverable_rate: number;
  max_attempts: number;
  max_notifications: number;
  ai_mode: "auto" | "llm" | "heuristic";
  scenarios: string[];
};

const ALL_SCENARIOS = [
  { key: "FAILED_PAYMENT", label: "Failed payments" },
  { key: "CHECKOUT_ABANDONMENT", label: "Checkout abandonment" },
  { key: "FAILED_SUBSCRIPTION", label: "Failed subscriptions" },
];

const PRESETS: Record<string, Partial<ConfigState>> = {
  demo: { num_customers: 300, num_payments: 600, num_checkouts: 300, num_subscriptions: 150, label: "Demo run" },
  competition: {
    num_customers: 1000, num_payments: 2000, num_checkouts: 1000, num_subscriptions: 500,
    label: "Competition demo",
  },
  stress: {
    num_customers: 2000, num_payments: 5000, num_checkouts: 2500, num_subscriptions: 1000,
    payment_success_rate: 0.55, label: "High failure stress",
  },
};

export default function SimulationPage() {
  const queryClient = useQueryClient();
  const [config, setConfig] = useState<ConfigState>({
    seed: 2026,
    label: "",
    num_customers: 300,
    num_payments: 600,
    num_checkouts: 300,
    num_subscriptions: 150,
    payment_success_rate: 0.7,
    checkout_completion_rate: 0.7,
    subscription_renewal_success_rate: 0.85,
    unrecoverable_rate: 0.18,
    max_attempts: 3,
    max_notifications: 2,
    ai_mode: "heuristic",
    scenarios: ALL_SCENARIOS.map((s) => s.key),
  });

  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [progress, setProgress] = useState<SimulationRun["progress"] | null>(null);
  const [streamStatus, setStreamStatus] = useState<string>("");
  const sourceRef = useRef<EventSource | null>(null);

  const runs = useQuery({
    queryKey: ["simulation-runs"],
    queryFn: () => api.get<SimulationRun[]>("/api/simulation", { limit: 10 }),
  });

  const start = useMutation({
    mutationFn: () =>
      api.post<{ run_id: string }>("/api/simulation/run", {
        ...config,
        label: config.label || null,
      }),
    onSuccess: (data) => {
      setActiveRunId(data.run_id);
      setProgress(null);
      setStreamStatus("RUNNING");
      subscribe(data.run_id);
    },
  });

  /** Stream progress from the server rather than polling (§35). */
  function subscribe(runId: string) {
    sourceRef.current?.close();
    const source = new EventSource(`/api/simulation/${runId}/stream`);
    sourceRef.current = source;

    source.addEventListener("progress", (event) => {
      const payload = JSON.parse((event as MessageEvent).data);
      setProgress(payload.progress);
      setStreamStatus(payload.status);
    });
    source.addEventListener("complete", (event) => {
      const payload = JSON.parse((event as MessageEvent).data);
      setProgress(payload.progress);
      setStreamStatus(payload.status);
      source.close();
      queryClient.invalidateQueries({ queryKey: ["simulation-runs"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
    });
    source.onerror = () => {
      source.close();
      setStreamStatus("DISCONNECTED");
    };
  }

  useEffect(() => () => sourceRef.current?.close(), []);

  const running = streamStatus === "RUNNING";
  const counters = progress?.counters ?? {};

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-ink">Simulation Control Centre</h1>
        <p className="mt-0.5 text-xs text-ink-2">
          Generate a synthetic merchant, then run it through the real recovery pipeline.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title="Configuration" subtitle="Every knob is stored with the run" className="lg:col-span-2">
          <div className="flex flex-wrap gap-2 pb-3">
            {Object.entries(PRESETS).map(([key, preset]) => (
              <Button
                key={key}
                variant="secondary"
                onClick={() => setConfig((c) => ({ ...c, ...preset } as ConfigState))}
              >
                {key === "competition" ? "Competition demo" : key === "stress" ? "Stress test" : "Quick demo"}
              </Button>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Field label="Customers">
              <input
                type="number"
                className={inputClass}
                value={config.num_customers}
                onChange={(e) => setConfig({ ...config, num_customers: Number(e.target.value) })}
              />
            </Field>
            <Field label="Payments">
              <input
                type="number"
                className={inputClass}
                value={config.num_payments}
                onChange={(e) => setConfig({ ...config, num_payments: Number(e.target.value) })}
              />
            </Field>
            <Field label="Checkouts">
              <input
                type="number"
                className={inputClass}
                value={config.num_checkouts}
                onChange={(e) => setConfig({ ...config, num_checkouts: Number(e.target.value) })}
              />
            </Field>
            <Field label="Subscriptions">
              <input
                type="number"
                className={inputClass}
                value={config.num_subscriptions}
                onChange={(e) => setConfig({ ...config, num_subscriptions: Number(e.target.value) })}
              />
            </Field>
          </div>

          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <RateField
              label="Payment success"
              value={config.payment_success_rate}
              onChange={(v) => setConfig({ ...config, payment_success_rate: v })}
              hint={`${percent(1 - config.payment_success_rate, 0)} fail`}
            />
            <RateField
              label="Checkout completion"
              value={config.checkout_completion_rate}
              onChange={(v) => setConfig({ ...config, checkout_completion_rate: v })}
              hint={`${percent(1 - config.checkout_completion_rate, 0)} abandon`}
            />
            <RateField
              label="Renewal success"
              value={config.subscription_renewal_success_rate}
              onChange={(v) => setConfig({ ...config, subscription_renewal_success_rate: v })}
              hint={`${percent(1 - config.subscription_renewal_success_rate, 0)} fail`}
            />
            <RateField
              label="Unrecoverable share"
              value={config.unrecoverable_rate}
              onChange={(v) => setConfig({ ...config, unrecoverable_rate: v })}
              hint="Genuinely dead, whatever you try"
            />
          </div>

          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Field label="Seed" hint="Same seed reproduces the run">
              <input
                type="number"
                className={inputClass}
                value={config.seed}
                onChange={(e) => setConfig({ ...config, seed: Number(e.target.value) })}
              />
            </Field>
            <Field label="Max attempts">
              <input
                type="number"
                min={1}
                max={10}
                className={inputClass}
                value={config.max_attempts}
                onChange={(e) => setConfig({ ...config, max_attempts: Number(e.target.value) })}
              />
            </Field>
            <Field label="Max customer messages">
              <input
                type="number"
                min={0}
                max={10}
                className={inputClass}
                value={config.max_notifications}
                onChange={(e) => setConfig({ ...config, max_notifications: Number(e.target.value) })}
              />
            </Field>
            <Field label="Decision engine" hint={config.ai_mode === "heuristic" ? "Fully reproducible" : "LLM-driven"}>
              <select
                className={inputClass}
                value={config.ai_mode}
                onChange={(e) => setConfig({ ...config, ai_mode: e.target.value as ConfigState["ai_mode"] })}
              >
                <option value="heuristic">Deterministic</option>
                <option value="auto">Auto (LLM + fallback)</option>
                <option value="llm">LLM only</option>
              </select>
            </Field>
          </div>

          <fieldset className="mt-3">
            <legend className="text-xs font-medium text-ink-2">Scenarios</legend>
            <div className="mt-1.5 flex flex-wrap gap-3">
              {ALL_SCENARIOS.map((s) => (
                <label key={s.key} className="flex items-center gap-1.5 text-xs text-ink-2">
                  <input
                    type="checkbox"
                    checked={config.scenarios.includes(s.key)}
                    onChange={(e) =>
                      setConfig({
                        ...config,
                        scenarios: e.target.checked
                          ? [...config.scenarios, s.key]
                          : config.scenarios.filter((x) => x !== s.key),
                      })
                    }
                  />
                  {s.label}
                </label>
              ))}
            </div>
          </fieldset>

          <div className="mt-4 flex items-center gap-3 border-t border-line pt-3">
            <Button onClick={() => start.mutate()} disabled={start.isPending || running || !config.scenarios.length}>
              {running ? "Simulation running…" : "Run simulation"}
            </Button>
            {start.isError && (
              <span className="text-xs text-critical">{(start.error as Error).message}</span>
            )}
          </div>
        </Card>

        <Card title="Progress" subtitle={activeRunId ? `Run ${activeRunId.slice(0, 8)}` : "Idle"}>
          {!activeRunId ? (
            <p className="py-8 text-center text-xs text-ink-muted">
              Start a run to watch it progress stage by stage.
            </p>
          ) : (
            <div className="space-y-3">
              <div>
                <div className="flex items-baseline justify-between">
                  <span className="text-xs font-medium text-ink">{progress?.stage ?? "Queued"}</span>
                  <span className="tabular text-xs text-ink-2">{progress?.percent ?? 0}%</span>
                </div>
                <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-surface-2">
                  <div
                    className="h-full rounded-r-[4px] transition-all duration-300"
                    style={{ width: `${progress?.percent ?? 0}%`, background: "var(--series-1)" }}
                  />
                </div>
                <p className="mt-1 text-2xs text-ink-muted">{progress?.message ?? "Waiting to start…"}</p>
              </div>

              <dl className="space-y-1 border-t border-line pt-2 text-xs">
                <Counter label="Customers" value={counters.customers} />
                <Counter label="Payments failed" value={counters.payments_failed} />
                <Counter label="Checkouts abandoned" value={counters.checkouts_abandoned} />
                <Counter label="Renewals failed" value={counters.renewals_failed} />
                <Counter label="Opportunities" value={counters.opportunities} />
                <Counter label="Recovery attempts" value={counters.recovery_attempts} />
                <Counter label="Blocked actions" value={counters.blocked_actions} />
                {counters.revenue_at_risk_minor !== undefined && (
                  <div className="flex justify-between border-t border-line pt-1">
                    <dt className="text-ink-muted">Revenue at risk</dt>
                    <dd className="tabular font-medium text-ink">
                      {compactInr(counters.revenue_at_risk_minor)}
                    </dd>
                  </div>
                )}
                {counters.recovered_revenue_minor !== undefined && (
                  <div className="flex justify-between">
                    <dt className="text-ink-muted">Recovered</dt>
                    <dd className="tabular font-semibold text-good">
                      {compactInr(counters.recovered_revenue_minor)}
                    </dd>
                  </div>
                )}
              </dl>

              {streamStatus === "COMPLETED" && (
                <div className="flex gap-2 border-t border-line pt-2">
                  <Link href={`/simulation/${activeRunId}`} className="flex-1">
                    <Button className="w-full">View results →</Button>
                  </Link>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

      <Card title="Previous runs" subtitle="Every run stores its seed and configuration">
        {runs.isError ? (
          <ErrorState message={(runs.error as Error).message} />
        ) : runs.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : !runs.data?.length ? (
          <p className="py-6 text-center text-xs text-ink-muted">No runs yet.</p>
        ) : (
          <div className="-mx-4 overflow-x-auto">
            <table className="w-full min-w-[820px] text-sm">
              <thead>
                <tr className="border-b border-line text-left">
                  {["Run", "Label", "Seed", "Engine", "Status", "Opportunities", "Recovered", "Started", ""].map((h) => (
                    <th key={h} className="px-3 py-2 text-2xs font-semibold uppercase tracking-wide text-ink-muted">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {runs.data.map((run) => {
                  const c = (run.progress?.counters ?? {}) as Record<string, number>;
                  return (
                    <tr key={run.id} className="border-b border-line last:border-0 hover:bg-surface-2">
                      <td className="px-3 py-2 font-mono text-xs text-ink">{run.run_ref}</td>
                      <td className="px-3 py-2 text-xs text-ink-2">{run.label ?? "—"}</td>
                      <td className="tabular px-3 py-2 font-mono text-xs text-ink-2">{run.seed}</td>
                      <td className="px-3 py-2 text-2xs text-ink-2">{run.ai_mode}</td>
                      <td className="px-3 py-2"><StatusBadge status={run.status} /></td>
                      <td className="tabular px-3 py-2 text-xs text-ink-2">{c.opportunities ?? "—"}</td>
                      <td className="tabular px-3 py-2 text-xs font-medium text-good">
                        {c.recovered_revenue_minor !== undefined ? compactInr(c.recovered_revenue_minor) : "—"}
                      </td>
                      <td className="px-3 py-2 text-2xs text-ink-muted">{dateTime(run.started_at)}</td>
                      <td className="px-3 py-2">
                        <Link href={`/simulation/${run.id}`} className="text-xs text-[var(--series-1)] hover:underline">
                          Report →
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function RateField({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  hint?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-2 w-full accent-[var(--series-1)]"
      />
      <span className="tabular text-xs text-ink">{percent(value, 0)}</span>
    </Field>
  );
}

function Counter({ label, value }: { label: string; value?: number }) {
  if (value === undefined) return null;
  return (
    <div className="flex justify-between">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="tabular text-ink-2">{value.toLocaleString("en-IN")}</dd>
    </div>
  );
}
