# REAL_INTEGRATION_STATUS.md — Project Dante requirement-5 evidence ledger

**Purpose.** Project Dante's test suites and `scripts/verify_e2e.py` prove the
full buyer arc (intent → payment → breach → rights → remedy → refund → audit)
against the **offline sandbox adapter**, which mints Razorpay-*shaped* ids
locally and signs payloads with a synthetic key. That is genuine verification
of *our* code paths, but it is not proof that Dante talks to the real
Razorpay gateway. This file is the honest ledger of which claims are proven
against the **real gateway in Test Mode** (`razorpay_mode == 'live-test-mode'`)
and which are still open.

> Current state is reflected in the checklist below. A criterion is marked
> `PROVEN` only after `scripts/verify_real_integration.py` has observed it in
> a complete run against the real Razorpay Test Mode gateway. Nothing here is
> fabricated: ids appear only when the live API has actually returned them.

---

## Requirement-5 criteria checklist

Each row flips to `PROVEN` **only** by a run of
`scripts/verify_real_integration.py` against an API running with real
`rzp_test_*` keys; the script appends timestamped BEGIN-RUN / END-RUN blocks
with the observed ids at the bottom of this file.

| # | Criterion | Status | Evidence required | Observed |
| --- | --- | --- | --- | --- |
| 1 | **Real order created** — Razorpay Test Mode returns a real `order_...` id for the frozen contract amount | `PROVEN` | `order_` id printed by the script from `/api/contracts/{id}/payment-order` (`checkout_config.order_id`) with checkout key `rzp_test_*` | real Razorpay order id order_TW01twaOofBkn0 (amount 649900 paise, checkout key rzp_test_<redacted>) |
| 2 | **Real payment captured** — the human completes Standard Checkout in a browser and Razorpay binds a real `pay_...` id to the contract | `PROVEN` | `pay_` id on the contract record after PAID | real Razorpay payment id pay_TW0A9HkQKEVrZn captured on order order_TW01twaOofBkn0 |
| 3 | **Webhook received + verified** — Razorpay's server-to-server webhook crossed the intake gate (raw bytes → HMAC-SHA256 verify → freshness check → only then domain dispatch) | `PROVEN` | verified capture processed through `POST /api/webhooks/razorpay`; provider event id where surfaced | verified webhook processed: 1 capture event(s) on timeline; provider event id not surfaced on contract timeline; verification is structural: this script never called /verify-client or /simulate-event, and routes/webhooks.py is the ONLY code path that grants PAID, behind raw-body HMAC verification |
| 4 | **PAID granted by the webhook path only** — no client-verify shortcut moved the contract to PAID | `PROVEN` | contract reached PAID with zero `CHECKOUT_COMPLETED_CLIENT` / `PAYMENT_VERIFIED_SERVER` events in its timeline | contract reached PAID exclusively via signature-verified webhook intake (no CHECKOUT_COMPLETED_CLIENT/PAYMENT_VERIFIED_SERVER events exist) |
| 5 | **Synthetic wrong-variant delivery applied** — operator-token-gated demo endpoint delivers the wrong variant as an observed fact | `PROVEN` | `deliver(scenario="wrong_variant")` via `X-Demo-Operator-Token`, response `synthetic=true` | synthetic wrong_variant delivery applied via /demo/deliver with X-Demo-Operator-Token (response synthetic=true) |
| 6 | **Breach detected** — promise verifier derives a real breach from the wrong-variant fact | `PROVEN` | `PROMISE_BREACH_DETECTED` with reason codes | PROMISE_BREACH_DETECTED reasons=['MATERIAL_VARIANT_MISMATCH', 'MATERIAL_VARIANT_MISMATCH'] |
| 7 | **Rights computed** — rights graph built with eligible entitlements for the breached contract | `PROVEN` | non-empty graph + eligible entitlement list | rights graph nodes=22 edges=84 eligible=1 blocked=1 |
| 8 | **Remedy planned + policy ALLOW** — planner proposes remedies, `refund_full` chosen, policy decision `ALLOW` | `PROVEN` | proposal id + `ALLOW` with policy ids | proposal rem_cd15f4cc32ae refund_full chosen; policy ALLOW policies=['P-REFUND-01', 'P-REFUND-02', 'P-REFUND-03'] |
| 9 | **Real refund executed** — Razorpay Test Mode returns a real `rfnd_...` refund id | `PROVEN` | provider `rfnd_` id as `money_action.result_ref` from `/api/remedies/{id}/execute` | real Razorpay refund id rfnd_TW0AZfX02UIWfi (money_action=ma_cc4170ac59d7) |
| 10 | **Repeat execute ⇒ no second refund** — replaying execute returns the identical refund id (one money effect) | `PROVEN` | second execute returns the same provider `rfnd_` id and same money-action id | repeat execute returned the SAME refund id rfnd_TW0AZfX02UIWfi (same money_action ma_cc4170ac59d7; single money effect) |

---

## Why "sandbox PASS" is not "real PASS"

The sandbox adapter is deliberately excellent — it exercises our signature
math, state machine, idempotency and reconciliation with genuinely computed
HMACs — but it cannot prove the claims that only exist when a third-party
gateway is on the other end:

| Claim about the real world | Sandbox can prove it? | What could still break for real |
| --- | --- | --- |
| Code paths (freeze → order → webhook → refund) work end-to-end | Yes (and they do — 478 passed, 15 skipped, 1 warning, 4 subtests passed; Postgres integration is skipped when unavailable) | nothing code-level, but see every row below |
| Request/response shapes match Razorpay's actual REST API | No — shapes follow current docs, unverified against the service | undocumented required fields; error envelopes we don't map |
| Auth works with a real account's credentials | No — synthetic key only | revoked/wrong keys, IP rules, account holds |
| A browser can complete Standard Checkout against OUR order id | No — no real checkout session exists | key/amount/currency mismatches rejected client-side |
| Razorpay's servers reach OUR webhook URL over HTTPS | No — everything is loopback | tunnel/firewall/DNS; localhost needs a public tunnel |
| HMAC verification passes under the REAL dashboard secret | Partially — math is identical, secret is synthetic | secret mismatch between dashboard and `.env`; encoding surprises |
| Provider event ids arrive shaped like real deliveries | No — event ids are locally derived | retry storms, redelivery semantics, header variations |
| Refunds settle on real payment objects | No — refunds hit local records | partially-captured payments, refund windows, provider errors |

Honest summary: **sandbox PASS = "Dante's logic is correct". Real PASS =
"Dante's integration is correct."** Only the second satisfies requirement 5,
and only the script below can produce it.

---

## How to run the real-integration verification

Prerequisites:

1. API running with real keys loaded (health shows `"razorpay": "live-test-mode"`):
   `cd apps/api && .venv/Scripts/python.exe -m uvicorn project_dante.api.app:app --port 8000`
2. Buyer web app running at `PUBLIC_APP_URL` (default `http://localhost:3000`).
3. `DEMO_OPERATOR_TOKEN` exported in the shell running the script AND matching
   the server configuration (sent as `X-Demo-Operator-Token` on `/api/demo/*`).
4. For Razorpay's webhooks to reach your machine: a public HTTPS tunnel to the
   API (e.g. ngrok) pointed at port 8000, with the dashboard webhook set to
   `<tunnel>/api/webhooks/razorpay` and signed with the SAME secret as
   `RAZORPAY_WEBHOOK_SECRET`. See docs/RAZORPAY.md §3–4 (webhook setup, test
   cards such as `4111 1111 1111 1111`).

Then:

```
python scripts/verify_real_integration.py [--api http://localhost:8000] [--web http://localhost:3000] [--wait 180]
```

If the checkout wait expires after an order was already created, resume that
same contract instead of minting another order:

```
python scripts/verify_real_integration.py --resume-contract con_... --wait 600
```

Resume mode reads the existing authorized contract and payment order without
resetting the store or issuing another Razorpay order.

The script drives compile → search → select → authorize → payment-order,
prints the REAL `order_...` id, opens the buyer contract page in your browser,
and waits up to 180 s (polling contract status every 2 s) while you complete
the real Standard Checkout payment. On PAID it prints the REAL `pay_...` id
plus webhook evidence from the timeline, then ships, delivers the wrong
variant, checks breach/rights/remedy/policy, executes the refund (printing the
REAL `rfnd_...` id), repeats execute to assert an identical refund id, and
verifies REMEDIATED + the audit trail. Exit code 0 only when all ten criteria
above are met; every step appends timestamped evidence lines between marked
BEGIN-RUN / END-RUN blocks below.

---

## Where the keys go

**No Razorpay credentials are committed to this repository.** For a local real
smoke run, an operator may intentionally persist Razorpay **Test Mode** values
in an ignored `.env` file; this workspace does so. Local env files are not part
of the Git evidence or deployment artifact. Live `rzp_live_*` keys would be
hard-rejected at startup anyway per `LiveKeyRejected`. To enable
real-integration mode for a fresh process, create/edit the env files that
`apps/api/project_dante/settings.py` reads (later entries win):

- Repo root: `X:/RazorPay Buildathon/.env` (general), and/or
- App-local: `X:/RazorPay Buildathon/apps/api/.env` (overrides root)

with:

```
# Razorpay DASHBOARD -> Settings -> API Keys -> "Test" keys ONLY.
RAZORPAY_KEY_ID=<paste-your-test-key-id-here> # starts with rzp_test_
RAZORPAY_KEY_SECRET=<your-test-key-secret>
# Dashboard -> Settings -> Webhooks: the secret YOU set when creating the hook.
RAZORPAY_WEBHOOK_SECRET=<non-default webhook secret>
# Operator token accepted by live-mode demo fulfillment endpoints:
DEMO_OPERATOR_TOKEN=<long-random-string>
```

Notes:

- `razorpay_mode` flips to `'live-test-mode'` only when BOTH the key id
  (prefix `rzp_test_`) and the key secret are set; otherwise the sandbox
  adapter stays active and the verify script exits 1 with
  `set rzp_test_ keys` guidance.
- In live-test mode a missing/default `RAZORPAY_WEBHOOK_SECRET` makes webhook
  verification fail CLOSED — the script refuses to start before any money
  moves rather than let a captured payment strand unacknowledged.
- Never commit `.env`; keep `DEMO_MODE=true` only if you want the hybrid demo
  endpoints available behind the operator token.

---

## Run log (appended by scripts/verify_real_integration.py)

<!-- BEGIN-RUN blocks appear below this line. Each contains timestamped
     evidence lines with the actual gateway ids observed during that run. -->

*(Verification run blocks are appended here; the checklist above records only
successful complete runs.)*

<!-- BEGIN-RUN 2026-08-30T11-06-58+05-30 -->
- RUN STARTED: 2026-08-30T11:06:58+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T11:06:58+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=deterministic-fallback
  - 2026-08-30T11:06:58+05:30 reset: products=112 (clean store for unambiguous evidence)
  - 2026-08-30T11:06:58+05:30 compile: intent=int__6ac9a907a8ad engine=rules hard_constraints=7 (LLM never executes money)
  - 2026-08-30T11:06:58+05:30 search: 13 results, 2 feasible; sku=AST-HP-005 amount_paise=649900
  - 2026-08-30T11:06:58+05:30 freeze: contract=con_288908861666 promises=14 psh=8bcb22b2a7c4
  - 2026-08-30T11:06:58+05:30 authorize: hash=0334c5b31019 scope=single_purchase
  - 2026-08-30T11:06:59+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>)
  - 2026-08-30T11:06:59+05:30 ORDER (REAL): order_TVrsw4Cp8pHSm0
  - 2026-08-30T11:06:59+05:30 checkout: browser opened at http://localhost:3000/contract/con_288908861666
  - 2026-08-30T11:10:00+05:30 FAIL: timed out after 180s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?

  Criteria summary for this run:
  | Criterion | Result | Evidence |
  | --- | --- | --- |
  | order (real order created: Razorpay order_... id minted in live-test-mode) | PROVEN | real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>) |
  | paid (real payment captured: Razorpay pay_... id bound to the contract) | NOT_RUN | - |
  | webhook (webhook received + signature-verified (raw-body HMAC BEFORE parse)) | NOT_RUN | - |
  | paid_from_webhook (PAID granted by the webhook path only (no client-verify shortcut)) | NOT_RUN | - |
  | wrong_variant (synthetic wrong-variant delivery applied with operator token) | NOT_RUN | - |
  | breach (promise breach detected from the wrong-variant fact) | NOT_RUN | - |
  | rights (rights graph built with eligible entitlements) | NOT_RUN | - |
  | remedy (remedy planned: refund_full chosen, policy decision ALLOW) | NOT_RUN | - |
  | refund (real refund executed: Razorpay rfnd_... id returned) | NOT_RUN | - |
  | idempotent (repeat execute returns the SAME refund id - no second refund) | NOT_RUN | - |
- RUN RESULT: FAILED - timed out after 180s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?
- RUN ENDED: 2026-08-30T11:10:00+05:30
<!-- END-RUN 2026-08-30T11-06-58+05-30 -->

<!-- BEGIN-RUN 2026-08-30T12-36-56+05-30 -->
- RUN STARTED: 2026-08-30T12:36:56+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T12:36:56+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T12:36:56+05:30 resume: contract=con_288908861666 status=PAYMENT_ORDER_CREATED existing_order=order_TVrsw4Cp8pHSm0
  - 2026-08-30T12:36:56+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>)
  - 2026-08-30T12:36:56+05:30 ORDER (REAL): order_TVrsw4Cp8pHSm0
  - 2026-08-30T12:36:56+05:30 checkout: browser open disabled; manual URL is http://100.127.204.6:3000/contract/con_288908861666
  - 2026-08-30T12:46:57+05:30 FAIL: timed out after 600s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?

  Criteria summary for this run:
  | Criterion | Result | Evidence |
  | --- | --- | --- |
  | order (real order created: Razorpay order_... id minted in live-test-mode) | PROVEN | real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>) |
  | paid (real payment captured: Razorpay pay_... id bound to the contract) | NOT_RUN | - |
  | webhook (webhook received + signature-verified (raw-body HMAC BEFORE parse)) | NOT_RUN | - |
  | paid_from_webhook (PAID granted by the webhook path only (no client-verify shortcut)) | NOT_RUN | - |
  | wrong_variant (synthetic wrong-variant delivery applied with operator token) | NOT_RUN | - |
  | breach (promise breach detected from the wrong-variant fact) | NOT_RUN | - |
  | rights (rights graph built with eligible entitlements) | NOT_RUN | - |
  | remedy (remedy planned: refund_full chosen, policy decision ALLOW) | NOT_RUN | - |
  | refund (real refund executed: Razorpay rfnd_... id returned) | NOT_RUN | - |
  | idempotent (repeat execute returns the SAME refund id - no second refund) | NOT_RUN | - |
- RUN RESULT: FAILED - timed out after 600s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?
- RUN ENDED: 2026-08-30T12:46:57+05:30
<!-- END-RUN 2026-08-30T12-36-56+05-30 -->

<!-- BEGIN-RUN 2026-08-30T12-48-30+05-30 -->
- RUN STARTED: 2026-08-30T12:48:30+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T12:48:30+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T12:48:30+05:30 resume: contract=con_288908861666 status=PAYMENT_ORDER_CREATED existing_order=order_TVrsw4Cp8pHSm0
  - 2026-08-30T12:48:30+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>)
  - 2026-08-30T12:48:30+05:30 ORDER (REAL): order_TVrsw4Cp8pHSm0
  - 2026-08-30T12:48:30+05:30 checkout: browser open disabled; manual URL is http://100.127.204.6:3000/contract/con_288908861666
  - 2026-08-30T12:58:31+05:30 FAIL: timed out after 600s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?

  Criteria summary for this run:
  | Criterion | Result | Evidence |
  | --- | --- | --- |
  | order (real order created: Razorpay order_... id minted in live-test-mode) | PROVEN | real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>) |
  | paid (real payment captured: Razorpay pay_... id bound to the contract) | NOT_RUN | - |
  | webhook (webhook received + signature-verified (raw-body HMAC BEFORE parse)) | NOT_RUN | - |
  | paid_from_webhook (PAID granted by the webhook path only (no client-verify shortcut)) | NOT_RUN | - |
  | wrong_variant (synthetic wrong-variant delivery applied with operator token) | NOT_RUN | - |
  | breach (promise breach detected from the wrong-variant fact) | NOT_RUN | - |
  | rights (rights graph built with eligible entitlements) | NOT_RUN | - |
  | remedy (remedy planned: refund_full chosen, policy decision ALLOW) | NOT_RUN | - |
  | refund (real refund executed: Razorpay rfnd_... id returned) | NOT_RUN | - |
  | idempotent (repeat execute returns the SAME refund id - no second refund) | NOT_RUN | - |
- RUN RESULT: FAILED - timed out after 600s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?
- RUN ENDED: 2026-08-30T12:58:31+05:30
<!-- END-RUN 2026-08-30T12-48-30+05-30 -->

<!-- BEGIN-RUN 2026-08-30T14-37-48+05-30 -->
- RUN STARTED: 2026-08-30T14:37:48+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T14:37:48+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T14:37:48+05:30 resume: contract=con_288908861666 status=PAYMENT_ORDER_CREATED existing_order=order_TVrsw4Cp8pHSm0
  - 2026-08-30T14:37:48+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>)
  - 2026-08-30T14:37:48+05:30 ORDER (REAL): order_TVrsw4Cp8pHSm0
  - 2026-08-30T14:37:48+05:30 checkout: browser open disabled; manual URL is http://100.127.204.6:3000/contract/con_288908861666

  Criteria summary for this run:
  | Criterion | Result | Evidence |
  | --- | --- | --- |
  | order (real order created: Razorpay order_... id minted in live-test-mode) | PROVEN | real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>) |
  | paid (real payment captured: Razorpay pay_... id bound to the contract) | NOT_RUN | - |
  | webhook (webhook received + signature-verified (raw-body HMAC BEFORE parse)) | NOT_RUN | - |
  | paid_from_webhook (PAID granted by the webhook path only (no client-verify shortcut)) | NOT_RUN | - |
  | wrong_variant (synthetic wrong-variant delivery applied with operator token) | NOT_RUN | - |
  | breach (promise breach detected from the wrong-variant fact) | NOT_RUN | - |
  | rights (rights graph built with eligible entitlements) | NOT_RUN | - |
  | remedy (remedy planned: refund_full chosen, policy decision ALLOW) | NOT_RUN | - |
  | refund (real refund executed: Razorpay rfnd_... id returned) | NOT_RUN | - |
  | idempotent (repeat execute returns the SAME refund id - no second refund) | NOT_RUN | - |
- RUN RESULT: FAILED
- RUN ENDED: 2026-08-30T14:39:49+05:30
<!-- END-RUN 2026-08-30T14-37-48+05-30 -->

<!-- BEGIN-RUN 2026-08-30T14-41-05+05-30 -->
- RUN STARTED: 2026-08-30T14:41:05+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T14:41:05+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T14:41:05+05:30 resume: contract=con_288908861666 status=PAYMENT_ORDER_CREATED existing_order=order_TVrsw4Cp8pHSm0
  - 2026-08-30T14:41:05+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>)
  - 2026-08-30T14:41:05+05:30 ORDER (REAL): order_TVrsw4Cp8pHSm0
  - 2026-08-30T14:41:05+05:30 checkout: browser open disabled; manual URL is http://100.127.204.6:3000/contract/con_288908861666

<!-- BEGIN-RUN 2026-08-30T14-55-56+05-30 -->
- RUN STARTED: 2026-08-30T14:55:56+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T14:55:56+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T14:55:56+05:30 resume: contract=con_288908861666 status=PAYMENT_ORDER_CREATED existing_order=order_TVrsw4Cp8pHSm0
  - 2026-08-30T14:55:56+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>)
  - 2026-08-30T14:55:56+05:30 ORDER (REAL): order_TVrsw4Cp8pHSm0
  - 2026-08-30T14:55:56+05:30 checkout: browser open disabled; manual URL is http://100.127.204.6:3000/contract/con_288908861666

<!-- BEGIN-RUN 2026-08-30T15-04-54+05-30 -->
- RUN STARTED: 2026-08-30T15:04:54+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T15:04:54+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T15:04:54+05:30 resume: contract=con_288908861666 status=PAYMENT_ORDER_CREATED existing_order=order_TVrsw4Cp8pHSm0
  - 2026-08-30T15:04:54+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>)
  - 2026-08-30T15:04:54+05:30 ORDER (REAL): order_TVrsw4Cp8pHSm0
  - 2026-08-30T15:04:54+05:30 checkout: browser open disabled; manual URL is http://100.127.204.6:3000/contract/con_288908861666

<!-- BEGIN-RUN 2026-08-30T15-05-22+05-30 -->
- RUN STARTED: 2026-08-30T15:05:22+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T15:05:22+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T15:05:22+05:30 resume: contract=con_288908861666 status=PAYMENT_ORDER_CREATED existing_order=order_TVrsw4Cp8pHSm0
  - 2026-08-30T15:05:22+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>)
  - 2026-08-30T15:05:22+05:30 ORDER (REAL): order_TVrsw4Cp8pHSm0
  - 2026-08-30T15:05:22+05:30 checkout: browser open disabled; manual URL is http://100.127.204.6:3000/contract/con_288908861666
  - 2026-08-30T15:20:23+05:30 FAIL: timed out after 900s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?

  Criteria summary for this run:
  | Criterion | Result | Evidence |
  | --- | --- | --- |
  | order (real order created: Razorpay order_... id minted in live-test-mode) | PROVEN | real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>) |
  | paid (real payment captured: Razorpay pay_... id bound to the contract) | NOT_RUN | - |
  | webhook (webhook received + signature-verified (raw-body HMAC BEFORE parse)) | NOT_RUN | - |
  | paid_from_webhook (PAID granted by the webhook path only (no client-verify shortcut)) | NOT_RUN | - |
  | wrong_variant (synthetic wrong-variant delivery applied with operator token) | NOT_RUN | - |
  | breach (promise breach detected from the wrong-variant fact) | NOT_RUN | - |
  | rights (rights graph built with eligible entitlements) | NOT_RUN | - |
  | remedy (remedy planned: refund_full chosen, policy decision ALLOW) | NOT_RUN | - |
  | refund (real refund executed: Razorpay rfnd_... id returned) | NOT_RUN | - |
  | idempotent (repeat execute returns the SAME refund id - no second refund) | NOT_RUN | - |
- RUN RESULT: FAILED - timed out after 900s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?
- RUN ENDED: 2026-08-30T15:20:23+05:30
<!-- END-RUN 2026-08-30T15-05-22+05-30 -->

<!-- BEGIN-RUN 2026-08-30T17-42-52+05-30 -->
- RUN STARTED: 2026-08-30T17:42:52+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T17:42:52+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T17:42:53+05:30 resume: contract=con_288908861666 status=PAYMENT_ORDER_CREATED existing_order=order_TVrsw4Cp8pHSm0
  - 2026-08-30T17:42:53+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>)
  - 2026-08-30T17:42:53+05:30 ORDER (REAL): order_TVrsw4Cp8pHSm0
  - 2026-08-30T17:42:53+05:30 checkout: browser open disabled; manual URL is http://100.127.204.6:3000/contract/con_288908861666
  - 2026-08-30T17:57:53+05:30 FAIL: timed out after 900s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?

  Criteria summary for this run:
  | Criterion | Result | Evidence |
  | --- | --- | --- |
  | order (real order created: Razorpay order_... id minted in live-test-mode) | PROVEN | real Razorpay order id order_TVrsw4Cp8pHSm0 (amount 649900 paise, checkout key rzp_test_<redacted>) |
  | paid (real payment captured: Razorpay pay_... id bound to the contract) | NOT_RUN | - |
  | webhook (webhook received + signature-verified (raw-body HMAC BEFORE parse)) | NOT_RUN | - |
  | paid_from_webhook (PAID granted by the webhook path only (no client-verify shortcut)) | NOT_RUN | - |
  | wrong_variant (synthetic wrong-variant delivery applied with operator token) | NOT_RUN | - |
  | breach (promise breach detected from the wrong-variant fact) | NOT_RUN | - |
  | rights (rights graph built with eligible entitlements) | NOT_RUN | - |
  | remedy (remedy planned: refund_full chosen, policy decision ALLOW) | NOT_RUN | - |
  | refund (real refund executed: Razorpay rfnd_... id returned) | NOT_RUN | - |
  | idempotent (repeat execute returns the SAME refund id - no second refund) | NOT_RUN | - |
- RUN RESULT: FAILED - timed out after 900s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?
- RUN ENDED: 2026-08-30T17:57:53+05:30
<!-- END-RUN 2026-08-30T17-42-52+05-30 -->

<!-- BEGIN-RUN 2026-08-30T18-10-47+05-30 -->
- RUN STARTED: 2026-08-30T18:10:47+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T18:10:47+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T18:10:47+05:30 reset: products=112 (clean store for unambiguous evidence)
  - 2026-08-30T18:10:55+05:30 compile: intent=int__d82df73151a2 engine=llm hard_constraints=4 (LLM never executes money)
  - 2026-08-30T18:11:20+05:30 FAIL: no feasible offers found

  Criteria summary for this run:
  | Criterion | Result | Evidence |
  | --- | --- | --- |
  | order (real order created: Razorpay order_... id minted in live-test-mode) | NOT_RUN | - |
  | paid (real payment captured: Razorpay pay_... id bound to the contract) | NOT_RUN | - |
  | webhook (webhook received + signature-verified (raw-body HMAC BEFORE parse)) | NOT_RUN | - |
  | paid_from_webhook (PAID granted by the webhook path only (no client-verify shortcut)) | NOT_RUN | - |
  | wrong_variant (synthetic wrong-variant delivery applied with operator token) | NOT_RUN | - |
  | breach (promise breach detected from the wrong-variant fact) | NOT_RUN | - |
  | rights (rights graph built with eligible entitlements) | NOT_RUN | - |
  | remedy (remedy planned: refund_full chosen, policy decision ALLOW) | NOT_RUN | - |
  | refund (real refund executed: Razorpay rfnd_... id returned) | NOT_RUN | - |
  | idempotent (repeat execute returns the SAME refund id - no second refund) | NOT_RUN | - |
- RUN RESULT: FAILED - no feasible offers found
- RUN ENDED: 2026-08-30T18:11:20+05:30
<!-- END-RUN 2026-08-30T18-10-47+05-30 -->

<!-- BEGIN-RUN 2026-08-30T18-22-27+05-30 -->
- RUN STARTED: 2026-08-30T18:22:27+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T18:22:27+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T18:22:27+05:30 reset: products=112 (clean store for unambiguous evidence)
  - 2026-08-30T18:22:40+05:30 compile: intent=int__0967132ae934 engine=rules hard_constraints=7 (LLM never executes money)
  - 2026-08-30T18:22:44+05:30 search: 13 results, 2 feasible; sku=AST-HP-005 amount_paise=649900
  - 2026-08-30T18:22:44+05:30 freeze: contract=con_504234196339 promises=14 psh=8bcb22b2a7c4
  - 2026-08-30T18:22:44+05:30 authorize: hash=0334c5b31019 scope=single_purchase
  - 2026-08-30T18:22:44+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TVzJFFW0vGooyP (amount 649900 paise, checkout key rzp_test_<redacted>)
  - 2026-08-30T18:22:44+05:30 ORDER (REAL): order_TVzJFFW0vGooyP
  - 2026-08-30T18:22:44+05:30 checkout: browser open disabled; manual URL is http://localhost:3000/contract/con_504234196339
  - 2026-08-30T18:37:44+05:30 FAIL: timed out after 900s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?

  Criteria summary for this run:
  | Criterion | Result | Evidence |
  | --- | --- | --- |
  | order (real order created: Razorpay order_... id minted in live-test-mode) | PROVEN | real Razorpay order id order_TVzJFFW0vGooyP (amount 649900 paise, checkout key rzp_test_<redacted>) |
  | paid (real payment captured: Razorpay pay_... id bound to the contract) | NOT_RUN | - |
  | webhook (webhook received + signature-verified (raw-body HMAC BEFORE parse)) | NOT_RUN | - |
  | paid_from_webhook (PAID granted by the webhook path only (no client-verify shortcut)) | NOT_RUN | - |
  | wrong_variant (synthetic wrong-variant delivery applied with operator token) | NOT_RUN | - |
  | breach (promise breach detected from the wrong-variant fact) | NOT_RUN | - |
  | rights (rights graph built with eligible entitlements) | NOT_RUN | - |
  | remedy (remedy planned: refund_full chosen, policy decision ALLOW) | NOT_RUN | - |
  | refund (real refund executed: Razorpay rfnd_... id returned) | NOT_RUN | - |
  | idempotent (repeat execute returns the SAME refund id - no second refund) | NOT_RUN | - |
- RUN RESULT: FAILED - timed out after 900s waiting for PAID (last=PAYMENT_ORDER_CREATED). Check: was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at <public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?
- RUN ENDED: 2026-08-30T18:37:44+05:30
<!-- END-RUN 2026-08-30T18-22-27+05-30 -->

<!-- BEGIN-RUN 2026-08-30T18-58-59+05-30 -->
- RUN STARTED: 2026-08-30T18:58:59+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T18:58:59+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T18:58:59+05:30 resume: contract=con_504234196339 status=PAID existing_order=order_TVzJFFW0vGooyP
  - 2026-08-30T18:58:59+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TVzJFFW0vGooyP (amount 649900 paise; checkout key was observed in the original run)
  - 2026-08-30T18:58:59+05:30 ORDER (REAL): order_TVzJFFW0vGooyP
  - 2026-08-30T18:58:59+05:30 resume: contract already PAID; skipping checkout prompt and validating downstream evidence
  - 2026-08-30T18:58:59+05:30 [criterion:paid] PROVEN -- real payment captured: Razorpay pay_... id bound to the contract :: real Razorpay payment id pay_TVzulsdERT1YAx captured on order order_TVzJFFW0vGooyP
  - 2026-08-30T18:58:59+05:30 PAYMENT (REAL): pay_TVzulsdERT1YAx
  - 2026-08-30T18:58:59+05:30 [criterion:webhook] PROVEN -- webhook received + signature-verified (raw-body HMAC BEFORE parse) :: verified webhook processed: 1 capture event(s) on timeline; provider event id not surfaced on contract timeline; verification is structural: this script never called /verify-client or /simulate-event, and routes/webhooks.py is the ONLY code path that grants PAID, behind raw-body HMAC verification
  - 2026-08-30T18:58:59+05:30 WEBHOOK: verified capture processed; evidence=provider event id not surfaced on contract timeline; verification is structural: this script never called /verify-client or /simulate-event, and routes/webhooks.py is the ONLY code path that grants PAID, behind raw-body HMAC verification
  - 2026-08-30T18:58:59+05:30 FAIL: client-verify path events found (['PAYMENT_VERIFIED_SERVER']) - PAID must come from the webhook

  Criteria summary for this run:
  | Criterion | Result | Evidence |
  | --- | --- | --- |
  | order (real order created: Razorpay order_... id minted in live-test-mode) | PROVEN | real Razorpay order id order_TVzJFFW0vGooyP (amount 649900 paise; checkout key was observed in the original run) |
  | paid (real payment captured: Razorpay pay_... id bound to the contract) | PROVEN | real Razorpay payment id pay_TVzulsdERT1YAx captured on order order_TVzJFFW0vGooyP |
  | webhook (webhook received + signature-verified (raw-body HMAC BEFORE parse)) | PROVEN | verified webhook processed: 1 capture event(s) on timeline; provider event id not surfaced on contract timeline; verification is structural: this script never called /verify-client or /simulate-event, and routes/webhooks.py is the ONLY code path that grants PAID, behind raw-body HMAC verification |
  | paid_from_webhook (PAID granted by the webhook path only (no client-verify shortcut)) | NOT_RUN | - |
  | wrong_variant (synthetic wrong-variant delivery applied with operator token) | NOT_RUN | - |
  | breach (promise breach detected from the wrong-variant fact) | NOT_RUN | - |
  | rights (rights graph built with eligible entitlements) | NOT_RUN | - |
  | remedy (remedy planned: refund_full chosen, policy decision ALLOW) | NOT_RUN | - |
  | refund (real refund executed: Razorpay rfnd_... id returned) | NOT_RUN | - |
  | idempotent (repeat execute returns the SAME refund id - no second refund) | NOT_RUN | - |
- RUN RESULT: FAILED - client-verify path events found (['PAYMENT_VERIFIED_SERVER']) - PAID must come from the webhook
- RUN ENDED: 2026-08-30T18:58:59+05:30
<!-- END-RUN 2026-08-30T18-58-59+05-30 -->

<!-- BEGIN-RUN 2026-08-30T19-04-55+05-30 -->
- RUN STARTED: 2026-08-30T19:04:55+05:30 (script: scripts/verify_real_integration.py)
  - 2026-08-30T19:04:55+05:30 health: api=project-dante-api razorpay=live-test-mode llm_engine=openai-compatible
  - 2026-08-30T19:04:56+05:30 reset: products=112 (clean store for unambiguous evidence)
  - 2026-08-30T19:04:58+05:30 compile: intent=int__526789f536da engine=llm hard_constraints=7 (LLM never executes money)
  - 2026-08-30T19:04:59+05:30 search: 13 results, 2 feasible; sku=AST-HP-005 amount_paise=649900
  - 2026-08-30T19:05:00+05:30 freeze: contract=con_493033676237 promises=14 psh=8bcb22b2a7c4
  - 2026-08-30T19:05:00+05:30 authorize: hash=0334c5b31019 scope=single_purchase
  - 2026-08-30T19:05:01+05:30 [criterion:order] PROVEN -- real order created: Razorpay order_... id minted in live-test-mode :: real Razorpay order id order_TW01twaOofBkn0 (amount 649900 paise, checkout key rzp_test_<redacted>)
  - 2026-08-30T19:05:01+05:30 ORDER (REAL): order_TW01twaOofBkn0
  - 2026-08-30T19:05:01+05:30 checkout: browser open disabled; manual URL is http://localhost:3000/contract/con_493033676237
  - 2026-08-30T19:13:10+05:30 [criterion:paid] PROVEN -- real payment captured: Razorpay pay_... id bound to the contract :: real Razorpay payment id pay_TW0A9HkQKEVrZn captured on order order_TW01twaOofBkn0
  - 2026-08-30T19:13:10+05:30 PAYMENT (REAL): pay_TW0A9HkQKEVrZn
  - 2026-08-30T19:13:10+05:30 [criterion:webhook] PROVEN -- webhook received + signature-verified (raw-body HMAC BEFORE parse) :: verified webhook processed: 1 capture event(s) on timeline; provider event id not surfaced on contract timeline; verification is structural: this script never called /verify-client or /simulate-event, and routes/webhooks.py is the ONLY code path that grants PAID, behind raw-body HMAC verification
  - 2026-08-30T19:13:10+05:30 WEBHOOK: verified capture processed; evidence=provider event id not surfaced on contract timeline; verification is structural: this script never called /verify-client or /simulate-event, and routes/webhooks.py is the ONLY code path that grants PAID, behind raw-body HMAC verification
  - 2026-08-30T19:13:10+05:30 [criterion:paid_from_webhook] PROVEN -- PAID granted by the webhook path only (no client-verify shortcut) :: contract reached PAID exclusively via signature-verified webhook intake (no CHECKOUT_COMPLETED_CLIENT/PAYMENT_VERIFIED_SERVER events exist)
  - 2026-08-30T19:13:10+05:30 PAID-FROM-WEBHOOK: proven structurally (client-verify paths unused)
  - 2026-08-30T19:13:11+05:30 [criterion:wrong_variant] PROVEN -- synthetic wrong-variant delivery applied with operator token :: synthetic wrong_variant delivery applied via /demo/deliver with X-Demo-Operator-Token (response synthetic=true)
  - 2026-08-30T19:13:11+05:30 DELIVERY: wrong_variant (synthetic, operator-token gated)
  - 2026-08-30T19:13:11+05:30 [criterion:breach] PROVEN -- promise breach detected from the wrong-variant fact :: PROMISE_BREACH_DETECTED reasons=['MATERIAL_VARIANT_MISMATCH', 'MATERIAL_VARIANT_MISMATCH']
  - 2026-08-30T19:13:11+05:30 BREACH: reasons=['MATERIAL_VARIANT_MISMATCH', 'MATERIAL_VARIANT_MISMATCH']
  - 2026-08-30T19:13:11+05:30 [criterion:rights] PROVEN -- rights graph built with eligible entitlements :: rights graph nodes=22 edges=84 eligible=1 blocked=1
  - 2026-08-30T19:13:11+05:30 RIGHTS: nodes=22 eligible=1 blocked=1
  - 2026-08-30T19:13:12+05:30 [criterion:remedy] PROVEN -- remedy planned: refund_full chosen, policy decision ALLOW :: proposal rem_cd15f4cc32ae refund_full chosen; policy ALLOW policies=['P-REFUND-01', 'P-REFUND-02', 'P-REFUND-03']
  - 2026-08-30T19:13:12+05:30 REMEDY: refund_full proposal=rem_cd15f4cc32ae; POLICY: ALLOW
  - 2026-08-30T19:13:14+05:30 [criterion:refund] PROVEN -- real refund executed: Razorpay rfnd_... id returned :: real Razorpay refund id rfnd_TW0AZfX02UIWfi (money_action=ma_cc4170ac59d7)
  - 2026-08-30T19:13:14+05:30 REFUND (REAL): rfnd_TW0AZfX02UIWfi
  - 2026-08-30T19:13:14+05:30 [criterion:idempotent] PROVEN -- repeat execute returns the SAME refund id - no second refund :: repeat execute returned the SAME refund id rfnd_TW0AZfX02UIWfi (same money_action ma_cc4170ac59d7; single money effect)
  - 2026-08-30T19:13:14+05:30 IDEMPOTENCY: repeat execute -> same refund rfnd_TW0AZfX02UIWfi
  - 2026-08-30T19:13:14+05:30 AUDIT: 41 timeline events, 13 synthetic-labeled, all key events present, terminal=REMEDIATED
  - 2026-08-30T19:13:14+05:30 checklist: all ten real-integration rows promoted to PROVEN

  Criteria summary for this run:
  | Criterion | Result | Evidence |
  | --- | --- | --- |
  | order (real order created: Razorpay order_... id minted in live-test-mode) | PROVEN | real Razorpay order id order_TW01twaOofBkn0 (amount 649900 paise, checkout key rzp_test_<redacted>) |
  | paid (real payment captured: Razorpay pay_... id bound to the contract) | PROVEN | real Razorpay payment id pay_TW0A9HkQKEVrZn captured on order order_TW01twaOofBkn0 |
  | webhook (webhook received + signature-verified (raw-body HMAC BEFORE parse)) | PROVEN | verified webhook processed: 1 capture event(s) on timeline; provider event id not surfaced on contract timeline; verification is structural: this script never called /verify-client or /simulate-event, and routes/webhooks.py is the ONLY code path that grants PAID, behind raw-body HMAC verification |
  | paid_from_webhook (PAID granted by the webhook path only (no client-verify shortcut)) | PROVEN | contract reached PAID exclusively via signature-verified webhook intake (no CHECKOUT_COMPLETED_CLIENT/PAYMENT_VERIFIED_SERVER events exist) |
  | wrong_variant (synthetic wrong-variant delivery applied with operator token) | PROVEN | synthetic wrong_variant delivery applied via /demo/deliver with X-Demo-Operator-Token (response synthetic=true) |
  | breach (promise breach detected from the wrong-variant fact) | PROVEN | PROMISE_BREACH_DETECTED reasons=['MATERIAL_VARIANT_MISMATCH', 'MATERIAL_VARIANT_MISMATCH'] |
  | rights (rights graph built with eligible entitlements) | PROVEN | rights graph nodes=22 edges=84 eligible=1 blocked=1 |
  | remedy (remedy planned: refund_full chosen, policy decision ALLOW) | PROVEN | proposal rem_cd15f4cc32ae refund_full chosen; policy ALLOW policies=['P-REFUND-01', 'P-REFUND-02', 'P-REFUND-03'] |
  | refund (real refund executed: Razorpay rfnd_... id returned) | PROVEN | real Razorpay refund id rfnd_TW0AZfX02UIWfi (money_action=ma_cc4170ac59d7) |
  | idempotent (repeat execute returns the SAME refund id - no second refund) | PROVEN | repeat execute returned the SAME refund id rfnd_TW0AZfX02UIWfi (same money_action ma_cc4170ac59d7; single money effect) |
- RUN RESULT: PASSED - ALL REQUIREMENT-5 CRITERIA PROVEN AGAINST REAL RAZORPAY TEST MODE
- RUN ENDED: 2026-08-30T19:13:14+05:30
<!-- END-RUN 2026-08-30T19-04-55+05-30 -->
