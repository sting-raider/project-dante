"use client";

/**
 * /contract/[id]/breach — the full-page red editorial spread (plan §28).
 * Giant serif MATERIAL BREACH headline; PROMISED vs OBSERVED comparison
 * table from breaches[] + material promises; materiality line; evidence
 * artifacts with sha256 shortHash + trusted-level chips.
 *
 * Agent I.
 */

import { useParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import Folio from "@/components/editorial/Folio";
import SectionLabel from "@/components/editorial/SectionLabel";
import MarginNote from "@/components/editorial/MarginNote";
import Badge from "@/components/commerce/Badge";
import SyntheticBadge from "@/components/commerce/SyntheticBadge";
import { apiGet } from "@/lib/api";
import type {
  Breach,
  ContractResponse,
  EvidenceArtifactRec,
  PromiseRec,
} from "@/lib/rights-ui";
import { formatDateTime, prettyJson } from "@/lib/format";
import { cn } from "@/lib/cn";

type VerifyShape = {
  breaches: Breach[];
  status: string;
  satisfied: boolean;
};

function renderValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export default function BreachPage() {
  const params = useParams<{ id: string }>();
  const contractId = params.id;

  const [detail, setDetail] = useState<ContractResponse | null>(null);
  const [breaches, setBreaches] = useState<Breach[] | null>(null);
  const [evidence, setEvidence] = useState<EvidenceArtifactRec[]>([]);
  const [sandbox, setSandbox] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const d = await apiGet<ContractResponse>(`/api/contracts/${contractId}`);
        if (!alive) return;
        setDetail(d);
        setSandbox(!!d.contract.sandbox_mode);

        const b = await apiGet<{ breaches: Breach[] }>(
          `/api/contracts/${contractId}/breaches`
        );
        if (!alive) return;
        setBreaches(b.breaches ?? []);

        // Evidence referenced by promises + breach observed-facts; the frozen
        // contract response doesn't carry artifacts, pull them per promise ref.
        const ids = new Set<string>();
        for (const p of d.promises ?? []) {
          if (p.source_artifact_id) ids.add(p.source_artifact_id);
        }
        // The timeline's EVIDENCE_SNAPSHOT_CREATED events carry artifact payloads.
        try {
          const tl = await apiGet<{ events: Record<string, unknown>[] }>(
            `/api/contracts/${contractId}/timeline?category=Evidence`
          );
          for (const ev of tl.events) {
            const payload = (ev.payload ?? {}) as Record<string, unknown>;
            const art = (payload.artifact ?? payload.evidence) as
              | EvidenceArtifactRec
              | undefined;
            if (art?.id && !ids.has(art.id)) {
              ids.add(art.id);
              setEvidence((prev) => [...prev, ...(art ? [art] : [])]);
            } else if (art?.id && ids.has(art.id)) {
              setEvidence((prev) => [...prev.filter((p) => p.id !== art.id), art]);
            }
          }
        } catch {
          /* evidence panel degrades to empty */
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "failed to load contract");
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contractId]);

  // Also verify on load so the page reflects current truth even before the
  // demo deliver endpoint has been hit (breaches may already be recorded).
  useEffect(() => {
    apiGet<VerifyShape>(`/api/contracts/${contractId}/verify`)
      .then((v) => {
        setBreaches((prev) => (prev && prev.length > 0 ? prev : v.breaches));
      })
      .catch(() => undefined);
  }, [contractId]);

  const promises: PromiseRec[] = detail?.promises ?? [];
  const materialPromises = promises.filter((p) => p.material_to_intent);
  const byPromiseId = new Map(promises.map((p) => [p.id, p]));
  const breachedPromiseIds = new Set((breaches ?? []).map((b) => b.promise_id));

  /** Comparison rows: every material promise, marked MISMATCH when breached. */
  const rows = materialPromises.map((p) => ({
    promise: p,
    breach: (breaches ?? []).find((b) => b.promise_id === p.id),
    mismatch: breachedPromiseIds.has(p.id),
  }));

  const anyBreach = (breaches ?? []).length > 0;
  const severityRank: Record<string, number> = {
    informational: 0,
    minor: 1,
    material: 2,
    critical: 3,
  };

  return (
    <main className="min-h-screen bg-paper">
      {/* Red breach rule entering the top of the page (plan §27.6). */}
      <div aria-hidden={true} className="h-1.5 w-full bg-signal" />

      <div className="dante-container py-8 md:py-12">
        <Folio
          issue="ISSUE 04 / BREACH"
          running={`DOSSIER / ${contractId.slice(0, 13).toUpperCase()}`}
          href={`/contract/${contractId}`}
        />

        <header className="mt-10 md:mt-14">
          <SectionLabel>VERIFICATION REPORT</SectionLabel>
          <h1
            className={cn(
              "mt-4 font-display text-[clamp(3rem,10vw,7.5rem)] leading-[0.95] tracking-[-0.02em]",
              anyBreach ? "text-signal" : "text-ink"
            )}
          >
            {anyBreach ? "MATERIAL BREACH" : "NO BREACH"}
          </h1>

          <p className="mt-6 max-w-prose text-base leading-relaxed text-ink-soft">
            {anyBreach ? (
              <>
                What the merchant froze into this contract at purchase time did
                not survive contact with reality. Every mismatch below is backed
                by a hashed evidence artifact and re-derivable from the audit
                trail.
              </>
            ) : (
              <>
                Every material promise checked against an observed fact so far
                matches. This page will turn red the moment verification finds a
                mismatch.
              </>
            )}
          </p>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            {detail?.contract.status ? <Badge>{detail.contract.status}</Badge> : null}
            <span className="folio-label">{sandbox ? "SANDBOX RAIL" : "LIVE TEST-MODE RAIL"}</span>
            {(breaches ?? []).map((b) => (
              <span key={b.id}>
                <Badge tone={severityRank[b.severity] >= 2 ? "signal" : "warning"}>
                  {b.reason_code}
                </Badge>
              </span>
            ))}
          </div>
        </header>

        {error && (
          <p role="alert" className="mt-8 border-l-2 border-danger pl-3 font-mono text-xs text-danger">
            {error} — is the API on :8000?
          </p>
        )}

        {!detail && !error && (
          <p className="mt-10 font-mono text-xs uppercase tracking-[0.14em] text-ink-soft">
            Loading dossier…
          </p>
        )}

        {detail && (
          <>
            {/* PROMISED vs OBSERVED spread */}
            <section aria-label="Promised versus observed" className="mt-12">
              <div className="grid grid-cols-2 border-b border-ink pb-2">
                <h2 className="font-mono text-xs font-medium uppercase tracking-[0.18em] text-ink">
                  Promised
                </h2>
                <h2 className="font-mono text-xs font-medium uppercase tracking-[0.18em] text-ink">
                  Observed
                </h2>
              </div>

              {rows.length === 0 && (
                <p className="border-b border-rule py-6 font-mono text-xs uppercase tracking-[0.14em] text-ink-soft">
                  No material promises recorded on this contract.
                </p>
              )}

              {rows.map(({ promise: p, breach, mismatch }) => {
                // Observed value comes from the breach explanation when the
                // fact record itself isn't exposed on this endpoint.
                const observed = breach
                  ? extractObserved(breach.explanation, p.key)
                  : null;
                return (
                  <div
                    key={p.id}
                    className={cn(
                      "grid grid-cols-2 items-baseline gap-x-6 border-b py-5",
                      mismatch ? "border-signal/60 bg-signal/[0.04]" : "border-rule"
                    )}
                  >
                    {/* promised */}
                    <div className="pr-4">
                      <p className="folio-label">{humanKey(p.key)}</p>
                      <p className="tabular mt-1 text-lg leading-snug text-ink">
                        {renderValue(p.normalized_value ?? p.value)}
                      </p>
                    </div>
                    {/* observed */}
                    <div className="pr-4">
                      <p className="folio-label flex flex-wrap items-center gap-2">
                        Delivered reality
                        {mismatch && (
                          <span
                            role="img"
                            aria-label="Mismatch"
                            className="inline-flex items-center gap-1 rounded-sm border border-signal bg-signal px-1.5 py-[2px] font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-paper-bright"
                          >
                            ✗ MISMATCH
                          </span>
                        )}
                        {!mismatch && (
                          <span className="inline-flex items-center gap-1 rounded-sm border border-success/50 bg-success/[0.07] px-1.5 py-[2px] font-mono text-[0.5625rem] uppercase tracking-[0.14em] text-success">
                            ✓ HELD
                          </span>
                        )}
                      </p>
                      <p
                        className={cn(
                          "tabular mt-1 text-lg leading-snug",
                          mismatch ? "font-medium text-signal-deep" : "text-ink"
                        )}
                      >
                        {mismatch
                          ? (observed ?? "differs from the frozen promise")
                          : (observed ?? "matches as promised")}
                      </p>
                    </div>
                  </div>
                );
              })}
            </section>

            {/* Materiality verdict */}
            <section className="mt-12 border-y border-ink py-6">
              <p className="font-display text-2xl leading-snug md:text-3xl">
                MATERIAL TO ORIGINAL INTENT:{" "}
                <span className={anyBreach ? "text-signal" : "text-success"}>
                  {anyBreach ? "YES" : "PROMISES HELD"}
                </span>
              </p>
              {(breaches ?? []).length > 0 && (
                <div className="mt-4 space-y-2">
                  {breaches!.map((b) => (
                    <details key={b.id} className="max-w-prose">
                      <summary className="cursor-pointer font-mono text-xs uppercase tracking-[0.12em] text-ink-soft hover:text-signal">
                        {b.reason_code} · {b.severity}
                      </summary>
                      <p className="mt-2 border-l-2 border-signal pl-3 text-sm leading-relaxed text-ink-soft">
                        {b.explanation}
                      </p>
                      <pre className="mt-2 overflow-x-auto rounded-md border border-rule bg-paper-bright p-3 font-mono text-[0.6875rem] text-ink">
                        {prettyJson(b)}
                      </pre>
                    </details>
                  ))}
                </div>
              )}
              <MarginNote marker="§">
                A promise is material when it was among the reasons the buyer
                chose this offer — Dante freezes that linkage at purchase time,
                not after the dispute.
              </MarginNote>
            </section>

            {/* Evidence */}
            <section className="mt-12" aria-label="Evidence artifacts">
              <SectionLabel>SOURCE EVIDENCE · HASHED</SectionLabel>
              {evidence.length === 0 ? (
                <p className="mt-4 font-mono text-xs uppercase tracking-[0.14em] text-ink-soft">
                  Evidence artifacts are attached at freeze and delivery time.
                </p>
              ) : (
                <ul className="mt-4 divide-y divide-rule border-y border-rule">
                  {evidence.map((a) => (
                    <li key={a.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-3">
                      <code className="font-mono text-xs text-ink">{short(a.sha256)}</code>
                      <span className="folio-label">{a.source_type}</span>
                      <TrustedChip level={a.trusted_level} />
                      <SyntheticBadge synthetic={!!a.synthetic} />
                      <span className="ml-auto folio-label">
                        {formatDateTime(a.observed_at)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* Where next */}
            {anyBreach && (
              <nav aria-label="Rights and remedies" className="mt-14 flex flex-wrap gap-4">
                <Link
                  href={`/contract/${contractId}/rights`}
                  className="border border-ink px-6 py-3 font-mono text-xs uppercase tracking-[0.16em] text-ink transition-colors hover:bg-ink hover:text-paper-bright"
                >
                  View rights graph →
                </Link>
                <Link
                  href={`/contract/${contractId}/remedy`}
                  className="bg-signal px-6 py-3 font-mono text-xs uppercase tracking-[0.16em] text-paper-bright transition-colors hover:bg-signal-deep"
                >
                  Plan the remedy →
                </Link>
              </nav>
            )}
          </>
        )}
      </div>
    </main>
  );
}

/* ------------------------------------------------------------ helpers */

function humanKey(key: string): string {
  return key.replace(/[._]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function short(h: string): string {
  return h.length > 16 ? `${h.slice(0, 16)}…` : h;
}

/** Best-effort extraction of the observed value from a breach explanation. */
function extractObserved(explanation: string, key: string): string | null {
  if (!explanation) return null;
  // Explanations often read like `warranty.region expected IN, got AE`.
  const m = explanation.match(/(?:got|observed|actual|found)\s*[:=]?\s*"?([^".;]+)"?/i);
  if (m) return m[1].trim();
  const km = explanation.match(new RegExp(`${key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}[^\\n]*`, "i"));
  return km ? km[0].slice(0, 120) : null;
}

function TrustedChip({ level }: { level: string }) {
  const tone =
    level === "structured_verified"
      ? "border-success/40 bg-success/[0.07] text-success"
      : level === "synthetic"
        ? "border-warning/40 bg-warning/[0.08] text-warning"
        : level === "external"
          ? "border-rule bg-paper-bright text-ink-soft"
          : "border-rule bg-paper-bright text-ink-soft";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border px-1.5 py-[2px] font-mono text-[0.5625rem] uppercase tracking-[0.14em]",
        tone
      )}
    >
      {level.replace(/_/g, " ")}
    </span>
  );
}
