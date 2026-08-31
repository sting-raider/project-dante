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
  TimelineResponse,
} from "@/lib/rights-ui";
import { cn } from "@/lib/cn";

/** String|undefined coercion for payload fields of any shape. */
function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

/** Number|undefined coercion for payload fields of any shape. */
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

type AgentRun = {
  agent_name: string;
  engine: string;
  input_summary: string;
  output_summary: string;
  latency_ms?: number;
  validation_retries?: number;
  trace_id?: string;
  compilation_provenance?: {
    provider?: string | null;
    model?: string | null;
    item_count?: number;
    fallback_reason?: string | null;
  };
  created_at: string;
};

/**
 * One derived money-action row, built from the POLICY_* / REFUND_* event
 * payloads the backend actually emits (project_dante/domain/money/policy.py):
 *   POLICY_DECIDED/ALLOWED/DENIED → {decision, reason_codes, explanation,
 *     money_action_id, amount_paise, action_type, policy_snapshot_hash}
 *   REFUND_REQUESTED → {payment_id, amount_paise, idempotency_key,
 *     reason_code, mode}
 *   REFUND_PROCESSED → {refund_id, amount_paise, payment_id, sandbox, mode}
 */
type DerivedMoneyAction = {
  key: string;
  money_action_id: string | null;
  type: string | null;
  decision: string | null;
  status: string;
  amount_paise: number | null;
  reason_codes: unknown[];
  reason_code: string | null;
  human_explanation: string | null;
  idempotency_key: string | null;
  policy_snapshot_hash: string | null;
  razorpay_payment_id: string | null;
  result_ref: string | null;
  sandbox: boolean | null;
  event_type: string;
  at: string;
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
  const [moneyActions, setMoneyActions] = useState<DerivedMoneyAction[]>([]);
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
  // the source of truth; no private endpoints assumed). Payload shapes mirror
  // the backend append_event calls exactly:
  //   - POLICY_DECIDED/_ALLOWED/_DENIED (domain/money/policy.py _persist_and_link)
  //   - REFUND_REQUESTED / REFUND_PROCESSED / REFUND_FAILED (policy.py executor)
  // Agent-run rows come from INTENT_COMPILED / OFFER_EVALUATED events whose
  // payloads carry engine + constraint keys; the STORE-level _log_agent_run
  // records (agents/provider.py: input_summary/output_summary/latency_ms) are
  // not exposed over HTTP, so the timeline's own payloads stand in for them.
  useEffect(() => {
    const maByKey = new Map<string, DerivedMoneyAction>();
    for (const e of events) {
      const p = (e.payload ?? {}) as Record<string, unknown>;
      if (
        e.event_type !== "POLICY_DECIDED" &&
        e.event_type !== "POLICY_ALLOWED" &&
        e.event_type !== "POLICY_DENIED" &&
        e.event_type !== "REFUND_REQUESTED" &&
        e.event_type !== "REFUND_PROCESSED"
      ) {
        continue;
      }
      const key =
        (typeof e.idempotency_key === "string" && e.idempotency_key) ||
        `${e.aggregate_id}:${p.money_action_id ?? ""}`;
      const prev = maByKey.get(key);
      const row: DerivedMoneyAction = {
        key,
        money_action_id:
          str(p.money_action_id) ?? prev?.money_action_id ?? null,
        type:
          str(p.action_type) ??
          (e.event_type === "REFUND_REQUESTED" || e.event_type === "REFUND_PROCESSED"
            ? "refund_full"
            : prev?.type ?? null),
        decision: str(p.decision) ?? prev?.decision ?? null,
        status:
          e.event_type === "REFUND_PROCESSED"
            ? "executed"
            : e.event_type === "POLICY_DENIED"
              ? "denied"
              : e.event_type === "POLICY_ALLOWED"
                ? "allowed"
                : e.event_type === "POLICY_DECIDED"
                  ? (str(p.decision)?.toLowerCase() ?? "decided")
                  : prev?.status ?? "proposed",
        amount_paise:
          num(p.amount_paise) ?? prev?.amount_paise ?? null,
        reason_codes: Array.isArray(p.reason_codes)
          ? (p.reason_codes as unknown[])
          : (prev?.reason_codes ?? []),
        reason_code: str(p.reason_code) ?? prev?.reason_code ?? null,
        human_explanation: str(p.explanation) ?? prev?.human_explanation ?? null,
        idempotency_key: str(p.idempotency_key) ?? prev?.idempotency_key ?? null,
        policy_snapshot_hash:
          str(p.policy_snapshot_hash) ?? prev?.policy_snapshot_hash ?? null,
        razorpay_payment_id:
          str(p.payment_id) ?? prev?.razorpay_payment_id ?? null,
        result_ref: str(p.refund_id) ?? prev?.result_ref ?? null,
        sandbox: typeof p.sandbox === "boolean" ? p.sandbox : (prev?.sandbox ?? null),
        event_type: e.event_type,
        at: e.created_at ?? "",
      };
      maByKey.set(key, row);
    }
    setMoneyActions([...maByKey.values()].sort((a, b) => a.at.localeCompare(b.at)));

    // Agent runs from the Agent-category events the compiler/evaluator emit.
    const runs: AgentRun[] = [];
    for (const e of events) {
      const p = (e.payload ?? {}) as Record<string, unknown>;
      if (e.event_type === "INTENT_COMPILED") {
        const evidence =
          p.compilation_provenance && typeof p.compilation_provenance === "object"
            ? (p.compilation_provenance as Record<string, unknown>)
            : null;
        const evidenceEngine = str(evidence?.engine);
        const engine = evidenceEngine === "llm" ? "llm" : evidence ? "rules" : str(p.engine) ?? "rules";
        const provider = str(evidence?.provider);
        const model = str(evidence?.model);
        const itemCount = num(evidence?.item_count);
        runs.push({
          agent_name: "IntentCompiler",
          engine,
          input_summary: "raw buyer brief → typed hard constraints + soft preferences",
          output_summary: `${engine === "llm" ? "LLM compiled" : "Deterministic fallback"}${
            provider || model
              ? ` · ${provider ?? "provider"} · ${model ?? "model"}`
              : ""
          }${itemCount != null ? ` · ${itemCount} basket lines verified` : ""} · constraint keys: ${(Array.isArray(p.hard_constraint_keys)
            ? (p.hard_constraint_keys as unknown[])
            : []
          ).join(", ")}`,
          trace_id: e.trace_id ?? undefined,
          compilation_provenance: evidence
            ? {
                provider,
                model,
                item_count: itemCount ?? undefined,
                fallback_reason: str(evidence.fallback_reason),
              }
            : undefined,
          created_at: e.created_at ?? "",
        });
      } else if (e.event_type === "OFFER_EVALUATED") {
        runs.push({
          agent_name: "OfferEvaluator",
          engine: "rules", // deterministic evaluator; enrichment rephrase never changes order
          input_summary: `offers evaluated: ${num(p.offers_evaluated) ?? "?"}`,
          output_summary: `feasible: ${num(p.feasible_count) ?? "?"}`,
          trace_id: e.trace_id ?? undefined,
          created_at: e.created_at ?? "",
        });
      } else if (e.event_type === "CATALOG_SEARCHED") {
        runs.push({
          agent_name: "MerchantSearch",
          engine: "keyword",
          input_summary: "distilled intent keywords",
          output_summary: `source=${str(p.source) ?? "?"} · candidates=${num(p.candidates) ?? "?"}`,
          trace_id: e.trace_id ?? undefined,
          created_at: e.created_at ?? "",
        });
      }
    }
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
              none recorded on this trace yet — money actions appear once a
              remedy is policy-checked or executed.
            </p>
          ) : (
            <ul className="mt-3 space-y-3">
              {moneyActions.map((ma) => (
                <li key={ma.key} className="rounded-md border border-rule bg-paper-bright p-4">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                    <span className="font-medium uppercase">{ma.type ?? "money action"}</span>
                    {ma.amount_paise != null && (
                      <span className="tabular">{ma.amount_paise} paise</span>
                    )}
                    <span className="uppercase text-ink-soft">status: {ma.status}</span>
                    {ma.decision && (
                      <span
                        className={cn(
                          "uppercase",
                          ma.decision === "ALLOW"
                            ? "text-success"
                            : ma.decision === "DENY"
                              ? "text-danger"
                              : "text-warning"
                        )}
                      >
                        decision: {ma.decision}
                      </span>
                    )}
                    {ma.sandbox === true && (
                      <span className="uppercase text-warning">sandbox adapter</span>
                    )}
                    {ma.result_ref && <span className="text-success">refund_id: {ma.result_ref}</span>}
                  </div>
                  <dl className="mt-2 space-y-1 text-ink-soft">
                    {(ma.reason_codes.length > 0 || ma.reason_code) && (
                      <div>
                        <dt className="inline">reason codes: </dt>
                        <dd className="inline text-ink">
                          {[...new Set([...(ma.reason_codes.map(String)), ...(ma.reason_code ? [ma.reason_code] : [])])].join(", ")}
                        </dd>
                      </div>
                    )}
                    {ma.human_explanation && (
                      <div><dt className="inline">explanation: </dt><dd className="inline text-ink">{ma.human_explanation}</dd></div>
                    )}
                    {ma.idempotency_key && (
                      <div><dt className="inline">idempotency_key: </dt><dd className="inline"><Mono>{ma.idempotency_key}</Mono></dd></div>
                    )}
                    {ma.policy_snapshot_hash && (
                      <div><dt className="inline">policy_snapshot_hash: </dt><dd className="inline"><Mono>{ma.policy_snapshot_hash}</Mono></dd></div>
                    )}
                    {ma.razorpay_payment_id && (
                      <div><dt className="inline">razorpay payment: </dt><dd className="inline"><Mono>{ma.razorpay_payment_id}</Mono></dd></div>
                    )}
                  </dl>
                  <p className="mt-1 text-[0.625rem] uppercase tracking-[0.1em] text-ink-soft/70">
                    from event {ma.event_type} · {ma.at.replace("T", " ").slice(11, 19)}
                  </p>
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
                <li key={`${r.created_at}:${i}`} className="border-l-2 border-rule pl-3">
                  <span className="font-medium uppercase">{r.agent_name}</span>
                  {r.engine && <span className="text-ink-soft"> · engine: {r.engine}</span>}
                  <span className="text-ink-soft"> · {(r.created_at ?? "").replace("T", " ").slice(11, 19)}</span>
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
