# Handoff: Agent E — Rights & Remedy Lead

## Goal

Build Project Dante's post-breach half: Purchase Rights Graph derivation,
deterministic eligibility, remedy planning with the §14.2 scorer, the
deterministic money policy engine (§15), and THE GATED PIPELINE that turns an
approved RemedyProposal into a real/sandbox Razorpay refund with idempotency,
audit events, and state-machine-legal transitions.

## Completed

- Merchant money policy YAML exactly per plan §15 (thresholds, reason
  allow-lists, agent permissions).
- Deterministic policy engine: ALLOW / REQUIRE_APPROVAL / DENY with policy-id
  citations (P-REFUND-01..04, P-PAYMENT-01, P-AGENT-01), persisted
  `policy_decision` records, POLICY_DECIDED/ALLOWED/DENIED audit events,
  snapshot hash of the exact policy used.
- Gated execution pipeline: build MoneyActionProposal -> evaluate -> FINAL
  EXECUTOR CHECK -> refund -> contract REMEDIATED, with idempotent replay.
- Human approval path for threshold-exceeding full refunds.
- Rights engine: entitlement derivation (5 rights), eligibility evaluation
  against live facts/breaches/evidence/expiry, graph projection {nodes, edges}.
- Remedy planner: deterministic scoring + ranking + replacement-unavailable
  rule; explanations generated from facts; LLM never decides anything here.
- Routes per frozen API_CONTRACT.md; FastAPI auto-registration picks them up.

## Files changed (all exclusively owned)

- `apps/api/project_dante/domain/money/policies/aster_electronics.yaml`
- `apps/api/project_dante/domain/money/policy.py`
- `apps/api/project_dante/domain/rights/engine.py`
- `apps/api/project_dante/domain/remedies/planner.py`
- `apps/api/project_dante/api/routes/rights.py`
- `apps/api/tests/test_rights_engine.py` (10 tests)
- `apps/api/tests/test_policy_engine.py` (20 tests)
- `apps/api/tests/test_remedy_flow.py` (15 tests)

## Public interfaces

```python
# project_dante.domain.rights.engine
derive_entitlements(contract_id) -> list[dict]      # idempotent re-derive
evaluate_eligibility(contract_id) -> list[dict]     # statuses: eligible/blocked/
                                                    # invalid/expired/dormant/consumed
build_rights_graph(contract_id) -> {"nodes": [...], "edges": [...]}
get_breaches(contract_id) -> list[dict]
resolve_ctx(...) / MATERIAL_REASONS / SLA_REASONS   # shared vocabulary

# project_dante.domain.remedies.planner
plan_remedies(contract_id) -> {"proposals": [...], "chosen": {...}|None}
get_proposals(contract_id) -> list[dict]            # ranked order
score_remedy(rtype, value_paise, captured_paise, hours) -> components+score

# project_dante.domain.money.policy
load_policy() -> dict                               # cached; safe defaults if YAML missing
policy_snapshot_hash() -> str
normalize_reason_code(raw) -> str                   # breach code -> policy reason
evaluate_money_action(proposal_dict) -> PolicyDecision dict
execute_remedy(proposal_id) -> {"decision", "money_action", "refund", "executed", ["error"|"note"]}
approve_remedy(proposal_id) -> {"money_action", "refund"}
build_money_action_for_remedy(proposal_id) -> dict
```

Routes: GET `/api/contracts/{id}/rights|breaches|remedies`;
POST `/api/remedies/{id}/policy|approve|execute`.

## Tests (real results)

```
cd apps/api && .venv/Scripts/python.exe -m pytest \
  tests/test_rights_engine.py tests/test_policy_engine.py tests/test_remedy_flow.py -q
=> 49 passed + 4 subtests in ~0.7s   (45 original + 4 new security regressions)
```

Red-team cross-check (`tests/test_security_redteam.py`): every non-K01/K02
attack passes; the three strict-xfail regression markers for K-01/K-02 now
XPASS — both vulnerabilities are FIXED. Agent K should flip those markers on
their next run (until then those three show as XPASS(strict) "failures").

Also verified through the assembled FastAPI app (TestClient): rights/breaches/
remedies 200, policy ALLOW, execute -> executed:true -> REMEDIATED, repeated
execute returns the SAME refund id with no second refund record.

Full repo suite at time of handoff: everything green except the three
expected `[XPASS(strict)]` lines above (and any suites other agents are mid-
edit on).

## Security fixes (Agent K findings — patched post-handoff)

- **K-01 HIGH — under-amount full refund** (`FULL_REFUND_AMOUNT_MISMATCH`):
  `refund_full` now requires amount == captured amount EXACTLY. A half-amount
  "full" refund previously auto-approved and closed the case while the buyer
  was under-refunded, bypassing the partial-refund reason list and its cap.
  Enforced TWICE: at policy evaluation AND mirrored in the executor structural
  check (downward tamper after evaluation also fails). Smaller compensations
  must use `refund_partial` with an allowed partial reason.
  Consequence: REQUIRE_APPROVAL threshold scenarios are reached via contracts
  whose captured amount exceeds 2000000 paise — a full refund always equals
  captured by definition.
- **K-02 MEDIUM — type coercion** (`INVALID_AMOUNT_TYPE`): `amount_paise` must
  be a genuine int (bools rejected too). Strings ("11499"), floats (114.99
  rupee-truncation), True->1, and None are DENYed un-coerced per plan §19.
  Also enforced in `_executor_structural_check`.

## Key behaviors guaranteed (with test coverage)

1. Hero scenario end-to-end: wrong variant breach -> replacement eligible ->
   inventory False -> replacement blocked/rejected
   (`replacement_inventory_unavailable`, rank None) -> refund_full chosen ->
   policy ALLOW under Rs 20,000 -> sandbox refund -> REMEDIATED.
2. Idempotency: N executions of the same remedy => exactly ONE refund record;
   replay short-circuits before any gateway call.
3. Amount manipulation (plan §23): inflating OR deflating the amount after a
   prior ALLOW is caught by pipeline re-evaluation / executor check
   (AMOUNT_EXCEEDS_CAPTURED / FULL_REFUND_AMOUNT_MISMATCH); missing payment
   id or changed target payment fails the final executor check with status
   `failed` and REFUND_FAILED event, contract recovered to breached family,
   no money moved.
4. Denial path leaves contract BREACH_DETECTED, money action `denied`,
   POLICY_DENIED event; denied actions cannot be approved.
5. REQUIRE_APPROVAL above 2000000 paise (exact threshold still autonomous);
   approve walks AWAITING_REMEDY_APPROVAL -> REMEDY_EXECUTING -> REMEDIATED.
6. Policy snapshot hash stable across evaluations and equal to sha256 of the
   loaded YAML policy dict.

## Known risks / limitations

- `_razorpay_service()` late-binds Agent B's service at call time. If both
  B's module and my fallback stub were somehow unreachable there'd be no
  executor; in practice B's service is present and its SandboxClient handles
  payment validation + amount_refunded tracking correctly (tests exercise it).
- Entitlement ids are content-derived (`ent_<hash(contract,slug)>`) so they're
  stable across re-derivation; do not "fix" this to uuid4 or graph edges and
  remedies will orphan.
- `replacement.available` fact semantics: None (unknown) keeps replacement
  eligible; only explicit False blocks it. Demo inject must write value False.
- Warranty validity is judged on RECEIVED-unit attributes when observed facts
  exist (falls back to promises pre-delivery) — intended per spec, but any new
  fact keys must keep using `warranty.type` / `product.region`.
- Planner does not re-plan once proposals exist (protects bound money
  actions); delete remedy records explicitly if a scenario must re-plan.

## Integration notes

- Reason-code bridge: upstream verifiers/demo may emit UPPERCASE codes
  (e.g. MATERIAL_VARIANT_MISMATCH, DELIVERY_SLA_MISS); `normalize_reason_code`
  maps them into the YAML allow-list vocabulary. Unknown codes DENY safely.
- The demo "mark replacement unavailable" control just needs to append a fact
  `{key: "replacement.available", value: False}` — eligibility + planner pick
  it up automatically on next evaluation.
- Frontend (Agent I): graph node ids are prefixed `purchase:`/`promise:`/
  `entitlement:`/`breach:`/`evidence:`/`remedy:`; edge kinds are exactly the
  plan §13 set plus PROPOSED_FOR for rejected remedies. Entitlement nodes
  carry status for coloring; remedy nodes carry rank + rejected_reason.
- Execute route response includes `executed: bool` in addition to the frozen
  contract fields (additive only).
- No git commits made, per instructions.
