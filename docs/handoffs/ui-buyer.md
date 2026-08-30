# Handoff: Agent H — Buyer / Contract UI

## Goal

`/buy` buyer desk + `/contract/[id]` dossier + `useContractFlow` client state
machine, per master plan §27/§28/§52 and frozen `docs/API_CONTRACT.md`.
Ownership: `app/buy/**`, `app/contract/[id]/**` (page + _components only),
`lib/useContractFlow.ts`. Nothing committed; no other agent's files touched.

## Completed

- **/buy** (§28): pull-quote brief prefilled with hero text ("Buy me over-ear
  ANC headphones under ₹12,000…"), BUYING BRIEF column of parsed typed
  constraints (hard ✓ marks, soft prefs, spend authority), agent activity
  ticker ("Compiling intent… → Searching merchant… → Evaluating offers… →
  Freezing promises…"), offer comparison spread: feasible rows radio-selectable
  then confirmed via [Freeze & open contract]; infeasible rows grayed but fully
  visible with exact hard-failure reasons (expected vs got) and rejection notes.
- **/contract/[id]** (§28): intent recap pull quote · selected offer panel
  (title/sku/price/delivery/warranty/returns) · WHY SELECTED rationale from
  evaluator soft scores · MATERIAL PROMISES table (key/value/material flag/
  material_reason/verification chips) · authorization envelope panel with
  hash-drift warning · CONTRACT HASHES mono panel · RAZORPAY panel (mode chip,
  order/payment ids, live polling status) · RIGHTS overview count chip linking
  to rights graph. PAID banner "PAID — verified by webhook truth" links to
  timeline.
- **§52 sticky authorization bar** at AWAITING_BUYER_AUTH: amount, product
  title, contract code, material-promise checkmarks, [Authorize & Open
  Razorpay].
- **Payment flows**: sandbox → notice "SANDBOX MODE — no Razorpay keys
  configured" + [Simulate test payment (SANDBOX)] → POST
  `/api/demo/razorpay/simulate-event {event_type:"payment.captured", order_id}`
  → poll to PAID. live-test-mode → checkout.js via next/script lazyOnload →
  Standard Checkout (`checkout_config.key_id` mapped to the checkout.js `key`
  option — never `key_id`; name ASTER ELECTRONICS, description = title,
  prefill Demo Buyer, theme #F04A2D) → handler POSTs
  `/api/payments/verify-client {contract_id, razorpay_order_id,
  razorpay_payment_id, signature}` → poll to PAID. Window-dismiss resumes
  polling (§33.5). Client success never treated as truth.
- Migrated onto Agent G's `lib/api.ts` (apiGet/apiPost/ApiError) once it
  landed; atoms still local in `app/buy/_components/atoms.tsx` (styled to
  G's token spec, utilities verified identical) with a re-export shim for the
  contract tree.

## Files

- `apps/web/lib/useContractFlow.ts`
- `apps/web/app/buy/page.tsx`
- `apps/web/app/buy/_components/{atoms,BuyingBrief,ActivityTicker,OfferSpread}.tsx`
- `apps/web/app/contract/[id]/page.tsx`
- `apps/web/app/contract/[id]/_components/{atoms-shim,AuthorizationCard,ContractHashes,MaterialPromises,RazorpayPanel,SelectedOfferPanel}.tsx`

## API of useContractFlow

States: `idle → compiling → searching → shortlist → awaiting_selection →
freezing → awaiting_authorization → opening_checkout → checkout_ready |
sandbox_ready → payment_pending → paid`, plus
`error_{compile,search,select,contract_load,authorize,order,verify,poll}`.

Exposes: `compileAndSearch(rawText)`, `chooseOffer(id)` (local),
`selectOffer(id)` (network, returns contract id), `loadContract(id)` (resumes
polling when payment pending), `authorizeAndOpenCheckout(id, openCheckout)`
(full §52 sequence; injected opener receives `{mode, checkout_config}`),
`simulateSandboxPayment()`, `verifyClient(id, rzpResponse)`,
`recheckStatus(id)`, `pollUntilResolved(id)`, `refreshContract(id)`,
`resetError()`; state: `phase, intent, engine, results, selectedOfferId,
contract, promises, entitlements, orderInfo, error, verifyNote,
isBusy, pollingActive`.

Helpers exported: `rupees(paise)`, `shortHash(h, n)`,
`rememberOfferSelection(contractId, memo)` / `rememberBuyerBrief(text)` /
`readOfferSelection(id)` / `BRIEF_SESSION_KEY` (sessionStorage handoff since
GET /api/contracts/{id} carries no offer snapshot; page degrades gracefully on
cold refresh). Mirrored domain types from `project_dante/domain/types.py`
(additive-only: `scope?` on AuthorityEnvelope).

## Build results

- `tsc --noEmit`: 0 errors in my paths; full-repo tsc green after Agent I's
  fixes landed.
- `next build`: all 11 routes pass, including /buy (4.47 kB) and
  /contract/[id] (7.54 kB).

## Known risks

1. Sandbox simulate relies on documented endpoint shape
   `{event_type, order_id}`; if B's route requires `payment_id`, add it back
   in `simulateSandboxCapture` (one line).
2. Offer memo is sessionStorage-scoped: cold refresh loses WHY-SELECTED /
   selected-offer detail (promise table still renders from API).
3. Razorpay script blocked → authorize surfaces readable retry note; onLoad
   gate tracked via `rzpScriptReady`.

## Notes for integration

- Single swap point for G's primitives: replace internals of
  `app/buy/_components/atoms.tsx` with re-exports from
  `@/components/editorial|commerce|ui`; delete the contract-tree shim.
  G's Badge auto-derives tone from status strings; Rule supports
  `weight="signal"` for breach lines.
- Poll interval constant `POLL_INTERVAL_MS = 2000` in the hook.
