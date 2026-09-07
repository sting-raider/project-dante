import Link from "next/link";
import { ButtonLink } from "@/components/ui/Button";
import Badge from "@/components/commerce/Badge";
import Rule from "@/components/editorial/Rule";
import Folio from "@/components/editorial/Folio";
import SectionLabel from "@/components/editorial/SectionLabel";
import StatStrip from "@/components/commerce/StatStrip";
import RailStatus from "@/components/commerce/RailStatus";

const lifecycle = [
  ["Intent", "Your brief becomes typed constraints."],
  ["Offer", "Every candidate is checked before it appears."],
  ["Contract", "The accepted promise set is frozen."],
  ["Payment", "Authorization opens a controlled Razorpay rail."],
  ["Delivery", "Synthetic or live evidence is reconciled."],
  ["Remedy", "Rights and money actions stay policy-gated."],
] as const;

export default function LandingPage() {
  return (
    <main className="landing-page dante-container py-8 md:py-10">
      <Folio issue="PROJECT DANTE / OVERVIEW" running="BUYER-OWNED COMMERCE" />

      <div className="landing-context-row mt-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="folio-label text-action-deep">Workspace overview</p>
          <p className="mt-1 text-sm text-ink-soft">Aster Electronics · buyer-owned commerce runtime</p>
        </div>
        <Badge tone="success">Runtime online</Badge>
      </div>

      <section className="landing-hero-grid mt-8 grid gap-6 lg:grid-cols-[minmax(0,1.45fr)_minmax(20rem,0.75fr)]">
        <div className="landing-hero-card rounded-xl border border-rule bg-paper-bright p-7 shadow-[0_8px_28px_rgba(16,24,40,0.04)] md:p-10">
          <SectionLabel>Buyer-owned commerce · issue 00</SectionLabel>
          <h1 className="landing-hero-title mt-5 max-w-3xl text-[clamp(2.4rem,5.5vw,5.2rem)] font-semibold leading-[0.98] tracking-[-0.055em] text-ink">
            Commerce that remembers the promise.
          </h1>
          <p className="mt-6 max-w-2xl text-base leading-relaxed text-ink-soft md:text-lg">
            Dante turns intent into a checked bundle, freezes the terms that made it acceptable,
            and keeps watching after the payment. When reality moves, the purchase already knows
            what the buyer is entitled to.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <ButtonLink href="/buy" size="lg">
              Start a buying brief <span aria-hidden="true">→</span>
            </ButtonLink>
            <ButtonLink href="/demo" variant="secondary" size="lg">
              Open demo room <span aria-hidden="true">↗</span>
            </ButtonLink>
          </div>
          <div className="landing-proof-note mt-8 flex flex-wrap gap-x-5 gap-y-2 border-t border-rule pt-4">
            <span>typed intent</span>
            <span>frozen promise</span>
            <span>verified remedy</span>
          </div>
        </div>

        <aside className="landing-control-card relative overflow-hidden rounded-xl border border-rule bg-[#101828] p-6 text-white shadow-[0_8px_28px_rgba(16,24,40,0.10)] md:p-7">
          <div className="absolute right-[-2rem] top-[-2rem] h-32 w-32 rounded-full bg-action/30 blur-2xl" aria-hidden="true" />
          <div className="relative flex items-center justify-between gap-3">
            <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/55">Dante control plane</span>
            <span className="h-2 w-2 rounded-full bg-[#32d583]" aria-label="Healthy" />
          </div>
          <div className="relative mt-12">
            <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/55">Promise ledger</p>
            <p className="mt-2 text-4xl font-semibold tracking-[-0.05em]">Always on.</p>
            <p className="mt-3 text-sm leading-relaxed text-white/65">
              Intent, authorization, fulfillment evidence and remedy decisions share one auditable
              contract identity.
            </p>
          </div>
          <div className="relative mt-10 border-t border-white/15 pt-4">
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="text-white/65">Payment rail</span>
              <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-[#98b8ff]">Razorpay compatible</span>
            </div>
          </div>
        </aside>
      </section>

      <section aria-label="Live system statistics" className="mt-6">
        <StatStrip />
      </section>

      <section className="landing-lifecycle mt-12 rounded-xl border border-rule bg-paper-bright p-6 md:p-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <SectionLabel>One purchase, six controlled states</SectionLabel>
            <h2 className="mt-3 text-2xl font-semibold tracking-[-0.03em] text-ink">The contract stays alive after checkout.</h2>
          </div>
          <Badge tone="signal">Promise rail</Badge>
        </div>
        <div className="mt-7 grid gap-px overflow-hidden rounded-lg border border-rule bg-rule md:grid-cols-3 xl:grid-cols-6">
          {lifecycle.map(([title, body], index) => (
            <div key={title} className="bg-paper-bright p-4">
              <span className="font-mono text-[10px] font-medium text-action">{String(index + 1).padStart(2, "0")}</span>
              <h3 className="mt-4 text-base font-semibold text-ink">{title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-soft">{body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-surface-grid mt-6 grid gap-6 md:grid-cols-3" aria-label="Dante surfaces">
        {[
          ["Promise Ledger", "The exact offer facts and hard constraints frozen before money moves.", "/buy", "Build a contract"],
          ["Purchase Rights Graph", "Entitlements derived from the contract, evidence and verified breach state.", "/demo", "Run the proof"],
          ["Append-only audit", "Every agent decision and money effect attached to a traceable event.", "/demo", "Open demo room"],
        ].map(([title, body, href, cta]) => (
          <Link key={title} href={href} className="group rounded-xl border border-rule bg-paper-bright p-6 transition-shadow hover:shadow-[0_8px_24px_rgba(16,24,40,0.06)]">
            <p className="folio-label text-ink-soft">Dante surface</p>
            <h2 className="mt-4 text-xl font-semibold tracking-[-0.025em] text-ink">{title}</h2>
            <p className="mt-3 text-sm leading-relaxed text-ink-soft">{body}</p>
            <span className="mt-6 inline-flex items-center gap-2 text-sm font-semibold text-action group-hover:text-action-deep">{cta} <span aria-hidden="true">→</span></span>
          </Link>
        ))}
      </section>

      <Rule className="mt-10" />
      <footer className="flex flex-col gap-3 py-6 md:flex-row md:items-center md:justify-between">
        <span className="folio-label">Project Dante · agentic commerce runtime</span>
        <RailStatus />
      </footer>
    </main>
  );
}
