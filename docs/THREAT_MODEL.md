# PROJECT DANTE — THREAT MODEL

Owner: Agent K (Security/Red Team Lead). Companion artifacts:

- Attack catalog: `fixtures/adversarial/security_cases.json`
- Red-team suite: `apps/api/tests/test_security_redteam.py`, `apps/api/tests/test_webhook_chaos.py`
- Findings log: `docs/handoffs/security_findings.md`

Status legend in tables below: **[LANDED]** = mitigation exists in merged code at the cited
location; **[CONTRACTED]** = frozen interface obligation owned by another agent, enforced by the
red-team suite once that module merges; **[RESIDUAL]** = accepted gap, see Residual Risks.

---

## 1. Assets (what an attacker wants)

| # | Asset | Why it matters | Where it lives |
|---|-------|----------------|----------------|
| A1 | Razorpay Key Secret / Webhook Secret | Full payment authority on the account | `settings.py` (env-only), never client-side |
| A2 | Money actions (refunds) | Direct financial loss; each paise moved is real test-mode money but stands for real liability | `domain/money/policy.py`, `razorpay_refund` records |
| A3 | Evidence integrity | Hashed purchase-time promises are the system's memory; poisoned evidence = fraudulent breach/remedy decisions | `evidence_artifacts` (`sha256` per artifact) |
| A4 | Buyer authorization envelope | Bound to exact contract hash; theft/replay = purchases the buyer never approved | `AuthorityEnvelope` (`domain/types.py`) |
| A5 | Append-only event stream | The audit story judges verify; tampering destroys replayability | `domain/events.py:EventLog` |
| A6 | Contract state machine truth | Illegal transitions fabricate PAID/SATISFIED states | `domain/state_machine.py` |
| A7 | Merchant catalog data + text | Untrusted input channel that reaches every agent | `integrations/merchant/service.py`, fixtures |

## 2. Trust boundaries

```
                         TRUST BOUNDARIES (T1..T7)

  [Internet / Razorpay]                        [Attacker-controlled inputs]
        │  T1: webhook delivery                          ▲
        ▼                                                │ T5 raw buyer text
  ┌───────────────────────────────┐   T2 HTTPS   ┌───────┴──────────┐
  │ api/routes/webhooks.py        │◄─────────────│ FastAPI edge     │
  │ HMAC-SHA256(raw_body) gate    │              │ (all /api/*)     │
  └──────────────┬────────────────┘              └───────┬──────────┘
                 │ only verified events                  │ typed pydantic bodies
                 ▼                                       ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │ DOMAIN CORE (trusted): store, events, state_machine, policy      │
  │   • deterministic code only — no LLM output trusted here         │◄── T4 agent
  └───────▲───────────────────────────▲─────────────────────────────┘    outputs
          │ T3 structured             │ T6 merchant text/terms
          │   merchant data           ▼
  ┌────────┴──────────┐      ┌──────────────────────────────┐
  │ merchant service  │      │ promise pipeline (extraction)│──► evidence w/ sha256
  │ catalog/fixtures  │      │ text treated as DATA ONLY    │    (T7 evidence boundary)
  └───────────────────┘      └──────────────────────────────┘

  Razorpay REST API ◄── server-only creds (A1) ── integrations/razorpay/service.py
```

- **T1 Razorpay → webhook endpoint**: unauthenticated network. Only defense is signature
  verification over the **raw** body before any JSON parsing.
- **T2 Browser/client → API edge**: no trust. Client "payment success" is advisory only (I9).
- **T3 Merchant structured data → domain**: semi-trusted (drives offers) but always hashed into
  evidence at freeze time.
- **T4 Agent (LLM) outputs → domain core**: zero trust. Schema-validated, then deterministic gates.
- **T5 Buyer prose → intent compiler**: data only; can raise or lower *preferences*, never limits.
- **T6 Merchant free text → promise extraction**: pure data; cannot mint verified/structured claims,
  cannot trigger tools.
- **T7 Evidence → verification/remedy**: evidence must carry source hash + trusted_level;
  `merchant_asserted` text can never outrank `structured_verified` values.

## 3. STRIDE per boundary

| Boundary | S (Spoofing) | T (Tampering) | R (Repudiation) | I (Info disclosure) | D (DoS) | E (Elevation) |
|---|---|---|---|---|---|---|
| T1 webhook | forged sender ⇒ HMAC check `service.verify_webhook_signature` | body mutated post-sign ⇒ HMAC mismatch; reorder ⇒ idempotent reconcile | Razorpay retries lost ⇒ events persisted pre-processing | payload logged raw ⇒ secrets never in payloads | 1MB bodies ⇒ reject ≤401/413 path, no parse | n/a — no auth context in webhooks |
| T2 API edge | anonymous caller ⇒ demo endpoints gated by `demo_mode`; money endpoints require proposal chain | client forges capture success ⇒ server-side verify + webhook-only final truth (I9) | request IDs + structured completion logs; every action emits domain event with idempotency key | CORS pinned to app origin; secrets server-only; request bodies/query strings omitted from HTTP logs | production client-address limiter (120 reads / 30 writes per 60s) + oversized-body limits + chaos tests | direct `/execute` calls ⇒ executor re-checks policy (I7) |
| T3/T6 merchant data | fake offer snapshot ⇒ snapshot id + sha256 evidence | price drift after freeze ⇒ `offer_hash` mismatch invalidates authorization | provenance via `source_snapshot_id` | n/a public-ish catalog | hostile giant descriptions ⇒ bounded fixture sizes | injection in text ⇒ T6 pipeline rules (§4) |
| T4 agent output | model output spoofing tool results ⇒ schema forbid-extra + enum bounds | amount/type confusion in proposals ⇒ pydantic strict ints (§4 AMT) | all runs logged (`agent_runs`) | prompts never contain secrets (I8) | retry loops capped | refund execution never exposed to agents (I1/I2) |
| T5 buyer text | impersonation ("as admin") ⇒ no authority channel from prose | tries to rewrite constraints ⇒ constraints only widen within mandate; limits not raisable by prose | intent stored verbatim as evidence | n/a | huge intents ⇒ length caps at compile | "refund me double" ⇒ policy engine deterministic, ignores prose |
| T7 evidence | fabricated observed facts ⇒ synthetic-flagged sources only; scenario ids recorded | hash swap ⇒ sha256 recomputed at read; append-only log | event stream immutable append | n/a | evidence flooding ⇒ one fact per key per contract | untrusted claim escalation ⇒ trusted_level lattice enforced |

## 4. Agent-specific threats and mitigations

Mitigation locations use `file:function`. `[LANDED]` items cite merged code; `[CONTRACTED]` items
cite the frozen contract the red team tests against.

### 4.1 Prompt injection via merchant text / tool args (PINJ)

Threat: product description or terms text says "IGNORE ALL PREVIOUS INSTRUCTIONS…", fake
`SYSTEM:` markers, homoglyph look-alikes, fabricated tool results. Goal: flip structured values
(warranty=manufacturer, region=IN), mint verified promises, or trigger money actions.

Mitigations:
- Text rendered into evidence as `merchant_asserted`, never `structured_verified` — trusted-level
  lattice in `domain/types.py:EvidenceArtifact.trusted_level` **[LANDED]**.
- Extraction treats text as untrusted data; structured fields win collisions —
  `domain/promises/pipeline.py:extract_promises` **[CONTRACTED→Agent D]**.
- Promise extractor has **no tools** and no money-action surface (plan §17.3/§18.3).
- Pydantic `extra="forbid"` base model blocks smuggled payload keys —
  `domain/types.py:DanteModel` **[LANDED]**.
- Red-team corpus of 20 vectors incl. homoglyphs + Hindi-language injection:
  `tests/test_security_redteam.py:TestPromptInjectionCorpus`.

### 4.2 Confused deputy (PESC, CCS)

Threat: agent is socially engineered (or a bug steers it) into proposing actions outside its
mandate: refund double, refund another contract's payment, spend past the buyer cap.

Mitigations:
- Deterministic policy engine decides every proposal —
  `domain/money/policy.py:evaluate_money_action` **[CONTRACTED→Agent E]**; decision vocabulary
  closed set ALLOW/REQUIRE_APPROVAL/DENY (`domain/types.py:PolicyDecisionLiteral`) **[LANDED]**.
- Executor re-validates immediately before any Razorpay call (I7) —
  `domain/money/policy.py:execute_remedy` **[CONTRACTED→Agent E]**.
- Cross-contract substitution denied: proposal's `contract_id` must own the referenced
  `razorpay_payment_id` (store lookup before execution).
- Prose has no authority channel: `human_explanation`/`reason_code` are labels, never inputs to
  limit computation — tested in `tests/test_security_redteam.py:TestPrivilegeEscalationViaBuyerText`.
- LLM never holds Razorpay credentials (I1); agents cannot execute refunds (I2).

### 4.3 Amount manipulation (AMT)

Threat: ₹11,499 purchase refunded as ₹114,990; string/float type confusion ("11499", 114.99);
negative amounts; zero; int64 overflow `2**63`.

Mitigations:
- Money is strictly integer paise end-to-end (`API_CONTRACT.md`), enforced by pydantic `int`
  fields on `MoneyActionProposal.amount_paise` **[LANDED]**.
- Policy caps refunds at captured amount per contract; full-refund-below-captured rejected;
  negative/zero rejected **[CONTRACTED→Agent E]**.
- Executor refuses unknown payment ids; no phantom refunds.
- Suite: `tests/test_security_redteam.py:TestAmountManipulation` (8 vectors).

### 4.4 Evidence poisoning (T7)

Threat: attacker injects "observed facts" (fake delivery, fake wrong-variant) to trigger
fraudulent breach → refund.

Mitigations:
- Observed facts require `source_artifact_id` pointing at hashed evidence;
  fulfillment simulation marks everything `"synthetic": true` with `scenario_id` (I17) —
  `domain/types.py:ObservedFact/EvidenceArtifact` **[LANDED]**.
- Demo-injected observations only possible when `DEMO_MODE=true` (routes/demo.py guard)
  **[CONTRACTED→Agent F]**; guarded by `tests/test_security_redteam.py:TestDemoEndpointGuards`.
- Append-only event stream preserves who-recorded-what —
  `domain/events.py:EventLog.append` **[LANDED]**.

### 4.5 Idempotency bypass (RRP, WHC)

Threat: replayed remedy executions or webhooks create duplicate refunds/captures; shared or
reused idempotency keys collapse distinct actions (financial corruption either way).

Mitigations:
- Idempotency keys mandatory on every money action (`types.MoneyActionProposal.idempotency_key`)
  **[LANDED]**; convention `project-dante:{contract_id}:{remedy_id}:{action_version}` (plan §16.9).
- Event log suppresses duplicates by `(aggregate_id, idempotency_key)` —
  `domain/events.py:EventLog.append` **[LANDED]** (unit-tested).
- Webhook dedup by event id; duplicate deliveries get 200 + zero effects (I11) —
  `api/routes/webhooks.py` **[CONTRACTED→Agent B]**.
- Store-level uniqueness of refund per idempotency key asserted in
  `tests/test_security_redteam.py:TestRefundReplay`.

### 4.6 Webhook forgery / replay / reorder (WHF, WHC)

Threat: attacker posts fake `payment.captured` (fabricates PAID), replays old events, or sends
events out of order to desynchronize projections.

Mitigations:
- HMAC-SHA256 over **raw bytes** with webhook secret, compare before parsing (I10) —
  `integrations/razorpay/service.py:verify_webhook_signature` **[CONTRACTED→Agent B]**;
  constant-time compare required.
- Freshness gate requires Razorpay's top-level integer `created_at` to be within
  five minutes (or to belong to an already-claimed failed event being
  redelivered), blocking old signed bodies that arrive with a new delivery id.
- Failure mode: HTTP 401, **no** domain event appended, **no** `webhook_event` row —
  asserted in both suites.
- Out-of-order tolerance: captured-before-authorized reconciles from fetched payment state and
  logs `STATE_RECONCILED` rather than corrupting state (I12) —
  `api/routes/webhooks.py` **[CONTRACTED→Agent B]**.
- Refund binding is conjunctive when identifiers are present: known payment, order, contract,
  and refund projections must agree; mismatches are audited and withheld. Refund-before-capture
  remains supported when the already-issued order is the only binding available.
- Captured-payment binding is conjunctive as well: a capture must carry a non-empty payment id,
  and any known contract, order, and payment projection must agree before the payment id is
  attached or `PAID` is granted. Missing or conflicting identifiers are audited as
  `STATE_RECONCILED` with `action=paid_withheld`; compare-and-swap/put-if-absent writes and a
  final contract re-read close concurrent repointing gaps. Regression coverage lives in
  `tests/test_webhooks.py`.
- Unknown-entity events (refund for foreign payment id) stored, never acted upon.
- Chaos coverage: `tests/test_webhook_chaos.py` (duplicate×5, orphan, skipped-stages, forged,
  unsigned, missing-header, 1MB, non-JSON).

### 4.7 State machine abuse (STA)

Threat: skipping the payment spine (`DRAFT→PAID`), resurrecting terminal states, or laundering a
breach straight to REMEDIATED without executing anything.

Mitigation: single validation point —
`domain/state_machine.py:TRANSITIONS` + `validate_transition` **[LANDED]**; terminal states have
empty transition sets; red-team asserts 11 illegal pairs rejected and the legal spine accepted.

### 4.8 Secrets leakage (SEC)

Threat: live/test keys or private keys committed to the repo (judges check this explicitly).

Mitigations:
- Settings env-only with `.env.example` pattern; dev webhook secret default clearly non-production.
- Repo-wide scan in CI/local: `tests/test_security_redteam.py:TestSecretsHygiene` walks the tree
  (excluding `.venv`/`node_modules`/`.git`) matching `rzp_live_`/`rzp_test_`+8 alnum,
  `sk-ant-`, `gsk_`, PEM private-key headers. Any hit fails the suite.

## 5. Residual risks (stated honestly)

1. **JSON snapshot persistence is best-effort** (`db/store.py:_persist` swallows OSError). A
   filesystem failure or crash during snapshot replacement can still lose the newest tail of
   the audit trail. The Postgres backend removes the single-file failure mode when configured.
2. **Single-process idempotency**: dedup lives in process memory (`EventLog._idem_seen`) +
   store file. Two API replicas could double-process concurrent first-time events until Postgres
   unique constraints land. Deployment is single-replica for the buildathon.
3. **Webhook secret default**: `settings.py` ships a dev default (`dante-dev-webhook-secret`) so
   sandbox flows work out-of-the-box. Production deployments MUST override via env; the secrets
   scan does not flag it because it is not a credential format.
4. **No distributed rate limiting or general API authentication** — production now applies a
   bounded process-local limiter (120 reads / 30 writes per client address per rolling 60 seconds),
   while health/readiness, CORS preflights, and the signed Razorpay webhook intake are exempt.
   The single-replica deployment requirement remains, and money-mutation endpoints are protected
   by the proposal chain rather than caller identity. General authentication and a shared limiter
   remain post-buildathon work.
5. **LLM provider paths** (when enabled) inherit the same schema gates, but prompt-level hardening
   is provider-dependent; the deterministic layer is the actual control plane.
6. **Synthetic fulfillment writes** are guarded by `demo_mode`, which defaults true for the
   buildathon demo. Operators must set `DEMO_MODE=false` outside demos.

## 6. Red-team results

Latest run: `cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_security_redteam.py tests/test_webhook_chaos.py -q`
→ **72 passed / 0 failed / 0 skipped**. Full API tree: **478 passed, 15 skipped**
(Postgres/Docker unavailable).

Three vulnerabilities found during red-teaming were VERIFIED FIXED and are now
covered by permanent regression tests:

| ID | Severity | Vulnerability | Fix location | Regression test |
|---|---|---|---|---|
| K-01 | HIGH | `refund_full` under-amount auto-approved → case closed while buyer under-refunded | `domain/money/policy.py:385` (DENY unless `amount == captured`) + executor mirror at `policy.py:710` | `TestAmountManipulation::test_under_amount_not_allowed_silently` |
| K-02 | MEDIUM | string/float/bool amounts coerced via `int()` instead of rejected | `policy.py:281` strict typing (`INVALID_AMOUNT_TYPE`) + mirror at `policy.py:699` | `TestAmountManipulation::test_string_amount_never_becomes_money`, `::test_float_rupee_confusion_never_becomes_money` |
| K-03 | HIGH | signature-valid captured webhook force-wrote PAID onto CANCELLED/FAILED/DRAFT contracts, bypassing the state machine | `api/routes/webhooks.py`: `_walk_to_paid` legal-path walk + record-withhold fallback (`paid_withheld`) | `TestWebhookChaos::test_captured_never_resurrects_cancelled_or_draft_contracts` |

Post-fix hardening also verified: the observed payment id is no longer grafted
onto non-payable contracts (`_withhold_captured_event` gate), and post-paid states
never regress under late redelivery. Capture identifiers and known payment projections
are also binding-checked before any projection or contract write
(`_payment_record_binding_conflict`, `_safe_transition`, and `test_webhooks.py`).
These cases are covered by permanent attacks and regression tests.

Full per-vector status:

| Vector group | Cases | Result | Notes |
|---|---|---|---|
| STA state machine abuse | 23 (11 illegal + 12 legal) | PASS | all illegal pairs raise `InvalidTransition` |
| SEC secrets hygiene | repo-wide scan | PASS | 0 unexplained offenders; single documented inert allowlist entry (`client.py` synthetic sandbox key); owners replaced other secret-shaped literals with non-key-shaped placeholders |
| Event-log idempotency primitive | 1 | PASS | duplicate `(aggregate,key)` suppressed |
| AMT amount manipulation vs policy | 10 | PASS | inflated/negative/zero/overflow/non-int all DENY; full-refund exactness enforced at evaluate AND execute |
| CCS cross-contract substitution | 2 | PASS | executor derives payment id from stored contract; structural check refuses drift; unknown payment ⇒ no refund |
| RRP refund replay (remedy + client level) | 8 | PASS | replay ×2/×3 ⇒ exactly one refund record, cached result returned |
| WHF forged webhook signatures | 6 | PASS | garbage/wrong-secret/tampered/empty ⇒ False + 401 route + zero persistence; positive control verifies True |
| WHC webhook chaos | 13 | PASS | duplicates ×5, orphan, reorder, amount-mismatch capture safe; non-payable states neither resurrected nor grafted; post-paid states no-regression |
| PINJ injection corpus | 50-case Agent J corpus + 20 inline vectors | PASS | text never overrides structured truth; zero side effects |
| PESC privilege escalation via prose | 3 | PASS | compile/policy treat escalation text as data; no limits inflated |
| Client payment verification abuse | 3 | PASS | verify-client never mints PAID; forged sigs rejected; order swap rejected |
| DEM demo guards (incl. simulate-event) | 5 endpoints | PASS | 403 with `demo_mode=False` |

Vulnerability lifecycle (found → assigned → fixed → verified) is documented in
`docs/handoffs/security_findings.md`. The suites remain permanent regression
guards: any future change reintroducing these vectors fails CI.
