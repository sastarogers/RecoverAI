"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { NotificationMessage } from "@/lib/types";
import { relativeTime } from "@/lib/format";
import { Button, Card, StatusBadge, inputClass } from "./ui";

type MessagingStatus = {
  enabled: boolean;
  live: boolean;
  provider: string | null;
  channels: { whatsapp: boolean; sms: boolean };
  preferred_channel: string;
  messages_by_status: Record<string, number>;
  delivered_externally: number;
};

type TestResult = {
  channel: string;
  to: string;
  body: string;
  status: string;
  provider: string;
  delivered_externally: boolean;
  error: string | null;
  explanation: string;
};

/**
 * Demonstrates the WhatsApp/SMS path. It renders the exact template a live recovery
 * sends and, when Twilio is configured, actually delivers it — so the claim can be
 * checked on a real handset rather than taken on trust.
 */
export function MessagingPanel() {
  const [to, setTo] = useState("+91");
  const [channel, setChannel] = useState<"WHATSAPP" | "SMS">("WHATSAPP");

  const status = useQuery({
    queryKey: ["messaging-status"],
    queryFn: () => api.get<MessagingStatus>("/api/notifications/status"),
    refetchInterval: 10000,
  });

  const recent = useQuery({
    queryKey: ["messages-recent"],
    queryFn: async () => {
      const res = await fetch("/api/notifications?page_size=6", { cache: "no-store" });
      return (await res.json()).data as NotificationMessage[];
    },
    refetchInterval: 10000,
  });

  const send = useMutation({
    mutationFn: () =>
      api.post<TestResult>("/api/notifications/test", { to, channel, customer_name: "Rahul" }),
    onSuccess: () => recent.refetch(),
  });

  const s = status.data;
  const composed = Object.values(s?.messages_by_status ?? {}).reduce((a, b) => a + b, 0);

  return (
    <Card
      title="Customer messaging"
      subtitle="Sent when only the customer can fix the payment method — expired card, invalid VPA, revoked mandate"
      action={
        <span
          className={`rounded-full border px-2 py-0.5 text-2xs font-medium ${
            s?.live
              ? "border-good/40 bg-good/10 text-good"
              : "border-line-strong bg-surface-2 text-ink-muted"
          }`}
        >
          {s?.live ? `live via ${s.provider}` : "simulated"}
        </span>
      }
    >
      <div className="grid grid-cols-3 gap-3 border-b border-line pb-3">
        <Stat label="Messages composed" value={composed} />
        <Stat label="Delivered to a handset" value={s?.delivered_externally ?? 0} />
        <Stat
          label="Channels"
          value={
            s ? [s.channels.whatsapp && "WhatsApp", s.channels.sms && "SMS"].filter(Boolean).join(" + ") || "none" : "—"
          }
        />
      </div>

      <div className="pt-3">
        <p className="text-xs font-medium text-ink">Send the real message to your own phone</p>
        <p className="mt-0.5 text-2xs text-ink-muted">
          Renders the exact template a live expired-card recovery sends.
          {!s?.live && " With no provider configured it is composed and shown, not delivered."}
        </p>
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <div className="min-w-[180px] flex-1">
            <label className="text-2xs font-medium text-ink-2">Phone number (E.164)</label>
            <input className={inputClass} value={to} onChange={(e) => setTo(e.target.value)} placeholder="+919812345678" />
          </div>
          <div className="min-w-[120px]">
            <label className="text-2xs font-medium text-ink-2">Channel</label>
            <select className={inputClass} value={channel} onChange={(e) => setChannel(e.target.value as "WHATSAPP" | "SMS")}>
              <option value="WHATSAPP">WhatsApp</option>
              <option value="SMS">SMS</option>
            </select>
          </div>
          <Button onClick={() => send.mutate()} disabled={send.isPending || to.length < 8}>
            {send.isPending ? "Sending…" : "Send"}
          </Button>
        </div>

        {send.isError && <p className="mt-2 text-xs text-critical">{(send.error as Error).message}</p>}

        {send.isSuccess && send.data && (
          <div
            className={`mt-3 rounded-md border p-3 ${
              send.data.delivered_externally
                ? "border-good/40 bg-good/5"
                : "border-line bg-surface-2/40"
            }`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded border border-line-strong px-1.5 py-0.5 text-2xs font-medium text-ink-2">
                {send.data.channel}
              </span>
              <StatusBadge status={send.data.status} />
              <span className="font-mono text-2xs text-ink-muted">{send.data.to}</span>
            </div>
            <pre className="mt-2 whitespace-pre-wrap font-sans text-xs leading-relaxed text-ink-2">
              {send.data.body}
            </pre>
            <p className="mt-2 text-2xs text-ink-muted">{send.data.explanation}</p>
            {send.data.error && <p className="mt-1 text-2xs text-critical">{send.data.error}</p>}
          </div>
        )}
      </div>

      {(recent.data?.length ?? 0) > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <p className="text-xs font-medium text-ink">Recent messages</p>
          <ul className="mt-2 space-y-1.5">
            {recent.data!.map((m) => (
              <li key={m.id} className="flex flex-wrap items-center gap-2 text-2xs">
                <span className="text-ink-muted">{relativeTime(m.created_at)}</span>
                <span className="rounded border border-line-strong px-1.5 py-0.5 text-ink-2">{m.channel}</span>
                <StatusBadge status={m.status} />
                <span className="font-mono text-ink-muted">{m.recipient ?? "no number"}</span>
                {m.opportunity_ref && <span className="font-mono text-ink-muted">{m.opportunity_ref}</span>}
                {m.reason && <span className="text-ink-muted">{m.reason}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="mt-3 border-t border-line pt-2 text-2xs leading-snug text-ink-muted">
        A delivered message is a recovery action, never recovered revenue — it only counts once
        the customer actually pays.
      </p>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <p className="text-2xs uppercase tracking-wide text-ink-muted">{label}</p>
      <p className="tabular mt-0.5 text-sm font-semibold text-ink">{value}</p>
    </div>
  );
}
