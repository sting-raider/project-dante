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
→ 70 passed / 0 failed / 0 skipped   (<1s)
```
Full API tree at verification time: **301 passed** (one unrelated midnight-boundary flake in
Agent C's `test_intent_rules.py::test_delivery_within_n_days`, passes standalone — the test
computes its expectation from module-import time while rule_compile reads the clock per call;
worth a fix to pin `now`).

## VULNERABILITIES FOUND — all three VERIFIED FIXED (2026-08-26)

Red team found 3 vulnerabilities across 2 owners during Wave 1; all were fixed by their
owners and verified by re-running these suites against the patched modules:

| ID | Severity | Summary | Verified fix |
|---|---|---|---|
| K-03 | HIGH | Webhook capture force-wrote PAID onto CANCELLED/FAILED/DRAFT contracts, bypassing validate_transition | `webhooks.py`: `_walk_to_paid` legal-path walk + record-withhold fallback (`paid_withheld`) — no status write from non-payable states |
| K-01 | HIGH | `refund_full` under-amount auto-approved → contract REMEDIATED while buyer under-refunded | `policy.py:385` exact-amount DENY + executor mirror at `policy.py:710` (downward tamper also blocked) |
| K-02 | MEDIUM | string/float/bool amounts coerced via `int()` | `policy.py:281` strict typing, `INVALID_AMOUNT_TYPE`, bools rejected; mirrored at `policy.py:699` |

Lifecycle details + original reproductions preserved in
`docs/handoffs/security_findings.md`. The regression tests stay permanent.

Process note: my owned test files were edited once externally (xfail markers removed after
the K-01/K-02 fixes landed). Test bodies/assertions were intact and I independently verified
each fix against policy.py/webhooks.py source before accepting the green run.

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
