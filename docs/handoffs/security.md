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
  webhook-secret default, no general edge authentication or distributed rate limiting).
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
5. Production has a bounded process-local API limiter (120 reads / 30 writes per client address
   per rolling 60 seconds); health/readiness and signed webhook intake are exempt. There is still
   no general API authentication or distributed limiter, so deployment remains single-replica.

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

## Docs remediation (2026-08-26, docs-remediation agent)

Fresh-engineer review findings, each fixed and verified against the live repo:

1. **README verify-e2e path** — `.venv/Scripts/python.exe ../scripts/verify_e2e.py`
   from `apps/api` resolved to nonexistent `apps/scripts/`. Fixed to
   `../../scripts/verify_e2e.py`; verified `scripts/verify_e2e.py` is repo-root.
   Also implemented as `make e2e`.
2. **`uv sync --extra-dev` invalid flag** — confirmed via `uv sync --help`
   (`--extra <EXTRA>`). Fixed the one bad occurrence (README backend quickstart).
   README:145, CI (`uv sync --extra dev`) and Dockerfile.api (plain
   `uv pip install .`, no flag) were already correct; grep confirms no other
   `extra-dev` remains.
3. **Root .env not honored** — settings.py used CWD-relative `env_file=".env"`,
   so README's `cp ../../.env.example ../../.env` produced a file the server
   never read. Code fix in `project_dante/settings.py`: absolute env_file list
   `[<repo_root>/.env, <repo_root>/apps/api/.env]` resolved via
   `Path(__file__).resolve().parents[3]`. Verified pydantic-settings merge order:
   files merge in listed order, later entries override earlier (apps/api/.env wins),
   real environment variables override both files — proven with a temp-file A/B test.
   README updated to state root .env works. docs/RAZORPAY.md's existing
   "In apps/api/.env" instruction stays valid under both semantics — unchanged.
4. **Makefile defects** — added `setup` target (`uv sync --extra dev` + `npm install`);
   `test`, `lint`, `typecheck` now depend on it. Removed phantom `worker` target
   (apps/worker holds only an empty src/; ARQ worker already listed as future work in
   docs/FUTURE.md). Replaced silent no-op `npx next lint || true` (no eslint config in
   apps/web) with `npx tsc --noEmit`, the gate CI actually runs. Added missing `e2e`
   target recipe. `make -n lint` dry-run verifies correct expansion.
5. **Fixed rights-graph node count** — "(22-node graph)" reworded to a dynamic span
   ("spanning promises, entitlements, evidence, breaches, and remedies").
6. **Repository structure listing** — packages/contracts annotated "(reserved; empty)";
   docs enumeration now includes API_CONTRACT.md and injection corpus under fixtures.
7. **EVALS.md case-count misattribution** — datasets/*.json hold 147 cases
   (68 intent + 26 offer + 25 breach + 28 money-safety), not 197; the other 50 payloads
   are fixtures/adversarial/injection_corpus.json. Sentence rewritten with per-dataset
   counts (verified against the JSON files and evals/reports/summary.json).
8. **Stale test count** — suite now collects more than the documented 320 (remediation
   wave added tests); replaced fixed counts in README with "full suite" wording so the
   number can't rot again.

Verification: full pytest run green after the settings change (322→324 passed across
reruns as concurrent remediation landed); one transient failure
(test_stale_approval_voided_when_amount_changes) reproduced under BOTH old and new
settings and vanished on rerun — unrelated to these changes, attributed to concurrent
edits. Server boot-tested on port 8001: `/api/health` returned OK JSON
(sandbox-adapter, deterministic-fallback), then killed cleanly. README eval command
executed verbatim: all five suites PASS. No git commits made.

Previously flagged runbook issue closed on 2026-08-29: docs/DEMO_SCRIPT.md now makes
Docker/Postgres optional for the default JSON-store demo and states that the verifier
must be invoked from the repository root with its exact path.
