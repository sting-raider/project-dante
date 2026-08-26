# REAL_INTEGRATION_STATUS.md — Project Dante requirement-5 evidence ledger

**Purpose.** Project Dante's test suites and `scripts/verify_e2e.py` prove the
full buyer arc (intent → payment → breach → rights → remedy → refund → audit)
against the **offline sandbox adapter**, which mints Razorpay-*shaped* ids
locally and signs payloads with a synthetic key. That is genuine verification
of *our* code paths, but it is not proof that Dante talks to the real
Razorpay gateway. This file is the honest ledger of which claims are proven
against the **real gateway in Test Mode** (`razorpay_mode == 'live-test-mode'`)
and which are still open.

> Current state: **real keys are absent from this environment**, so every
> criterion below is `NOT_YET_PROVEN`. Nothing here is fabricated: ids appear
> only when `scripts/verify_real_integration.py` has actually observed them
> from the live API.

---

## Requirement-5 criteria checklist

Each row flips to `PROVEN` **only** by a run of
`scripts/verify_real_integration.py` against an API running with real
`rzp_test_*` keys; the script appends timestamped BEGIN-RUN / END-RUN blocks
with the observed ids at the bottom of this file.

| # | Criterion | Status | Evidence required | Observed |
| --- | --- | --- | --- | --- |
| 1 | **Real order created** — Razorpay Test Mode returns a real `order_...` id for the frozen contract amount | `NOT_YET_PROVEN` | `order_` id printed by the script from `/api/contracts/{id}/payment-order` (`checkout_config.order_id`) with checkout key `rzp_test_*` | none |
| 2 | **Real payment captured** — the human completes Standard Checkout in a browser and Razorpay binds a real `pay_...` id to the contract | `NOT_YET_PROVEN` | `pay_` id on the contract record after PAID | none |
| 3 | **Webhook received + verified** — Razorpay's server-to-server webhook crossed the intake gate (raw bytes → HMAC-SHA256 verify → only then parse) | `NOT_YET_PROVEN` | verified capture processed through `POST /api/webhooks/razorpay`; provider event id where surfaced | none |
| 4 | **PAID granted by the webhook path only** — no client-verify shortcut moved the contract to PAID | `NOT_YET_PROVEN` | contract reached PAID with zero `CHECKOUT_COMPLETED_CLIENT` / `PAYMENT_VERIFIED_SERVER` events in its timeline | none |
| 5 | **Synthetic wrong-variant delivery applied** — operator-token-gated demo endpoint delivers the wrong variant as an observed fact | `NOT_YET_PROVEN` | `deliver(scenario="wrong_variant")` via `X-Demo-Operator-Token`, response `synthetic=true` | none |
| 6 | **Breach detected** — promise verifier derives a real breach from the wrong-variant fact | `NOT_YET_PROVEN` | `PROMISE_BREACH_DETECTED` with reason codes | none |
| 7 | **Rights computed** — rights graph built with eligible entitlements for the breached contract | `NOT_YET_PROVEN` | non-empty graph + eligible entitlement list | none |
| 8 | **Remedy planned + policy ALLOW** — planner proposes remedies, `refund_full` chosen, policy decision `ALLOW` | `NOT_YET_PROVEN` | proposal id + `ALLOW` with policy ids | none |
| 9 | **Real refund executed** — Razorpay Test Mode returns a real `rf_...` refund id | `NOT_YET_PROVEN` | `rf_` id as `money_action.result_ref` from `/api/remedies/{id}/execute` | none |
| 10 | **Repeat execute ⇒ no second refund** — replaying execute returns the identical refund id (one money effect) | `NOT_YET_PROVEN` | second execute returns same `rf_` id and same money-action id | none |

---

## Why "sandbox PASS" is not "real PASS"

The sandbox adapter is deliberately excellent — it exercises our signature
math, state machine, idempotency and reconciliation with genuinely computed
HMACs — but it cannot prove the claims that only exist when a third-party
gateway is on the other end:

| Claim about the real world | Sandbox can prove it? | What could still break for real |
| --- | --- | --- |
| Code paths (freeze → order → webhook → refund) work end-to-end | Yes (and they do — 352 tests green) | nothing code-level, but see every row below |
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

The script drives compile → search → select → authorize → payment-order,
prints the REAL `order_...` id, opens the buyer contract page in your browser,
and waits up to 180 s (polling contract status every 2 s) while you complete
the real Standard Checkout payment. On PAID it prints the REAL `pay_...` id
plus webhook evidence from the timeline, then ships, delivers the wrong
variant, checks breach/rights/remedy/policy, executes the refund (printing the
REAL `rf_...` id), repeats execute to assert an identical refund id, and
verifies REMEDIATED + the audit trail. Exit code 0 only when all ten criteria
above are met; every step appends timestamped evidence lines between marked
BEGIN-RUN / END-RUN blocks below.

---

## Where the keys go

**No Razorpay credentials are present in this repository or environment**
(and live `rzp_live_*` keys would be hard-rejected at startup anyway per
`LiveKeyRejected`). To enable real-integration mode, create/edit the env files
that `apps/api/project_dante/settings.py` reads (later entries win):

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

*(no runs yet — criteria above remain NOT_YET_PROVEN)*
