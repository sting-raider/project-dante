"use client";

/**
 * /demo — DEMO SIMULATION CONTROL (plan §20). The resumable 15-step hero-arc
 * orchestrator console: intent → frozen promises → authorization → Razorpay
 * order → [WAITING: buyer completes real/sandbox checkout on
 * /contract/{id}] → capture → synthetic ship → wrong_variant deliver →
 * material breach → rights → replacement-unavailable → refund selected →
 * policy ALLOW → refund executed / REMEDIATED.
 *
 * Every step is a timestamped mono ticker row with ✓ / WAITING / ✗ status and
 * a stable failure reason code; errors halt with a retry button scoped to the
 * failed step. State persists to sessionStorage keyed by the run id, so a
 * refresh resumes at the current step with completed rows shown as historical
 * checkmarks. Sandbox vs live-test-mode is surfaced per money row
 * (SANDBOX / RAZORPAY TEST MODE badges) from GET /api/demo/status + the run's
 * payment-order rail. Operator-token input for live-test-mode demo gating —
 * never hardcoded, sent as X-Demo-Operator-Token on state-changing demo calls.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import Folio from "@/components/editorial/Folio";
import SectionLabel from "@/components/editorial/SectionLabel";
import SyntheticBadge from "@/components/commerce/SyntheticBadge";
import { ApiError } from "@/lib/api";
import { Button, ButtonLink } from "@/components/ui/Button";
import Panel from "@/components/ui/Panel";
import { cn } from "@/lib/cn";
import { formatINR, formatTime } from "@/lib/format";
import {
  HERO_INTENT,
  useDemoOrchestrator,
  type DemoPaymentMode,
  type DemoStepRow,
} from "@/lib/useDemoOrchestrator";

/* ------------------------------------------------------------ small parts */

function RailBadge({ mode }: { mode: DemoPaymentMode | null | undefined }) {
  if (!mode) return null;
  return mode === "sandbox" ? (
    <span className="inline-flex items-center rounded-sm border border-rule bg-paper-bright px-1.5 py-[1px] font-mono text-[0.5625rem] uppercase tracking-[0.12em] leading-none text-ink-soft">
      SANDBOX
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-sm border border-success/40 bg-success/[0.07] px-1.5 py-[1px] font-mono text-[0.5625rem] uppercase tracking-[0.12em] leading-none text-success">
      <span aria-hidden={true} className="inline-block h-1 w-1 rounded-full bg-success" />
      RAZORPAY TEST MODE
    </span>
  );
}

const STATUS_GLYPH: Record<DemoStepRow["status"], { g: string; cls: string; label: string }> = {
  ok: { g: "✓", cls: "text-success", label: "done" },
  waiting: { g: "◔", cls: "text-warning", label: "waiting" },
  fail: { g: "✗", cls: "text-danger", label: "failed" },
  running: { g: "▸", cls: "animate-pulse text-signal", label: "running" },
  pending: { g: "·", cls: "text-ink-soft", label: "pending" },
};

/** One timestamped mono ticker row (#10: stamped once at each transition). */
function TickerRow({ s, rail }: { s: DemoStepRow; rail: DemoPaymentMode | null }) {
  const st = STATUS_GLYPH[s.status];
  const ts =
    s.status === "ok" || s.status === "fail"
      ? s.finishedAt ?? s.startedAt
      : s.startedAt;
  const dim = s.status === "pending";
  return (
    <li className="flex items-baseline gap-3 py-[3px]">
      <time className="w-[4.5rem] shrink-0 tabular text-ink-soft" dateTime={ts}>
        {ts ? formatTime(ts) : ""}
      </time>
      <span
        className={cn("w-4 shrink-0 text-center font-bold", st.cls)}
        aria-label={st.label}
        role="img"
      >
        {st.g}
      </span>
      <span className={cn("font-medium tracking-[0.04em]", dim && "text-ink-soft")}>
        {s.name}
        {s.money && (
          <span className="ml-2 inline-block align-middle">
            <RailBadge mode={rail} />
          </span>
        )}
      </span>
      {s.failureCode && (
        <span className="shrink-0 font-mono text-danger/80">[{s.failureCode}]</span>
      )}
      {s.detail && (
        <span
          className={cn(
            "min-w-0 break-all",
            s.status === "fail" ? "text-danger" : "text-ink-soft",
          )}
        >
          — {s.detail}
        </span>
      )}
    </li>
  );
}

/* --------------------------------------------------------------- the page */

export default function DemoPage() {
  const orch = useDemoOrchestrator();
  const reduceMotion = useReducedMotion();
  const logRef = useRef<HTMLOListElement>(null);

  // Auto-scroll the ticker as new rows land.
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [orch.steps.length]);

  const busy = orch.phase === "running" || orch.phase === "waiting_payment";
  const anyBusy = busy;

  /* --------------------------------------------------- manual controls */

  const [contractInput, setContractInput] = useState("");
  const [deliverScenario, setDeliverScenario] = useState<"correct" | "wrong_variant" | "late">(
    "wrong_variant",
  );
  const [manualFlash, setManualFlash] = useState<{ tone: "ok" | "fail"; text: string } | null>(
    null,
  );

  async function manual(path: string, body?: unknown, tag?: string) {
    setManualFlash(null);
    try {
      const res = await orch.operatorPost<Record<string, unknown>>(path, body);
      setManualFlash({
        tone: "ok",
        text: `${tag ?? path} → ${JSON.stringify(res).slice(0, 220)}`,
      });
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `${e.status ? `HTTP ${e.status}: ` : ""}${e.message}`
          : e instanceof Error
            ? e.message
            : String(e);
      setManualFlash({ tone: "fail", text: `${tag ?? path} → ${msg}` });
    }
  }

  return (
    <main className="min-h-screen bg-paper">
      {/* Warning strip */}
      <div className="border-b border-warning bg-warning/[0.08] px-6 py-3 md:px-10">
        <p className="folio-label text-warning flex flex-wrap items-center gap-2">
          <span className="rounded-sm border border-warning px-1.5 py-[2px]">DEMO SIMULATION CONTROL</span>
          <span className="normal-case tracking-normal">
            Fulfillment events are SYNTHETIC; payment/refund actions execute against
            Razorpay (sandbox adapter unless test keys configured).
          </span>
        </p>
      </div>

      <div className="dante-container py-8 md:py-12">
        <Folio issue="ISSUE 00 / CONTROL ROOM" running="PRIVATE PANEL / OPERATOR" />

        {/* ---------------------------------------------- hero arc header */}
        <header className="mt-8 grid grid-cols-1 gap-6 md:grid-cols-12">
          <div className="md:col-span-7">
            <SectionLabel>THE FIVE-MINUTE ARC · RESUMABLE</SectionLabel>
            <h1 className="mt-3 font-display text-5xl leading-[1.02] md:text-6xl">
              One click buys it, breaks it,
              <br />
              and makes it right.
            </h1>
            <p className="mt-4 max-w-prose text-sm leading-relaxed text-ink-soft">
              The hero scenario runs the entire thesis in fifteen recorded steps:
              intent → frozen promises → authorization → Razorpay order →{" "}
              <em className="not-italic text-warning">WAITING on your checkout</em> →
              capture → wrong variant delivered → material breach → rights evaluated →
              replacement unavailable → refund planned → policy ALLOW → refund executed.
              Refresh the page mid-arc and the console picks up where it left off.
            </p>
          </div>
          <div className="flex items-start gap-3 md:col-span-5 md:justify-end">
            {!anyBusy && (
              <Button onClick={orch.startNewRun} size="lg" data-testid="run-hero">
                ▶ RUN HERO SCENARIO
              </Button>
            )}
          </div>
        </header>

        {/* ------------------------------------------------- posture strip */}
        <section aria-label="Payment rail posture" className="mt-8">
          <Panel
            tone="bright"
            label="RAIL POSTURE"
            aside={
              orch.demoStatus ? (
                <RailBadge mode={orch.railMode} />
              ) : (
                <span className="folio-label text-ink-soft">probing…</span>
              )
            }
          >
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <div>
                <p className="text-sm leading-relaxed text-ink-soft">
                  {orch.railMode === "live-test-mode"
                    ? "Real Razorpay TEST MODE keys configured: checkout collects a real (non-money) test payment; refunds execute through Razorpay's test refund API."
                    : orch.railMode === "sandbox"
                      ? "No Razorpay keys configured: payments run on Dante's sandbox adapter via real signed webhooks — identical verification path, no gateway."
                      : "Posture unknown until the API answers."}
                </p>
                <dl className="mt-3 grid max-w-xs grid-cols-2 gap-x-4 gap-y-1 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-ink-soft">
                  <dt>Demo endpoints</dt>
                  <dd className={cn(orch.demoStatus?.demo_mode === false && "text-danger")}>
                    {orch.demoStatus?.demo_mode === false ? "DISABLED" : "enabled"}
                  </dd>
                  <dt>Operator token</dt>
                  <dd>{orch.tokenRequired ? "required" : "not required"}</dd>
                </dl>
                {orch.demoStatus?.operator_token_required &&
                  !orch.demoStatus.operator_token_configured && (
                    <p role="status" className="mt-2 font-mono text-xs text-warning">
                      Token gate armed but no token configured server-side — demo writes stay
                      LOCKED until DEMO_OPERATOR_TOKEN is set on the API.
                    </p>
                  )}
              </div>

              {/* operator token input */}
              <div>
                <label htmlFor="demo-operator-token" className="folio-label block">
                  OPERATOR TOKEN{" "}
                  <span className="normal-case tracking-normal">
                    {orch.tokenRequired ? "(automatic locally)" : "(optional)"} — never exposed by the local bridge
                  </span>
                </label>
                <input
                  id="demo-operator-token"
                  type="password"
                  autoComplete="off"
                  value={orch.operatorToken}
                  onChange={(e) => orch.setOperatorToken(e.target.value)}
                  placeholder={
                    orch.tokenRequired ? "optional manual override…" : "only needed when test keys are live"
                  }
                  className="mt-1 w-full rounded-md border border-rule bg-paper-bright px-3 py-2 font-mono text-xs text-ink outline-none focus:border-ink"
                />
                <p className="mt-1 text-xs leading-relaxed text-ink-soft">
                  Local development authorizes through a same-origin server bridge. A manual
                  override is sent as <code className="font-mono">X-Demo-Operator-Token</code>
                  and kept in this tab&apos;s session storage only.
                </p>
              </div>
            </div>
          </Panel>
        </section>

        {/* ------------------------------------------------------- flash */}
        {orch.flash && (
          <p
            role="alert"
            className={cn(
              "mt-6 break-all rounded-md border p-3 font-mono text-xs",
              orch.flash.tone === "ok"
                ? "border-success bg-success/[0.07] text-success"
                : "border-danger bg-danger/[0.06] text-danger",
            )}
          >
            {orch.flash.text}
            {orch.ctx.contractId && (
              <>
                {" "}
                <Link
                  href={`/contract/${orch.ctx.contractId}`}
                  className="underline underline-offset-4"
                >
                  open dossier →
                </Link>
              </>
            )}
          </p>
        )}

        {/* ------------------------------------------------ WAITING card */}
        {orch.waiting && (
          <section aria-label="Awaiting checkout completion" className="mt-6">
            <Panel
              tone="bright"
              className="border-warning"
              label={`STEP 6 / 15 · AWAITING CHECKOUT · ${orch.waiting.mode === "live-test-mode" ? "RAZORPAY TEST MODE" : "SANDBOX"}`}
              aside={<RailBadge mode={orch.waiting.mode} />}
            >
              <div className="grid grid-cols-1 gap-6 md:grid-cols-12">
                <div className="md:col-span-8">
                  <h2 className="font-display text-3xl leading-tight">
                    {orch.waiting.mode === "live-test-mode"
                      ? "Complete the real test-mode checkout."
                      : "Sandbox capture fired — or pay manually."}
                  </h2>
                  <p className="mt-2 max-w-prose text-sm leading-relaxed text-ink-soft">
                    {orch.waiting.mode === "live-test-mode" ? (
                      <>
                        Open the contract page and pay order{" "}
                        <code className="font-mono">{orch.waiting.orderId ?? "—"}</code> through
                        Razorpay Checkout. This console polls server-side webhook truth every 2s
                        and continues steps 7–15 the moment the contract reads PAID.
                      </>
                    ) : (
                      <>
                        A signed-webhook capture was already fired for order{" "}
                        <code className="font-mono">{orch.waiting.orderId ?? "—"}</code>. If you
                        want the human-in-the-loop path instead, pay on the contract page — the
                        first PAID reading wins either way.
                      </>
                    )}
                  </p>
                  <ul className="mt-3 space-y-1 font-mono text-xs text-ink-soft">
                    <li>
                      contract <span className="text-ink">{orch.waiting.contractId}</span>
                    </li>
                    <li>
                      amount{" "}
                      <span className="text-ink">
                        {formatINR(orch.waiting.amountPaise)}
                      </span>
                    </li>
                    <li>polling GET /api/contracts/&#123;id&#125; every 2s · webhook truth only</li>
                  </ul>
                </div>
                <div className="md:col-span-4 md:text-right">
                  <ButtonLink
                    href={`/contract/${orch.waiting.contractId}`}
                    size="lg"
                    data-testid="open-contract-pay"
                  >
                    OPEN CONTRACT &amp; PAY →
                  </ButtonLink>
                  <p className="mt-2 text-xs text-ink-soft">
                    /contract/{orch.waiting.contractId.slice(0, 14)}…
                  </p>
                </div>
              </div>
            </Panel>
          </section>
        )}

        {/* --------------------------------------------------- ticker */}
        {orch.steps.length > 0 && (
          <motion.ol
            ref={logRef}
            initial={reduceMotion ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            aria-label="Hero scenario step log"
            data-testid="hero-ticker"
            className="mt-6 max-h-96 overflow-y-auto rounded-md border border-rule bg-paper-bright p-4 font-mono text-xs leading-relaxed"
          >
            {orch.steps.map((s, i) => (
              <TickerRow
                key={s.key}
                s={{ ...s, name: `${String(i + 1).padStart(2, "0")} ${s.name}` }}
                rail={orch.railMode}
              />
            ))}
          </motion.ol>
        )}

        {/* ------------------------------------------- halted retry strip */}
        {orch.failedIndex != null && orch.phase === "halted" && (
          <section aria-label="Retry failed step" className="mt-4 flex flex-wrap items-center gap-3">
            <Button variant="secondary" size="sm" onClick={orch.retryFailedStep}>
              ↻ RETRY STEP {orch.failedIndex + 1}/15 ONLY
            </Button>
            <p className="font-mono text-xs text-ink-soft">
              resume semantics: completed steps stay checked; the chain re-enters at the failed
              row.
            </p>
          </section>
        )}

        {/* -------------------------------------------------- run controls */}
        {orch.steps.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <span className="font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-ink-soft">
              run {orch.runId ?? "—"} · phase {orch.phase}
            </span>
            {!busy && (
              <Button variant="ghost" size="sm" onClick={orch.clearConsole}>
                clear console &amp; forget run
              </Button>
            )}
            {!busy && orch.runId && orch.phase !== "complete" && (
              <Button variant="secondary" size="sm" onClick={orch.startNewRun}>
                start fresh run
              </Button>
            )}
          </div>
        )}

        {/* --------------------------------------------- manual controls */}
        <section className="mt-14" aria-label="Manual demo controls">
          <SectionLabel>MANUAL CONTROLS</SectionLabel>
          <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Panel label="SEED" aside={<SyntheticBadge synthetic />}>
              <p className="text-sm leading-relaxed text-ink-soft">
                Reset catalog + stores to fixture seed. Wipes every contract (and any
                persisted orchestrator run).
              </p>
              <div className="mt-3">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={anyBusy}
                  data-testid="manual-reset"
                  onClick={() => {
                    orch.clearConsole();
                    void manual("/api/demo/reset", undefined, "POST /demo/reset");
                  }}
                >
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
                  onClick={() =>
                    manual(
                      `/api/demo/contracts/${contractInput.trim()}/ship`,
                      undefined,
                      "ship",
                    )
                  }
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
                    manual(
                      `/api/demo/contracts/${contractInput.trim()}/deliver`,
                      { scenario: deliverScenario },
                      `deliver:${deliverScenario}`,
                    )
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
                    manual(
                      `/api/demo/contracts/${contractInput.trim()}/replacement-unavailable`,
                      undefined,
                      "replacement-unavailable",
                    )
                  }
                >
                  Mark replacement unavailable
                </Button>
              </div>

              {manualFlash && (
                <p
                  role="status"
                  className={cn(
                    "mt-3 break-all rounded-md border p-2 font-mono text-[0.6875rem]",
                    manualFlash.tone === "ok"
                      ? "border-success bg-success/[0.07] text-success"
                      : "border-danger bg-danger/[0.06] text-danger",
                  )}
                >
                  {manualFlash.text}
                </p>
              )}
            </Panel>
          </div>
        </section>

        {/* --------------------------------------------------- brief note */}
        <section className="mt-10 border-t border-rule pt-4" aria-label="Hero brief">
          <SectionLabel>THE COMPILED BRIEF</SectionLabel>
          <p className="mt-2 max-w-prose font-mono text-xs leading-relaxed text-ink-soft">
            “{HERO_INTENT}”
          </p>
        </section>
      </div>
    </main>
  );
}
