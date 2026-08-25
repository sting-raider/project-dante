# Security Findings — Agent K (Red Team)

Last updated: 2026-08-25, after all Wave 1 modules landed.
Suites: `apps/api/tests/test_security_redteam.py` + `tests/test_webhook_chaos.py`
Latest run: **66 passed / 1 failed / 3 xfailed (strict), 0 skipped**.

Severity scale: CRITICAL = unauthorized money movement or forged payment truth;
HIGH = invariant violation reachable by an authenticated/valid-signature party;
MEDIUM = hardening gap bounded by other controls; LOW = defense-in-depth nit.

---

## CONFIRMED VULNERABILITIES

### K-03 — Webhook capture teleports CANCELLED/FAILED/DRAFT contracts to PAID (HIGH)

**Status:** OPEN — assigned to Agent B (webhooks.py owner)
**File:** `apps/api/project_dante/api/routes/webhooks.py:300` (`_on_payment_captured`, final fallback)
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

**Status:** OPEN — assigned to Agent E (policy.py owner)
**File:** `apps/api/project_dante/domain/money/policy.py:361` (`evaluate_money_action`) + `policy.py:902` (`_execute_allowed` → REMEDIATED)
**Marker:** strict xfail at `tests/test_security_redteam.py::TestAmountManipulation::test_under_amount_not_allowed_silently`

Attack: propose `type="refund_full"` with `amount_paise = captured // 2`.
`evaluate_money_action` checks only `amount <= captured` and positivity — there
is no equality requirement for full refunds — so it returns ALLOW
(AUTO-APPROVED BY POLICY P-REFUND-03). The executor then runs the refund and
marks the contract REMEDIATED (`policy.py:902`). Result: the buyer is silently
under-refunded while the case shows resolved; the partial-refund reason
allow-list and its `max_auto_amount_paise` cap are bypassed entirely (a partial
refund of the same amount would have required an allowed partial reason).

Exploitability note: requires the ability to author a MoneyActionProposal (an
agent or route caller). It does NOT require approval above ₹20,000 since the
half amount stays under the threshold. Bounded by captured amount, so it cannot
over-refund — this is case-closure fraud, not money multiplication.

Fix direction (Agent E): for `refund_full`, DENY (or REQUIRE_APPROVAL) when
`amount < captured`; alternatively clamp to captured and re-type as
refund_partial subject to partial rules.

---

### K-02 — Non-int amounts coerced instead of rejected (MEDIUM)

**File:** `apps/api/project_dante/domain/money/policy.py:280` (`amount = int(proposal.get("amount_paise"))`)
**Status:** OPEN — assigned to Agent E
**Markers:** strict xfail x2 at `tests/test_security_redteam.py::TestAmountManipulation::test_string_amount_never_becomes_money` and `::test_float_rupee_confusion_never_becomes_money`

`int()` accepts `"11499"` → 11499, `114.99` → 114 (silent truncation), `True`
→ 1. Plan §19 says never coerce malformed financial values. Not currently an
over-refund path (positivity + captured-ceiling bounds hold), but truncation of
114.99 → 114 paise is silent data corruption of a financial field, and bool is
a footgun. Fix: reject non-int types outright:
```python
raw_amt = proposal.get("amount_paise")
if isinstance(raw_amt, bool) or not isinstance(raw_amt, int):
    -> DENY INVALID_AMOUNT_TYPE
```

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
