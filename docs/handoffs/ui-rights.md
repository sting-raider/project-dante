# HANDOFF — Agent I (Breach / Rights / Audit UI)

**Date:** 2026-08-25
**Scope:** plan §28 pages `/contract/[id]/timeline`, `/contract/[id]/breach`,
`/contract/[id]/rights`, `/contract/[id]/remedy`, `/merchant`, `/audit/[id]`,
`/demo`; plus the custom SVG `RightsGraph` component.

## Goal

Build the rights-side editorial surfaces of Project Dante against the frozen
API contract (docs/API_CONTRACT.md) and Agent G's design system: the event
trace, the red breach spread, the rights graph with entitlement drawer, the
gated remedy pipeline UI, the merchant analytics newspaper, the raw audit
dossier, and the demo control room with a one-click hero orchestrator.

## Completed

- **RightsGraph** (`components/rights-graph/RightsGraph.tsx`) — pure-SVG,
  force-free deterministic layered layout (purchase → promises → breach →
  entitlements → evidence → remedies). Node vocabulary: purchase solid square,
  promise outlined square, entitlement status-colored rectangle
  (eligible=green outline, blocked=warning, invalid=muted danger,
  dormant/consumed/expired=gray), breach red diamond, evidence small circle,
  remedy triangle. Orthogonal edges with tiny mono labels; FALLBACK_TO/BLOCKS
  dashed. Click/keyboard-select emits `onSelect`. `<title>` per node + graph
  summary aria-label + figcaption; motion/react fade on status change honoring
  `prefers-reduced-motion`.
- **Timeline page** — three-column trace (mono time | event_type + category
  chip | payload summary + expandable `<details>` full JSON). Filter chips for
  All/Agent/Money/Merchant/Fulfillment/Policy/Evidence wired to
  `?category=`. Synthetic events badged. Polls every 3s while contract not in
  a terminal state.
- **Breach page** — full-page red spread: signal-colored giant serif headline,
  PROMISED vs OBSERVED two-column comparison built from material promises +
  `GET .../breaches`, ✗ MISMATCH / ✓ HELD text-labeled row marks (color never
  sole indicator), "MATERIAL TO ORIGINAL INTENT" verdict block, hashed
  evidence list with trusted-level chips + SYNTHETIC badges, links to rights
  and remedy pages. Falls back to `POST .../verify` results when the breaches
  endpoint is empty.
- **Rights page** — RightsGraph top, side drawer on node select with
  entitlement detail (issuer, type, status Badge, expiry, required evidence
  types, remedy_value MoneyText, estimated_resolution_hours, fallback/block
  relationships, activation predicates JSON). Edge-type legend section. Light
  5s re-poll so eligibility recolors live after `replacement-unavailable`.
- **Remedy page** — ranked proposal cards (rank numeral, remedy type, amount
  MoneyText) with visible score bars using the §14.2 weights (value 0.40 /
  intent-restoration 0.35 / speed 0.15 / inconvenience −0.10), explanation,
  prominent `REJECTED: <reason>` for non-selected candidates. Action panel:
  `POST /api/remedies/{id}/policy` renders ALLOW as green
  "AUTO-APPROVED BY POLICY P-…" citing policy_ids (auto-executes),
  REQUIRE_APPROVAL as an approval card whose [Approve] calls `/approve` then
  executes, DENY shown red with reason codes. Execute shows result_ref,
  REMEDIATED success state resolving green; idempotency key rendered mono;
  reason_code + human_explanation always visible.
- **Merchant page** — business-newspaper dashboard from
  `GET /api/merchant/analytics`: masthead, StatNumeral row
  (ai_transactable_rate %, total_products, warranty_metadata_coverage %,
  evaluated intents), blocker_distribution horizontal bar rows with readable
  constraint labels, machine-readable coverage meters, recommendation pull
  quote when the API provides one, honesty notice that fulfillment is
  synthetic. Every metric tolerates absence — zeros, never NaN.
- **Audit page** — dense mono dossier: full contract record table with
  complete-length hashes, money actions extracted from the event stream with
  policy snapshot hashes + idempotency keys, agent runs (inputs/outputs only),
  Razorpay order/payment ids, webhook events with duplicate-suppression flags,
  unfiltered complete event stream table (time | event | category |
  idempotency_key | trace_id | synthetic flags), SandboxBadge everywhere.
- **Demo page** — DEMO SIMULATION CONTROL header strip with the exact warning
  line. Manual controls: reset, ship, deliver (correct/wrong_variant/late
  dropdown), replacement-unavailable. One-click "Run hero scenario"
  orchestrator chains compile hero intent → search → select first feasible →
  authorize → payment-order → capture (sandbox: signed simulate-event; live:
  instructs manual checkout payment) → ship → deliver wrong_variant →
  replacement-unavailable → plan remedies → policy verdict → execute refund,
  logging each step to a timestamped mono ticker with per-step pass/fail;
  errors halt the chain with a visible message and link into the dossier.

## Files

Owned + written by this agent:

- `apps/web/components/rights-graph/RightsGraph.tsx`
- `apps/web/app/contract/[id]/timeline/page.tsx`
- `apps/web/app/contract/[id]/breach/page.tsx`
- `apps/web/app/contract/[id]/rights/page.tsx`
- `apps/web/app/contract/[id]/remedy/page.tsx`
- `apps/web/app/merchant/page.tsx`
- `apps/web/app/audit/[id]/page.tsx`
- `apps/web/app/demo/page.tsx`
- `apps/web/lib/rights-ui.ts` (additive shared types/helpers for these pages)

Consumed (not modified): `lib/api.ts`, `lib/format.ts`, `lib/design.ts`,
`lib/cn.ts`, all `components/editorial|commerce|ui/*` from Agent G.

## Component APIs

### `RightsGraph` (default export)

```ts
type GraphNode = { id: string; type: string; label: string; status?: string; [k: string]: unknown };
type GraphEdge = { source: string; target: string; type: string };
type RightsGraphProps = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onSelect?: (node: GraphNode) => void; // click or Enter/Space; keyboard focusable
  selectedId?: string | null;
  className?: string;
  ariaLabel?: string;   // overrides auto-generated summary
};
```

Node `type` drives both shape and layout row (`purchase|promise|entitlement|
breach|evidence|remedy`, unknown types sink last). Entitlement color comes
from `status`. Edges are routed orthogonally between rows; same-row edges dip
below. The component is dependency-light (only `motion/react`).

### `lib/rights-ui.ts`

Domain mirrors (`DanteContractFull`, `PromiseRec`, `EvidenceArtifactRec`,
`Breach`, `Entitlement`, `RemedyProposal`, `PolicyDecision`, `MoneyAction`),
response types (`ContractResponse`, `TimelineResponse`, `VerifyResponse`,
`RightsResponse`, `RemediesResponse`, `PolicyResponse`, `ApproveResponse`,
`ExecuteResponse`, `MerchantAnalytics`, demo types), `isTerminal()`,
`TERMINAL_STATUSES`, and `normalizeEdges()` which maps the backend's `kind`
field to the graph's `type`.

## Build results

- `npx tsc --noEmit` → **PASS (exit 0)** — gate green.
- `npm run build` (next build) → **PASS**, all routes compiled:

```
ƒ /audit/[id]                5.09 kB   111 kB
ƒ /contract/[id]/breach      5.4 kB    111 kB
ƒ /contract/[id]/remedy      6.6 kB    156 kB
ƒ /contract/[id]/rights      7.76 kB   157 kB
ƒ /contract/[id]/timeline    4.58 kB   111 kB
○ /demo                      5.73 kB   155 kB
○ /merchant                  7.96 kB   137 kB
```

## Known risks

1. **Evidence panel sourcing** — `GET /api/contracts/{id}` doesn't return
   artifact records directly; the breach page reconstructs them from
   Evidence-category timeline events (`payload.artifact`). If Agent D changes
   that payload key, update `app/contract/[id]/breach/page.tsx` (~line 90).
2. **Observed values on the breach spread** are best-effort extracted from
   breach `explanation` strings (regex on got/observed/actual/found). When the
   verifier starts returning observed facts inline, swap `extractObserved()`.
3. **Audit money actions** are derived from POLICY_*/REFUND_* event payloads
   (`money_action` key); if Agent E nests it differently, adjust the audit
   page's derivation effect.
4. **Remedy auto-execute on ALLOW** follows plan §52 semantics (policy ALLOW =
   autonomous). If judges should always confirm, insert an explicit button
   before `execute(proposalId)` in `runPolicy`.
5. Hero orchestrator assumes sandbox mode for the capture step; in
   live-test-mode it pauses semantically (prints instruction) but continues
   fulfillment steps so the arc still demos end-to-end.
6. `speedScore()` maps estimated_time_hours→0..1 linearly over 72h; planner
   scores are displayed as bars, not recomputed totals — ranking authority
   stays server-side.

## Notes

- Backend edge payloads use `kind`; `normalizeEdges()` in lib/right-ui.ts is
  the single adaptation point.
- All polling stops at terminal statuses (SATISFIED/REMEDIATED/CANCELLED/
  FAILED) and the timeline also halts when it observes
  CONTRACT_SATISFIED/CONTRACT_REMEDIATED events.
- Reduced motion respected globally via globals.css plus per-component
  `useReducedMotion`.
- No git commits made, per instructions. Only owned files touched.
- Coordination: initially drafted standalone lib/api/types/format files when
  apps/web was empty; Agent G landed canonical versions mid-flight and my
  drafts were superseded — pages import G's modules exclusively now.
