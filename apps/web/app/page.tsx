import Link from "next/link";
import { ButtonLink } from "@/components/ui/Button";
import Rule from "@/components/editorial/Rule";
import SectionLabel from "@/components/editorial/SectionLabel";
import StatStrip from "@/components/commerce/StatStrip";

const nav = [
  { href: "/buy", label: "Buy" },
  { href: "/merchant", label: "Merchant" },
  { href: "/demo", label: "Demo" },
];

export default function LandingPage() {
  return (
    <div className="min-h-dvh">
      {/* Masthead / site nav */}
      <header className="dante-container flex items-center justify-between border-b border-rule py-4">
        <Link href="/" className="font-mono text-sm font-semibold uppercase tracking-[0.2em] text-ink">
          Dante
        </Link>
        <nav aria-label="Primary" className="flex items-center gap-6">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="folio-label underline-offset-4 hover:text-signal hover:underline"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </header>

      <main>
        {/* Hero — giant serif masthead (plan §28) */}
        <section className="dante-container pt-16 pb-14 md:pt-24 md:pb-20">
          <p className="folio-label mb-6">Issue 01 · Agentic Commerce Quarterly</p>
          <h1 className="font-display text-[clamp(3.5rem,12vw,10rem)] leading-[0.92] tracking-[-0.03em] text-ink">
            PROJECT DANTE
          </h1>
          <div className="mt-8 grid grid-cols-1 gap-10 md:grid-cols-12">
            <p className="font-display text-2xl italic leading-snug text-ink md:col-span-5 md:text-[1.75rem]">
              Commerce that stays responsible after checkout.
            </p>
            <div className="md:col-span-7 md:pl-8 lg:pl-16">
              <Rule weight="hairline" />
              <p className="mt-6 max-w-prose text-base leading-relaxed text-ink-soft md:text-lg">
                Payments remember that you paid. Dante remembers what you paid for — the intent,
                the frozen promises that made the offer acceptable, and the rights those promises
                created. When reality diverges, the contract knows.
              </p>
              <div className="mt-8 flex items-center gap-4">
                <ButtonLink href="/buy" size="lg">
                  Launch buyer →
                </ButtonLink>
                <Link href="/merchant" className="folio-label hover:text-signal">
                  Merchant desk
                </Link>
              </div>
            </div>
          </div>
        </section>

        <Rule weight="ink" />

        {/* Stats strip — live from the API, em-dashes when down */}
        <section aria-label="Live system statistics" className="dante-container py-10">
          <SectionLabel>State of the runtime</SectionLabel>
          <div className="mt-5">
            <StatStrip />
          </div>
        </section>

        <Rule weight="double" />

        {/* Lifecycle strip */}
        <section className="dante-container py-14">
          <SectionLabel>The contract stays alive</SectionLabel>
          <ol className="mt-6 grid grid-cols-1 gap-x-8 gap-y-6 md:grid-cols-3 lg:grid-cols-6">
            {[
              ["01", "Intent", "The buyer's brief, compiled into hard constraints."],
              ["02", "Offer", "Candidates marked against every constraint."],
              ["03", "Contract", "Promises frozen at selection time."],
              ["04", "Payment", "Razorpay test-mode order, buyer authorized."],
              ["05", "Delivery", "Observed facts checked against promises."],
              ["06", "Remedy", "Rights derived; refund executed under policy."],
            ].map(([n, title, body]) => (
              <li key={n} className="border-t border-rule pt-3">
                <span className="folio-label">{n}</span>
                <h2 className="mt-1 font-display text-xl text-ink">{title}</h2>
                <p className="mt-1 text-sm leading-relaxed text-ink-soft">{body}</p>
              </li>
            ))}
          </ol>
        </section>

        <Rule weight="hairline" />

        {/* Thesis pull-quote band */}
        <section className="bg-paper-bright">
          <div className="dante-container py-16">
            <blockquote className="mx-auto max-w-3xl text-center font-display text-3xl leading-tight tracking-[-0.01em] text-ink md:text-[2.5rem]">
              “A payment is an event. A promise kept — or broken — is a record.
              Dante keeps the record.”
            </blockquote>
            <p className="folio-label mt-6 text-center">Editorial note · Project Dante</p>
          </div>
        </section>

        <Rule weight="hairline" />
      </main>

      <footer className="dante-container py-10">
        <div className="flex flex-col items-start justify-between gap-4 md:flex-row md:items-baseline">
          <span className="folio-label">Project Dante · Razorpay AI Buildathon</span>
          <span className="folio-label">Payments on Razorpay Test Mode · No real money moves</span>
        </div>
      </footer>
    </div>
  );
}
