# API CONTRACT — FROZEN for Wave 1

All request/response bodies are JSON. Money is integer **paise**. All timestamps ISO-8601.
Domain shapes come from `apps/api/project_dante/domain/types.py` — serialized Pydantic models.
Synthetic/demo data always carries `"synthetic": true`.

Every HTTP response includes `X-Trace-Id` and `X-Correlation-Id`; responses for
contract URL paths also include `X-Contract-Id`. These identifiers are safe to
return to the caller and correspond to the structured `http_request_completed`
log record. Request bodies, query strings, credentials, and exception text are
not logged.

## Buyer / intent — routes/intents.py (Agent C)

```
POST /api/intents/compile            {raw_text: str}
  → 200 {intent: BuyerIntent, engine: "rules"|"llm"}

POST /api/intents/{intent_id}/search
  → 200 {intent, results: [{offer: MerchantOffer,
          evaluation: {feasible: bool, hard_failures: [{key, op, expected, actual}],
                       soft_scores: [{key, weight, score, note}], explanation: str}}],
         items: [{item_id, label, quantity, max_price_paise, feasible_count,
                  recommended_offer_id, results: [...]}],
         bundle_recommendation: {available, engine, offer_ids,
                                total_amount_paise, score, reason}, engine}

  For a multi-item intent, `intent.items[]` is the typed line-item envelope.
  Each line is evaluated independently against its own hard constraints and
  unit cap. `bundle_recommendation` is optional decision support: it can only
  choose rows whose deterministic evaluation is already feasible and whose
  quantity-adjusted aggregate stays within `intent.max_total_amount_paise`.
  An unavailable recommendation is fail-closed and never relaxes a line.

POST /api/intents/{intent_id}/select-offer
  single item: {offer_id: str}
  bundle: {items: [{item_id: str, offer_id: str}]}
  → 200 {contract: DanteContract, promises: Promise[], evidence: EvidenceArtifact[]}
  The server requires every requested line exactly once, rechecks its stored
  feasible evaluation, rechecks quantity inventory and the parent total cap,
  then creates one aggregate contract with one frozen line per selection.
  (compiles intent→OFFER_SELECTED, freezes promise set → CONTRACT_FROZEN)
```

## Contracts — routes/contracts.py (Agent D)

```
GET  /api/contracts/{id}
  → {contract, promises: Promise[], entitlements: Entitlement[]}
  `contract.line_items[]` carries the frozen `intent_item_id`, offer identity,
  quantity, unit/line amount, offer hash and line promise ids. `amount_paise`
  is the sum of every frozen line amount.

POST /api/contracts/{id}/authorize   {}
  → {contract}   # sets AuthorityEnvelope bound to contract_hash; BUYER_AUTHORIZED event

GET  /api/contracts/{id}/timeline    ?category=Agent|Money|Merchant|Fulfillment|Policy|Evidence
  → {events: DomainEvent[]}

POST /api/contracts/{id}/verify
  → {breaches: Breach[], status: ContractStatus, satisfied: bool}
```

## Payments — routes/payments.py + webhooks.py (Agent B)

```
POST /api/contracts/{id}/payment-order   {}
  → {mode: "live-test-mode"|"sandbox", razorpay_order: {...},
     checkout_config: {key_id, order_id, amount_paise, currency}}
  `checkout_config.key_id` is the public key-id transport field. The browser
  maps its value to Razorpay Standard Checkout's `key` option; `key_id` is
  never passed to `new Razorpay(...)`.

GET /api/contracts/{id}/payment-order
  → the same existing order response (read-only; only when status is
    PAYMENT_ORDER_CREATED or PAYMENT_PENDING; 404/409 when the stored order
    is unavailable or no longer matches the frozen contract)

POST /api/payments/verify-client  {contract_id, razorpay_order_id, razorpay_payment_id, signature}
  → {status: "client_confirmed", contract_status}

POST /api/webhooks/razorpay        # RAW body; X-Razorpay-Signature header
  → 200 {"ok": true}               # 401 bad signature; 400 stale/missing created_at; duplicates idempotent

POST /api/demo/razorpay/simulate-event   # DEMO_MODE+sandbox only
  {event_type: "payment.captured", order_id, payment_id}
  → {delivered: true}              # generates REAL signed webhook payload internally
```

## Rights & remedies — routes/rights.py (Agent E)

```
GET  /api/contracts/{id}/rights
  → {graph: {nodes: [], edges: []}, entitlements: Entitlement[]}
GET  /api/contracts/{id}/breaches  → {breaches: Breach[]}
GET  /api/contracts/{id}/remedies → {proposals: RemedyProposal[]}   # ranked + rejected_reason

POST /api/remedies/{proposal_id}/policy   → {decision: PolicyDecision, money_action}
POST /api/remedies/{proposal_id}/approve  → {money_action}
  Header: X-Demo-Operator-Token (required; must match DEMO_OPERATOR_TOKEN)
  → 403 for a missing/invalid token; 503 when human approval is unconfigured
POST /api/remedies/{proposal_id}/execute  → {money_action, refund: {...}|null, decision}
```

## Merchant — routes/merchant.py (Agent F)

```
GET  /api/merchant/catalog/search?q=&category=&max_price_paise=&limit=
  → {results: [MerchantOffer]}
GET  /api/merchant/products/{sku}           → {product, offers}
GET  /api/merchant/analytics                → {metrics for dashboard}
```

## Demo control — routes/demo.py (Agent F)

```
POST /api/demo/reset                        → {reset: true, products: N}
POST /api/demo/contracts/{id}/ship          → {event}
POST /api/demo/contracts/{id}/deliver       {scenario: "correct"|"wrong_variant"|"late"}
  → {observed_facts, breaches, contract_status}   # auto-runs verification
```

## Frozen service-module interfaces (cross-agent imports)

```python
# Agent F provides — project_dante/integrations/merchant/service.py
def search_catalog(query=None, filters=None, limit=50) -> list[dict]      # MerchantOffer dicts
def get_product(sku) -> dict | None
def check_inventory(sku) -> int
def freeze_offer(offer_id) -> dict                                        # snapshot + evidence payload
def apply_fulfillment_event(contract_id, kind, scenario=None) -> dict     # ship/deliver, appends facts
def seed_catalog() -> int

# Agent D provides — project_dante/domain/promises/pipeline.py
def build_evidence(source_type, payload, trusted_level, synthetic=False, scenario_id=None) -> dict
def extract_promises(offer_dict, evidence_dict) -> list[dict]
def link_materiality(promises, intent_dict) -> list[dict]
def freeze_promise_set(offer_dict, intent_dict) -> dict                   # {promises, evidence_ids, promise_set_hash}
# Agent D provides — project_dante/domain/promises/verifier.py
def evaluate_contract(contract_id) -> dict                                # {breaches, satisfied}

# Agent B provides — project_dante/integrations/razorpay/service.py
def mode() -> str
def create_order(amount_paise, receipt="", notes=None) -> dict
def verify_checkout_signature(order_id, payment_id, signature) -> bool
def verify_webhook_signature(raw_body: bytes, signature: str) -> bool
def fetch_payment(payment_id) -> dict | None
def create_refund(payment_id, amount_paise=None, idempotency_key="", notes=None) -> dict

# Agent E provides — project_dante/domain/rights/engine.py
def build_rights_graph(contract_id) -> dict
def evaluate_eligibility(contract_id) -> list[dict]
# Agent E provides — project_dante/domain/remedies/planner.py
def plan_remedies(contract_id) -> dict
# Agent E provides — project_dante/domain/money/policy.py
def evaluate_money_action(proposal_dict) -> dict                          # PolicyDecision
def execute_remedy(proposal_id) -> dict                                   # full gated pipeline
```

Store/event APIs everyone shares:

```python
from project_dante.db.store import STORE      # put/get/update/find/list/find_one/delete/reset
from project_dante.domain.events import append_event, EVENT_TYPES
from project_dante.domain.hashing import sha256_hex
from project_dante.domain.state_machine import validate_transition
from project_dante.domain.types import ...    # all frozen models
```
