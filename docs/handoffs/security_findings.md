# Security Findings — Agent K (Red Team)

Last updated: 2026-08-26 — **all three confirmed vulnerabilities VERIFIED FIXED**
by re-running the red-team suites against the patched modules.

Suites: `apps/api/tests/test_security_redteam.py` + `tests/test_webhook_chaos.py`
Latest run: **72 passed / 0 failed / 0 skipped** (red-team suites);
full API tree: **303 passed**.

Post-fix hardening verified (2026-08-26, second pass): Agent B additionally
gated the `razorpay_payment_id` graft (`webhooks.py:245`) so orphaned captures
on non-payable contracts can no longer plant their payment id for downstream
refund lookups to trip over; post-paid states confirmed no-regression under
late redelivery. New permanent attacks:
`test_captured_does_not_graft_payment_id_onto_non_payable_contracts`,
`test_captured_still_records_on_post_paid_states_without_regression`.
Secrets-scan allowlist pruned back to a single entry after owners replaced
secret-shaped test/doc literals with non-key-shaped placeholders.

Severity scale: CRITICAL = unauthorized money movement or forged payment truth;
HIGH = invariant violation reachable by an authenticated/valid-signature party;
MEDIUM = hardening gap bounded by other controls; LOW = defense-in-depth nit.

---

## CONFIRMED VULNERABILITIES (all RESOLVED + regression-tested)

### K-03 — Webhook capture teleports CANCELLED/FAILED/DRAFT contracts to PAID (HIGH)

**Status: VERIFIED FIXED (2026-08-26).** Agent B replaced the force-write fallback
with a record-but-withhold path: `_walk_to_paid` (`webhooks.py:275`) legally walks
every pre-payment state along the transition spine; non-payable states
(DRAFT/CANCELLED/FAILED) now get a `STATE_RECONCILED` event with
`reason="captured_event_for_non_payable_state"`, `action="paid_withheld"` and NO
status write (`webhooks.py:300`). Regression test
`test_webhook_chaos.py::TestWebhookChaos::test_captured_never_resurrects_cancelled_or_draft_contracts`
passes against all three illegal source states.
**Suite:** `tests/test_webhook_chaos.py::TestWebhookChaos::test_captured_never_resurrects_cancelled_or_draft_contracts`

Reproduction (verified standalone):
```python
contract = {..., "status": "CANCELLED", "razorpay_order_id": "order_repro_x",
            "amount_paise": 1149900}
STORE.put(contract)
body = {"event": "payment.captured", "payload": {"payment": {"entity":
        {"id": "pay_repro_x", "order_id": "order_repro_x",
         "amount": 1149900, "status": "captured"}}}}
POST /api/webhooks/razorpay with valid HMAC signature
→ HTTP 200, contract.status == "PAID"
```
(Exact runnable repro lives in the failing test; run it.)

Root cause: the out-of-order fallback force-writes `STORE.update(contract_id,
status="PAID")` for ANY status not in `_CAPTURE_WALK` — including terminal
CANCELLED/FAILED and pre-frozen DRAFT. This bypasses `validate_transition`
(invariant: state transitions validated in exactly one place) and violates I12
(out-of-order events must not corrupt state). A replayed capture for a
buyer-cancelled order fabricates a PAID purchase; if any downstream logic keys
off PAID (fulfillment triggers, analytics GMV), the corruption propagates.

Suggested fix (Agent B): gate the fallback on membership in
`_CAPTURE_WALK`'s source states only:
```python
if status not in _CAPTURE_WALK and status != "PAYMENT_PENDING" ... :
    append_event(... STATE_RECONCILED reason="captured_for_non_payment_state",
                 from_status=status, action="paid_withheld")
    return
```
i.e., record the verified event, log `STATE_RECONCILED` with `paid_withheld`,
and never write PAID from CANCELLED/FAILED/DRAFT/OFFER_SELECTED/etc. The legal
walk states keep their existing reconciliation behavior.

---

### K-01 — refund_full under-amount auto-approves and closes the case (HIGH)

**Status: VERIFIED FIXED (2026-08-26).** Agent E enforced exact-amount semantics
TWICE: `evaluate_money_action` now DENIES with `FULL_REFUND_AMOUNT_MISMATCH`
(P-REFUND-01) unless `amount == captured` (`policy.py:385`), and the new shared
`_executor_structural_check` mirrors the rule at call time (`policy.py:710`) so a
downward tamper after a prior ALLOW still fails the final executor check.
Legitimate smaller compensations must travel through `refund_partial` with an
allowed reason and its 50000-paise cap. Semantic consequence noted by Agent E:
full refunds are exact-captured only, so the ₹20k approval threshold is reached
via contracts with captured > ₹20,000, not via under-amount full refunds.

Original attack (now blocked): propose `type="refund_full"` with
`amount_paise = captured // 2` — previously ALLOWed (P-REFUND-03), executed,
contract marked REMEDIATED while the buyer was under-refunded; the
partial-refund reason allow-list and cap were bypassed entirely. Bounded by the
captured ceiling, so it was case-closure fraud rather than money multiplication.
Regression tests: `test_under_amount_not_allowed_silently`,
`test_over_by_one_paise_denied`, plus Agent E's `FullRefundAmountIntegrityTests`.

---

### K-02 — Non-int amounts coerced instead of rejected (MEDIUM)

**Status: VERIFIED FIXED (2026-08-26).** Strict money typing per plan §19:
bools explicitly rejected, any non-int (`"11499"`, `114.99`, `True`, `None`)
DENIED with `INVALID_AMOUNT_TYPE`, un-coerced (`policy.py:281-296`); mirrored in
`_executor_structural_check` (`policy.py:699`).

Original gap: `int()` accepted `"11499"` → 11499, truncated `114.99` → 114,
mapped `True` → 1. Not an over-refund path (bounds held) but silent corruption
of a financial field. Regression tests:
`test_string_amount_never_becomes_money`,
`test_float_rupee_confusion_never_becomes_money`.

---

## DEFENSES VERIFIED HOLDING

- **Forged webhook signatures** (WHF-01..05): garbage/wrong-secret/tampered-after-signing/
  empty body/huge body all → 401, zero domain events, zero stored rows;
  positive control confirms correctly-signed traffic passes.
  `integrations/razorpay/service.py:verify_webhook_signature` — constant-time compare.
- **Duplicate webhooks** (WHC-01): same event id ×5 → one RAZORPAY_PAYMENT_CAPTURED effect,
  duplicates get 200 + WEBHOOK_DUPLICATE_IGNORED, no double PAID walk.
- **Amount-mismatched capture** (AMT via webhook): captured amount ≠ frozen contract amount
  → event recorded, PAID withheld (`webhooks.py:224` guard).
- **verify-client discipline**: never mints PAID; forged client signatures → 400;
  cross-order substitution → 400/403 (`payments.py:216`).
- **Cross-contract substitution** (CCS): executor derives the payment id FROM the stored
  contract and `_executor_final_check` refuses drift; tampered proposals cannot move
  another contract's money (`policy.py:677`).
- **Refund idempotency** (RRP): remedy-level replay ×2/×3 → single refund record, cached
  result returned; client-level replay → single effect; distinct contracts never share effects.
- **Over-refund bound**: refunds above captured rejected at policy AND adapter level
  (`client.py` SandboxClient raises before persisting).
- **Prompt injection corpus** (PINJ, 20 vectors incl. homoglyph + Hindi-language payloads):
  extract_promises never lets text override structured truth; contradictory text becomes
  unverified/low-confidence promises only; zero side effects (no orders/refunds/money actions).
- **Privilege escalation via buyer prose** (PESC): compile treats escalation text as data —
  no inflated limits, no refund constraints minted, no side effects.
- **Demo guards** (DEM): all five demo endpoints (incl. simulate-event) → 403 with demo_mode off.
- **Secrets hygiene** (SEC): repo-wide scan clean (one documented synthetic allowlist entry).
- **State machine core** (STA): 11 illegal pairs raise InvalidTransition; legal spine intact.

## RESIDUAL WATCH ITEMS (not vulnerabilities)

1. `payments.py:_recompute_contract_hash` recomputes from STORE contents rather than the
   original frozen snapshot; if promises were mutated in place post-freeze the recomputed hash
   tracks the mutation. Drift detection would benefit from storing the frozen payload hashes.
2. Webhook secret default `dante-dev-webhook-secret` ships in settings; production must
   override via env (documented in THREAT_MODEL residual risks).
3. In-memory store persistence is best-effort (`store.py:_persist` swallows OSError).
