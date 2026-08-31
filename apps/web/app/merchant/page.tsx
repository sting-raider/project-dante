"use client";

/**
 * /merchant — the business-newspaper dashboard (plan §28 + §54).
 * Masthead "WHAT YOUR AI BUYERS COULDN'T VERIFY", StatNumeral row,
 * blocker distribution bars, machine-readable coverage meters, and the
 * honest note that fulfillment is synthetic. Empty store renders zeros —
 * never NaN.
 *
 * Agent I.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import Folio from "@/components/editorial/Folio";
import SectionLabel from "@/components/editorial/SectionLabel";
import StatNumeral from "@/components/editorial/StatNumeral";
import MarginNote from "@/components/editorial/MarginNote";
import { apiGet } from "@/lib/api";
import type { MerchantAnalytics, MerchantProfile } from "@/lib/rights-ui";

/** Constraint key → readable label for blocker rows. */
function blockerLabel(key: string): string {
  const map: Record<string, string> = {
    max_price_paise: "over budget",
    category: "no such category",
    warranty_type: "warranty unsupported",
    warranty_region: "warranty region mismatch",
    delivery_deadline: "delivery deadline unsupported",
    delivery: "delivery unsupported",
    region: "region mismatch",
    anc: "feature missing",
    condition: "condition mismatch",
    inventory: "out of stock",
  };
  if (map[key]) return map[key];
  return key.replace(/[._]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function MerchantPage() {
  const [data, setData] = useState<MerchantAnalytics | null>(null);
  const [profile, setProfile] = useState<MerchantProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    apiGet<MerchantAnalytics>("/api/merchant/analytics")
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "analytics unavailable"));
    apiGet<MerchantProfile>("/api/merchant/profile")
      .then((d) => alive && setProfile(d))
      .catch((e) => alive && setProfileError(e instanceof Error ? e.message : "profile unavailable"));
    return () => {
      alive = false;
    };
  }, []);

  // Normalizers — every field tolerates absence (zeros, not NaN; plan §54).
  const totalProducts = num(data?.total_products);
  const transactableRate = num(data?.ai_transactable_rate); // 0..1
  const warrantyCoverage = num(data?.warranty_metadata_coverage); // 0..1
  const returnPolicyCoverage = num(data?.machine_readable_return_policy);
  const evaluatedIntents = num(data?.evaluated_intents);

  const blockers = Object.entries(data?.blocker_distribution ?? {})
    .map(([key, count]) => ({ key, count: num(count) }))
    .filter((b) => b.count > 0)
    .sort((a, b) => b.count - a.count);
  const blockerMax = Math.max(1, ...blockers.map((b) => b.count));
  const blockerTotal = blockers.reduce((s, b) => s + b.count, 0);

  const coverageMeters = [
    {
      label: "Warranty metadata",
      value: warrantyCoverage,
      note: "SKUs with a structured warranty_type",
    },
    {
      label: "Return policy",
      value: returnPolicyCoverage,
      note: "SKUs exposing a numeric return window",
    },
  ];

  // The single most expensive blocker: highest count.
  const worstBlocker = blockers[0];
  const catalogStats = profile?.catalog_stats;
  const deliveryCoverage = profile
    ? fmtPct(num(catalogStats?.delivery_promise_coverage))
    : "probing";
  const machineEndpointCount = Object.keys(profile?.machine_endpoints ?? {}).length;

  return (
    <main className="merchant-dossier-page min-h-screen bg-paper">
      <div className="dante-container py-8 md:py-12">
        <Folio issue="THE MERCHANT LEDGER" running="DAILY DOSSIER / OPERATIONS" />

        {/* Masthead */}
        <header className="merchant-masthead mt-10 border-b-2 border-ink pb-8 md:mt-14">
          <SectionLabel>MACHINE-OPINION SECTION</SectionLabel>
          <h1 className="mt-4 font-display text-[clamp(2.5rem,7vw,5.5rem)] leading-[0.98] tracking-[-0.02em]">
            What your AI buyers couldn&apos;t verify.
          </h1>
          <p className="mt-4 max-w-prose text-base leading-relaxed text-ink-soft">
            Every blocked intent below is revenue that reached your catalog and
            bounced off missing metadata. Dante logs the exact constraint that
            failed — fixable in data, not marketing.
          </p>
        </header>

        {error && (
          <p role="alert" className="mt-8 border-l-2 border-danger pl-3 font-mono text-xs text-danger">
            {error} — is the API on :8000?
          </p>
        )}

        {!data && !error && (
          <p className="mt-10 font-mono text-xs uppercase tracking-[0.14em] text-ink-soft">
            Setting the type…
          </p>
        )}

        {data && (
          <>
            {/* Stat numeral row */}
            <section className="mt-10 grid grid-cols-2 gap-x-8 gap-y-10 md:grid-cols-4" aria-label="Headline metrics">
              <StatNumeral
                value={Math.round(transactableRate * 1000) / 10}
                format={(v) => `${v % 1 === 0 ? v.toFixed(0) : v.toFixed(1)}%`}
                caption="AI-transactable rate"
              />
              <StatNumeral value={totalProducts} caption="Products live" />
              <StatNumeral
                value={Math.round(warrantyCoverage * 1000) / 10}
                format={(v) => `${v % 1 === 0 ? v.toFixed(0) : v.toFixed(1)}%`}
                caption="Warranty metadata coverage"
              />
              <StatNumeral
                value={evaluatedIntents}
                caption={`Intents evaluated${evaluatedIntents === 0 ? " yet" : ""}`}
              />
            </section>

            {/* Computed merchant surface — this is the machine-readable
                capability statement an AI buyer can rely on, not a marketing
                claim copied from the dashboard. */}
            <section
              className="mt-16 border-y-2 border-ink py-7"
              aria-label="Computed merchant capability profile"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <SectionLabel>COMPUTED CAPABILITY PROFILE</SectionLabel>
                {profile?.gateway?.mode && (
                  <span className="folio-label">PAYMENT RAIL · {profile.gateway.mode}</span>
                )}
              </div>
              <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-soft">
                Aster&apos;s machine-readable promise: each capability below is
                derived from the live catalog and runtime wiring. Buyers can
                query this profile before they delegate a purchase.
              </p>
              {profileError && (
                <p className="mt-4 font-mono text-xs text-warning">
                  Capability profile unavailable — {profileError}
                </p>
              )}
              {!profile && !profileError && (
                <p className="mt-5 font-mono text-xs uppercase tracking-[0.14em] text-ink-soft">
                  Measuring merchant capabilities…
                </p>
              )}
              {profile && (
                <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {Object.entries(profile.capabilities ?? {}).map(([key, enabled]) => (
                    <div
                      key={key}
                      className="flex items-center justify-between gap-3 rounded-[2px] border border-rule bg-paper-bright px-4 py-3"
                    >
                      <span className="text-sm text-ink">{capabilityLabel(key)}</span>
                      <span
                        className={`font-mono text-[10px] uppercase tracking-[0.14em] ${
                          enabled ? "text-success" : "text-ink-soft"
                        }`}
                      >
                        {enabled ? "available" : "unavailable"}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* Runtime profile — compact evidence for the merchant capabilities
                the buyer-facing evaluator can actually query. */}
            <section
              className="merchant-profile-panel mt-10 rounded-xl border border-rule bg-paper-bright p-5 md:p-7"
              aria-label="Merchant runtime profile"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-rule pb-4">
                <div>
                  <SectionLabel>RUNTIME PROFILE · MACHINE-READABLE</SectionLabel>
                  <h2 className="mt-3 text-2xl font-semibold tracking-[-0.035em] text-ink">
                    The catalog behind the promise.
                  </h2>
                </div>
                <span className="folio-label">{profile?.merchant_id ?? "PROFILE PROBING"}</span>
              </div>
              {!profile && !profileError ? (
                <p className="mt-5 font-mono text-xs uppercase tracking-[0.14em] text-ink-soft">
                  Measuring live catalog capabilities…
                </p>
              ) : profile ? (
                <div className="merchant-profile-grid mt-5">
                  <div className="merchant-profile-cell">
                    <span className="merchant-profile-label">Merchant</span>
                    <strong className="merchant-profile-value">{profile.name ?? profile.merchant_id ?? "—"}</strong>
                  </div>
                  <div className="merchant-profile-cell">
                    <span className="merchant-profile-label">Catalog</span>
                    <strong className="merchant-profile-value">{profile.catalog_version ?? "unversioned"}</strong>
                    <span className="merchant-profile-detail">{catalogStats?.total_skus ?? totalProducts} SKUs · {profile.currency ?? "INR"}</span>
                  </div>
                  <div className="merchant-profile-cell">
                    <span className="merchant-profile-label">Delivery evidence</span>
                    <strong className="merchant-profile-value">{deliveryCoverage}</strong>
                    <span className="merchant-profile-detail">structured promise coverage</span>
                  </div>
                  <div className="merchant-profile-cell">
                    <span className="merchant-profile-label">Machine endpoints</span>
                    <strong className="merchant-profile-value tabular">{machineEndpointCount}</strong>
                    <span className="merchant-profile-detail">buyer-queryable surfaces · {profile.gateway?.mode ?? "rail probing"}</span>
                  </div>
                </div>
              ) : (
                <p className="mt-5 font-mono text-xs text-warning">
                  Capability profile unavailable — {profileError}
                </p>
              )}
            </section>

            {/* Blocker distribution */}
            <section className="mt-16" aria-label="Blocker distribution">
              <div className="flex items-baseline justify-between gap-4">
                <SectionLabel>REJECTED INTENT REASONS</SectionLabel>
                <span className="folio-label">{blockerTotal} BLOCKED EVALUATIONS</span>
              </div>

              {blockers.length === 0 ? (
                <p className="mt-6 border-y border-rule py-8 font-mono text-xs uppercase tracking-[0.14em] text-ink-soft">
                  No blocked intents recorded{evaluatedIntents === 0 ? " — no intents evaluated yet" : ""}. Run buyer traffic on the{" "}
                  <Link href="/buy" className="underline underline-offset-4 hover:text-signal">
                    buyer desk
                  </Link>
                  .
                </p>
              ) : (
                <ol className="mt-4 divide-y divide-rule border-y border-rule">
                  {blockers.map((b, i) => (
                    <li key={b.key} className="grid grid-cols-[2rem_11rem_1fr_3.5rem] items-center gap-3 py-3 md:grid-cols-[2rem_13rem_1fr_4rem]">
                      <span className="tabular font-mono text-xs text-ink-soft">
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <span className="truncate text-sm font-medium text-ink" title={b.key}>
                        {blockerLabel(b.key)}
                      </span>
                      {/* share bar */}
                      <div className="h-3 w-full overflow-hidden rounded-sm bg-paper-bright">
                        <div
                          aria-hidden={true}
                          className="h-full rounded-sm bg-signal"
                          style={{ width: `${Math.max(2, (b.count / blockerMax) * 100)}%` }}
                        />
                      </div>
                      <span className="tabular text-right font-mono text-xs text-ink">
                        {b.count}
                      </span>
                    </li>
                  ))}
                </ol>
              )}

              {worstBlocker && (
                <MarginNote marker="¶">
                  Most expensive blocker right now: {blockerLabel(worstBlocker.key)} (
                  {worstBlocker.count} lost intents). Normalize that field first.
                </MarginNote>
              )}
            </section>

            {/* Machine-readable coverage meters */}
            <section className="mt-16" aria-label="Machine-readable catalog completeness">
              <SectionLabel>MACHINE-READABLE CATALOG COMPLETENESS</SectionLabel>
              <div className="mt-4 grid grid-cols-1 gap-6 md:grid-cols-2">
                {coverageMeters.map((m) => (
                  <div key={m.label} className="rounded-lg border border-rule bg-paper-bright p-5">
                    <div className="flex items-baseline justify-between">
                      <p className="text-sm font-medium text-ink">{m.label}</p>
                      <span className="tabular font-display text-3xl leading-none text-ink">
                        {fmtPct(m.value)}
                      </span>
                    </div>
                    <div
                      role="meter"
                      aria-valuenow={Math.round(m.value * 100)}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label={`${m.label} coverage`}
                      className="mt-3 h-2 w-full overflow-hidden rounded-sm bg-paper"
                    >
                      <div
                        aria-hidden={true}
                        className={
                          m.value >= 0.9
                            ? "h-full rounded-sm bg-success"
                            : m.value >= 0.5
                              ? "h-full rounded-sm bg-warning"
                              : "h-full rounded-sm bg-signal"
                        }
                        style={{ width: `${Math.max(1.5, m.value * 100)}%` }}
                      />
                    </div>
                    <p className="folio-label mt-2">{m.note}</p>
                  </div>
                ))}
              </div>

              {/* Recommendation strip */}
              {typeof data.recommendation === "string" && data.recommendation && (
                <blockquote className="mt-8 border-l-4 border-signal pl-5">
                  <p className="font-display text-xl italic leading-relaxed text-ink md:text-2xl">
                    “{String(data.recommendation)}”
                  </p>
                  <footer className="folio-label mt-2">— MERCHANT INSIGHT AGENT, DETERMINISTIC BASE</footer>
                </blockquote>
              )}
            </section>

            {/* Honest synthetic note */}
            <aside className="mt-16 rounded-md border border-warning/50 bg-warning/[0.06] p-5">
              <p className="folio-label text-warning">HONESTY NOTICE · SYNTHETIC FULFILLMENT</p>
              <p className="mt-2 max-w-prose text-sm leading-relaxed text-ink-soft">
                This merchant is fictional and its shipping/delivery events are{" "}
                <strong className="text-ink">synthetic</strong> — generated by the demo
                simulator, visibly badged everywhere they appear. Payment rails are
                real only when the computed profile reports live-test-mode:
                Razorpay Test Mode orders, webhooks and refunds then execute
                through genuine signed API traffic. Otherwise the sandbox
                adapter is the active rail and says so on every surface.
              </p>
            </aside>
          </>
        )}
      </div>
    </main>
  );
}

/* ------------------------------------------------------------ helpers */

function num(n: unknown): number {
  const v = typeof n === "string" ? Number(n) : n;
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function fmtPct(fraction: number): string {
  const pctVal = fraction <= 1 ? fraction * 100 : fraction;
  return `${Number.isInteger(pctVal) ? pctVal : Math.round(pctVal * 10) / 10}%`;
}

function capabilityLabel(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
