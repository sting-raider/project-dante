"use client";

/**
 * AUTHORIZATION ENVELOPE panel — §52 "YOU ARE ABOUT TO AUTHORIZE" style:
 * exact amount, hard-constraint checkmarks, contract identity, then the
 * single irreversible button. This is the human approval gate; nothing
 * moves money without it.
 */

import type { DanteContract, Promise_ } from "@/lib/useContractFlow";
import { rupees } from "@/lib/useContractFlow";
import { Badge, Button, ConstraintMark, MoneyText, Panel, Rule, SandboxBadge } from "./atoms";

export function formatPromiseValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") {
    if (/^\d{4}-\d{2}-\d{2}/.test(v)) {
      const d = new Date(v);
      if (!Number.isNaN(d.getTime())) {
        return d.toLocaleDateString("en-IN", {
          weekday: "short",
          day: "numeric",
          month: "short",
        });
      }
    }
    return v;
  }
  return String(v);
}

export function AuthorizationCard({
  contract,
  promises,
  onAuthorize,
  authorizing,
}: {
  contract: DanteContract;
  promises: Promise_[];
  onAuthorize: () => void;
  authorizing: boolean;
}) {
  const material = promises.filter((p) => p.material_to_intent);

  return (
    <Panel tone="signal" className="border-2">
      <div className="font-mono text-[11px] uppercase tracking-[0.3em] text-signal">
        You are about to authorize
      </div>

      <div className="mt-4 flex flex-wrap items-baseline justify-between gap-3">
        <MoneyText paise={contract.amount_paise} size="xl" />
        <div className="text-right">
          <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-soft">
            Dante contract
          </div>
          <div className="mt-1 font-display text-xl text-ink">
            {contract.display_code ?? contract.id}{" "}
            {contract.sandbox_mode && <SandboxBadge />}
          </div>
        </div>
      </div>

      {/* constraint checkmarks = material promises */}
      <ul className="mt-5 space-y-1.5">
        {material.length === 0 && (
          <li className="font-body text-[13px] italic text-ink-soft">
            No material promises recorded.
          </li>
        )}
        {material.map((p) => (
          <li key={p.id}>
            <ConstraintMark pass detail={p.material_reason ?? undefined}>
              {p.key} = {formatPromiseValue(p.value)}
            </ConstraintMark>
          </li>
        ))}
      </ul>

      {/* envelope facts */}
      {contract.buyer_authority && (
        <>
          <Rule className="my-4" />
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2 font-mono text-[11px] sm:grid-cols-2">
            <div className="flex justify-between gap-3">
              <dt className="uppercase tracking-[0.18em] text-ink-soft">Max amount</dt>
              <dd className="tabular-nums text-ink">
                {rupees(contract.buyer_authority.max_amount_paise)}
              </dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="uppercase tracking-[0.18em] text-ink-soft">Scope</dt>
              <dd className="text-ink">{contract.buyer_authority.scope}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="uppercase tracking-[0.18em] text-ink-soft">Authorized by</dt>
              <dd className="text-ink">{contract.buyer_authority.authorized_by}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="uppercase tracking-[0.18em] text-ink-soft">
                Hash at authorization
              </dt>
              <dd className="text-ink">{shortHash(contract.buyer_authority.contract_hash_at_authorization)}</dd>
            </div>
          </dl>
        </>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-4">
        <Button onClick={onAuthorize} disabled={authorizing}>
          {authorizing ? "Authorizing…" : "Authorize & open Razorpay"}
        </Button>
        <span className="max-w-sm font-body text-[12px] leading-snug text-ink-soft">
          Authorization is bound to this exact frozen contract hash. Any drift
          invalidates it.
        </span>
      </div>

      {!contract.sandbox_mode && (
        <p className="mt-4 font-body text-[12px] text-ink-soft">
          Opens Razorpay Standard Checkout in test mode.{" "}
          <Badge tone="neutral">test keys</Badge>
        </p>
      )}
    </Panel>
  );
}
