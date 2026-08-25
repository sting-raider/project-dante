"use client";

/**
 * CONTRACT HASHES panel (§28 §6) — offer / promise-set / contract hashes in
 * mono, shortHash display with title attr for the full value.
 */

import type { DanteContract } from "@/lib/useContractFlow";
import { shortHash } from "@/lib/useContractFlow";
import { Panel, SectionLabel } from "./atoms";

export function ContractHashes({ contract }: { contract: DanteContract }) {
  const rows: [string, string | null | undefined][] = [
    ["offer", contract.offer_hash],
    ["promise set", contract.promise_set_hash],
    ["contract", contract.contract_hash],
  ];

  return (
    <Panel>
      <SectionLabel index="§6">Contract hashes</SectionLabel>
      <dl className="mt-4 space-y-2.5">
        {rows.map(([label, hash]) => (
          <div key={label} className="flex items-baseline justify-between gap-3">
            <dt className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-soft">
              {label}
            </dt>
            <dd className="text-right font-mono text-[11px] text-ink" title={hash ?? undefined}>
              {shortHash(hash, 14)}
            </dd>
          </div>
        ))}
      </dl>
      {contract.contract_hash && (
        <p className="mt-4 border-t border-rule pt-3 font-body text-[12px] leading-snug text-ink-soft">
          Buyer authorization is bound to the contract hash at authorize time;
          drift after that invalidates approval.
        </p>
      )}
    </Panel>
  );
}
