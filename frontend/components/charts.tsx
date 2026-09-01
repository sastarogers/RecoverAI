"use client";

/**
 * Charts.
 *
 * Colour is assigned by the job it does — categorical for scenario identity, a single
 * ordinal ramp for funnel stages, one hue for a single-series comparison. Every palette
 * here was run through the validator for both surfaces before being written down.
 * Text always wears ink tokens; a colour swatch beside a label carries identity.
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ReactNode } from "react";
import { compactInr, percent } from "@/lib/format";

const AXIS = { fontSize: 11, fill: "var(--ink-muted)" };

function TooltipShell({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-md border border-line-strong bg-surface px-2.5 py-2 text-xs shadow-lg">
      {children}
    </div>
  );
}

/** Legend is always present for >= 2 series, so identity is never colour-alone. */
export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5 text-xs text-ink-2">
          <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Baseline comparison (§36): one measure, four strategies.            */
/* One series -> one hue, with RecoverAI emphasised. No legend needed.  */
/* ------------------------------------------------------------------ */

export type BaselineDatum = {
  strategy: string;
  label: string;
  recovered: number;
  display: string;
  rate: number;
  attempts: number;
  efficiency: string;
};

export function BaselineBars({ data }: { data: BaselineDatum[] }) {
  return (
    <div className="h-[260px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }} barCategoryGap="28%">
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--axis)" }} />
          <YAxis
            tick={AXIS}
            tickLine={false}
            axisLine={false}
            width={62}
            tickFormatter={(v) => compactInr(Number(v))}
          />
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload as BaselineDatum;
              return (
                <TooltipShell>
                  <p className="font-semibold text-ink">{d.label}</p>
                  <p className="tabular mt-1 text-ink-2">Recovered {d.display}</p>
                  <p className="tabular text-ink-2">Rate {percent(d.rate)}</p>
                  <p className="tabular text-ink-2">Attempts {d.attempts.toLocaleString("en-IN")}</p>
                  <p className="tabular text-ink-2">Per attempt {d.efficiency}</p>
                </TooltipShell>
              );
            }}
          />
          <Bar dataKey="recovered" radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {data.map((d) => (
              <Cell
                key={d.strategy}
                fill={d.strategy === "RECOVERAI" ? "var(--series-1)" : "var(--ramp-1)"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Scenario comparison: at-risk vs recovered, per scenario.            */
/* ------------------------------------------------------------------ */

export type ScenarioDatum = {
  scenario: string;
  label: string;
  atRisk: number;
  recovered: number;
  /** atRisk - recovered. Precomputed so the chart never does money arithmetic. */
  unrecovered: number;
  atRiskDisplay: string;
  recoveredDisplay: string;
  rate: number;
};

export function ScenarioBars({ data }: { data: ScenarioDatum[] }) {
  return (
    <div>
      <div className="h-[230px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }} barGap={2}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis dataKey="label" tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--axis)" }} />
            <YAxis
              tick={AXIS}
              tickLine={false}
              axisLine={false}
              width={62}
              tickFormatter={(v) => compactInr(Number(v))}
            />
            <Tooltip
              cursor={{ fill: "var(--surface-2)" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const d = payload[0].payload as ScenarioDatum;
                return (
                  <TooltipShell>
                    <p className="font-semibold text-ink">{d.label}</p>
                    <p className="tabular mt-1 text-ink-2">At risk {d.atRiskDisplay}</p>
                    <p className="tabular text-ink-2">Recovered {d.recoveredDisplay}</p>
                    <p className="tabular text-ink-2">Rate {percent(d.rate)}</p>
                  </TooltipShell>
                );
              }}
            />
            {/*
              Part-to-whole, not two series. The full bar is the revenue at risk and the
              filled portion is what was actually recovered, so the recovery rate is the
              shape of the bar. Two steps of one ramp would have encoded two identities
              with a magnitude scale, which is the thing a sequential ramp must not do.
              The 2px stroke in the surface colour is the required gap between segments.
            */}
            <Bar
              dataKey="recovered"
              stackId="risk"
              fill="var(--series-1)"
              stroke="var(--surface)"
              strokeWidth={2}
              isAnimationActive={false}
            />
            <Bar
              dataKey="unrecovered"
              stackId="risk"
              radius={[4, 4, 0, 0]}
              fill="var(--bar-remainder)"
              stroke="var(--surface)"
              strokeWidth={2}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2">
        <Legend
          items={[
            { label: "Recovered", color: "var(--series-1)" },
            { label: "Not recovered", color: "var(--bar-remainder)" },
          ]}
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Calibration (§37): predicted vs realised, against the ideal line.   */
/* ------------------------------------------------------------------ */

export type CalibrationDatum = { predicted: number; actual: number; count: number; bucket: string };

export function CalibrationChart({ data }: { data: CalibrationDatum[] }) {
  return (
    <div>
      <div className="h-[240px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
            <CartesianGrid stroke="var(--grid)" />
            <XAxis
              dataKey="predicted"
              type="number"
              domain={[0, 1]}
              tick={AXIS}
              tickLine={false}
              axisLine={{ stroke: "var(--axis)" }}
              tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`}
              label={{ value: "Predicted", position: "insideBottom", offset: -2, fill: "var(--ink-muted)", fontSize: 11 }}
            />
            <YAxis
              type="number"
              domain={[0, 1]}
              tick={AXIS}
              tickLine={false}
              axisLine={false}
              width={46}
              tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`}
            />
            {/* Perfect calibration is the y = x diagonal, drawn as a neutral reference. */}
            <ReferenceLine
              segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
              stroke="var(--line-strong)"
              strokeDasharray="4 4"
            />
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const d = payload[0].payload as CalibrationDatum;
                return (
                  <TooltipShell>
                    <p className="font-semibold text-ink">Predicted {d.bucket}</p>
                    <p className="tabular mt-1 text-ink-2">Actual {percent(d.actual)}</p>
                    <p className="tabular text-ink-2">{d.count} decisions</p>
                  </TooltipShell>
                );
              }}
            />
            <Line
              type="monotone"
              dataKey="actual"
              stroke="var(--series-1)"
              strokeWidth={2}
              dot={{ r: 4, fill: "var(--series-1)", stroke: "var(--surface)", strokeWidth: 2 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2">
        <Legend
          items={[
            { label: "Observed recovery rate", color: "var(--series-1)" },
            { label: "Perfect calibration", color: "var(--line-strong)" },
          ]}
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Pipeline funnel (§30): ordered stages, one hue, light -> dark.      */
/* ------------------------------------------------------------------ */

export type FunnelStage = { label: string; value: number; note?: string };

export function PipelineFunnel({ stages }: { stages: FunnelStage[] }) {
  const max = Math.max(...stages.map((s) => s.value), 1);
  return (
    <ol className="space-y-2">
      {stages.map((stage, i) => {
        const width = Math.max(2, (stage.value / max) * 100);
        return (
          <li key={stage.label}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-xs text-ink-2">{stage.label}</span>
              <span className="tabular text-xs font-semibold text-ink">
                {stage.value.toLocaleString("en-IN")}
              </span>
            </div>
            <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-surface-2">
              <div
                className="h-full rounded-r-[4px]"
                style={{ width: `${width}%`, background: `var(--ramp-${Math.min(i + 1, 5)})` }}
              />
            </div>
            {stage.note && <p className="mt-0.5 text-2xs text-ink-muted">{stage.note}</p>}
          </li>
        );
      })}
    </ol>
  );
}
