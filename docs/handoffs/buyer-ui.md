# Handoff: Agent H — Buyer / Contract UI

## Goal

`/buy` buyer desk + `/contract/[id]` dossier + client flow state machine,
per master plan §28 / §52 and the frozen `docs/API_CONTRACT.md`.

## Completed

- `/buy`: editorial pull-quote brief (hero text prefilled), BUYING BRIEF parsed-constraints column (hard vs soft, spend authority), agent activity ticker with error/retry states, offer comparison spread — feasible rows radio-selectable, infeasible rows grayed with exact hard-failure reasons always visible. Two-step selection (choose → freeze) → navigates to contract.
- `/contract/[id]`: intent recap pull quote · selected offer panel · WHY SELECTED rationale (soft scores) · MATERIAL PROMISES table (key/value/material flag/material_reason/verification chips) · authorization envelope (post-authorize, incl. hash-drift warning) · CONTRACT HASHES mono panel · RAZORPAY panel (mode chip, order/payment ids, live status) · RIGHTS overview count linking to rights graph. PAID banner links to timeline.
- Sticky §52 "YOU ARE ABOUT TO AUTHORIZE" bar at AWAITING_BUYER_AUTH: amount, material-promise checkmarks, [Authorize & Open Razorpay].
- Payment flows exactly per API_CONTRACT.md: sandbox → POST /api/demo/razorpay/simulate-event {event_type:"payment.captured", order_id} then poll; live-test-mode → next/script checkout.js lazyOnload, Standard Checkout handler → POST /api/payments/verify-client then poll. Window-dismiss fallback resumes polling (§33.5). Client success never treated as truth — 2s polling until server state leaves PAYMENT_*.
- Local editorial/commerce atoms under `app/buy/_components/atoms.tsx` styled to Agent G's frozen token spec; re-exported to contract tree via shim. Swap point for components/editorial|commerce at integration.

## Files changed

- `apps/web/lib/useContractFlow.ts` (hook + mirrored domain types + session handoff helpers)
- `apps/web/app/buy/page.tsx`, `app/buy/_components/{atoms,BuyingBrief,ActivityTicker,OfferSpread}.tsx`
- `apps/web/app/contract/[id]/page.tsx`, `_components/{atoms-shim,AuthorizationCard,ContractHashes,MaterialPromises,RazorpayPanel,SelectedOfferPanel}.tsx`

No other agents' files touched. Not committed (per instruction).

## Public interfaces created

- `useContractFlow()` — phases idle→compiling→searching→shortlist→selecting→navigating→awaiting_authorization→creating_order→sandbox_ready|checkout_ready→payment_pending→paid (+error_* with retry). Exposes compileAndSearch, chooseOffer/selectOffer, loadContract, authorize, createPaymentOrder, verifyClient, simulateSandboxCapture, recheckStatus, pollUntilResolved, pollingActive.
- `shortHash(h,n=10)`, `rupees(paise)` formatters; `rememberOfferSelection/rememberBuyerBrief/readOfferSelection/BRIEF_SESSION_KEY` sessionStorage handoff (/buy → /contract offer snapshot + evaluator rationale, since GET /api/contracts/{id} carries no offer snapshot).

## Tests

`tsc --noEmit` scoped to my paths: **clean**. Remaining repo errors are in
`app/audit/[id]/page.tsx`, `app/merchant/page.tsx`, `app/contract/[id]/remedy/page.tsx`
(Agent I territory — flagged to them). No runtime E2E yet (API routes still landing).

## Known risks / integration notes

1. Domain types are hand-mirrored from `project_dante/domain/types.py`; additive-only drift tolerated (`scope?` added on AuthorityEnvelope). If backend adds required fields, update the mirror.
2. Offer memo lives in sessionStorage → cold refresh of /contract shows promise-derived facts only; page degrades gracefully by design.
3. Sandbox simulate sends `payment_id: pay_sim_<order-suffix>`; if Agent B validates payment_id format differently, adjust `simulateSandboxCapture`.
4. When Agent G's primitives land: replace `app/buy/_components/atoms` contents (single swap point), delete contract-tree shim.
5. `next/script` onLoad gates checkout readiness; if Razorpay script blocked, authorize button surfaces a readable retry note.
