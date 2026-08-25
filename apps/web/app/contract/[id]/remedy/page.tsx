"use client";

/**
 * /contract/[id]/remedy — ranked remedy candidates, score bars
 * (value 0.40 / intent-restoration 0.35 / speed 0.15 / inconvenience -0.10),
 * rejected alternatives with reasons, then the gated money-action panel:
 * policy decision → [Approve] when required → Execute → refund id +
 * REMEDIATED success state (plan §28 + §52).
 *
 * Agent I.
 */

import { useParams } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import Folio from "@/components/editorial/Folio";
import SectionLabel from "@/components/editorial/SectionLabel";
import Badge from "@/components/commerce/Badge";
import MoneyText from "@/components/commerce/MoneyText";
import SandboxBadge from "@/components/commerce/SandboxBadge";
import { Button } from "@/components/ui/Button";
import Panel from "@/components/ui/Panel";
import { apiGet, apiPost } from "@/lib/api";
import type {
  ApproveResponse,
  ContractResponse,
  ExecuteResponse,
  MoneyAction,
  PolicyDecision,
  PolicyResponse,
  RemediesResponse,
  RemedyProposal,
} from "@/lib/rights-ui";
import { prettyJson } from "@/lib/format";
import { cn } from "@/lib/cn";

/* Score-bar weights straight from plan §14.2 — the visible scoring fn. */
const WEIGHTS = [
  { key: "value", label: "Buyer value", weight: 0.4 },
  { key: "intent", label: "Intent restoration", weight: 0.35 },
  { key: "speed", label: "Speed", weight: 0.15 },
] as const;
const INCONVENIENCE_WEIGHT = -0.1;

type Phase = "idle" | "policy" | "approval" | "executing" | "done" | "error";

export default function RemedyPage() {
  const params = useParams<{ id: string }>();
  const contractId = params.id;

  const [proposals, setProposals] = useState<RemedyProposal[] | null>(null);
  const [sandbox, setSandbox] = useState<boolean | null>(null);
  const [contractStatus, setContractStatus] = useState<string | null>(null);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [decision, setDecision] = useState<PolicyDecision | null>(null);
  const [moneyAction, setMoneyAction] = useState<MoneyAction | null>(null);
  const [refund, setRefund] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    let alive = true;
    apiGet<RemediesResponse>(`/api/contracts/${contractId}/remedies`)
      .then((d) => {
        if (!alive) return;
        const ranked = [...(d.proposals ?? [])].sort(
          (a, b) => (a.rank ?? 99) - (b.rank ?? 99)
        );
        setProposals(ranked);
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : "failed to load remedies"));
    apiGet<ContractResponse>(`/api/contracts/${contractId}`)
      .then((d) => {
        if (!alive) return;
        setSandbox(!!d.contract.sandbox_mode);
        setContractStatus(d.contract.status);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [contractId]);

  /* -------------------------------------------------- pipeline actions */

  const runPolicy = useCallback(
    async (proposalId: string) => {
      setPhase("policy");
      setError(null);
      try {
        const res = await apiPost<PolicyResponse>(
          `/api/remedies/${proposalId}/policy`
        );
        setDecision(res.decision ?? null);
        setMoneyAction(res.money_action ?? null);

        if (res.decision?.decision === "ALLOW") {
          // Auto-approved by policy — proceed to execution immediately.
          await execute(proposalId);
        } else if (res.decision?.decision === "REQUIRE_APPROVAL") {
          setPhase("approval");
        } else {
          setPhase("error");
          setError(
            res.decision
              ? `DENIED: ${(res.decision.reason_codes ?? []).join(", ") || res.decision.explanation}`
              : "no decision returned"
          );
        }
      } catch (e) {
        setPhase("error");
        setError(e instanceof Error ? e.message : "policy evaluation failed");
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const approveThenExecute = useCallback(
    async (proposalId: string) => {
      setPhase("executing");
      setError(null);
      try {
        await apiPost<ApproveResponse>(`/api/remedies/${proposalId}/approve`);
        await execute(proposalId);
      } catch (e) {
        setPhase("error");
        setError(e instanceof Error ? e.message : "approval failed");
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const execute = useCallback(
    async (proposalId: string) => {
      setPhase("executing");
      try {
        const res = await apiPost<ExecuteResponse>(
          `/api/remedies/${proposalId}/execute`
        );
        if (res.error && !res.money_action) {
          setPhase("error");
          setError(res.error);
          return;
        }
        setMoneyAction(res.money_action ?? null);
        setRefund(res.refund ?? null);
        if (res.money_action?.result_ref) {
          setRefund((r) => r ?? { id: res.money_action!.result_ref });
        }
        setPhase("done");
        setContractStatus("REMEDIATED");
      } catch (e) {
        setPhase("error");
        setError(e instanceof Error ? e.message : "execution failed");
      }
    },
    []
  );

  const busy = phase === "policy" || phase === "executing";
  const chosen = proposals?.find((p) => p.id === chosenId) ?? proposals?.[0];

  return (
    <main className="dante-container py-8 md:py-12">
      <Folio
        issue="ISSUE 06 / REMEDY"
        running={`DOSSIER / ${contractId.slice(0, 13).toUpperCase()}`}
        href={`/contract/${contractId}`}
      />

      <header className="mt-8 grid grid-cols-1 gap-6 md:grid-cols-12">
        <div className="md:col-span-8">
          <SectionLabel>THE REMEDY PLANNER</SectionLabel>
          <h1 className="mt-3 font-display text-5xl leading-[1.02] md:text-6xl">
            Making it right,
            <br />
            by the numbers.
          </h1>
          <p className="mt-4 max-w-prose text-sm leading-relaxed text-ink-soft">
            Candidates are ranked by a visible scoring function — buyer value
            0.40, intent restoration 0.35, speed 0.15, inconvenience −0.10.
            Money moves only after the deterministic policy engine speaks.
          </p>
        </div>
        <div className="flex flex-col items-start gap-2 md:col-span-4 md:items-end">
          {contractStatus && <Badge>{contractStatus}</Badge>}
          {sandbox !== null && <SandboxBadge sandbox={sandbox} />}
        </div>
      </header>

      {error && phase !== "error" && (
        <p role="alert" className="mt-8 border-l-2 border-danger pl-3 font-mono text-xs text-danger">
          {error}
        </p>
      )}

      {!proposals && !error && (
        <p className="mt-10 font-mono text-xs uppercase tracking-[0.14em] text-ink-soft">
          Planning remedies…
        </p>
      )}

      {proposals && proposals.length === 0 && (
        <Panel className="mt-10" tone="bright" label="NO PROPOSALS">
          <p className="text-sm text-ink-soft">
            The planner has no remedy candidates for this contract yet. Trigger a
            breach first on the{" "}
            <Link href={`/demo`} className="underline underline-offset-4 hover:text-signal">
              demo console
            </Link>{" "}
            or check back after verification.
          </p>
        </Panel>
      )}

      {/* Ranked candidates */}
      {proposals && proposals.length > 0 && (
        <ol className="mt-10 space-y-6">
          {proposals.map((p) => {
            const isChosen = chosen ? p.id === chosen.id : false;
            return (
              <li key={p.id}>
                <article
                  className={cn(
                    "rounded-lg border bg-paper-bright",
                    isChosen && !p.rejected_reason
                      ? "border-success"
                      : p.rejected_reason
                        ? "border-rule"
                        : "border-rule"
                  )}
                >
                  <header className="flex flex-wrap items-baseline justify-between gap-3 border-b border-rule px-5 py-3">
                    <div className="flex items-baseline gap-3">
                      <span className="tabular font-display text-4xl leading-none text-ink">
                        #{p.rank ?? "—"}
                      </span>
                      <h2 className="font-display text-2xl leading-tight">
                        {titleCase(p.remedy_type)}
                      </h2>
                      {isChosen && !p.rejected_reason && (
                        <Badge tone="success">SELECTED</Badge>
                      )}
                    </div>
                    <MoneyText paise={p.amount_paise} size="lg" />
                  </header>

                  <div className="grid grid-cols-1 gap-6 px-5 py-5 md:grid-cols-2">
                    {/* score breakdown bars */}
                    <div aria-label="Score breakdown">
                      <ScoreBar label="Value" weight={0.4} value={clamp01(p.expected_buyer_value)} />
                      <ScoreBar label="Intent restoration" weight={0.35} value={clamp01(p.confidence)} />
                      <ScoreBar
                        label="Speed"
                        weight={0.15}
                        value={speedScore(p.estimated_time_hours)}
                      />
                      <ScoreBar
                        label="Inconvenience"
                        weight={INCONVENIENCE_WEIGHT}
                        value={clamp01(p.inconvenience_score)}
                        penalty
                      />
                    </div>

                    <div className="min-w-0 space-y-3">
                      <p className="text-sm leading-relaxed text-ink-soft">{p.explanation}</p>
                      {p.rejected_reason && (
                        <p className="border-l-2 border-signal pl-3 font-mono text-xs uppercase tracking-[0.1em] text-signal-deep">
                          REJECTED: {p.rejected_reason}
                        </p>
                      )}
                      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[0.6875rem] text-ink-soft">
                        <div>
                          <dt className="inline">est time </dt>
                          <dd className="inline tabular text-ink">
                            {p.estimated_time_hours} h
                          </dd>
                        </div>
                        <div>
                          <dt className="inline">confidence </dt>
                          <dd className="inline tabular text-ink">
                            {Math.round((p.confidence ?? 0) * 100)}%
                          </dd>
                        </div>
                      </dl>
                    </div>
                  </div>

                  {/* Action row for the selected candidate */}
                  {!p.rejected_reason && isChosen && (
                    <footer className="border-t border-rule px-5 py-4">
                      {phase === "done" && moneyAction?.status === "executed" ? (
                        <SuccessState
                          refund={refund}
                          moneyAction={moneyAction}
                        />
                      ) : (
                        <>
                          {decision ? (
                            <PolicyVerdict decision={decision} moneyAction={moneyAction} />
                          ) : (
                            <p className="folio-label">
                              AWAITING POLICY VERDICT — NO MONEY MOVES UNTIL THE ENGINE SPEAKS
                            </p>
                          )}
                          <div className="mt-4 flex flex-wrap items-center gap-3">
                            <Button
                              onClick={() => runPolicy(p.id)}
                              disabled={busy || phase === "done"}
                              size="sm"
                            >
                              {phase === "policy" ? "Evaluating policy…" : "Evaluate policy"}
                            </Button>

                            {phase === "approval" && (
                              <Button
                                variant="secondary"
                                size="sm"
                                disabled={busy}
                                onClick={() => approveThenExecute(p.id)}
                              >
                                {`Approve ${p.remedy_type === "refund_full" ? "refund" : "action"}`}
                              </Button>
                            )}

                            {moneyAction && phase !== "done" && (
                              <span className="font-mono text-[0.625rem] uppercase tracking-[0.12em] text-ink-soft">
                                idem key{" "}
                                <code className="break-all normal-case tracking-normal text-ink">
                                  {moneyAction.idempotency_key}
                                </code>
                              </span>
                            )}
                          </div>

                          {moneyAction && (
                            <details className="mt-3">
                              <summary className="cursor-pointer folio-label">
                                Planned money action · {moneyAction.type}
                              </summary>
                              <pre className="mt-2 overflow-x-auto rounded-md border border-rule bg-paper-bright p-3 font-mono text-[0.6875rem]">
                                {prettyJson(moneyAction)}
                              </pre>
                            </details>
                          )}
                        </>
                      )}
                    </footer>
                  )}
                </article>
              </li>
            );
          })}
        </ol>
      )}
    </main>
  );

  /* ------------------------------------------------ local subcomponents */

  function PolicyVerdict({
    decision: d,
    moneyAction: ma,
  }: {
    decision: PolicyDecision;
    moneyAction: MoneyAction | null;
  }) {
    const tone =
      d.decision === "ALLOW"
        ? "border-success bg-success/[0.07]"
        : d.decision === "DENY"
          ? "border-danger bg-danger/[0.06]"
          : "border-warning bg-warning/[0.08]";
    return (
      <motion.div
        initial={reduceMotion ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        className={cn("rounded-md border p-4", tone)}
        role="status"
      >
        <p className="font-display text-xl leading-tight text-ink">
          {d.decision === "ALLOW"
            ? `AUTO-APPROVED BY POLICY ${(d.policy_ids ?? []).join(" · ") || "P"}`
            : d.decision === "REQUIRE_APPROVAL"
              ? "MANUAL APPROVAL REQUIRED"
              : "DENIED BY POLICY"}
        </p>
        {(d.reason_codes ?? []).length > 0 && (
          <p className="mt-1 font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-ink-soft">
            {(d.reason_codes ?? []).join(" · ")}
          </p>
        )}
        {d.explanation && (
          <p className="mt-2 text-sm leading-relaxed text-ink-soft">{d.explanation}</p>
        )}
        {ma?.human_explanation && ma.human_explanation !== d.explanation && (
          <p className="mt-2 border-l-2 border-rule pl-3 text-sm leading-relaxed text-ink-soft">
            {ma.human_explanation}
          </p>
        )}
        {ma && (
          <p className="mt-2 break-all font-mono text-[0.5625rem] uppercase tracking-[0.12em] text-ink-soft">
            reason_code {ma.reason_code} · snapshot {shortHashOf(d.policy_snapshot_hash)}
          </p>
        )}
      </motion.div>
    );
  }

  function SuccessState({
    refund: rf,
    moneyAction: ma,
  }: {
    refund: Record<string, unknown> | null;
    moneyAction: MoneyAction | null;
  }) {
    const refundId =
      (rf && typeof rf.id === "string" && rf.id) ||
      rf?.["id"] ||
      ma?.result_ref ||
      null;
    return (
      <motion.div
        initial={reduceMotion ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.5 }}
        className="rounded-md border border-success bg-success/[0.07] p-5"
        role="status"
      >
        <p className="font-display text-3xl leading-tight text-success">
          REMEDIATED — refund resolved green.
        </p>
        <div className="mt-4 grid grid-cols-1 gap-3 text-sm md:grid-cols-2">
          <div>
            <p className="folio-label">REFUND ID (RESULT_REF)</p>
            <code className="break-all font-mono text-xs text-ink">
              {(refundId as string) ?? "pending…"}
            </code>
          </div>
          {ma && (
            <>
              <div>
                <p className="folio-label">AMOUNT</p>
                <MoneyText paise={ma.amount_paise} size="sm" precise />
              </div>
              <div>
                <p className="folio-label">REASON CODE</p>
                <code className="font-mono text-xs text-ink">{ma.reason_code}</code>
              </div>
              <div>
                <p className="folio-label">HUMAN EXPLANATION</p>
                <p className="text-sm leading-relaxed text-ink-soft">
                  {ma.human_explanation}
                </p>
              </div>
            </>
          )}
        </div>
        <Link
          href={`/audit/${contractId}`}
          className="folio-label mt-4 inline-block underline underline-offset-4 hover:text-signal"
        >
          Inspect the full audit dossier →
        </Link>
      </motion.div>
    );
  }
}

/* ------------------------------------------------------------ helpers */

function ScoreBar({
  label,
  weight,
  value,
  penalty = false,
}: {
  label: string;
  weight: number;
  value: number;
  penalty?: boolean;
}) {
  const widthPct = Math.round(clamp01(Math.abs(value)) * 100);
  return (
    <div className="mb-2.5 last:mb-0" role="img" aria-label={`${label}: ${widthPct}% of weight ${weight}`}>
      <div className="flex items-baseline justify-between font-mono text-[0.625rem] uppercase tracking-[0.12em] text-ink-soft">
        <span>{label}</span>
        <span className="tabular">
          {weight >= 0 ? "+" : ""}
          {weight.toFixed(2)} × {value.toFixed(2)}
        </span>
      </div>
      <div className="mt-1 h-2 w-full overflow-hidden rounded-sm bg-paper">
        <div
          className={cn("h-full rounded-sm", penalty ? "bg-danger/60" : "bg-ink")}
          style={{ width: `${penalty ? Math.min(100, widthPct / 2) : widthPct}%` }}
        />
      </div>
    </div>
  );
}

function clamp01(n: number | null | undefined): number {
  if (n == null || Number.isNaN(n)) return 0;
  return Math.max(0, Math.min(1, n));
}

/** Speed score decays with estimated hours; instant ≈ 1, ≥72h ≈ 0. */
function speedScore(hours: number | null | undefined): number {
  if (hours == null || hours <= 0) return 1;
  return clamp01(1 - hours / 72);
}

function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function shortHashOf(h?: string): string {
  if (!h) return "—";
  return h.length <= 12 ? h : `${h.slice(0, 12)}…`;
}
