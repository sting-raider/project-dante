# SCREENSHOTS — capture checklist (11 shots)

> Finish plan §35. One table row per shot: the exact page URL, the UI state to
> reach, and what MUST be visible before you press the shutter. The checked-in
> set below is a local sandbox capture; it is visual evidence of the product
> flow, not evidence of a live Razorpay gateway run.
>
> Pre-flight (docs/DEMO_SCRIPT.md): API on :8000, web on :3000, `verify_e2e.py`
> printing PASSED first so a fresh contract exists. Run the browser at ≥1440px
> wide; the editorial pages are built for wide viewports.

## Capture tracker

| # | File | Status |
|---|---|---|
| 01 | `docs/screenshots/01-landing.png` | CAPTURED — LOCAL UI |
| 02 | `docs/screenshots/02-buy-brief.png` | CAPTURED — SANDBOX RUN |
| 03 | `docs/screenshots/03-buy-offers.png` | CAPTURED — SANDBOX RUN |
| 04 | `docs/screenshots/04-contract-dossier.png` | CAPTURED — SANDBOX RUN |
| 05 | `docs/screenshots/05-payment-checkout.png` | CAPTURED — SANDBOX ONLY; NO REAL GATEWAY |
| 06 | `docs/screenshots/06-paid-webhook.png` | CAPTURED — SANDBOX ONLY; NO REAL GATEWAY |
| 07 | `docs/screenshots/07-demo-synthetic.png` | CAPTURED — SYNTHETIC FULFILLMENT |
| 08 | `docs/screenshots/08-breach.png` | CAPTURED — SANDBOX + SYNTHETIC |
| 09 | `docs/screenshots/09-rights-graph.png` | CAPTURED — SANDBOX + SYNTHETIC |
| 10 | `docs/screenshots/10-remedy-refund.png` | CAPTURED — SANDBOX + SYNTHETIC |
| 11 | `docs/screenshots/11-audit.png` + `docs/screenshots/11-merchant.png` | CAPTURED — SANDBOX + SYNTHETIC |

## Capture checklist

**For payment or fulfillment shots:** keep the SANDBOX vs LIVE-TEST-MODE badge
(whichever is true for the run) in frame and show the SYNTHETIC label wherever
fulfillment data appears. Editorial and input shots may not have a rail state
on screen yet.

| # | Page URL | UI state to reach | What must be visible |
|---|---|---|---|
| 01 | `http://localhost:3000/` | Landing at top of page, then scrolled to thesis line (two frames or one tall capture) | Masthead nav (Buy / Merchant / Demo); the thesis line "Payments remember that you paid. Dante remembers what you paid for…"; StatStrip numerals |
| 02 | `http://localhost:3000/buy` | Paste the hero brief verbatim: "Buy me over-ear ANC headphones under ₹12,000. I need an Indian manufacturer warranty, as they must arrive within 3 days, and do not show me anything over ₹12,000." → click **Compile**, stop right after constraints render | Brief shown as editorial pull quote; Buying Brief column with typed constraints (max price 1200000 paise · category headphones · ANC · over-ear · warranty.type=manufacturer · warranty.region=IN · delivery deadline); agent activity ticker below |
| 03 | `http://localhost:3000/buy` (same contract flow) | Scroll past the brief to the offer comparison spread after search completes | Ranked feasible offers incl. hero SKU; at least two infeasible cards still visible with their exact failure reasons (e.g. "over budget", "warranty region mismatch") — rejected-but-visible is the point of this shot |
| 04 | `http://localhost:3000/contract/[id]` | Click the hero offer → confirm freeze; dossier page loads before payment | Contract id + status chip; Promise Ledger rows with per-promise hashes and material flags; CONTRACT HASHES block (authorization hash vs current hash equal); authorization envelope card bound to the frozen hash |
| 05 | `http://localhost:3000/contract/[id]` (payment step) | Policy ALLOW on the authorization/Razorpay panel; click pay → Razorpay Standard Checkout window open alongside the page | Mode chip (LIVE-TEST-MODE or SANDBOX); real `order_…` id from checkout config; amount in ₹ matching contract amount_paise; the Razorpay checkout frame with test card `4111 1111 1111 1111` entered |
| 06 | `http://localhost:3000/contract/[id]` (post-webhook) | Complete the checkout; wait for webhook to flip status; open the timeline section showing the capture event | Status chip PAID (not from any client-verify path); real `pay_…` id on the panel; timeline entry for the signature-verified webhook event; no CHECKOUT_COMPLETED_CLIENT / PAYMENT_VERIFIED_SERVER events anywhere in the trace |
| 07 | `http://localhost:3000/demo` | In the demo panel: click **Ship**, then **Deliver** with scenario `wrong_variant`; keep response ticker visible | SYNTHETIC badge(s) on the demo controls and results; observed facts listing wrong variant/SKU; scenario name visible; operator-token note if shown |
| 08 | `http://localhost:3000/contract/[id]/breach` | Breach page after delivery | Giant serif MATERIAL BREACH headline; PROMISED vs OBSERVED spread aligned key-by-key (promised SKU/warranty vs observed variant); breach reason codes; OBSERVED side carrying its synthetic source label |
| 09 | `http://localhost:3000/contract/[id]/rights` | Rights graph rendered post-breach; click the manufacturer entitlement node to open the side drawer | Interactive SVG graph with issuer-colored nodes and typed edges; edge legend (SUPPORTED_BY / MATERIAL_TO / ACTIVATED_BY / REQUIRES / BLOCKS / FALLBACK_TO / REMEDIES / ISSUED_BY); drawer open showing issuer, eligibility, expiry, required evidence, fallback target |
| 10 | `http://localhost:3000/contract/[id]/remedy` | Remedy ranking → policy decision → execute refund; then re-click Execute once for the replay proof (second frame or crop) | Score bars with weights (value .40 / intent-restoration .35 / speed .15 / inconvenience −.10); replacement candidate visibly BLOCKED (inventory unavailable), not hidden; policy decision ALLOW with policy_ids + reason codes; money-action row with idempotency_key `project-dante:{contract}:{proposal}:v1`; refund id (real provider `rfnd_…`, sandbox `rf_…`); second execute returning the identical id (one money effect) |
| 11 | `http://localhost:3000/audit/[id]` + `http://localhost:3000/merchant` | Full event trace on the audit page; merchant dashboard in a second capture/crop | Audit: "EVENT TRACE · APPEND-ONLY" label; mono timestamps; full event-type sequence compile→freeze→authorize→order→capture→ship→deliver→breach→policy→refund; synthetic events labeled inline. Merchant: masthead "WHAT YOUR AI BUYERS COULDN'T VERIFY"; StatNumeral row; blocker distribution bars; machine-readable coverage meters |

## Notes

- `[id]` = the contract id printed by `verify_e2e.py` (`[05] freeze -- contract=…`)
  or shown in the `/demo` orchestrator ticker.
- Shots 05–07 need the payment arc mid-flight; when real keys are absent, capture
  the sandbox simulate-button path and keep the SANDBOX badge in frame — do not
  crop badges out of any shot. The current checked-in 05–06 captures use that
  fallback and must not be described as real Razorpay checkout/webhook evidence.
- The checked-in files were captured with the local in-app browser at its
  available 1280px viewport; the production layout remains intended for a wider
  browser window.
- If a shot can't be reached end-to-end that day, prefer the terminal backup:
  `verify_e2e.py` output covering steps [01]–[16] (see docs/PITCH.md shot 16).
- After a real Test Mode run: add the live checkout/webhook/refund evidence,
  flip the 05–06 labels only when those criteria are actually proven, and
  refresh the README screenshots table.
