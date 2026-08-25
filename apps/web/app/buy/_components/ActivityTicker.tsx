"use client";

/**
 * Agent activity ticker (§28) — a running ledger of step strings driven by
 * hook phase. Completed steps resolve to ✓; the active step carries a
 * blinking cursor; error steps render in danger with a retry affordance.
 */

import type { FlowPhase } from "@/lib/useContractFlow";
import { Button } from "./atoms";

type StepState = "done" | "active" | "pending" | "error";

const STEPS: { label: string; phases: FlowPhase[] }[] = [
  { label: "Compiling intent", phases: ["compiling", "error_compile"] },
  { label: "Searching merchant", phases: ["searching", "error_search"] },
  {
    label: "Evaluating offers",
    phases: ["shortlist", "selecting", "navigating", "error_select"],
  },
  { label: "Freezing promises", phases: ["awaiting_authorization"] },
];

const ERROR_PHASES: FlowPhase[] = [
  "error_compile",
  "error_search",
  "error_select",
  "error_contract_load",
  "error_authorize",
  "error_order",
  "error_verify",
  "error_poll",
];

function stepState(stepPhases: FlowPhase[], current: FlowPhase): StepState {
  if (stepPhases.includes(current) && ERROR_PHASES.includes(current)) return "error";
  const idx = STEPS.findIndex((s) => s.phases.includes(current));
  const myIdx = STEPS.findIndex((s) => s.phases === stepPhases);
  // active: this step's phase set contains current; done: later step reached
  if (current === "idle") return "pending";
  if (idx === -1) {
    // current phase is beyond the ticker (contract-side states)
    return myIdx === STEPS.length - 1 ? "done" : "done";
  }
  return myIdx < idx ? "done" : myIdx === idx ? "active" : "pending";
}

export function ActivityTicker({
  phase,
  error,
  onRetry,
}: {
  phase: FlowPhase;
  error?: string | null;
  onRetry?: () => void;
}) {
  const isError = ERROR_PHASES.includes(phase);

  return (
    <div
      aria-live="polite"
      className="rounded-[2px] border border-rule bg-paper-bright px-5 py-4"
    >
      <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-ink-soft">
        Agent activity
      </div>
      <ol className="mt-3 space-y-1.5">
        {STEPS.map((s) => {
          const state = stepState(s.phases, phase);
          return (
            <li key={s.label} className="flex items-center gap-2.5">
              <span
                aria-hidden="true"
                className={`w-4 text-center font-mono text-[11px] ${
                  state === "error"
                    ? "text-danger"
                    : state === "done"
                      ? "text-success"
                      : state === "active"
                        ? "text-ink"
                        : "text-rule"
                }`}
              >
                {state === "error" ? "✗" : state === "done" ? "✓" : state === "active" ? "▸" : "·"}
              </span>
              <span
                className={`font-mono text-[12px] tracking-wide ${
                  state === "error"
                    ? "text-danger"
                    : state === "active"
                      ? "text-ink"
                      : state === "done"
                        ? "text-ink-soft"
                        : "text-rule"
                }`}
              >
                {s.label}
                {state === "active" && !isError && (
                  <span className="ml-0.5 inline-block animate-pulse" aria-hidden="true">
                    …
                  </span>
                )}
                {state === "error" && " — failed"}
              </span>
            </li>
          );
        })}
      </ol>

      {isError && (
        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-rule pt-3">
          <span className="max-w-xl font-body text-[13px] leading-snug text-danger">
            {error ?? "Something failed."}
          </span>
          {onRetry && (
            <Button variant="secondary" onClick={onRetry}>
              Retry
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
