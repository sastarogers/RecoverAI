"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { getPaginated } from "@/lib/api";
import { OpportunityRow } from "@/lib/types";
import { SCENARIO_SHORT, dateTime, percent, titleCase } from "@/lib/format";
import { Button, Card, EmptyState, ErrorState, ScenarioChip, Skeleton, StatusBadge, inputClass } from "@/components/ui";

const SCENARIOS = ["", "FAILED_PAYMENT", "CHECKOUT_ABANDONMENT", "FAILED_SUBSCRIPTION"];
const STATUSES = ["", "RECOVERED", "EXHAUSTED", "BLOCKED", "DETECTED", "EXECUTING", "FAILED", "EXPIRED"];

export default function OpportunitiesPage() {
  const [scenario, setScenario] = useState("");
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);

  const query = useQuery({
    queryKey: ["opportunities", scenario, status, source, q, page],
    queryFn: () =>
      getPaginated<OpportunityRow>("/api/opportunities", {
        scenario: scenario || undefined,
        status: status || undefined,
        source: source || undefined,
        q: q || undefined,
        page,
        page_size: 25,
      }),
  });

  const rows = query.data?.data ?? [];
  const meta = query.data?.meta;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-ink">Recovery Opportunities</h1>
        <p className="mt-0.5 text-xs text-ink-2">
          Every unit of revenue at risk, and exactly what happened to it.
        </p>
      </div>

      {/* Filters live in one row above the table. */}
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-[150px]">
          <label className="text-2xs font-medium text-ink-2">Scenario</label>
          <select
            className={inputClass}
            value={scenario}
            onChange={(e) => {
              setScenario(e.target.value);
              setPage(1);
            }}
          >
            {SCENARIOS.map((s) => (
              <option key={s} value={s}>
                {s ? SCENARIO_SHORT[s] : "All scenarios"}
              </option>
            ))}
          </select>
        </div>
        <div className="min-w-[130px]">
          <label className="text-2xs font-medium text-ink-2">Status</label>
          <select
            className={inputClass}
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s ? titleCase(s) : "All statuses"}
              </option>
            ))}
          </select>
        </div>
        <div className="min-w-[130px]">
          <label className="text-2xs font-medium text-ink-2">Source</label>
          <select
            className={inputClass}
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setPage(1);
            }}
          >
            <option value="">All sources</option>
            <option value="SIMULATOR">Simulator</option>
            <option value="RAZORPAY">Razorpay</option>
          </select>
        </div>
        <div className="min-w-[190px] flex-1">
          <label className="text-2xs font-medium text-ink-2">Search</label>
          <input
            className={inputClass}
            placeholder="OPP0001 or customer"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        </div>
      </div>

      {query.isError ? (
        <ErrorState message={(query.error as Error).message} />
      ) : (
        <Card
          title={meta ? `${meta.total.toLocaleString("en-IN")} opportunities` : "Opportunities"}
          subtitle={meta ? `Page ${meta.page} of ${meta.pages}` : undefined}
          className="overflow-hidden"
        >
          {query.isLoading ? (
            <Skeleton className="h-[400px] w-full" />
          ) : rows.length === 0 ? (
            <EmptyState title="No opportunities match these filters" />
          ) : (
            <>
              <div className="-mx-4 overflow-x-auto">
                <table className="w-full min-w-[1000px] border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-line text-left">
                      {[
                        "Opportunity",
                        "Scenario",
                        "Customer",
                        "At risk",
                        "Reason",
                        "AI recommendation",
                        "Policy",
                        "Status",
                        "Recovered",
                        "Detected",
                      ].map((h) => (
                        <th
                          key={h}
                          className="whitespace-nowrap px-3 py-2 text-2xs font-semibold uppercase tracking-wide text-ink-muted"
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <Row key={row.id} row={row} />
                    ))}
                  </tbody>
                </table>
              </div>

              {meta && meta.pages > 1 && (
                <div className="mt-3 flex items-center justify-between">
                  <Button variant="secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                    ← Previous
                  </Button>
                  <span className="tabular text-xs text-ink-muted">
                    {meta.page} / {meta.pages}
                  </span>
                  <Button
                    variant="secondary"
                    disabled={page >= meta.pages}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    Next →
                  </Button>
                </div>
              )}
            </>
          )}
        </Card>
      )}
    </div>
  );
}

function Row({ row }: { row: OpportunityRow }) {
  return (
    <tr className="border-b border-line last:border-0 hover:bg-surface-2">
      <td className="px-3 py-2">
        <Link
          href={`/opportunities/${row.opportunity_ref}`}
          className="font-mono text-xs font-medium text-[var(--series-1)] hover:underline"
        >
          {row.opportunity_ref}
        </Link>
      </td>
      <td className="px-3 py-2">
        <ScenarioChip scenario={row.scenario} label={SCENARIO_SHORT[row.scenario] ?? row.scenario} />
      </td>
      <td className="px-3 py-2">
        <div className="text-xs text-ink">{row.customer_name ?? "—"}</div>
        <div className="font-mono text-2xs text-ink-muted">{row.customer_ref}</div>
      </td>
      <td className="tabular whitespace-nowrap px-3 py-2 text-xs font-medium text-ink">
        {row.amount_at_risk.display}
      </td>
      <td className="px-3 py-2">
        <span className="font-mono text-2xs text-ink-2">
          {row.failure_code ?? row.reason_code ?? "—"}
        </span>
      </td>
      <td className="px-3 py-2 text-xs text-ink-2">
        <AiCell row={row} />
      </td>
      <td className="px-3 py-2">
        <span className="text-2xs text-ink-2">{row.attempt_count} attempts</span>
      </td>
      <td className="px-3 py-2">
        <StatusBadge status={row.status} />
      </td>
      <td className="tabular whitespace-nowrap px-3 py-2 text-xs font-semibold">
        <span className={row.recovered_amount.minor > 0 ? "text-good" : "text-ink-muted"}>
          {row.recovered_amount.display}
        </span>
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-2xs text-ink-muted">
        {dateTime(row.detected_at)}
      </td>
    </tr>
  );
}

/** The list endpoint is deliberately lean; the recommendation lives on the detail page. */
function AiCell({ row }: { row: OpportunityRow }) {
  if (row.status === "RECOVERED") {
    return <span className="text-good">Recovered in {row.attempt_count} attempt(s)</span>;
  }
  if (row.status === "BLOCKED") return <span className="text-serious">Blocked by policy</span>;
  if (row.status === "EXHAUSTED") return <span className="text-ink-muted">Attempts exhausted</span>;
  return <span className="text-ink-muted">—</span>;
}
