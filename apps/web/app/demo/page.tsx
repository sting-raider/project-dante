"use client";

/**
 * /demo — DEMO SIMULATION CONTROL (plan §28). Manual controls for reset /
 * ship / deliver(scenario) / replacement-unavailable, plus the one-click
 * hero orchestrator that fires the whole arc end-to-end with a visible
 * mono step ticker: compile → search → select → authorize → payment-order
 * → capture (sandbox simulate or live handoff) → ship → deliver
 * wrong_variant → replacement-unavailable → remedies → policy → execute.
 *
 * Agent I.
 */

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import Folio from "@/components/editorial/Folio";
import SectionLabel from "@/components/editorial/SectionLabel";
import SyntheticBadge from "@/components/commerce/SyntheticBadge";
import { Button } from "@/components/ui/Button";
import Panel from "@/components/ui/Panel";
import { apiGet, apiPost } from "@/lib/api";
import { cn } from "@/lib/cn";

/* ------------------------------------------------------- API shapes (local) */

type CompileResponse = {
  intent: { id: string; raw_text: string };
  engine?: string;
};
type SearchResponse = {
  intent: { id: string };
  results: {
    offer: { id: string; title: string; unit_amount_paise: number };
    evaluation: { feasible: boolean; explanation?: string };
  }[];
};
type SelectResponse = { contract: { id: string; status: string; sandbox_mode?: boolean } };
type AuthorizeResponse = { contract: { id: string; status: string } };
type PaymentOrderResponse = {
  mode: "live-test-mode" | "sandbox";
  checkout_config?: { key_id: string; order_id: string; amount_paise: number };
  razorpay_order?: Record<string, unknown>;
};
type SimulateResponse = { delivered?: boolean };
type DeliverResponse = {
  breaches?: unknown[];
  status?: string | null;
  verification_error?: string | null;
  synthetic?: boolean;
};
type RemediesResponse = {
  proposals: { id: string; remedy_type: string; rank?: number | null; rejected_reason?: string | null }[];
};
type PolicyResponse = {
  decision?: {
    decision: string;
    policy_ids?: string[];
    reason_codes?: string[];
    explanation?: string;
  } | null;
  money_action?: {
    status?: string;
    reason_code?: string;
  } | null;
};
type ExecuteResponse = {
  money_action?: {
    result_ref?: string | null;
    amount_paise?: number;
    status?: string;
    reason_code?: string;
  } | null;
  refund?: Record<string, unknown> | null;
  error?: string;
};

const HERO_INTENT =
  "Buy the Aster ANC Pro over-ear wireless headphones for ₹11,499 or less — must have " +
  "active noise cancellation and a manufacturer warranty valid in India. Delivery within " +
  "4 days. Do NOT substitute alternatives.";

type StepStatus = "pending" | "running" | "ok" | "fail";

interface Step {
  label: string;
  run: (ctx: Ctx) => Promise<string>;
}

/** One ticker row; timestamps are stamped once at each transition (#10). */
interface StepState {
  name: string;
  status: StepStatus;
  detail?: string;
  startedAt?: string;
  finishedAt?: string;
}

/** Shared mutable context threaded through orchestrator steps. */
interface Ctx {
  intentId?: string;
  offerId?: string;
  contractId?: string;
  orderId?: string;
  paymentId?: string;
  mode?: "live-test-mode" | "sandbox";
  proposalId?: string;
}

export default function DemoPage() {
  const [contractInput, setContractInput] = useState("");
  const [deliverScenario, setDeliverScenario] = useState<"correct" | "wrong_variant" | "late">("wrong_variant");
  const [busy, setBusy] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ tone: "ok" | "fail"; text: string } | null>(null);

  // Orchestrator state
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState<StepState[]>([]);
  const ctxRef = useRef<Ctx>({});
  const logRef = useRef<HTMLOListElement>(null);
  const reduceMotion = useReducedMotion();

  function setStep(i: number, patch: Partial<Omit<StepState, "name">>) {
    setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)));
  }

  /* ------------------------------------------------- manual controls */

  async function manual(path: string, body?: unknown, tag?: string) {
    setBusy(tag ?? path);
    setFlash(null);
    try {
      const res = await apiPost<Record<string, unknown>>(path, body);
      setFlash({ tone: "ok", text: `${path} → ${JSON.stringify(res).slice(0, 220)}` });
    } catch (e) {
      setFlash({ tone: "fail", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  /* ------------------------------------------------- hero orchestrator */

  const stepsDef: Step[] = [
    {
      label: `compile hero intent`,
      run: async (c) => {
        const r = await apiPost<CompileResponse>("/api/intents/compile", { raw_text: HERO_INTENT });
        c.intentId = r.intent.id;
        return `intent ${r.intent.id.slice(0, 18)}… engine=${r.engine ?? "rules"}`;
      },
    },
    {
      label: "search merchant catalog",
      run: async (c) => {
        if (!c.intentId) throw new Error("no intent");
        const r = await apiPost<SearchResponse>(`/api/intents/${c.intentId}/search`);
        const feasible = r.results.filter((x) => x.evaluation.feasible);
        if (feasible.length === 0) throw new Error("no feasible offer");
        c.offerId = feasible[0].offer.id;
        return `${r.results.length} candidates · ${feasible.length} feasible → ${feasible[0].offer.title}`;
      },
    },
    {
      label: "select first feasible offer",
      run: async (c) => {
        if (!c.intentId || !c.offerId) throw new Error("no intent/offer");
        const r = await apiPost<SelectResponse>(`/api/intents/${c.intentId}/select-offer`, {
          offer_id: c.offerId,
        });
        c.contractId = r.contract.id;
        return `contract ${r.contract.id.slice(0, 18)}… status=${r.contract.status}`;
      },
    },
    {
      label: "authorize (buyer authority envelope)",
      run: async (c) => {
        if (!c.contractId) throw new Error("no contract");
        const r = await apiPost<AuthorizeResponse>(`/api/contracts/${c.contractId}/authorize`, {});
        return `status=${r.contract.status}`;
      },
    },
    {
      label: "create Razorpay payment-order",
      run: async (c) => {
        if (!c.contractId) throw new Error("no contract");
        const r = await apiPost<PaymentOrderResponse>(
          `/api/contracts/${c.contractId}/payment-order`,
          {}
        );
        c.mode = r.mode;
        c.orderId =
          (r.checkout_config?.order_id as string) ??
          ((r.razorpay_order as { id?: string })?.id ?? "");
        return `mode=${r.mode} order=${c.orderId}`;
      },
    },
    {
      label: "capture payment",
      run: async (c) => {
        if (!c.contractId || !c.orderId) throw new Error("no contract/order");
        if (c.mode === "sandbox") {
          const payId = `pay_sbx_${Date.now().toString(36)}`;
          await apiPost<SimulateResponse>("/api/demo/razorpay/simulate-event", {
            event_type: "payment.captured",
            order_id: c.orderId,
            payment_id: payId,
          });
          c.paymentId = payId;
          return `SANDBOX simulated capture ${payId} via real signed webhook`;
        }
        // Live test-mode: a human must complete Razorpay Checkout.
        return (
          `LIVE TEST-MODE — open Razorpay Checkout on the contract page and pay order ${c.orderId}; ` +
          `the webhook will confirm server-side. Continuing fulfillment steps anyway.`
        );
      },
    },
    {
      label: "ship (SYNTHETIC fulfillment)",
      run: async (c) => {
        if (!c.contractId) throw new Error("no contract");
        await apiPost(`/api/demo/contracts/${c.contractId}/ship`);
        return `FULFILLMENT_SHIPPED recorded`;
      },
    },
    {
      label: "deliver wrong_variant (SYNTHETIC)",
      run: async (c) => {
        if (!c.contractId) throw new Error("no contract");
        const r = await apiPost<DeliverResponse>(
          `/api/demo/contracts/${c.contractId}/deliver`,
          { scenario: "wrong_variant" }
        );
        return `${(r.breaches ?? []).length} breach(es) · contract_status=${r.status ?? "?"}`;
      },
    },
    {
      label: "mark replacement unavailable",
      run: async (c) => {
        if (!c.contractId) throw new Error("no contract");
        await apiPost(`/api/demo/contracts/${c.contractId}/replacement-unavailable`);
        return `replacement inventory = 0 recorded`;
      },
    },
    {
      label: "plan remedies",
      run: async (c) => {
        if (!c.contractId) throw new Error("no contract");
        const r = await apiGet<RemediesResponse>(`/api/contracts/${c.contractId}/remedies`);
        const live = r.proposals.filter((p) => !p.rejected_reason);
        if (live.length === 0) throw new Error("planner returned no viable proposal");
        live.sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
        c.proposalId = live[0].id;
        return `rank #1 → ${live[0].remedy_type} (${live[0].id.slice(0, 16)}…)`;
      },
    },
    {
      label: "policy verdict",
      run: async (c) => {
        if (!c.proposalId) throw new Error("no proposal");
        const r = await apiPost<PolicyResponse>(`/api/remedies/${c.proposalId}/policy`);
        const d = r.decision?.decision;
        // A DENY is a real policy refusal, not a step success — halt the
        // chain with the reason codes so the ticker can't read as green (#6).
        if (d === "DENY" || !r.decision || r.money_action?.status === "denied") {
          throw new Error(
            `policy DENIED — reasons: ${(r.decision?.reason_codes ?? ["unknown"]).join(", ")}` +
              (r.decision?.explanation ? ` · ${r.decision.explanation.slice(0, 140)}` : ""),
          );
        }
        if (d === "REQUIRE_APPROVAL") {
          return `REQUIRE_APPROVAL (${(r.decision?.policy_ids ?? []).join(",")}) — approving`;
        }
        return `ALLOW by ${(r.decision?.policy_ids ?? []).join(", ")}`;
      },
    },
    {
      label: "execute refund",
      run: async (c) => {
        if (!c.proposalId) throw new Error("no proposal");
        const r = await apiPost<ExecuteResponse>(`/api/remedies/${c.proposalId}/execute`);
        if (r.error && !r.money_action) throw new Error(r.error);
        const status = r.money_action?.status;
        // executed:false / failed money action must fail the step — the hero
        // arc only reports success when the refund actually went through (#6).
        if (status === "failed" || status === "denied") {
          throw new Error(
            `refund did not execute — money_action=${status}` +
              (r.money_action?.reason_code ? ` · ${r.money_action.reason_code}` : ""),
          );
        }
        const ref = r.money_action?.result_ref ?? (r.refund as { id?: string })?.id;
        if (!ref && status !== "executed") {
          throw new Error(`refund not confirmed — no result_ref, money_action=${status ?? "?"}`);
        }
        return `refund ${ref ?? "confirmed"} · money_action=${status ?? "executed"}`;
      },
    },
  ];

  const runHero = useCallback(async () => {
    setRunning(true);
    setFlash(null);
    ctxRef.current = {};
    const now = () => new Date().toLocaleTimeString("en-GB", { hour12: false });
    // Each step carries its own timestamp, stamped at transition (#10).
    setSteps(
      stepsDef.map((s) => ({
        name: s.label,
        status: "pending" as StepStatus,
        startedAt: now(),
      })),
    );

    let halted = false;
    for (let i = 0; i < stepsDef.length; i++) {
      if (halted) {
        setStep(i, { status: "pending", detail: "skipped — chain halted" });
        continue;
      }
      const startedAt = now();
      setStep(i, { status: "running", startedAt });
      try {
        const detail = await stepsDef[i].run(ctxRef.current);
        setStep(i, { status: "ok", detail, finishedAt: now() });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setStep(i, { status: "fail", detail: msg, finishedAt: now() });
        setFlash({ tone: "fail", text: `Chain halted at "${stepsDef[i].label}": ${msg}` });
        halted = true;
      }
      logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
    }
    if (!halted) {
      const cid = ctxRef.current.contractId;
      setFlash({
        tone: "ok",
        text: `HERO ARC COMPLETE — contract ${cid?.slice(0, 18)}… remediated.`,
      });
    }
    setRunning(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const anyBusy = busy !== null || running;

  return (
    <main className="min-h-screen bg-paper">
      {/* Warning strip */}
      <div className="border-b border-warning bg-warning/[0.08] px-6 py-3 md:px-10">
        <p className="folio-label text-warning flex flex-wrap items-center gap-2">
          <span className="rounded-sm border border-warning px-1.5 py-[2px]">DEMO SIMULATION CONTROL</span>
          <span className="normal-case tracking-normal">
            Fulfillment events are SYNTHETIC; payment/refund actions execute against
            Razorpay (sandbox adapter unless live keys configured).
          </span>
        </p>
      </div>

      <div className="dante-container py-8 md:py-12">
        <Folio issue="ISSUE 00 / CONTROL ROOM" running="PRIVATE PANEL / OPERATOR" />

        <header className="mt-8 grid grid-cols-1 gap-6 md:grid-cols-12">
          <div className="md:col-span-7">
            <SectionLabel>THE FIVE-MINUTE ARC</SectionLabel>
            <h1 className="mt-3 font-display text-5xl leading-[1.02] md:text-6xl">
              One click buys it, breaks it,
              <br />
              and makes it right.
            </h1>
            <p className="mt-4 max-w-prose text-sm leading-relaxed text-ink-soft">
              The hero scenario runs the entire thesis in sequence: intent → frozen
              promises → authorization → Razorpay order → capture → wrong variant
              delivered → material breach → refund remedy planned, policy-gated,
              executed. Watch the ticker.
            </p>
          </div>
          <div className="flex items-start md:col-span-5 md:justify-end">
            <Button onClick={runHero} disabled={anyBusy} size="lg" data-testid="run-hero">
              {running ? "RUNNING ARC…" : "▶ RUN HERO SCENARIO"}
            </Button>
          </div>
        </header>

        {/* Ticker */}
        {steps.length > 0 && (
          <motion.ol
            ref={logRef}
            initial={reduceMotion ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            aria-label="Hero scenario step log"
            className="mt-8 max-h-96 overflow-y-auto rounded-md border border-rule bg-paper-bright p-4 font-mono text-xs leading-relaxed"
          >
            {steps.map((s, i) => (
              <li key={i} className="flex items-baseline gap-3 py-0.5">
                <time className="shrink-0 text-ink-soft">
                  {s.status === "ok" || s.status === "fail" ? (s.finishedAt ?? s.startedAt ?? "") : s.status === "running" ? s.startedAt ?? "" : ""}
                </time>
                <span
                  className={cn(
                    "w-4 shrink-0 text-center",
                    s.status === "ok" && "text-success",
                    s.status === "fail" && "text-danger",
                    s.status === "running" && "animate-pulse text-signal",
                    s.status === "pending" && "text-ink-soft"
                  )}
                  aria-label={s.status}
                >
                  {s.status === "ok" ? "✓" : s.status === "fail" ? "✗" : s.status === "running" ? "▸" : "·"}
                </span>
                <span className={cn("font-medium uppercase tracking-[0.04em]", s.status === "pending" && "text-ink-soft")}>
                  {s.name}
                </span>
                {s.detail && (
                  <span className={cn("min-w-0 break-all", s.status === "fail" ? "text-danger" : "text-ink-soft")}>
                    — {s.detail}
                  </span>
                )}
              </li>
            ))}
          </motion.ol>
        )}

        {flash && (
          <p
            role="status"
            className={cn(
              "mt-4 break-all rounded-md border p-3 font-mono text-xs",
              flash.tone === "ok"
                ? "border-success bg-success/[0.07] text-success"
                : "border-danger bg-danger/[0.06] text-danger"
            )}
          >
            {flash.text}
            {ctxRef.current.contractId && (
              <>
                {" "}
                <Link href={`/contract/${ctxRef.current.contractId}`} className="underline underline-offset-4">
                  open dossier →
                </Link>
              </>
            )}
          </p>
        )}

        {/* Manual controls */}
        <section className="mt-14" aria-label="Manual demo controls">
          <SectionLabel>MANUAL CONTROLS</SectionLabel>
          <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Panel label="SEED" aside={<SyntheticBadge synthetic />}>
              <p className="text-sm leading-relaxed text-ink-soft">
                Reset catalog + stores to fixture seed. Wipes every contract.
              </p>
              <div className="mt-3">
                <Button variant="secondary" size="sm" disabled={anyBusy} onClick={() => manual("/api/demo/reset")}>
                  POST /demo/reset
                </Button>
              </div>
            </Panel>

            <Panel label="FULFILLMENT · SYNTHETIC" aside={<SyntheticBadge synthetic />}>
              <label htmlFor="contract-id" className="folio-label block">
                Contract ID
              </label>
              <input
                id="contract-id"
                type="text"
                value={contractInput}
                onChange={(e) => setContractInput(e.target.value)}
                placeholder="paste a contract id…"
                className="mt-1 w-full rounded-md border border-rule bg-paper-bright px-3 py-2 font-mono text-xs text-ink outline-none focus:border-ink"
              />

              <div className="mt-4 flex flex-wrap items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={anyBusy || !contractInput.trim()}
                  onClick={() => manual(`/api/demo/contracts/${contractInput.trim()}/ship`)}
                >
                  Ship
                </Button>

                <label htmlFor="scenario" className="sr-only">
                  Delivery scenario
                </label>
                <select
                  id="scenario"
                  value={deliverScenario}
                  onChange={(e) => setDeliverScenario(e.target.value as typeof deliverScenario)}
                  className="h-8 rounded-md border border-rule bg-paper-bright px-2 font-mono text-xs text-ink"
                >
                  <option value="correct">correct</option>
                  <option value="wrong_variant">wrong_variant</option>
                  <option value="late">late</option>
                </select>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={anyBusy || !contractInput.trim()}
                  onClick={() =>
                    manual(`/api/demo/contracts/${contractInput.trim()}/deliver`, {
                      scenario: deliverScenario,
                    })
                  }
                >
                  Deliver
                </Button>
              </div>

              <div className="mt-4">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={anyBusy || !contractInput.trim()}
                  onClick={() =>
                    manual(`/api/demo/contracts/${contractInput.trim()}/replacement-unavailable`)
                  }
                >
                  Mark replacement unavailable
                </Button>
              </div>
            </Panel>
          </div>
        </section>
      </div>
    </main>
  );
}
