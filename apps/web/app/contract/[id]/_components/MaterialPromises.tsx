"use client";

/**
 * MATERIAL PROMISES table (§28 §4) — every frozen promise with key, promised
 * value, material_to_intent flag, material_reason, and verification_status
 * chip. Material rows carry a filled mark; the reason is always visible
 * because "why this mattered" is the product.
 */

import type { Promise_ } from "@/lib/useContractFlow";
import { Badge, MonoValue, Rule } from "./atoms";
import { formatPromiseValue } from "./AuthorizationCard";

function VerificationChip({ status }: { status: Promise_["verification_status"] }) {
  const tone =
    status === "verified" ? "success" : status === "merchant_asserted" ? "warning" : "neutral";
  return <Badge tone={tone}>{status.replace("_", " ")}</Badge>;
}

export function MaterialPromises({ promises }: { promises: Promise_[] }) {
  if (promises.length === 0) {
    return (
      <p className="font-body text-sm italic text-ink-soft">
        No promises frozen yet — select an offer on /buy first.
      </p>
    );
  }

  const materialCount = promises.filter((p) => p.material_to_intent).length;

  // Material first; within groups keep server order.
  const sorted = [
    ...promises.filter((p) => p.material_to_intent),
    ...promises.filter((p) => !p.material_to_intent),
  ];

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-display text-2xl text-ink">Material promises</h3>
        <span className="font-mono text-[10px] uppercase tracking-[0.25em] text-ink-soft">
          {materialCount} of {promises.length} material to intent
        </span>
      </div>

      <div className="mt-5 overflow-x-auto rounded-[2px] border border-rule">
        <table className="w-full min-w-[760px] border-collapse text-left">
          <thead>
            <tr className="border-b border-rule bg-paper-bright">
              <th className="px-4 py-2.5 font-mono text-[10px] font-normal uppercase tracking-[0.22em] text-ink-soft">
                Key
              </th>
              <th className="px-4 py-2.5 font-mono text-[10px] font-normal uppercase tracking-[0.22em] text-ink-soft">
                Promised value
              </th>
              <th className="px-4 py-2.5 font-mono text-[10px] font-normal uppercase tracking-[0.22em] text-ink-soft">
                Material
              </th>
              <th className="px-4 py-2.5 font-mono text-[10px] font-normal uppercase tracking-[0.22em] text-ink-soft">
                Why it matters
              </th>
              <th className="px-4 py-2.5 font-mono text-[10px] font-normal uppercase tracking-[0.22em] text-ink-soft">
                Verification
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((p) => (
              <tr
                key={p.id}
                className={`border-b border-rule last:border-b-0 ${
                  p.material_to_intent ? "bg-paper-bright/60" : ""
                }`}
              >
                <td className="px-4 py-3 font-mono text-[11px] font-medium text-ink">
                  {p.key}
                </td>
                <td className="px-4 py-3">
                  <MonoValue value={formatPromiseValue(p.value)} />
                </td>
                <td className="px-4 py-3">
                  {p.material_to_intent ? (
                    <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-success">
                      <span aria-hidden="true">●</span> material
                    </span>
                  ) : (
                    <span className="font-mono text-[11px] text-rule">—</span>
                  )}
                </td>
                <td className="px-4 py-3 font-body text-[12px] leading-snug text-ink-soft">
                  {p.material_reason ?? "—"}
                </td>
                <td className="px-4 py-3">
                  <VerificationChip status={p.verification_status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* verification legend */}
      <Rule className="my-4" />
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <VerificationChip status="verified" />
        <VerificationChip status="merchant_asserted" />
        <VerificationChip status="unverified" />
      </div>
    </div>
  );
}
