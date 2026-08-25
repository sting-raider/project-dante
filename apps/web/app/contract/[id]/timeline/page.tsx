"use client";

/**
 * /contract/[id]/timeline — full event trace, three editorial columns:
 * mono timestamp | event + category chip | payload summary with expandable
 * details (plan §28). Polls every 3s until the contract reaches a terminal
 * state so webhook truth lands in the trace without a refresh.
 *
 * Agent I.
 */

import { useParams } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import Folio from "@/components/editorial/Folio";
import SectionLabel from "@/components/editorial/SectionLabel";
import SyntheticBadge from "@/components/commerce/SyntheticBadge";
import Badge from "@/components/commerce/Badge";
import { apiGet } from "@/lib/api";
import type {
  ContractResponse,
  DanteEvent,
  TimelineResponse,
} from "@/lib/rights-ui";
import { isTerminal } from "@/lib/rights-ui";
import { formatTime, prettyJson, payloadSummary } from "@/lib/format";
import { cn } from "@/lib/cn";

const FILTERS = ["All", "Agent", "Money", "Merchant", "Fulfillment", "Policy", "Evidence"] as const;
type Filter = (typeof FILTERS)[number];

const CATEGORY_TONE: Record<string, string> = {
  Agent: "text-ink-soft border-rule",
  Money: "text-success border-success/40 bg-success/[0.07]",
  Merchant: "text-ink-soft border-rule",
  Fulfillment: "text-warning border-warning/40 bg-warning/[0.08]",
  Policy: "text-signal-deep border-signal/40 bg-signal/[0.07]",
  Evidence: "text-ink-soft border-rule",
};

function categoryClass(cat: string): string {
  return CATEGORY_TONE[cat] ?? "text-ink-soft border-rule";
}

export default function TimelinePage() {
  const params = useParams<{ id: string }>();
  const contractId = params.id;

  const [events, setEvents] = useState<DanteEvent[] | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [sandbox, setSandbox] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("All");

  const load = useCallback(async () => {
    try {
      const query = filter === "All" ? "" : `?category=${filter}`;
      const data = await apiGet<TimelineResponse>(
        `/api/contracts/${contractId}/timeline${query}`
      );
      setEvents(data.events);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load timeline");
    }
  }, [contractId, filter]);

  // Initial load + contract status probe (for the poll gate).
  useEffect(() => {
    let alive = true;
    apiGet<ContractResponse>(`/api/contracts/${contractId}`)
      .then((d) => {
        if (!alive) return;
        setStatus(d.contract.status);
        setSandbox(!!d.contract.sandbox_mode);
      })
      .catch(() => undefined); // timeline still renders from its own fetch
    return () => {
      alive = false;
    };
  }, [contractId]);

  // Re-fetch on filter change; poll every 3s while not terminal.
  useEffect(() => {
    load();
    if (isTerminal(status)) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load, status]);

  // Stop polling once a fetched event stream shows the terminal event.
  useEffect(() => {
    if (!events?.length) return;
    const last = events[events.length - 1];
    if (
      last.event_type === "CONTRACT_SATISFIED" ||
      last.event_type === "CONTRACT_REMEDIATED" ||
      last.event_type === "REFUND_FAILED"
    ) {
      setStatus((s) => s ?? "SATISFIED");
    }
  }, [events]);

  const rows = events;

  const counts = useMemo(() => {
    if (!rows) return {} as Record<string, number>;
    return rows.reduce<Record<string, number>>((acc, e) => {
      acc[e.category] = (acc[e.category] ?? 0) + 1;
      return acc;
    }, {});
  }, [rows]);

  return (
    <main className="dante-container py-8 md:py-12">
      <Folio
        issue="ISSUE 03 / TIMELINE"
        running={`DOSSIER / ${contractId.slice(0, 13).toUpperCase()}`}
        href={`/contract/${contractId}`}
      />

      <header className="mt-8 grid grid-cols-1 gap-6 md:grid-cols-12">
        <div className="md:col-span-8">
          <SectionLabel>EVENT TRACE · APPEND-ONLY</SectionLabel>
          <h1 className="mt-3 font-display text-5xl leading-[1.02] md:text-6xl">
            Everything that happened,
            <br />
            in the order it happened.
          </h1>
          <p className="mt-4 max-w-prose text-sm leading-relaxed text-ink-soft">
            The canonical audit history of one purchase — agent decisions, money
            actions, merchant calls, synthetic fulfillment, policy verdicts and
            evidence snapshots, oldest first.
          </p>
        </div>
        <div className="flex flex-col items-start gap-2 md:col-span-4 md:items-end">
          {status ? <Badge>{status}</Badge> : null}
          {sandbox !== null && <span className="folio-label">{sandbox ? "SANDBOX RAIL" : "LIVE TEST-MODE RAIL"}</span>}
          <Link
            href={`/audit/${contractId}`}
            className="folio-label underline-offset-4 hover:text-signal hover:underline"
          >
            Raw audit dossier →
          </Link>
        </div>
      </header>

      {/* Filter chips */}
      <nav aria-label="Event category filters" className="mt-10 flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            aria-pressed={filter === f}
            className={cn(
              "rounded-md border px-3 py-1.5 font-mono text-[0.6875rem] uppercase tracking-[0.14em] transition-colors",
              filter === f
                ? "border-ink bg-ink text-paper-bright"
                : "border-rule bg-paper-bright text-ink-soft hover:border-ink hover:text-ink"
            )}
          >
            {f}
            {f !== "All" && counts[f] ? ` (${counts[f]})` : ""}
          </button>
        ))}
      </nav>

      {error && (
        <p role="alert" className="mt-6 border-l-2 border-danger pl-3 font-mono text-xs text-danger">
          {error} — is the API on :8000?
        </p>
      )}

      {!rows && !error && (
        <p className="mt-10 font-mono text-xs uppercase tracking-[0.14em] text-ink-soft">
          Loading trace…
        </p>
      )}

      {rows && rows.length === 0 && (
        <p className="mt-10 font-mono text-xs uppercase tracking-[0.14em] text-ink-soft">
          No events{filter !== "All" ? ` in category ${filter}` : ""}.
        </p>
      )}

      {/* The trace */}
      {rows && rows.length > 0 && (
        <ol className="mt-2">
          {rows.map((e, i) => (
            <li key={e.id}>
              <div
                className={cn(
                  "grid grid-cols-1 gap-x-6 gap-y-2 py-4 md:grid-cols-12",
                  i > 0 && "border-t border-rule"
                )}
              >
                {/* col 1 — timestamp */}
                <time
                  dateTime={e.created_at ?? undefined}
                  className="tabular pt-0.5 font-mono text-xs text-ink-soft md:col-span-2"
                >
                  {formatTime(e.created_at)}
                </time>

                {/* col 2 — event + category chip */}
                <div className="md:col-span-3">
                  <span className="block break-all font-mono text-[0.8125rem] font-medium text-ink">
                    {e.event_type}
                  </span>
                  <span
                    className={cn(
                      "mt-1.5 inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-[2px] font-mono text-[0.5625rem] uppercase tracking-[0.14em]",
                      categoryClass(e.category)
                    )}
                  >
                    {e.category}
                  </span>
                  <span className="ml-1.5 inline-flex align-middle">
                    <SyntheticBadge synthetic={!!e.synthetic} />
                  </span>
                </div>

                {/* col 3 — payload summary + expandable details */}
                <div className="md:col-span-7">
                  <code className="block break-all font-mono text-xs leading-relaxed text-ink-soft">
                    {payloadSummary(e.payload ?? {})}
                  </code>
                  {Object.keys(e.payload ?? {}).length > 0 && (
                    <details className="group mt-1.5">
                      <summary className="cursor-pointer select-none font-mono text-[0.625rem] uppercase tracking-[0.14em] text-ink-soft underline-offset-4 hover:text-signal hover:underline">
                        Payload
                      </summary>
                      <pre className="mt-2 overflow-x-auto rounded-md border border-rule bg-paper-bright p-3 font-mono text-[0.6875rem] leading-relaxed text-ink">
                        {prettyJson(e.payload)}
                      </pre>
                    </details>
                  )}
                  {(e.correlation_id || e.trace_id) && (
                    <p className="mt-1 break-all font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-ink-soft/70">
                      corr {e.correlation_id ?? "—"} · trace {e.trace_id ?? "—"}
                    </p>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </main>
  );
}
