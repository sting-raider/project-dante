# Handoff: Agent B — Razorpay Integration Lead

## Goal

Build the complete Razorpay integration layer: dual adapters (real Test Mode
REST + offline sandbox), the frozen service facade, payment-order /
verify-client / webhook routes with executor drift re-check and out-of-order
reconciliation, a demo simulate-event path that crosses the real verification
gate, tests, and the Test Mode setup guide.

## Completed

- `integrations/razorpay/client.py` — one interface, two adapters:
  - **LiveTestModeClient**: real Razorpay Test Mode REST via httpx (Basic auth,
    `https://api.razorpay.com/v1`); orders / payments / refunds; secrets never
    logged or serialized into exceptions.
  - **SandboxClient**: zero network; mints Razorpay-shaped ids
    (`order_`/`pay_`/`rf_` + 14 alphanumerics); persists STORE records
    (`razorpay_order`/`razorpay_payment`/`razorpay_refund`) all flagged
    `"sandbox": true`; computes REAL HMAC-SHA256 signatures under a clearly
    synthetic key so verification paths are genuinely exercised.
- Refund idempotency in BOTH adapters: idempotency key checked against STORE
  before any effect; retries return the original refund unchanged. Key also
  forwarded as refund receipt + note for upstream alignment.
- `integrations/razorpay/service.py` — exact frozen facade (mode, create_order,
  verify_checkout_signature, verify_webhook_signature, fetch_payment,
  create_refund) plus sandbox-only helpers (sign_webhook_payload,
  capture_sandbox_payment, fetch_order_payments).
- `api/routes/payments.py`:
  - `POST /api/contracts/{id}/payment-order`: authorization + status + amount
    gates → EXECUTOR RE-CHECK recomputes sha256 over frozen offer+promises and
    compares to `contract_hash` (409 `contract_drift`, no order) → order
    created → persisted → transitioned → RAZORPAY_ORDER_CREATED. Idempotent
    re-entry returns the existing order instead of minting a second payable.
  - `POST /api/payments/verify-client`: server-side checkout-signature check;
    CHECKOUT_COMPLETED_CLIENT + PAYMENT_VERIFIED_SERVER; moves to
    PAYMENT_PENDING only — never PAID (webhook-exclusive).
  - `POST /api/demo/razorpay/simulate-event`: guarded by DEMO_MODE AND sandbox
    (403 otherwise); mints the capture Razorpay would have made, builds a REAL
    signed payload, pushes it through the production intake (verification
    included). Deterministic derived event id makes re-simulation behave like
    an upstream redelivery.
- `api/routes/webhooks.py`: raw body → HMAC verify BEFORE json.loads (401 +
  nothing stored on failure); every verified event stored by provider event id;
  duplicates → `200 {"ok":true,"duplicate":true}` + WEBHOOK_DUPLICATE_IGNORED,
  zero domain effect; `payment.captured` is the ONLY grantor of PAID with
  amount-tampering guard; out-of-order captures walk the legal transition path
  to PAID logging STATE_RECONCILED hops (forced reconciliation documented if no
  legal path exists); post-PAID states never regress; fast ACK, no external
  calls beyond verification.
- `docs/RAZORPAY.md`: Test Mode keys, env vars, dashboard webhook setup,
  test cards (`4111 1111 1111 1111`), localhost tunnel note, troubleshooting.

## Files changed

- `apps/api/project_dante/integrations/razorpay/client.py` (new)
- `apps/api/project_dante/integrations/razorpay/service.py` (new)
- `apps/api/project_dante/api/routes/payments.py` (new)
- `apps/api/project_dante/api/routes/webhooks.py` (new)
- `apps/api/tests/test_razorpay_service.py` (new)
- `apps/api/tests/test_webhooks.py` (new)
- `docs/RAZORPAY.md` (new)
- `docs/handoffs/razorpay.md` (this file)

No shared/frozen files touched.

## Public interfaces created/changed

Frozen service interface (consumed by Agent E remedies executor and others):

```python
from project_dante.integrations.razorpay import service
service.mode() -> "live-test-mode" | "sandbox"
service.create_order(amount_paise, receipt="", notes=None) -> dict
service.verify_checkout_signature(order_id, payment_id, signature) -> bool
service.verify_webhook_signature(raw_body: bytes, signature) -> bool
service.fetch_payment(payment_id) -> dict | None
service.create_refund(payment_id, amount_paise=None, idempotency_key="", notes=None) -> dict
# extras (documented in module): key_id_public(), sign_webhook_payload(),
#   capture_sandbox_payment(order_id), fetch_order_payments(order_id)
```

HTTP endpoints per docs/API_CONTRACT.md:

```
POST /api/contracts/{id}/payment-order   {mode, razorpay_order, checkout_config{key_id, order_id, amount_paise, currency}, contract_status}
POST /api/payments/verify-client          {status:"client_confirmed", contract_status}
POST /api/webhooks/razorpay               200 {"ok":true[, "duplicate":true]} | 401 invalid_signature
POST /api/demo/razorpay/simulate-event    {delivered:true, synthetic:true, event_id, payment_id, contract_status}
```

Notes for consumers:
- sandbox `checkout_config.key_id` is `""` by design (honest UI signal).
- refunds raise `client.RazorpayError` (with `.status_code`) on provider-side
  failure and `ValueError` on non-positive/non-integer amounts.
- `capture_sandbox_payment` raises `RazorpayError(404)` for unknown orders.

## Tests

```
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_razorpay_service.py tests/test_webhooks.py -q
=> 36 passed, 0 failed (0.8s)
ruff check <all Agent B files> => All checks passed!
```

Coverage highlights: signature happy+forged+tamper+empty-inputs; webhook
duplicate x5 ⇒ single domain effect + 4 audited ignores; forged webhook ⇒ 401
and nothing stored; out-of-order captured-before-pending reconciles to PAID;
amount-mismatch capture never grants PAID; late capture after FULFILLING does
not regress state; sandbox refund idempotency (identical refund id, single
STORE record, distinct keys distinct effects, over-refund rejected);
contract-drift blocks order with zero gateway records; full HTTP flow
contract → order → simulate-event → PAID; simulate-event guard 403 when real
keys present.

Tests mount ONLY the payments+webhooks routers on a minimal FastAPI app, so
the suite stays green independently of other agents' modules.

## Post-review fixes (integration + red team)

**CLOSED by security-lead after re-verification:** 72/0/0 on security suites,
303 passed full API tree. Two permanent regression attacks added to
test_webhook_chaos.py (graft-block on non-payable contracts, post-paid
idempotency under late redelivery). Secrets allowlist pruned to a single
entry: the clearly-labelled synthetic sandbox key in client.py
(`SANDBOX_KEY_SECRET`, marked "NOT-A-REAL-CREDENTIAL" — required for real
HMAC math in the offline adapter).


- **K-03 / HIGH (fixed):** `_on_payment_captured`'s final fallback force-wrote
  `status="PAID"` for ANY unhandled state — a signature-valid capture could
  teleport CANCELLED/FAILED/DRAFT contracts to PAID, bypassing
  `validate_transition`. Now: `_walk_to_paid` handles only legal pre-payment
  states (`_CAPTURE_WALK` map); non-payable states (DRAFT/CANCELLED/FAILED)
  keep their status and get a `STATE_RECONCILED` event with reason
  `captured_event_for_non_payable_state`, `action=paid_withheld` — orphaned
  payments documented for human handling, never resurrected. Also gated: the
  observed `razorpay_payment_id` is no longer grafted onto non-payable
  contracts (downstream refund lookups key off that field). Post-paid states
  (PAID/FULFILLING/SATISFIED/REMEDIATED/BREACH_DETECTED) remain idempotent
  no-op recordings per lead's instruction.
- **Secrets hygiene:** replaced key-shaped literals in tests/test_webhooks.py
  and docs/RAZORPAY.md env sample with non-key-shaped placeholders so the tree
  stays grep-clean by default.
- Regression coverage added: `test_captured_never_resurrects_non_payable_contracts`
  (DRAFT/CANCELLED/FAILED parametrized) +
  `test_captured_on_post_paid_states_is_idempotent_no_resurrection`.



1. **Contract-hash recipe assumption.** The drift gate recomputes
   `sha256_hex({"offer": offer_minus_type, "promise_set_hash": ...})`. If Agent
   D's freeze pipeline uses a different canonical composition, legitimate
   contracts would 409 as drift at integration time. Mitigation: the test
   fixture documents the exact expected recipe; aligning is a one-line change
   in `_recompute_contract_hash` (payments.py) — flag me or adjust there.
2. **LiveTestModeClient is untested against the real API** (no keys in this
   environment). Request/response shapes follow current Razorpay docs; first
   live run should be smoke-tested per docs/RAZORPAY.md §2.
3. Webhook processing is synchronous in-handler (fine at demo scale). Plan §16.8
   asks for async queueing of heavy work — trivially refactorable later since
   all effects funnel through `handle_webhook_bytes`.
4. Sandbox capture updates the stored order's `attempts`/`status` like the real
   gateway; any code assuming immutable sandbox orders would be surprised
   (none known).

## Integration notes

- No git commits made (lead integrates), per instructions.
- No frozen contracts were modified. One friction point worth recording: the
  state machine has no direct `PAYMENT_ORDER_CREATED → PAID` hop, so webhook
  reconciliation walks `PAYMENT_ORDER_CREATED → PAYMENT_PENDING → PAID`
  (both legal edges) — this works but a documented shortcut edge could simplify
  future reconciliations. Left to Agent A's judgment.
- `app.py` auto-registers both routers via the standard pkgutil scan; no app.py
  edit needed from the lead.
- For the demo: sandbox mode needs zero setup; real Test Mode switch is fully
  documented in docs/RAZORPAY.md (keys → health shows live-test-mode →
  simulate-event auto-disables with 403).
- Suggested idempotency-key shape for Agent E's refund executor:
  `project-dante:{contract_id}:{remedy_id}:{action_version}`.

## Commit

(none — lead integrates)
