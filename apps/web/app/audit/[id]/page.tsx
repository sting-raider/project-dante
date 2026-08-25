"use client";

/**
 * /audit/[id] — engineer/judge-facing raw dossier (plan §28 + §53).
 * Full contract record with complete hashes, the entire event stream
 * (no category filter) with idempotency_key + trace_id columns, money
 * actions with policy snapshot hashes, Razorpay ids, real/sandbox badges,
 * and webhook events flagged when duplicates were ignored. Dense mono,
 * paper-themed terminal.
 *
 * Agent I.
 */

import { useParams } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import Folio from "@/components/editorial/Folio";
import SectionLabel from "@/components/editorial/SectionLabel";
import SyntheticBadge from "@/components/commerce/SyntheticBadge";
import SandboxBadge from "@/components/commerce/SandboxBadge";
import Badge from "@/components/commerce/Badge";
import { apiGet } from "@/lib/api";
import type {
  ContractResponse,
  DanteEvent,
  MoneyAction,
  TimelineResponse,
} from "@/lib/rights-ui";

type AgentRun = {
  id?: string;
  agent_name?: string;
  engine?: string;
  input_summary?: string;
  output_summary?: string;
  decision_rationale?: string;
  tool_calls?: unknown;
  created_at?: string;
  [k: string]: unknown;
};

function Mono({ children }: { children: React.ReactNode }) {
  return (
    <code className="break-all font-mono text-[0.6875rem] leading-relaxed text-ink">
      {children}
    </code>
  );
}

export default function AuditPage() {
  const params = useParams<{ id: string }>();
  const contractId = params.id;

  const [detail, setDetail] = useState<ContractResponse | null>(null);
  const [events, setEvents] = useState<DanteEvent[]>([]);
  const [moneyActions, setMoneyActions] = useState<MoneyAction[]>([]);
  const [agentRuns, setAgentRuns] = useState<AgentRun[]>([]);
  const [error, setError] = useState<string | null>(null);

  const loadEvents = useCallback(() => {
    return apiGet<TimelineResponse>(`/api/contracts/${contractId}/timeline`)
      .then((d) => setEvents(d.events ?? []))
      .catch(() => undefined);
  }, [contractId]);

  useEffect(() => {
    let alive = true;
    apiGet<ContractResponse>(`/api/contracts/${contractId}`)
      .then(async (d) => {
        if (!alive) return;
        setDetail(d);
        // Money actions live behind remedy policy calls; surface them from
        // the timeline payload when present, else try the remedies route.
        await loadEvents();
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : "contract not found"));

    // Money actions: extract from POLICY_* / REFUND_* event payloads.
    loadEvents().then(() => undefined);

    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contractId]);

  // Derive money actions + agent runs from event payloads (the audit trail is
  // the source of truth; no private endpoints assumed).
  useEffect(() => {
    const maById = new Map<string, MoneyAction>();
    const runs: AgentRun[] = [];
    for (const e of events) {
      const p = (e.payload ?? {}) as Record<string, unknown>;
      const candidateMa = (p.money_action ?? p.moneyAction) as MoneyAction | undefined;
      if (candidateMa?.id) maById.set(candidateMa.id, { ...maById.get(candidateMa.id), ...candidateMa });
      const run = (p.agent_run ?? p.agentRun ?? p.run) as AgentRun | undefined;
      if (run?.agent_name) runs.push({ id: e.id, ...run });
      if (e.event_type === "REMEDY_PROPOSED" && p.planner_run) {
        runs.push(p.planner_run as AgentRun);
      }
    }
    setMoneyActions([...maById.values()]);
    setAgentRuns(runs);
  }, [events]);

  const webhookEvents = events.filter(
    (e) => e.event_type === "WEBHOOK_RECEIVED" || e.event_type === "WEBHOOK_DUPLICATE_IGNORED"
  );

  const contract = detail?.contract;

  return (
    <main className="min-h-screen bg-paper font-mono text-xs text-ink">
      <div className="dante-container py-8 md:py-12">
        <Folio issue="AUDIT DOSSIER / RAW" running={`TRACE ${contractId.slice(0, 18).toUpperCase()}`} />

        <header className="mt-8 flex flex-wrap items-baseline justify-between gap-4 border-b border-ink pb-6">
          <div>
            <SectionLabel>FOR ENGINEERS AND JUDGES · NO NARRATIVE</SectionLabel>
            <h1 className="mt-3 font-display text-4xl tracking-normal md:text-5xl">
              {contractId}
            </h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {contract && <Badge>{contract.status}</Badge>}
            {contract && <SandboxBadge sandbox={contract.sandbox_mode} />}
          </div>
        </header>

        {error && (
          <p role="alert" className="mt-8 border-l-2 border-danger pl-3 text-danger">
            {error} — is the API on :8000?
          </p>
        )}

        {/* ------------------------------------------------ contract record */}
        <section className="mt-10" aria-label="Contract record">
          <SectionLabel>CONTRACT RECORD · HASHES FULL-LENGTH</SectionLabel>
          {!contract ? (
            <p className="mt-3 text-ink-soft">loading…</p>
          ) : (
            <table className="mt-3 w-full max-w-4xl border-collapse">
              <tbody>
                <Row label="id"><Mono>{contract.id}</Mono></Row>
                <Row label="display_code"><Mono>{contract.display_code ?? "—"}</Mono></Row>
                <Row label="status"><Mono>{contract.status}</Mono></Row>
                <Row label="intent_id"><Mono>{contract.intent_id}</Mono></Row>
                <Row label="offer_id"><Mono>{contract.offer_id}</Mono></Row>
                <Row label="amount_paise">
                  <Mono>{contract.amount_paise != null ? `${contract.amount_paise} (${(contract.amount_paise / 100).toLocaleString("en-IN")} INR)` : "—"}</Mono>
                </Row>
                <Row label="contract_hash">
                  <span className="text-signal-deep"><Mono>{contract.contract_hash ?? "—"}</Mono></span>
                </Row>
                <Row label="promise_set_hash"><Mono>{contract.promise_set_hash ?? "—"}</Mono></Row>
                <Row label="offer_hash"><Mono>{contract.offer_hash ?? "—"}</Mono></Row>
                <Row label="razorpay_order_id"><Mono>{contract.razorpay_order_id ?? "—"}</Mono></Row>
                <Row label="razorpay_payment_id"><Mono>{contract.razorpay_payment_id ?? "—"}</Mono></Row>
                <Row label="buyer_authority">
                  <Mono>
                    {contract.buyer_authority
                      ? `max ${((contract.buyer_authority.max_amount_paise ?? 0) / 100).toLocaleString("en-IN")} INR by ${contract.buyer_authority.authorized_by} at ${contract.buyer_authority.authorized_at ?? "—"}`
                      : "—"}
                  </Mono>
                </Row>
                <Row label="created_at / frozen_at">
                  <Mono>{contract.created_at ?? "—"} → {contract.frozen_at ?? "—"}</Mono>
                </Row>
              </tbody>
            </table>
          )}
        </section>

        {/* ------------------------------------------------ money actions */}
        <section className="mt-12" aria-label="Money actions">
          <SectionLabel>MONEY ACTIONS · POLICY SNAPSHOT HASHES</SectionLabel>
          {moneyActions.length === 0 ? (
            <p className="mt-3 text-ink-soft">
              none recorded on this trace yet — money actions appear after a remedy executes.
            </p>
          ) : (
            <ul className="mt-3 space-y-3">
              {moneyActions.map((ma) => (
                <li key={ma.id} className="rounded-md border border-rule bg-paper-bright p-4">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                    <span className="font-medium uppercase">{ma.type}</span>
                    <span className="tabular">{ma.amount_paise} paise</span>
                    <span className="uppercase text-ink-soft">status: {ma.status}</span>
                    {ma.result_ref && <span className="text-success">result_ref: {ma.result_ref}</span>}
                  </div>
                  <dl className="mt-2 space-y-1 text-ink-soft">
                    <div><dt className="inline">reason_code: </dt><dd className="inline text-ink">{ma.reason_code}</dd></div>
                    <div><dt className="inline">explanation: </dt><dd className="inline text-ink">{ma.human_explanation}</dd></div>
                    <div><dt className="inline">idempotency_key: </dt><dd className="inline"><Mono>{ma.idempotency_key}</Mono></dd></div>
                    <div><dt className="inline">policy_snapshot_hash: </dt><dd className="inline"><Mono>{ma.policy_snapshot_hash || "—"}</Mono></dd></div>
                    <div><dt className="inline">razorpay payment/order: </dt><dd className="inline"><Mono>{ma.razorpay_payment_id ?? "—"} / {ma.razorpay_order_id ?? "—"}</Mono></dd></div>
                  </dl>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ------------------------------------------------ agent runs */}
        {agentRuns.length > 0 && (
          <section className="mt-12" aria-label="Agent runs">
            <SectionLabel>AGENT RUNS · INPUTS/OUTPUTS ONLY, NO HIDDEN REASONING</SectionLabel>
            <ul className="mt-3 space-y-2">
              {agentRuns.map((r, i) => (
                <li key={r.id ?? i} className="border-l-2 border-rule pl-3">
                  <span className="font-medium uppercase">{r.agent_name}</span>
                  {r.engine && <span className="text-ink-soft"> · engine: {r.engine}</span>}
                  {r.input_summary && <p className="text-ink-soft">in: {r.input_summary}</p>}
                  {r.output_summary && <p className="text-ink-soft">out: {r.output_summary}</p>}
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* ------------------------------------------------ webhook events */}
        <section className="mt-12" aria-label="Webhook events">
          <SectionLabel>RAZORPAY WEBHOOK EVENTS · DUPLICATES FLAGGED</SectionLabel>
          {webhookEvents.length === 0 ? (
            <p className="mt-3 text-ink-soft">no webhook traffic on this trace.</p>
          ) : (
            <ul className="mt-3 space-y-1.5">
              {webhookEvents.map((e) => (
                <li key={e.id} className="flex flex-wrap items-center gap-x-3">
                  <time className="text-ink-soft">{(e.created_at ?? "").slice(11, 19)}</time>
                  <span className="uppercase">{e.event_type}</span>
                  {e.event_type === "WEBHOOK_DUPLICATE_IGNORED" && (
                    <span className="uppercase text-warning">duplicate suppressed</span>
                  )}
                  <SyntheticBadge synthetic={!!e.synthetic} />
                  {(e.payload as Record<string, unknown>)?.["event_id"] ? (
                    <Mono>{String((e.payload as Record<string, unknown>)["event_id"])}</Mono>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ------------------------------------------------ full event stream */}
        <section className="mt-12" aria-label="Complete event stream">
          <SectionLabel>COMPLETE EVENT STREAM · UNFILTERED</SectionLabel>
          <p className="mt-1 text-ink-soft">
            {events.length} events · columns: time | type | category | idempotency_key |
            trace_id | synthetic
          </p>
          <div className="mt-3 overflow-x-auto rounded-md border border-rule bg-paper-bright">
            <table className="w-full min-w-[54rem] border-collapse">
              <thead>
                <tr className="border-b border-ink text-left uppercase tracking-[0.12em] text-ink-soft">
                  <th className="px-3 py-2 font-medium">Time</th>
                  <th className="px-3 py-2 font-medium">Event</th>
                  <th className="px-3 py-2 font-medium">Cat</th>
                  <th className="px-3 py-2 font-medium">Idempotency key</th>
                  <th className="px-3 py-2 font-medium">Trace</th>
                  <th className="px-3 py-2 font-medium">Flags</th>
                </tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id} className="border-b border-rule last:border-b-0 hover:bg-paper">
                    <td className="whitespace-nowrap px-3 py-1.5 align-top text-ink-soft">
                      {(e.created_at ?? "").replace("T", " ").slice(11, 23)}
                    </td>
                    <td className="px-3 py-1.5 align-top font-medium">{e.event_type}</td>
                    <td className="px-3 py-1.5 align-top text-ink-soft">{e.category}</td>
                    <td className="max-w-[16rem] break-all px-3 py-1.5 align-top text-ink-soft">
                      {e.idempotency_key ?? "—"}
                    </td>
                    <td className="max-w-[10rem] break-all px-3 py-1.5 align-top text-ink-soft">
                      {e.trace_id ?? "—"}
                    </td>
                    <td className="px-3 py-1.5 align-top">
                      <SyntheticBadge synthetic={!!e.synthetic} />
                    </td>
                  </tr>
                ))}
                {events.length === 0 && !error && (
                  <tr>
                    <td colSpan={6} className="px-3 py-4 text-ink-soft">
                      no events recorded.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <footer className="mt-14 flex flex-wrap items-center justify-between gap-4 border-t border-rule pt-6">
          <Link href={`/contract/${contractId}/timeline`} className="folio-label underline underline-offset-4 hover:text-signal">
            Human-readable timeline →
          </Link>
          <span className="folio-label">PROJECT DANTE · APPEND-ONLY TRUTH</span>
        </footer>
      </div>
    </main>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <tr className="border-b border-rule last:border-b-0">
      <th scope="row" className="w-56 px-3 py-1.5 text-left align-top font-medium uppercase tracking-[0.1em] text-ink-soft">
        {label}
      </th>
      <td className="px-3 py-1.5 align-top">{children}</td>
    </tr>
  );
}
