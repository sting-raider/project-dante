# Handoff: Agent D — Promise & Evidence Lead

## Goal

Build the freeze-time Promise Ledger and the deterministic outcome verifier
(plan §20, §12.3–12.7): hash merchant evidence, extract typed promises from
structured offer fields, treat rendered listing text strictly as untrusted
data (§23), link materiality to buyer intent, persist a hashed frozen promise
set; then after fulfillment compare observed facts against material promises
to reach SATISFIED or BREACH_DETECTED. Plus the frozen `/api/contracts/*`
routes.

## Completed

- **Evidence pipeline** (`build_evidence`): canonical sha256 of payload,
  inline `store://` raw-payload ref with payload kept on the record,
  `_type: evidence` in STORE, `EVIDENCE_SNAPSHOT_CREATED` event.
- **Promise extraction** (`extract_promises`): 14 structured keys from offer
  fields (terms.*, unit_amount_paise, delivery_promise.*, sku, category,
  attributes.*). `verification_status="verified"` iff source trusted level is
  `structured_verified`, else `merchant_asserted`. "unknown" enum placeholders
  are not extracted.
- **Untrusted-text scan** (`scan_text_claims` + integration): narrow regex
  pass over rendered listing text for warranty type/duration/region, delivery
  days, return window. Claims agreeing with structured data are dropped;
  contradicting/gap-filling claims are recorded as EXTRA promises with
  `verification_status="unverified"`, confidence 0.30 — they never override
  structured values and are never material. Injection prose ("IGNORE ALL...")
  yields at most product claims, never privileges.
- **Materiality** (`link_materiality`): material iff the promise satisfies a
  CRITICAL hard constraint via the constraint→promise-key map, or it carries
  baseline materiality (price.amount_paise, warranty.type, warranty.region,
  delivery.promised_by_date). Unverified claims excluded. `material_reason`
  cites the matched constraint value.
- **Freeze** (`freeze_promise_set`): orchestrates evidence → extraction →
  materiality; persists promises; computes `promise_set_hash` (sorted
  normalized key/value pairs), `offer_hash` (offer minus volatile
  expires_at/inventory) and `contract_hash = sha256({offer_hash,
  promise_set_hash})`. Emits PROMISE_SET_FROZEN.
- **Verifier** (`evaluate_contract`): loads contract's material promises +
  facts; latest fact per key wins; normalization aliases IN/India/IND→IN,
  AE/UAE/Dubai→AE, case-insensitive enums, ISO dates (date-only deadline =
  end of that day). Mappings: warranty.type/warranty.region/product.region →
  material `MATERIAL_VARIANT_MISMATCH`; condition → critical
  `CONDITION_MISMATCH`; delivered vs promised-by → ≤24h late minor /
  beyond material `DELIVERY_SLA_MISS`. Missing observation ⇒ inconclusive.
  Idempotent per (promise_id, observed_fact_id); repeated calls return the
  full stable breach list. Transitions DELIVERED/VERIFYING → SATISFIED |
  BREACH_DETECTED through validate_transition, CONTRACT_SATISFIED event on
  the happy path.
- **Selection-time severity floor** (`_evaluation_floor`): when Agent C's
  select-offer persists a STORE `_type=evaluation` record for the contract
  (stamped with `contract_id` + `constraints` snapshot per C's Wave-1 fix),
  any promise key that satisfied a CRITICAL hard constraint there gets a
  severity FLOOR of material on verification mismatch (e.g. price mismatch
  under a max_price-critical evaluation escalates minor → material, with the
  reason appended to the explanation). Variant/condition keys unaffected
  (already material/critical by default). **DELIVERY_SLA_MISS is exempt** —
  its ≤24h-minor split is the documented compensation policy (plan §8.3), so
  a critical delivery deadline escalates only beyond 24h.
- **Dotted attribute constraint keys**: CONSTRAINT_TO_PROMISE accepts both
  bare (`form_factor`, `anc`) and compiler-emitted dotted keys
  (`attributes.form_factor`, `attributes.anc`) — C finding, fixed.
- **Routes** (`api/routes/contracts.py`) exactly per docs/API_CONTRACT.md:
  GET /{id} → {contract, promises, entitlements}; POST /{id}/authorize →
  recomputes contract_hash NOW, binds AuthorityEnvelope to it, amount taken
  only from the frozen price promise (409 on missing hashes / drift / unknown
  price), BUYER_AUTHORIZED event; GET /{id}/timeline?category= sorted asc;
  POST /{id}/verify.
- Shared test fixture `tests/conftest.py`: isolated STORE+LOG per test
  (usable by every agent's suite).

## Files changed

- apps/api/project_dante/domain/promises/pipeline.py (new)
- apps/api/project_dante/domain/promises/verifier.py (new)
- apps/api/project_dante/domain/promises/__init__.py (docstring)
- apps/api/project_dante/api/routes/contracts.py (new)
- apps/api/tests/test_promises.py (new)
- apps/api/tests/test_verification.py (new)
- apps/api/tests/conftest.py (new, shared)

## Public interfaces

```python
# project_dante/domain/promises/pipeline.py   (frozen signatures per API_CONTRACT.md)
build_evidence(source_type, payload, trusted_level, synthetic=False,
               scenario_id=None, contract_id=None, excerpt=None) -> dict
extract_promises(offer_dict, evidence_dict) -> list[dict]
link_materiality(promises, intent_dict) -> list[dict]      # mutates + returns
freeze_promise_set(offer_dict, intent_dict) -> dict
# extras:
compute_contract_hash(offer_hash, promise_set_hash) -> str
bind_to_contract(contract_id, promise_ids=None, evidence_ids=None) -> int
unwrap_offer(offer_like) -> (offer, evidence_payload, rendered_text, trusted_hint)
normalize_value(key, value) / normalize_region(value) / parse_dt(value)

# project_dante/domain/promises/verifier.py
evaluate_contract(contract_id) -> dict
# {breaches: [Breach...], new_breach_count, satisfied: bool,
#  status_target: "SATISIFIED"|"BREACH_DETECTED"|"INCONCLUSIVE",
#  status: current ContractStatus, checked_promise_count,
#  unobserved_material_keys: [str]}
```

Accepts both bare MerchantOffer dicts and Agent F wrappers
`{offer, evidence_payload, rendered_text}` everywhere.

## Tests (real results)

```
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_promises.py tests/test_verification.py -q
41 passed in 0.71s     ruff check: All checks passed!
```

Coverage includes: evidence hashing/persistence; untrusted contradiction does
NOT override structured (value stays manufacturer/verified, extra seller claim
recorded unverified <0.5); correct facts ⇒ SATISFIED no breaches; wrong
warranty region/type/product.region ⇒ material MATERIAL_VARIANT_MISMATCH;
condition mismatch critical; late 2h minor / 3 days material; date-only
deadline = on-time all day; missing observations ⇒ neither satisfied nor
breached; latest-fact-wins; idempotent double-verify (no duplicate breach
records or events); state-machine transitions incl. terminal idempotency;
region alias satisfaction; injection text treated as data.

Live smoke against real Agent F service (`integrations/merchant/service.py`,
catalog SKU AST-HP-019): freeze_offer → freeze_promise_set → deliver
wrong_variant ⇒ status BREACH_DETECTED with material MATERIAL_VARIANT_MISMATCH
on warranty.type + product.region. Contracts routes exercised end-to-end via
TestClient (freeze → authorize → order → deliver → verify).

## Known risks

1. **F gap (happy path)**: `apply_fulfillment_event('deliver','correct')`
   emits no facts for `warranty.region`, `condition`, or paid price ⇒ a
   CORRECT delivery stays inconclusive, never SATISFIED. My side already
   aliases `payment.amount_paise`/`unit_amount_paise`/`amount_paid_paise` as
   price facts; F needs to emit condition + warranty.region + a price fact.
2. **F gap (late path)**: F reads promise key `delivery.latest`; I emit
   `delivery.promised_by_date`. I accept both (alias), but until F switches,
   its `late` scenario computes delivered from an empty lookup and no SLA
   breach fires.
3. Rendered-text scan is intentionally narrow regex, not an LLM extractor —
   exotic phrasings may be missed (fails safe: fewer unverified claims).
4. `evaluate_contract` treats any pre-existing breach records as blocking
   SATISFIED even if later facts match — breach remediation flow (Agent E)
   should drive state from BREACH_DETECTED, not re-verify.

## Integration notes

- Verifier consumes facts written by F's `apply_fulfillment_event`
  ({id,_type:'fact',contract_id,key,value,source_artifact_id,observed_at,
  synthetic,scenario_id}) — shape verified compatible.
- Fact-key aliases accepted: `payment.amount_paise`, `unit_amount_paise`,
  `amount_paid_paise` → price; `delivery.actual_date` → delivered-date;
  promise key alias `delivery.latest` → `delivery.promised_by_date`.
- Agent C: call `freeze_promise_set(offer, intent)` at select-offer, put the
  returned ids/hashes on the contract, then `bind_to_contract(cid, ...)` so
  verifier/timeline resolve by contract_id. `authorize` requires
  offer_hash+promise_set_hash present. If the evaluation record is persisted
  with `contract_id`, the verifier auto-applies the critical-constraint
  severity floor — no extra wiring needed.
- Agent E: breaches land as `_type:'breach'` + PROMISE_BREACH_DETECTED events
  (Policy category) keyed by contract aggregate — consume via
  `STORE.list('breach')` filter or timeline.
- Timeline categories follow `events.CATEGORY_BY_EVENT`; breach events are
  Policy, evidence/facts are Evidence.
- Nothing committed; no files outside ownership touched.

## Commit

(none — working tree per buildathon convention; do not commit yet)
