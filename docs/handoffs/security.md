# Handoff: Agent K — Security / Red Team Lead

## Goal

Attack the real Project Dante implementations across every trust boundary, document the
threat model, and leave behind a permanent adversarial regression suite. Per the master
plan: never fabricate results — skipped = skipped, fail = fail.

## Completed

- **Threat model** (`docs/THREAT_MODEL.md`): assets A1–A7; 7 trust boundaries with ASCII
  diagram (T1 webhook / T2 API edge / T3 merchant structured / T4 agent outputs /
  T5 buyer prose / T6 merchant text / T7 evidence); STRIDE-per-boundary table;
  8 agent-specific threat classes with mitigations mapped to `file:function`;
  honest residual risks (best-effort persistence, single-process idempotency, dev
  webhook-secret default, no edge rate limiting/auth).
- **Red team suites** (`apps/api/tests/test_security_redteam.py`, `apps/api/tests/test_webhook_chaos.py`)
  covering all requested vectors a–j plus extras found valuable in review:
  client-payment-verification abuse, amount-mismatched capture guard, terminal-state
  resurrection, bool amounts, distinct-key non-collision. Modules under attack are
  `importorskip`'d so the suite degrades gracefully when a module is absent; every skip
  was tracked and driven to zero as parallel agents merged.
- **Attack catalog** (`fixtures/adversarial/security_cases.json`): 23 structured cases,
  all now `"status": "tested"` with per-case results inline.
- **Findings doc** (`docs/handoffs/security_findings.md`): full reproductions + suggested
  fixes, owned separately from this handoff.
- Vulnerabilities reported directly to owners (razorpay-lead: K-03; rights-lead: K-01/K-02)
  with failing-test pointers.

## Files changed (all exclusively owned)

- `docs/THREAT_MODEL.md`
- `fixtures/adversarial/security_cases.json`
- `apps/api/tests/test_security_redteam.py`
- `apps/api/tests/test_webhook_chaos.py`
- `docs/handoffs/security_findings.md`
- `docs/handoffs/security.md` (this file)

## Tests — real results

```
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_security_redteam.py tests/test_webhook_chaos.py -q
→ 66 passed / 1 failed / 3 xfailed(strict) / 0 skipped   (~1s)
```

- The 1 FAILED is **K-03** (real vulnerability, live regression marker — see below).
- The 3 xfails are strict markers for **K-01/K-02**; they flip to hard passes when Agent E
  fixes policy.py.
- Earlier intermediate runs during module landing had up to 31 skips; every skip was
  resolved by re-running as modules merged. No test was ever relaxed to force a pass;
  two of my own tests were corrected where my seed state was wrong (not the defense).

## VULNERABILITIES FOUND

### K-03 — HIGH — webhook capture resurrects CANCELLED/FAILED/DRAFT contracts to PAID
`api/routes/webhooks.py:300` (`_on_payment_captured` fallback) force-writes
`status="PAID"` outside `validate_transition`. Verified standalone: valid-signed captured
event on a CANCELLED contract → HTTP 200, status PAID. Violates I12 + single-point
transition validation. Owner: Agent B. Live failing test:
`test_webhook_chaos.py::TestWebhookChaos::test_captured_never_resurrects_cancelled_or_draft_contracts`.

### K-01 — HIGH — refund_full under-amount auto-approves and closes the case
`domain/money/policy.py:361`: no equality check for refund_full → half-amount "full"
refund gets ALLOW, executes, contract marked REMEDIATED (`policy.py:902`). Buyer
under-refunded while case shows resolved; partial-refund reason list + ₹500 cap bypassed.
Owner: Agent E. Strict xfail markers ×1 in `TestAmountManipulation`.

### K-02 — MEDIUM — money amounts coerced instead of rejected
`policy.py:280`: `int()` accepts `"11499"`, truncates `114.99`→114, maps `True`→1.
Bounded by captured ceiling (not an over-refund path) but violates §19 strict typing.
Owner: Agent E. Strict xfail markers ×2.

Full details + fix directions: `docs/handoffs/security_findings.md`.

## Defenses verified holding (highlights)

Signature-before-parse webhook gate (401 + zero persistence on forgery, positive control
verifies legit traffic); duplicate-event dedup before any domain effect; captured-amount
mismatch withholds PAID; verify-client can never mint PAID; executor derives payment ids
from the stored contract + final drift check blocks cross-contract substitution; remedy-
and client-level refund idempotency (×2/×3 replay = one effect); prompt-injection corpus
(20 vectors incl. homoglyph/Hindi) cannot override structured truth or cause side effects;
buyer prose cannot inflate limits; all demo endpoints gated (incl. simulate-event);
repo-wide secrets scan clean with one documented synthetic allowlist entry; state machine
rejects all illegal transitions tested.

## Known risks / residual

1. K-03/K-01/K-02 open at handoff time — owners notified, regression markers in place.
2. `_recompute_contract_hash` recomputes from current STORE contents rather than the frozen
   snapshot; in-place promise mutation post-freeze would track the mutation. Recommend
   storing frozen payload hashes at freeze time (integration wave item).
3. Dev default webhook secret ships in settings; production must set env var.
4. Single-process idempotency until Postgres unique constraints land; deploy single-replica.
5. No rate limiting/auth on API edge (demo posture).

## Integration notes

- Suites are safe to run alongside other agents' tests: isolated store file
  (`.dante-redteam-store.json` via env default), fresh STORE+LOG per test.
- When K-03 is fixed, expect exactly one previously-failing test to pass; when K-01/K-02
  are fixed, the three strict xfails become XPASS→strict failures prompting me to flip
  them to plain asserts. Ping me either way and I'll refresh THREAT_MODEL §6 +
  security_findings.md.
- `TestSecretsHygiene.ALLOWLIST` requires justification per entry; adding entries without
  a written rationale should be treated as a process violation.

## Commit

Not committed per instructions (no git commits by Agent K).
