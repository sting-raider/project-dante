# UI SYSTEM HANDOFF — Agent G (Frontend Design System)

Status: **COMPLETE — tsc 0 errors app-wide, `next build` GREEN (all routes generated).**

---

## Goal

Deliver the frozen editorial magazine visual system (master plan §27) plus shared
primitives so Agents H and I could build pages against a stable, self-consistent
kit: tokens, fonts, layout conventions, editorial motifs, commerce semantics
(badges/marks/money), UI controls, typed API client, formatters, and the landing
page (§28 `/`).

## Completed

- Tailwind v4 CSS-first theme (`@theme`) with the full frozen palette + radius caps.
- next/font wiring: Instrument Serif / Inter / IBM Plex Mono exposed as
  `font-display` / `font-body` / `font-mono` utilities via `@theme inline`.
- House utilities: `folio-label`, `tabular`, `text-vertical`, `dante-container`.
- Editorial components (Rule, SectionLabel, Folio, PullQuote, Dateline,
  StatNumeral w/ rolling animation, MarginNote).
- Commerce components (Badge, ConstraintMark + inline variant, MoneyText,
  SyntheticBadge, SandboxBadge, StatStrip).
- UI primitives (Button/ButtonLink, Panel, Table family).
- `lib/api.ts` typed client (ApiError, apiGet/apiPost/apiTry), `lib/format.ts`
  money/datetime/hash formatters, `lib/design.ts` TS token mirror +
  statusTone(), `lib/types.ts` frontend mirrors of frozen domain models.
- Landing page `/`: giant serif PROJECT DANTE masthead, thesis line, core line,
  nav to /buy /merchant /demo, live stats strip with graceful em-dash fallback,
  lifecycle strip, pull-quote band, footer. Launch buyer CTA → /buy.

## Files (all under `apps/web/`, absolute: `X:\RazorPay Buildathon\apps\web\`)

| File | Purpose |
|---|---|
| `app/globals.css` | @theme palette/radius/fonts, base styles, house utilities |
| `app/layout.tsx` | next/font loading (vars: --font-instrument-serif/--font-inter/--font-plex-mono), metadata |
| `app/page.tsx` | landing page per plan §28 |
| `lib/design.ts` | palette consts, statusTone(status), MOTION consts |
| `lib/format.ts` | formatINR(paise), formatINRExact, formatDateTime, formatDate, formatTime, shortHash(10 chars), formatPct, prettyJson, payloadSummary |
| `lib/api.ts` | API const, ApiError{status,message,url}, apiGet<T>, apiPost<T>, apiTry<T> (null-on-failure) |
| `lib/types.ts` | ContractStatus, BuyerIntent, MerchantOffer, DanteContract, DomainEvent, TIMELINE_CATEGORIES |
| `components/editorial/*` | Rule, SectionLabel, Folio, PullQuote, Dateline, StatNumeral, MarginNote |
| `components/commerce/*` | Badge, ConstraintMark (+ConstraintMarkInline), MoneyText, SyntheticBadge, SandboxBadge, StatStrip |
| `components/ui/*` | Button + ButtonLink, Panel, Table/THead/TH/TBody/TR/TD |

## Component APIs (exact signatures — consume these)

All default exports unless noted. All accept `className`. Server-safe except
where marked "client".

### editorial/

```ts
// Rule.tsx — <hr>-based separator
<Rule weight?: "hairline"|"ink"|"double"|"signal" />   // hairline=1px rule; ink/signal=3px bar; double=newsprint

// SectionLabel.tsx
<SectionLabel orientation?: "inline"|"vertical" />      // children = label text; folio typography

// Folio.tsx — thin print-folio strip (NOT site nav)
<Folio issue: string running?: string href?: string />

// PullQuote.tsx — buyer brief / thesis quotes
<PullQuote attribution?: string accent?: "ink"|"signal" size?: "md"|"lg" />

// Dateline.tsx — mono timestamp; renders "—" when iso missing
<Dateline iso: string|null|undefined precision?: "date"|"datetime"|"time" prefix?: string />

// StatNumeral.tsx [client] — oversized numeral, rolls up in-view, reduced-motion safe
<StatNumeral value: number format?: (v:number)=>string caption?: string prefix?: string suffix?: string />

// MarginNote.tsx — side annotation for grid side-columns
<MarginNote marker?: string /* default "†" */ />
```

### commerce/

```ts
// Badge.tsx — SATISFIED/BREACH/etc; tone auto-derived from text via statusTone(); text always rendered
<Badge children: string tone?: "neutral"|"success"|"warning"|"danger"|"signal" />
// NOTE: named export also available: export type { BadgeTone }

// ConstraintMark.tsx — pass/fail row with glyph + PASS/FAIL word (never color-only)
<ConstraintMark label: string status: "pass"|"fail"|"unknown" detail?: string />
// named export for dense spreads:
import { ConstraintMarkInline } from "@/components/commerce/ConstraintMark";
<ConstraintMarkInline label: string status: "pass"|"fail"|"unknown" />

// MoneyText.tsx — integer paise in, ₹ en-IN out; "—" when null
<MoneyText paise: number|null|undefined precise?: boolean size?: "sm"|"md"|"lg"|"xl" />

// SyntheticBadge.tsx — SYNTHETIC chip when synthetic=true, renders null otherwise
<SyntheticBadge synthetic: boolean />

// SandboxBadge.tsx — LIVE · TEST MODE vs SANDBOX rail chip; always visible
<SandboxBadge sandbox: boolean|undefined />

// StatStrip.tsx [client] — landing stats; fetches /api/merchant/analytics + /api/health; em-dashes when down
<StatStrip />   // no props
```

### ui/

```ts
// Button.tsx [client] — named exports Button AND ButtonLink (next/link flavored)
import { Button, ButtonLink } from "@/components/ui/Button";
variant?: "primary"|"secondary"|"ghost"   // primary = signal ground/paper text
size?: "sm"|"md"|"lg"

// Panel.tsx — bordered paper panel, no shadow
<Panel label?: string aside?: ReactNode tone?: "paper"|"bright" />

// Table.tsx — overflow contained inside wrapper (mobile-safe)
import Table, { THead, TH, TBody, TR, TD } from "@/components/ui/Table";
// TH/TD take numeric?: boolean (right-align); TD takes mono?: boolean
```

### lib/

```ts
formatINR(paise)            // ₹11,499 (maximumFractionDigits 0)
formatINRExact(paise)       // ₹11,499.00 for audit surfaces
formatDateTime(iso)         // "25 Aug 2026, 4:32 pm"
shortHash(h)                // first 10 chars, "—" if falsy
prettyJson(v)               // indented JSON for audit viewers; "—" when empty
payloadSummary(payload)     // one-line "k=v, k=v" event summary
apiGet<T>(path), apiPost<T>(path, body?)   // throw ApiError{status,message,url} on failure
apiTry<T>(path)             // resolves null instead of throwing — use for stat strips
API                         // base URL from NEXT_PUBLIC_API_URL ?? http://localhost:8000
statusTone(status)          // ContractStatus -> badge tone ("SATISFIED"/"REMEDIATED"→success etc.)
palette                     // raw hex consts for SVG/canvas work
```

## Design conventions (frozen — follow these in pages)

- Palette ONLY via utilities: `bg-paper bg-paper-bright text-ink text-ink-soft
  border-rule text-signal text-success text-warning text-danger`. Raw hex is
  reserved for lib/design.ts consumers (SVG/graphs).
- Layout: `dante-container` (max-w-[1400px], px-6 md:px-12, mx-auto) +
  `grid-cols-12` inside. Section separators are full-width `<Rule/>`s, not gaps.
- Folio typography: `folio-label` utility (mono/uppercase/tracking-widest/xs/
  ink-soft). Headings get font-display automatically from globals base layer.
- Radius max 6px (theme caps rounded-xl/2xl/3xl). No shadows. No gradients.
- Motion: `motion/react` only, 8–16px slides, 300–500ms ease-out, wrap with
  `useReducedMotion()`. StatNumeral shows the pattern.
- Accessibility: semantic landmarks; focus-visible = 2px signal outline offset
  2px (global); badges carry literal text; ConstraintMark carries glyph + word;
  tables scroll internally.

## Tests / build results

- `npx tsc --noEmit`: **0 errors across the entire apps/web tree** (final run,
  after H/I landed their in-flight fixes). Owned files were clean throughout;
  I additionally unblocked Agent I's timeline page by adding the two helpers it
  imports from lib/format (`prettyJson`, `payloadSummary`).
- `npx next build`: **GREEN** — compiled + type-checked + static generation of
  all 7 pages succeeded. Route sizes: / 1.96 kB (108 kB first load),
  /buy 4.47 kB, /merchant 7.96 kB, dynamic contract/audit routes as listed.
- Not run: dev server (per instructions).

## Known risks

0. **`palette` in lib/design.ts is a cross-agent contract** — Agent I's
   `components/rights-graph/RightsGraph.tsx` imports `{ palette }` and reads its
   keys (`paper`, `ink`, `rule`, `signal`, `success`, `warning`, `danger`,
   ...). Keys are FROZEN; additive-only changes, never renames.
1. **Agent H's local atoms barrel duplicates this kit** (`app/buy/_components/atoms.tsx`
   re-exports Rule/Badge/etc. locally). If both survive, there will be two sources
   of truth for primitives. Recommend integration keeps `components/**` as canon
   and points H's barrels at `@/components/...`.
2. Fonts load from Google via next/font at build time — offline builds fall back
   to the stack in globals (`ui-serif Georgia…`, system-ui, monospace). Acceptable
   degradation, but first build should be online.
3. `StatStrip` reads `/api/merchant/analytics` fields
   (`total_products`, `ai_transactable_rate`, `evaluated_intents`) matching Agent F's
   implemented route; if analytics shape grows, extend the local type in StatStrip.
4. `apiTry` swallows errors by design (landing resilience) — don't use it where a
   real error state must surface to the user.

## Notes for H + I

- Import path alias `@/*` maps to `apps/web/*` (e.g. `@/components/ui/Panel`).
- `formatINR` takes **paise** (integers straight off the wire). Never divide yourself.
- Status badges: just render the literal status string (`<Badge>{c.status}</Badge>`);
  tone resolution is automatic via `statusTone`. Precedence: an **explicit
  `tone` prop always wins** over auto-derivation — existing call sites that
  pass `tone="neutral"` etc. keep working unchanged through the atoms swap;
  auto-derivation only fills the gap when `tone` is omitted. Only constraint:
  `children` must be a string.
- Breach spread (§28): use `<Rule weight="signal"/>` for the red breach line and
  `accent="signal"` PullQuote for PROMISED/OBSERVED headers.
- Demo surfaces: every synthetic row gets `<SyntheticBadge synthetic={ev.synthetic}/>`;
  payment panels get `<SandboxBadge sandbox={contract.sandbox_mode}/>`.
