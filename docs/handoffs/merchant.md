# Handoff: Agent F — Merchant Interface Lead

## Goal

Make the fictional merchant **Aster Electronics** usable by an AI buyer:
a committed 112-product catalog with deliberately mixed warranty metadata, a
frozen cross-agent service interface (`search / get_product / inventory /
freeze / fulfillment-sim / seed`), merchant + demo REST routes, and the
synthetic fulfillment scenarios that drive the demo failure paths.

## Completed

- `fixtures/catalog/aster_catalog.json` — 112 products (headphones 25,
  phones 20, routers 18, laptops 18, keyboards 8, mice 6, monitors 8,
  chargers-cables 9). Generated deterministically (seeded) at build time and
  committed; every row validated against the frozen `MerchantOffer` model.
- Hero SKU **AST-HP-ANC-001** exactly per storyboard: "Aster ANC Pro Wireless
  Over-Ear Headphones", Rs 11,499 (1149900 paise), over-ear/ANC/bluetooth-5.3,
  manufacturer warranty 12 months IN, return/replacement 10 days, new, 2–4 day
  delivery. Plus **5 feasible alternates** (AST-HP-002..006) and near-miss
  decoys: seller warranty (007/008), AE-region stock (009/010), over budget
  (011/012), refurbished (013) — so offer evaluation visibly fails them.
- Warranty mix across catalog: manufacturer 62 (55.4%), seller 17 (15.2%),
  none 11 (9.8%), unknown 22 (19.6%) — analytics will show metadata blockers.
- `catalog_loader.py` — module-level cached load of the fixture; deep copies out.
- `service.py` — full frozen interface + `render_listing_text`,
  `catalog_analytics_base`.
- `routes/merchant.py` — search / product / analytics endpoints.
- `routes/demo.py` — reset / ship / deliver(scenario) /
  replacement-unavailable, all gated on `settings.demo_mode` → 403.
- `db/seed.py` — `python -m project_dante.db.seed` CLI.
- `fixtures/catalog/README.md` — fixture provenance note.

## Files changed

```
fixtures/catalog/aster_catalog.json          (new)
fixtures/catalog/README.md                   (new)
apps/api/project_dante/integrations/merchant/catalog_loader.py  (new)
apps/api/project_dante/integrations/merchant/service.py         (new)
apps/api/project_dante/api/routes/merchant.py                   (new)
apps/api/project_dante/api/routes/demo.py                       (new)
apps/api/project_dante/db/seed.py                               (new)
apps/api/tests/test_merchant.py                                 (new)
apps/api/tests/test_demo_sim.py                                 (new)
```

## Public interfaces

```python
# project_dante.integrations.merchant.service  (frozen, docs/API_CONTRACT.md)
search_catalog(query=None, category=None, max_price_paise=None, limit=50) -> list[dict]
get_product(sku) -> dict | None            # {"product": ..., "offers": [...]}
check_inventory(sku) -> int
freeze_offer(offer_id) -> dict             # {offer(+inventory_snapshot,+snapshot_hash),
                                           #  evidence_payload, rendered_text}
apply_fulfillment_event(contract_id, kind, scenario=None) -> dict
seed_catalog() -> int                      # idempotent

# extras
render_listing_text(offer_dict) -> str     # human-readable listing paragraph
catalog_analytics_base() -> dict           # total/warranty coverage/return-policy share
catalog_loader.load_catalog() -> list[dict]

# REST
GET  /api/merchant/catalog/search?q=&category=&max_price_paise=&limit=
GET  /api/merchant/products/{sku}          # 404 on unknown sku
GET  /api/merchant/analytics               # {total_products, warranty_metadata_coverage,
                                           #  machine_readable_return_policy, evaluated_intents,
                                           #  ai_transactable_rate, blocker_distribution}
POST /api/demo/reset                       # STORE.reset + LOG.reset + seed -> {products: N}
POST /api/demo/contracts/{id}/ship
POST /api/demo/contracts/{id}/deliver      # body {scenario: correct|wrong_variant|late}
POST /api/demo/contracts/{id}/replacement-unavailable
# all demo routes: 403 when DEMO_MODE off, 404 unknown contract, every response synthetic:true
```

### Fulfillment semantics (what Agent D/E can rely on)

- Facts are STORE records `_type: fact`: `{id obs_*, contract_id, key, value,
  source_artifact_id, observed_at, synthetic: true, scenario_id}`.
- `ship` → facts `shipment.status=shipped`, `shipment.carrier="SynthEx"` +
  FULFILLMENT_SHIPPED event (synthetic).
- `deliver correct` → copies promised values from stored `_type promise`
  records of that contract: `warranty.type`, `product.region`;
  `delivery.delivered_date` within any promise.
- `deliver wrong_variant` → `warranty.type="seller"`, `product.region="AE"`.
- `deliver late` → promised values but `delivered_date = promised_by_date+3d`
  (needs a `delivery.latest` promise to anchor; else today+3) plus fact
  `delivery.days_late=3`.
- `replacement_check scenario="unavailable"` → fact `replacement.available=false`.

## Tests — real results

```
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_merchant.py tests/test_demo_sim.py -q
→ 33 passed in 1.12s   (1 unrelated StarletteDeprecation warning)
ruff check (owned files) → All checks passed
python -m project_dante.db.seed → "Seeded 112 Aster Electronics offers into STORE."
```

Coverage includes: fixture integrity/bounds, hero exact values, feasible set,
decoys, warranty mix, search scoring/ordering/filters, freeze snapshot +
rendered text variants, seed idempotency, all three deliver scenarios,
DEMO_MODE guard (403s), route 404/422s, honest analytics math.

## Known risks

1. **Verifier hook is defensive**: `/deliver` try/except-imports Agent D's
   `evaluate_contract`. Until that lands, responses carry
   `verification_error: "verifier module not available"` and empty breaches.
   No change needed from D — the call site already matches the frozen name.
2. **Analytics reads `_type: evaluation` records** written by Agent C's intent
   search. If C stores evaluations under another `_type`, only the
   `ai_transactable_rate` / `blocker_distribution` fields stay at zero —
   catalog metrics remain correct. Coordinate key names if different.
3. **Search is intentionally naive** (token gate + weighted keyword score,
   ties by price asc). Good enough for demo; not semantic.
4. **`freeze_offer` reads live fixture**, not STORE offers — snapshot is the
   source of truth for contracts. If integration wants inventory decrements,
   that needs a small follow-up.
5. Late-scenario anchoring prefers the frozen `delivery.promised_by_date`
   promise; without one it falls back to today+3 days.

## Integration notes

- No files outside my ownership were touched; nothing committed to git.
- Catalog regeneration script lives outside the repo (job tmp). The fixture is
  committed; treat it as source. If regeneration is needed later, ask me.
- `POST /api/demo/reset` wipes STORE **and** LOG then re-seeds — safe to call
  before any demo run.
- Offer ids are deterministic: `off_<SKU>`, e.g. `off_AST-HP-ANC-001`.

---

## Addendum (post-integration fixes, 2026-08-26)

Per Agent D's verification pass, two bugs in `apply_fulfillment_event` broke
the hero happy path and the late scenario. Both fixed in
`apps/api/project_dante/integrations/merchant/service.py`:

1. **Deliver now observes every verifiable material promise.** On deliver
   (all scenarios) facts additionally include:
   - `price.amount_paise` — contract `amount_paise` first, promise fallback;
   - `warranty.region` — copied from the frozen promise on correct/late,
     `"AE"` on wrong_variant;
   - `condition` — promised value on all scenarios.
2. **Deadline key corrected**: late scenario anchors on the frozen
   `delivery.promised_by_date` promise (was the never-populated
   `delivery.latest`). A `delivery.days_late = 3` fact is still emitted.

Supporting change in my `catalog_loader.py`: `load_catalog()` stamps a
concrete `delivery_promise.promised_by_date = today + max_days` onto every
listing — a live merchant API quotes dated promises at query time; without a
dated promise nothing downstream can freeze or verify an SLA deadline.

**Verified end-to-end** (hero offer, real freeze + verifier):
correct → `satisfied=true`, status SATISFIED; wrong_variant → 2×
MATERIAL_VARIANT_MISMATCH → BREACH_DETECTED; late → DELIVERY_SLA_MISS
(material) → BREACH_DETECTED.

**Tests after fixes:** mandated set
(`tests/test_demo_sim.py tests/test_merchant.py tests/test_verification.py tests/test_promises.py`)
→ **75 passed**; full repo suite → **301 passed, 4 subtests passed**; ruff clean.

### Cross-agent gaps found while verifying (NOT mine — for the lead)

These block the route-level happy path but are in other agents' files; I did
not touch them:

1. **Frozen promises are stored with `contract_id=None`**
   (`domain/promises/pipeline.py::freeze_promise_set` persists promises before
   any contract exists, and `api/routes/intents.py::select_offer` never
   back-fills). Consequence: authorize finds no price promise (409 "frozen
   price unknown"), verifier/fulfillment see zero per-contract promises.
   Fix belongs in select-offer: stamp `contract_id` from the contract's
   `promise_ids` after persisting the contract. I verified this one-line-ish
   wiring unblocks authorize + verify.
2. **CONTRACT DRIFT false positive at payment-order**
   (`integrations/razorpay/service.py` / `api/routes/payments.py`): B's
   `_recompute_contract_hash` hashes the raw STORE offer record and raw
   promise records, but the pipeline's frozen hashes are computed over
   strip-volatile offers (`expires_at`, `inventory` removed) and canonical
   `(key, normalized_value)` pairs — different schemes, guaranteed mismatch ⇒
   409 `contract_drift` on every pipeline-frozen contract. B must recompute
   using the pipeline's own functions instead of raw records.
3. Minor: intent text with a hard delivery deadline ("arriving by Thursday")
   currently makes the hero offer infeasible at evaluation time (Agent C's
   evaluator compares the deadline against `max_days` from query date); worth
   a look so the full storyboard brief compiles cleanly.

With gaps 1+2 shimmed around, the complete flow through my routes reaches
SATISFIED (verified via HTTP above).

