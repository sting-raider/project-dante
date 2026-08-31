"use client";

/**
 * useDemoOrchestrator — resumable 15-step hero-arc state machine (plan §20).
 *
 * Drives the /demo control room: intent compiled → merchant queried →
 * contract frozen → buyer authorized → order created →
 * [WAITING: buyer completes checkout on /contract/{id}] → payment captured →
 * synthetic shipment → wrong variant delivered → material breach →
 * rights evaluated → replacement unavailable → refund selected →
 * policy ALLOW → refund executed / contract REMEDIATED.
 *
 * Resumability: the whole run record (run id, context ids, per-step statuses
 * and once-stamped timestamps) persists to sessionStorage keyed by the
 * orchestrator run id; a pointer key names the current run. A page refresh
 * rehydrates — completed steps render their historical checkmarks verbatim
 * (never re-derived, never re-fired), a WAITING run resumes its 2s
 * server-truth poll, a RUNNING run resumes at the first non-completed step,
 * and a HALTED run keeps the failed row's retry affordance (retry re-runs
 * ONLY the failed step, then continues the chain).
 *
 * Operator gating (apps/api/project_dante/api/routes/demo.py): with real
 * rzp_test_* keys configured, state-changing /api/demo/* endpoints demand a
 * matching X-Demo-Operator-Token header (settings.demo_operator_token; an
 * empty configured token keeps them LOCKED). In local development, a narrow
 * same-origin Next.js bridge reads the existing server-side token and adds it
 * only to allowlisted operator calls. A manually entered override is attached
 * directly and remembered in sessionStorage for the tab.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiGet, apiPost, apiTry, appPost } from "@/lib/api";
import { formatINR } from "@/lib/format";
import type {
  ApproveResponse,
  Breach,
  ContractResponse,
  ExecuteResponse,
  PolicyResponse,
  RemediesResponse,
  RightsResponse,
} from "@/lib/rights-ui";

/* ------------------------------------------------------------------ types */

export type DemoPaymentMode = "sandbox" | "live-test-mode";

/** Canonical 15 steps, in execution order (plan §20). */
export type DemoStepKey =
  | "intent_compiled"
  | "merchant_queried"
  | "contract_frozen"
  | "buyer_authorized"
  | "order_created"
  | "checkout_waiting"
  | "payment_captured"
  | "synthetic_shipment"
  | "wrong_variant_delivered"
  | "material_breach"
  | "rights_evaluated"
  | "replacement_unavailable"
  | "refund_selected"
  | "policy_allow"
  | "refund_executed";

export type DemoStepStatus = "pending" | "running" | "ok" | "waiting" | "fail";

export type DemoPhase =
  | "idle"
  | "running"
  | "waiting_payment"
  | "halted"
  | "complete";

/** One ticker row. Timestamps are ISO strings stamped once at transition. */
export interface DemoStepRow {
  key: DemoStepKey;
  name: string;
  status: DemoStepStatus;
  /** True when the step moves/verifies money — renders the rail badge. */
  money?: boolean;
  detail?: string;
  startedAt?: string;
  finishedAt?: string;
  /** Stable failure reason code (e.g. E_POLICY) shown beside the message. */
  failureCode?: string;
}

/** Ids threaded through the arc; persisted so a refresh can resume. */
export interface DemoCtx {
  intentId?: string;
  offerId?: string;
  offerTitle?: string;
  contractId?: string;
  orderId?: string;
  amountPaise?: number | null;
  paymentId?: string;
  proposalId?: string;
  remedyType?: string;
  /** Rail captured from the payment-order response (authoritative per run). */
  mode?: DemoPaymentMode;
}

type DemoStatusResponse = {
  demo_mode: boolean;
  razorpay_mode: DemoPaymentMode;
  operator_token_required: boolean;
  operator_token_configured: boolean;
};

/* --------------------------------------------------- persisted run record */

const RECORD_VERSION = 1;
const CURRENT_RUN_KEY = "dante.demo.orchestrator.current";
const TOKEN_KEY = "dante.demo.operatorToken";
const POLL_INTERVAL_MS = 2000;
/** Consecutive failed polls tolerated before the wait step halts (~30s). */
const MAX_POLL_FAILURES = 15;

/** Index of the WAITING gate step inside the canonical 15. */
const WAIT_IDX = 5;

interface PersistedRun {
  v: number;
  runId: string;
  startedAt: string;
  phase: DemoPhase;
  ctx: DemoCtx;
  steps: DemoStepRow[];
}

function storeKey(runId: string): string {
  return `dante.demo.orchestrator.${runId}`;
}

function persistRec(rec: PersistedRun): void {
  try {
    window.sessionStorage.setItem(storeKey(rec.runId), JSON.stringify(rec));
    window.sessionStorage.setItem(CURRENT_RUN_KEY, rec.runId);
  } catch {
    /* storage unavailable — the run simply won't survive a refresh */
  }
}

function readCurrentRun(): PersistedRun | null {
  try {
    const rid = window.sessionStorage.getItem(CURRENT_RUN_KEY);
    if (!rid) return null;
    const raw = window.sessionStorage.getItem(storeKey(rid));
    if (!raw) return null;
    const rec = JSON.parse(raw) as PersistedRun;
    if (rec?.v !== RECORD_VERSION || !Array.isArray(rec.steps)) return null;
    return rec;
  } catch {
    return null;
  }
}

/* --------------------------------------------------------- api shapes */

type CompileResponse = { intent: { id: string }; engine?: string };
type SearchResponse = {
  results: {
    offer: { id: string; title: string; unit_amount_paise: number };
    evaluation: { feasible: boolean };
  }[];
};
type SelectResponse = { contract: { id: string; status: string; amount_paise?: number | null } };
type AuthorizeResponse = { contract: { id: string; status: string } };
type OrderResponse = {
  mode: DemoPaymentMode;
  checkout_config?: { order_id: string; amount_paise: number };
  razorpay_order?: Record<string, unknown>;
};
type DeliverResponse = {
  breaches?: unknown[];
  status?: string | null;
};

/* ------------------------------------------------------------- utilities */

function nowIso(): string {
  return new Date().toISOString();
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    return `${e.status ? `HTTP ${e.status}: ` : ""}${e.message}`;
  }
  return e instanceof Error ? e.message : String(e);
}

function shortId(id: string | null | undefined): string {
  return id ? `${id.slice(0, 12)}…` : "—";
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Module-level engine lock: guarantees a single active chain even through
 * React StrictMode's double-mounted effects in dev — a second resume attempt
 * is a no-op while one engine loop (or wait poll continuation) is alive.
 */
let ENGINE_LOCK = false;

/* ----------------------------------------------------------- the hook */

export function useDemoOrchestrator() {
  const [steps, setSteps] = useState<DemoStepRow[]>([]);
  const [phase, setPhase] = useState<DemoPhase>("idle");
  const [ctx, setCtx] = useState<DemoCtx>({});
  const [runId, setRunId] = useState<string | null>(null);
  const [flash, setFlash] = useState<{ tone: "ok" | "fail"; text: string } | null>(null);
  const [demoStatus, setDemoStatus] = useState<DemoStatusResponse | null>(null);
  const [operatorToken, setOperatorTokenState] = useState("");

  // Authoritative engine state lives in refs; React state mirrors are pushed
  // by update() so async chains never race stale setState closures.
  const recRef = useRef<PersistedRun | null>(null);
  const mountedRef = useRef(true);
  const waitTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const waitFailuresRef = useRef(0);
  const tokenRef = useRef("");
  /** Sandbox orders this hook instance has already fired a simulate for
   * (StrictMode's double-mount shares refs, so the guard also prevents the
   * double-mount from minting two captures; a real refresh gets fresh refs
   * and re-fires deliberately — server truth decides the rest). */
  const captureFiredRef = useRef<Set<string>>(new Set());

  /* ------------------------------------------------------ state plumbing */

  const update = useCallback((mut: (r: PersistedRun) => void) => {
    const r = recRef.current;
    if (!r) return;
    mut(r);
    persistRec(r);
    if (mountedRef.current) {
      setSteps(r.steps.map((s) => ({ ...s })));
      setPhase(r.phase);
      setCtx({ ...r.ctx });
    }
  }, []);

  const stopWaitPoll = useCallback(() => {
    if (waitTimerRef.current) {
      clearInterval(waitTimerRef.current);
      waitTimerRef.current = null;
    }
  }, []);

  const setOperatorToken = useCallback((t: string) => {
    tokenRef.current = t;
    setOperatorTokenState(t);
    try {
      window.sessionStorage.setItem(TOKEN_KEY, t);
    } catch {
      /* non-fatal */
    }
  }, []);

  /** Header for STATE-CHANGING /api/demo/* calls; empty when no token. */
  const opHeaders = useCallback((): Record<string, string> => {
    const t = tokenRef.current.trim();
    return t ? { "x-demo-operator-token": t } : {};
  }, []);

  /**
   * Operator-scoped POST. An explicitly entered token goes straight to the
   * API; otherwise local development uses the same-origin server bridge so
   * the secret never enters browser JavaScript.
   */
  const operatorPost = useCallback(
    <T,>(path: string, body?: unknown): Promise<T> => {
      const headers = opHeaders();
      if (headers["x-demo-operator-token"]) {
        return apiPost<T>(path, body, { headers });
      }
      const operatorPath = path.replace(/^\/api\//, "");
      return appPost<T>(`/api/operator/${operatorPath}`, body);
    },
    [opHeaders],
  );

  /* ------------------------------------------------------- step definitions */

  const stepDefs = useMemo(() => buildStepDefs(operatorPost), [operatorPost]);

  const makeRows = useCallback((): DemoStepRow[] => {
    return stepDefs.map((d) => ({
      key: d.key,
      name: d.name,
      status: "pending" as const,
      ...(d.money ? { money: true } : {}),
    }));
  }, [stepDefs]);

  /* ------------------------------------------------------------ halting */

  const haltStep = useCallback(
    (idx: number, code: string, msg: string) => {
      update((r) => {
        Object.assign(r.steps[idx], {
          status: "fail",
          detail: msg,
          failureCode: code,
          finishedAt: nowIso(),
        });
        r.phase = "halted";
      });
      setFlash({
        tone: "fail",
        text: `HALTED AT STEP ${idx + 1}/15 "${stepDefs[idx]?.name ?? "?"}" — ${code}: ${msg}`,
      });
    },
    [stepDefs, update],
  );

  /* ------------------------------------------------- WAITING gate + poll */

  const fireSandboxCapture = useCallback(async (): Promise<void> => {
    const orderId = recRef.current?.ctx.orderId;
    if (!orderId || captureFiredRef.current.has(orderId)) return;
    captureFiredRef.current.add(orderId);
    try {
      // Sandbox rail: mint the capture Razorpay's own gateway would have sent
      // — a REAL signed webhook through the same verification pipeline.
      await operatorPost("/api/demo/razorpay/simulate-event", {
        event_type: "payment.captured",
        order_id: orderId,
        payment_id: `pay_sbx_${Date.now().toString(36)}`,
      });
    } catch (e) {
      // Never fatal — the buyer can still complete payment on the contract
      // page; the poll keeps watching for server truth either way. Allow a
      // future re-entry to re-fire (transient failures shouldn't burn it).
      captureFiredRef.current.delete(orderId);
      update((r) => {
        const w = r.steps[WAIT_IDX];
        w.detail = `${w.detail ?? ""} · auto-simulate refused (${errMsg(e)}) — complete payment on the contract page`;
      });
    }
  }, [operatorPost, update]);

  const completeWait = useCallback(
    (waitIdx: number, contract: ContractResponse["contract"]) => {
      stopWaitPoll();
      update((r) => {
        const w = r.steps[waitIdx];
        w.status = "ok";
        w.startedAt = w.startedAt ?? nowIso();
        w.finishedAt = nowIso();
        w.detail = `server truth PAID · payment ${contract.razorpay_payment_id ?? "confirmed"}`;
        const cap = r.steps[waitIdx + 1];
        cap.status = "ok";
        cap.startedAt = cap.startedAt ?? nowIso();
        cap.finishedAt = nowIso();
        cap.detail =
          `captured ${formatINR(contract.amount_paise)} · confirmed by ` +
          `${r.ctx.mode === "sandbox" ? "signed sandbox webhook" : "Razorpay test-mode webhook"}`;
        if (contract.razorpay_payment_id) r.ctx.paymentId = contract.razorpay_payment_id;
        if (contract.amount_paise != null) r.ctx.amountPaise = contract.amount_paise;
        if (!r.ctx.mode && contract.sandbox_mode != null) {
          r.ctx.mode = contract.sandbox_mode ? "sandbox" : "live-test-mode";
        }
      });
      void runFrom(waitIdx + 2);
    },
    // runFrom is declared below; the engine only ever calls this at runtime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [stopWaitPoll, update],
  );

  const pollTick = useCallback(
    async (waitIdx: number, myRun: string) => {
      const cid = recRef.current?.ctx.contractId;
      if (!cid) return;
      if (recRef.current?.runId !== myRun) {
        stopWaitPoll();
        return;
      }
      try {
        const detail = await apiGet<ContractResponse>(`/api/contracts/${cid}`);
        waitFailuresRef.current = 0;
        const c = detail.contract;
        if (c.status === "PAID") {
          completeWait(waitIdx, c);
        }
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          stopWaitPoll();
          haltStep(waitIdx, "E_CHECKOUT_WAIT", `contract vanished: ${errMsg(e)}`);
          return;
        }
        // Transient network/5xx blips tolerate themselves; only a sustained
        // outage halts the run (with a retryable failed step).
        waitFailuresRef.current += 1;
        if (waitFailuresRef.current >= MAX_POLL_FAILURES) {
          stopWaitPoll();
          haltStep(
            waitIdx,
            "E_POLL",
            `contract unreachable after ${MAX_POLL_FAILURES} polls: ${errMsg(e)}`,
          );
        }
      }
    },
    [completeWait, haltStep, stopWaitPoll],
  );

  const beginWaitPoll = useCallback(
    (waitIdx: number) => {
      stopWaitPoll();
      waitFailuresRef.current = 0;
      const myRun = recRef.current?.runId ?? "";
      waitTimerRef.current = setInterval(() => {
        void pollTick(waitIdx, myRun);
      }, POLL_INTERVAL_MS);
    },
    [pollTick, stopWaitPoll],
  );

  const enterWaiting = useCallback(
    async (waitIdx: number, resumed: boolean) => {
      const cid = recRef.current?.ctx.contractId;
      if (!cid) {
        haltStep(waitIdx, "E_CHECKOUT_WAIT", "no contract id in run context");
        return;
      }
      const mode = recRef.current?.ctx.mode;
      const posture =
        mode === "live-test-mode"
          ? "RAZORPAY TEST MODE — complete the real checkout on the contract page; webhook truth continues the arc"
          : mode === "sandbox"
            ? "SANDBOX rail — signed-webhook capture fired; polling for server-truth PAID"
            : "awaiting checkout completion — polling for server-truth PAID";
      update((r) => {
        r.phase = "waiting_payment";
        Object.assign(r.steps[waitIdx], {
          status: "waiting",
          startedAt: r.steps[waitIdx].startedAt ?? nowIso(),
          finishedAt: undefined,
          failureCode: undefined,
          detail: posture,
        });
      });

      if (resumed) {
        // Refresh resume: check server truth once before re-arming the poll;
        // if the sandbox capture never got fired (refresh raced the fire),
        // idempotently re-send it while the order is still unpaid.
        try {
          const detail = await apiGet<ContractResponse>(`/api/contracts/${cid}`);
          const c = detail.contract;
          if (c.status === "PAID") {
            completeWait(waitIdx, c);
            return;
          }
          const effectiveMode =
            recRef.current?.ctx.mode ??
            (c.sandbox_mode != null
              ? c.sandbox_mode
                ? "sandbox"
                : "live-test-mode"
              : null);
          if (effectiveMode === "sandbox" && c.status === "PAYMENT_ORDER_CREATED") {
            await fireSandboxCapture();
          }
        } catch {
          /* transient — the poll below tolerates and retries */
        }
      } else if (mode === "sandbox") {
        await fireSandboxCapture();
      }

      beginWaitPoll(waitIdx);
    },
    [beginWaitPoll, completeWait, fireSandboxCapture, haltStep, update],
  );

  /* -------------------------------------------------------- chain runner */

  const runFrom = useCallback(
    async (startIdx: number) => {
      if (ENGINE_LOCK) return;
      ENGINE_LOCK = true;
      const myRun = recRef.current?.runId ?? "";
      try {
        update((r) => {
          r.phase = "running";
        });

        for (let i = startIdx; i < stepDefs.length; i++) {
          if (!mountedRef.current || recRef.current?.runId !== myRun) return;
          const def = stepDefs[i];

          // The WAITING gate hands control to the poll loop; its completion
          // path (completeWait) resumes the chain past the capture verify.
          if (def.kind === "wait") {
            await enterWaiting(i, false);
            return;
          }

          const t0 = nowIso();
          update((r) => {
            Object.assign(r.steps[i], {
              status: "running",
              startedAt: t0,
              finishedAt: undefined,
              detail: undefined,
              failureCode: undefined,
            });
          });
          try {
            const detail = await (def.run ? def.run(recRef.current!.ctx) : Promise.resolve(""));
            if (!mountedRef.current || recRef.current?.runId !== myRun) return;
            update((r) => {
              Object.assign(r.steps[i], { status: "ok", detail, finishedAt: nowIso() });
            });
          } catch (e) {
            if (!mountedRef.current || recRef.current?.runId !== myRun) return;
            update((r) => {
              Object.assign(r.steps[i], {
                status: "fail",
                detail: errMsg(e),
                failureCode: def.code,
                finishedAt: nowIso(),
              });
              r.phase = "halted";
            });
            setFlash({
              tone: "fail",
              text: `HALTED AT STEP ${i + 1}/15 "${def.name}" — ${def.code}: ${errMsg(e)}`,
            });
            return;
          }
        }

        if (!mountedRef.current || recRef.current?.runId !== myRun) return;
        update((r) => {
          r.phase = "complete";
        });
        setFlash({
          tone: "ok",
          text: `HERO ARC COMPLETE — contract ${shortId(recRef.current?.ctx.contractId)} fully remediated: wrong variant delivered, refund executed, REMEDIATED.`,
        });
      } finally {
        ENGINE_LOCK = false;
      }
    },
    [enterWaiting, stepDefs, update],
  );

  /* ---------------------------------------------------------- run control */

  const startNewRun = useCallback(() => {
    stopWaitPoll();
    setFlash(null);
    const id = `run_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
    const rec: PersistedRun = {
      v: RECORD_VERSION,
      runId: id,
      startedAt: nowIso(),
      phase: "running",
      ctx: {},
      steps: makeRows(),
    };
    recRef.current = rec;
    persistRec(rec);
    setSteps(rec.steps.map((s) => ({ ...s })));
    setPhase(rec.phase);
    setCtx({ ...rec.ctx });
    setRunId(id);
    void runFrom(0);
  }, [makeRows, runFrom, stopWaitPoll]);

  const retryFailedStep = useCallback(() => {
    const rec = recRef.current;
    if (!rec) return;
    const idx = rec.steps.findIndex((s) => s.status === "fail");
    if (idx < 0) return;
    setFlash(null);
    update((r) => {
      Object.assign(r.steps[idx], {
        status: "pending",
        detail: undefined,
        failureCode: undefined,
        startedAt: undefined,
        finishedAt: undefined,
      });
      r.phase = "running";
    });
    if (idx === WAIT_IDX) {
      // A halted wait step (poll outage / vanished contract) re-enters the
      // WAITING gate rather than blindly re-running a payment action.
      void enterWaiting(idx, true);
    } else {
      void runFrom(idx);
    }
  }, [enterWaiting, runFrom, update]);

  const clearConsole = useCallback(() => {
    stopWaitPoll();
    try {
      const rid = recRef.current?.runId;
      if (rid) window.sessionStorage.removeItem(storeKey(rid));
      window.sessionStorage.removeItem(CURRENT_RUN_KEY);
    } catch {
      /* non-fatal */
    }
    recRef.current = null;
    setSteps([]);
    setPhase("idle");
    setCtx({});
    setRunId(null);
    setFlash(null);
  }, [stopWaitPoll]);

  /* ------------------------------------------------------------- lifecycle */

  /**
   * Mount: hydrate the operator token + posture probe + the persisted run,
   * then resume. The ENGINE_LOCK makes StrictMode's double-invoked mount
   * effect safe — the second invocation sees the lock held and skips; the
   * first cleanup runs before either engine starts (both invocations are in
   * the same commit), so `mountedRef` is simply re-armed on every setup and
   * never left false by a stale teardown.
   */
  useEffect(() => {
    mountedRef.current = true;

    // Restore this tab's operator token (never hardcoded, never shared).
    try {
      const t = window.sessionStorage.getItem(TOKEN_KEY);
      if (!tokenRef.current && t) {
        tokenRef.current = t;
        setOperatorTokenState(t);
      }
    } catch {
      /* non-fatal */
    }

    // Posture probe — powers the SANDBOX / RAZORPAY TEST MODE awareness.
    void apiTry<DemoStatusResponse>("/api/demo/status").then((s) => {
      if (mountedRef.current && s) setDemoStatus(s);
    });

    const resume = async () => {
      // Case A: this component instance already owns a run (SPA navigation
      // away and back). The engine loop self-resumes once `mountedRef` is
      // re-armed above; only the WAITING poll needs explicit revival because
      // teardown killed its interval.
      const owned = recRef.current;
      if (owned) {
        if (
          owned.phase === "waiting_payment" &&
          waitTimerRef.current === null &&
          !ENGINE_LOCK
        ) {
          await enterWaiting(WAIT_IDX, true);
        }
        return;
      }

      // Case B: fresh mount adopting the persisted tab record.
      const rec = readCurrentRun();
      if (!rec) return;
      // A chain from a just-unmounted sibling instance may still hold the
      // module engine lock (in-flight awaits outlive their owner). Wait
      // briefly for release rather than silently dropping the resumption.
      for (let i = 0; i < 30 && ENGINE_LOCK; i++) {
        await sleep(100);
        if (!mountedRef.current) return;
      }
      if (ENGINE_LOCK || recRef.current) return;
      recRef.current = rec;
      setSteps(rec.steps.map((s) => ({ ...s })));
      setPhase(rec.phase);
      setCtx({ ...rec.ctx });
      setRunId(rec.runId);

      if (rec.phase === "waiting_payment") {
        await enterWaiting(WAIT_IDX, true);
        return;
      }
      if (rec.phase === "running") {
        const idx = rec.steps.findIndex((s) => s.status !== "ok");
        if (idx < 0) {
          update((r) => {
            r.phase = "complete";
          });
          return;
        }
        if (idx === WAIT_IDX) {
          await enterWaiting(idx, true);
          return;
        }
        // Normalize a step that was mid-flight at refresh time back to
        // pending — it will be re-run (all steps are idempotent server-side).
        update((r) => {
          if (r.steps[idx].status === "running" || r.steps[idx].status === "waiting") {
            Object.assign(r.steps[idx], { status: "pending", startedAt: undefined });
          }
        });
        await runFrom(idx);
      }
      // halted / complete render exactly as persisted — no engine work.
    };

    void resume();

    return () => {
      mountedRef.current = false;
      stopWaitPoll();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* -------------------------------------------------------------- derived */

  const railMode: DemoPaymentMode | null = ctx.mode ?? demoStatus?.razorpay_mode ?? null;
  const failedIndex = steps.findIndex((s) => s.status === "fail");
  const waiting =
    phase === "waiting_payment" && ctx.contractId
      ? {
          contractId: ctx.contractId,
          orderId: ctx.orderId ?? null,
          amountPaise: ctx.amountPaise ?? null,
          mode: railMode,
        }
      : null;

  return {
    // ticker state
    steps,
    phase,
    ctx,
    runId,
    flash,
    // posture
    demoStatus,
    railMode,
    isSandboxRail: railMode !== "live-test-mode",
    tokenRequired: !!demoStatus?.operator_token_required,
    tokenConfigured: !!demoStatus?.operator_token_configured,
    // operator token
    operatorToken,
    setOperatorToken,
    opHeaders,
    operatorPost,
    // derived views
    waiting,
    failedIndex: failedIndex >= 0 ? failedIndex : null,
    // actions
    startNewRun,
    retryFailedStep,
    clearConsole,
  };
}

/* ------------------------------------------------- step definitions */

/**
 * The canonical 15 steps. `code` is the stable failure reason code surfaced
 * on the ticker when the step halts the chain; `money: true` marks steps that
 * move or verify money (the SANDBOX / RAZORPAY TEST MODE badge renders on
 * those rows).
 */
function buildStepDefs(
  operatorPost: <T>(path: string, body?: unknown) => Promise<T>,
): StepDef[] {
  return [
    {
      key: "intent_compiled",
      name: "INTENT COMPILED",
      code: "E_INTENT",
      kind: "action",
          async run(c: DemoCtx) {
    const r = await apiPost<CompileResponse>("/api/intents/compile", {
      raw_text: HERO_INTENT,
    });
    c.intentId = r.intent.id;
    return `intent ${shortId(r.intent.id)} · engine=${r.engine || "rules"}`;
          },
        },
        {
          key: "merchant_queried",
          name: "MERCHANT QUERIED",
          code: "E_SEARCH",
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.intentId) throw new Error("no intent id from step 1");
    const r = await apiPost<SearchResponse>(`/api/intents/${c.intentId}/search`);
    const feasible = (r.results ?? []).filter((x) => x.evaluation.feasible);
    if (feasible.length === 0) {
      throw new Error(
        `0 of ${(r.results ?? []).length} offers feasible against frozen constraints`,
      );
    }
    c.offerId = feasible[0].offer.id;
    c.offerTitle = feasible[0].offer.title;
    return `${(r.results ?? []).length} evaluated · ${feasible.length} feasible → ${feasible[0].offer.title}`;
          },
        },
        {
          key: "contract_frozen",
          name: "CONTRACT FROZEN",
          code: "E_FREEZE",
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.intentId || !c.offerId) throw new Error("no intent/offer from prior steps");
    const r = await apiPost<SelectResponse>(`/api/intents/${c.intentId}/select-offer`, {
      offer_id: c.offerId,
    });
    c.contractId = r.contract.id;
    if (r.contract.amount_paise != null) c.amountPaise = r.contract.amount_paise;
    return `contract ${shortId(r.contract.id)} frozen · status=${r.contract.status}`;
          },
        },
        {
          key: "buyer_authorized",
          name: "BUYER AUTHORIZED",
          code: "E_AUTHORIZE",
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.contractId) throw new Error("no contract from step 3");
    const r = await apiPost<AuthorizeResponse>(
      `/api/contracts/${c.contractId}/authorize`,
      {},
    );
    return `authority envelope bound to contract hash · status=${r.contract.status}`;
          },
        },
        {
          key: "order_created",
          name: "ORDER CREATED",
          code: "E_ORDER",
          money: true,
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.contractId) throw new Error("no contract from step 3");
    const r = await apiPost<OrderResponse>(
      `/api/contracts/${c.contractId}/payment-order`,
      {},
    );
    c.mode = r.mode;
    c.orderId =
      r.checkout_config?.order_id ??
      String((r.razorpay_order as { id?: string } | undefined)?.id ?? "");
    if (r.checkout_config?.amount_paise != null) c.amountPaise = r.checkout_config.amount_paise;
    return `order ${c.orderId || "?"} · ${formatINR(c.amountPaise ?? null)} · rail=${r.mode}`;
          },
        },
        {
          key: "checkout_waiting",
          name: "COMPLETE CHECKOUT",
          code: "E_CHECKOUT_WAIT",
          money: true,
          kind: "wait",
        },
        {
          key: "payment_captured",
          name: "PAYMENT CAPTURED",
          code: "E_CAPTURE",
          money: true,
          kind: "action",
          // Normally marked ok by completeWait straight from webhook truth;
          // this run() is the safety net for exotic resume paths.
          async run(c: DemoCtx) {
    if (!c.contractId) throw new Error("no contract from step 3");
    const detail = await apiGet<ContractResponse>(`/api/contracts/${c.contractId}`);
    const ct = detail.contract;
    if (ct.status !== "PAID") {
      throw new Error(`expected PAID, contract is ${ct.status}`);
    }
    return `payment ${ct.razorpay_payment_id ?? "confirmed"} verified against server truth`;
          },
        },
        {
          key: "synthetic_shipment",
          name: "SYNTHETIC SHIPMENT",
          code: "E_SHIP",
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.contractId) throw new Error("no contract");
    await operatorPost(`/api/demo/contracts/${c.contractId}/ship`);
    return "FULFILLMENT_SHIPPED recorded · SYNTHETIC fact";
          },
        },
        {
          key: "wrong_variant_delivered",
          name: "WRONG VARIANT DELIVERED",
          code: "E_DELIVER",
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.contractId) throw new Error("no contract");
    const r = await operatorPost<DeliverResponse>(
      `/api/demo/contracts/${c.contractId}/deliver`,
      { scenario: "wrong_variant" },
    );
    return `${(r.breaches ?? []).length} breach candidate(s) · contract_status=${r.status ?? "?"} · SYNTHETIC`;
          },
        },
        {
          key: "material_breach",
          name: "MATERIAL BREACH",
          code: "E_BREACH",
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.contractId) throw new Error("no contract");
    const r = await apiGet<{ breaches: Breach[] }>(
      `/api/contracts/${c.contractId}/breaches`,
    );
    const material = (r.breaches ?? []).filter(
      (b) => b.severity === "material" || b.severity === "critical",
    );
    if (material.length === 0) {
      const seens = [...new Set((r.breaches ?? []).map((b) => b.severity))];
      throw new Error(
        `verifier found no material breach (severities: ${seens.join(", ") || "none"})`,
      );
    }
    return `${material.length} material/critical · ${material
      .slice(0, 3)
      .map((b) => b.reason_code)
      .join(", ")}`;
          },
        },
        {
          key: "rights_evaluated",
          name: "RIGHTS EVALUATED",
          code: "E_RIGHTS",
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.contractId) throw new Error("no contract");
    const r = await apiGet<RightsResponse>(`/api/contracts/${c.contractId}/rights`);
    const ents = r.entitlements ?? [];
    const eligible = ents.filter((e) => e.status === "eligible").length;
    return `${ents.length} entitlements · ${eligible} eligible · graph ${r.graph?.nodes?.length ?? 0} nodes`;
          },
        },
        {
          key: "replacement_unavailable",
          name: "REPLACEMENT UNAVAILABLE",
          code: "E_REPLACE",
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.contractId) throw new Error("no contract");
    await operatorPost(`/api/demo/contracts/${c.contractId}/replacement-unavailable`);
    let tail = "";
    try {
      const r = await apiGet<RightsResponse>(`/api/contracts/${c.contractId}/rights`);
      const rep = (r.entitlements ?? []).find((e) => /replace/i.test(e.type));
      if (rep) tail = ` · replacement entitlement → ${rep.status.toUpperCase()}`;
    } catch {
      /* cosmetic enrichment only */
    }
    return `replacement inventory = 0 recorded · SYNTHETIC${tail}`;
          },
        },
        {
          key: "refund_selected",
          name: "REFUND SELECTED",
          code: "E_REMEDIES",
          money: true,
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.contractId) throw new Error("no contract");
    const r = await apiGet<RemediesResponse>(`/api/contracts/${c.contractId}/remedies`);
    const viable = (r.proposals ?? [])
      .filter((p) => !p.rejected_reason)
      .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
    if (viable.length === 0) {
      throw new Error(
        `planner returned no viable proposal (${(r.proposals ?? []).length} total, all rejected)`,
      );
    }
    c.proposalId = viable[0].id;
    c.remedyType = viable[0].remedy_type;
    const amount =
      viable[0].amount_paise != null ? ` ${formatINR(viable[0].amount_paise)}` : "";
    const rejected = (r.proposals ?? []).length - viable.length;
    return `rank #1 → ${viable[0].remedy_type}${amount} · ${rejected} rejected`;
          },
        },
        {
          key: "policy_allow",
          name: "POLICY VERDICT · ALLOW",
          code: "E_POLICY",
          money: true,
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.proposalId) throw new Error("no remedy proposal from step 13");
    const r = await apiPost<PolicyResponse>(
      `/api/remedies/${c.proposalId}/policy`,
      {},
    );
    const d = r.decision;
    if (!d || d.decision === "DENY" || r.money_action?.status === "denied") {
      throw new Error(
        `policy DENIED — reasons: ${(d?.reason_codes ?? ["unknown"]).join(", ")}` +
          (d?.explanation ? ` · ${d.explanation.slice(0, 140)}` : ""),
      );
    }
    if (d.decision === "REQUIRE_APPROVAL") {
      const ap = await operatorPost<ApproveResponse>(
        `/api/remedies/${c.proposalId}/approve`,
        {},
      );
      if (ap.money_action?.status === "denied") {
        throw new Error("approval path produced a denied money action");
      }
      return `REQUIRE_APPROVAL (${(d.policy_ids ?? []).join(", ")}) → operator-approved`;
    }
    return `ALLOW by ${(d.policy_ids ?? []).join(", ") || "default policy"} — autonomous execution armed`;
          },
        },
        {
          key: "refund_executed",
          name: "REFUND EXECUTED · REMEDIATED",
          code: "E_EXECUTE",
          money: true,
          kind: "action",
          async run(c: DemoCtx) {
    if (!c.proposalId || !c.contractId) {
      throw new Error("no proposal/contract from prior steps");
    }
    const r = await apiPost<ExecuteResponse & { executed?: boolean }>(
      `/api/remedies/${c.proposalId}/execute`,
      {},
    );
    const ma = r.money_action;
    if (r.error && !ma) throw new Error(r.error);
    if (ma?.status === "denied") {
      throw new Error(`money action denied — ${ma.reason_code ?? "no reason given"}`);
    }
    if (ma?.status === "failed") {
      throw new Error(`refund failed — ${ma.reason_code ?? r.error ?? "unknown"}`);
    }
    const ref =
      ma?.result_ref ?? (r.refund as { id?: string } | null)?.id ?? null;
    if (!ref && ma?.status !== "executed" && r.executed !== true) {
      throw new Error(`refund not confirmed — money_action=${ma?.status ?? "?"}`);
    }
    // The 15th step's claim is REMEDIATED — verify it from the server,
    // with one grace re-fetch for the transition's write lag.
    let contract = (
      await apiGet<ContractResponse>(`/api/contracts/${c.contractId}`)
    ).contract;
    if (contract.status !== "REMEDIATED") {
      await sleep(600);
      contract = (
        await apiGet<ContractResponse>(`/api/contracts/${c.contractId}`)
      ).contract;
    }
    if (contract.status !== "REMEDIATED") {
      throw new Error(`refund ran but contract is ${contract.status}, expected REMEDIATED`);
    }
    return `refund ${ref} · money_action=${ma?.status ?? "executed"} · contract=REMEDIATED`;
          },
        },
      ];
}

/** The hero brief — the exact §20 scenario text compiled at step 1. */
export const HERO_INTENT =
  "Buy the Aster ANC Pro over-ear wireless headphones for ₹11,499 or less — must have " +
  "active noise cancellation and a manufacturer warranty valid in India. Delivery within " +
  "4 days. Do NOT substitute alternatives.";

type StepDef = {
  key: DemoStepKey;
  name: string;
  code: string;
  kind: "action" | "wait";
  money?: boolean;
  run?: (ctx: DemoCtx) => Promise<string>;
};
