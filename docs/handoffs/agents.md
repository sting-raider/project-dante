# Handoff — Agent C (Agent Runtime: provider, intent compiler, offer evaluator, intents routes)

## Goal

Build the buyer-side agent runtime for Project Dante: a typed model-provider
abstraction with an Anthropic structured-output implementation, an
`IntentCompilerAgent` that turns raw buyer text into a validated `BuyerIntent`
(LLM path plus an excellent deterministic rules path), a
deterministic-authority `OfferEvaluatorAgent`, and the `/api/intents/*` routes
from the frozen API contract — with the absolute invariant that no code path
can mark an offer feasible while violating a hard constraint.

## Completed

- **Provider abstraction** (`agents/provider.py`): `ModelProvider` protocol;
  `AnthropicProvider` calling `POST https://api.anthropic.com/v1/messages`
  directly over httpx (headers `x-api-key`, `anthropic-version: 2023-06-01`),
  tool-use-free structured output: schema-only JSON request → pydantic
  validation → one error-feedback retry → `AgentValidationError`. 30s timeout.
  Respects `settings.llm_model`. Transport errors fail fast to the caller's
  fallback. Secrets never enter prompts, logs, or STORE records.
  `DeterministicProvider` raises `NotImplementedError` by design — the rules
  engines are the real fallback. `get_provider()` returns `None` unless
  `settings.llm_enabled`; every caller drops to rules when None.
- **IntentCompilerAgent** (`agents/compiler.py`): two paths producing the
  identical frozen `BuyerIntent` shape. LLM path uses the §51 prompt
  principles (untrusted-text framing, schema-only output, unknown > invented,
  integer paise). Rules path handles price caps (₹/Rs/INR symbol, comma
  grouping, `12k`, `1.5 lakh`, `12000 rupees`, `<=`, "not over"), category
  keywords (headphones, earbuds, router, laptop, charger, cable, keyboard,
  mouse, monitor, phone), form factor (over-ear/on-ear/earbuds), ANC,
  warranty phrases ("india manufacturer warranty" / "official" / "brand"
  → manufacturer+IN; "seller warranty" → seller), delivery deadlines
  (`by <weekday>` next occurrence, `tomorrow`, `within N days`), brand soft
  preferences (17 brands incl. TP-Link/D-Link alias collapse, boAt casing),
  color/storage variants, and substitution language ("no substitutes" /
  "exactly"). Unknown values are omitted, never invented; contradictions are
  recorded and left for the evaluator to find infeasible. Every compile
  persists the intent record and appends INTENT_RECEIVED + INTENT_COMPILED.
- **OfferEvaluatorAgent** (`agents/evaluator.py`): deterministic core is
  authoritative. Checks every critical constraint against structured offer
  fields (price cap + `max_total_amount_paise`, category contains-style match,
  form_factor, anc, warranty type — where `"unknown"` FAILS a manufacturer
  constraint, warranty region, delivery deadline via promised_by_date OR
  min/max-day window). Hard failures carry `{key, op, expected, actual}`.
  Soft scores: brand preference, relative price, delivery speed, warranty
  duration. Feasible offers rank first by weighted soft total, then cheaper.
  Optional LLM pass rephrases explanations only and can never change
  feasibility or order; disagreement is logged to agent_run. Appends
  OFFER_EVALUATED per evaluation run.
- **Routes** (`api/routes/intents.py`) exactly per contract:
  - `POST /api/intents/compile {raw_text} → {intent, engine}`
  - `POST /api/intents/{id}/search → {intent, results:[{offer, evaluation}],
    engine}` — merchant service search first, STORE offers as fallback;
    persists `_type=evaluation` summaries; appends CATALOG_SEARCHED.
  - `POST /api/intents/{id}/select-offer {offer_id} → {contract, promises,
    evidence, _freeze_via}` — rejects infeasible/un-evaluated offers with 409
    (a hard-constraint violation can NEVER be selected), creates the contract
    through the state machine (INTENT_READY→OFFER_SELECTED→CONTRACT_FROZEN),
    `display_code = COV-<4 digits>`, hashes offer via `sha256_hex`, freezes
    promises through Agent D's `freeze_promise_set` (verified working), and
    appends OFFER_SELECTED + CONTRACT_CREATED.

## Files

- `apps/api/project_dante/agents/provider.py`
- `apps/api/project_dante/agents/compiler.py`
- `apps/api/project_dante/agents/evaluator.py`
- `apps/api/project_dante/api/routes/intents.py`
- `apps/api/tests/test_agents.py` (17 tests)
- `apps/api/tests/test_intent_rules.py` (28 tests)

## Public interfaces

```python
# project_dante.agents.provider
get_provider(settings) -> AnthropicProvider | None   # None => rules engine
class AgentValidationError(Exception)                # after 2 attempts
_log_agent_run(**kwargs)                             # shared agent_run persistence

# project_dante.agents.compiler
get_compiler() -> IntentCompilerAgent                # provider wired from settings
await IntentCompilerAgent.compile(raw_text, trace_id=None) -> BuyerIntent
rule_compile(raw_text) -> BuyerIntent                # pure rules path, no I/O
extract_price_caps / extract_category / extract_attributes /
extract_warranty / extract_delivery / extract_brands  # unit-testable pieces

# project_dante.agents.evaluator
get_evaluator() -> OfferEvaluatorAgent               # provider wired from settings
OfferEvaluatorAgent.evaluate(intent_dict, offers) -> [
    {"offer": dict, "rank": int,
     "evaluation": {"feasible": bool, "hard_failures": [...],
                    "soft_scores": [...], "soft_total": float,
                    "explanation": str}}, ...]
await OfferEvaluatorAgent.enrich_explanations(intent_dict, results) -> results
```

Note for evaluators-of-record: `evaluate()` returns items carrying an extra
`soft_total` key used internally for ranking; the route strips it before
responding so the wire format matches the contract exactly.

## Tests with real results

```
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_agents.py tests/test_intent_rules.py -q
64 passed in 0.56s
```

Eval iteration round 1 (Agent J's intent dataset, rules path): all 7 reported
gap clusters fixed — trailing price caps ("150k max", "cap at 12k", "Budget
10k.", "12k budget,"), word-number caps ("under fifteen thousand") with a
duration guard ("under three days" -> delivery, not money), "500 bucks tops",
"willing to go to 13k", warranty word-order variants ("Manufacturer India
warranty", "warranty from the manufacturer ... valid in India",
"manufacturer-backed AND valid in India"), condition extraction ("brand new",
"new condition"), Aster catalog brands (zephyr/orbio/soniq/kaira/voltaq/hexon/
lumenx/quanta/nucleon), bare "over-ears"/"over-ear cans" -> headphones,
delivery verbs "arriving" + "before this coming Thursday"/"before next Friday"
qualifiers + "under N days" windows, and "Do NOT substitute alternatives".
Each case is a regression test under the "eval round 1 gaps" section of
test_intent_rules.py. Known-arguable: INT-051 "seller warranty acceptable"
hard-gates seller warranty (no polarity detection for "acceptable"); flagged,
left as-is pending dataset-owner decision.

Coverage highlights: hero query parses to category=headphones,
form_factor=over-ear, anc=true, max price 1200000 paise, warranty
manufacturer+IN, delivery deadline = next Thursday; unknown-warranty offer
fails manufacturer constraint; missing terms fail closed; expensive fails cap;
late delivery fails deadline; feasible ranks before infeasible; contradictory
intent yields zero feasible; hard-failure shape matches contract; select-offer
rejects infeasible with 409 and requires prior search.

End-to-end route smoke (run manually, not part of pytest): hero query →
13 catalog candidates → exactly 1 feasible (`off_AST-HP-005`, Soniq ₹6,499,
manufacturer/IN warranty, over-ear ANC) → select freezes contract
`CONTRACT_FROZEN` via pipeline with offer_hash + promise_set_hash set →
infeasible select attempt → HTTP 409.

## Known risks

- The LLM compile path is exercised only by construction (no key in this
  environment); the rules path carries all test coverage. If the LLM path is
  enabled live, its output still flows into the same deterministic evaluator,
  so feasibility authority is unaffected.
- `search_catalog` requires every query token to match somewhere; raw buyer
  prose yields zero hits. Routes distill constraints/brands into keywords
  (`_keyword_query`). If Agent F later loosens tokenization, the distillation
  remains correct (subset semantics).
- Brand soft preferences come only from compiled intents; offers whose brand
  strings differ in case are matched case-insensitively but not fuzzily.
- Delivery check treats a min-days-only promise as its earliest arrival
  (conservative); offers with no delivery data fail any delivery-deadline
  constraint rather than pass optimistically.
- Contract ids use `con_<12 random digits>`; if another module expects a
  different id convention, adjust at integration.

## Integration notes

- **Agent D** (promises): route calls
  `freeze_promise_set(offer_dict, intent_dict)` from
  `project_dante.domain.promises.pipeline` — confirmed present and working
  (`_freeze_via: "pipeline"` in smoke test). Fallback inline freeze stores
  `offer_hash` + `promise_set_hash` and leaves `promises=[]` if the import
  disappears. Verifier should treat `"unknown"` warranty the same way the
  evaluator does: failing a manufacturer-warranty promise, never satisfying it.
- **Agent B** (payments): contracts created here are `CONTRACT_FROZEN`,
  `sandbox_mode=True`, with `amount_paise` from the offer — ready for
  `/payment-order`. No Razorpay fields beyond ids are touched here.
- **Agent D** (verifier): select-offer stamps the selected evaluation record
  with `contract_id` and a `constraints` snapshot (critical hard constraints,
  frozen intent keys verbatim). `evaluate_contract`'s `_evaluation_floor`
  matches by `contract_id` and floors mismatch severity at material for those
  keys. Known map gap flagged to D: `pipeline.CONSTRAINT_TO_PROMISE` lacks
  entries for `attributes.form_factor` / `attributes.anc` (it holds bare
  `form_factor` / `anc`), so those two floors need an alias on their side.
- **Agent E** (rights/remedies): evaluation records (`_type=evaluation`) and
  the frozen contract's `promise_ids` are the materiality inputs; breach
  severity for a violated manufacturer-region warranty should be material+
  because it is always a critical constraint at selection time.
- **Agent F** (merchant): `_fetch_offers` calls
  `search_catalog(query, category, max_price_paise, limit)` with the real
  signature; `_resolve_offer` scans `search_catalog(query=None, limit=500)`
  for id lookup. If a direct `get_offer(offer_id)` appears, swap it in.
- **API_CONTRACT.md deltas**: none broken. Two additive notes:
  (1) `/search` response `evaluation` objects omit the internal `soft_total`;
  (2) `/select-offer` adds `_freeze_via` to the response for demo
  transparency. Both documented above; flag if the frontend needs them
  removed.

## Cross-module needs (for team-lead triage)

- None blocking. Optional niceties: a `get_offer(offer_id)` on the merchant
  service would replace my scan-based resolver, and a shared trace-id header
  convention across all routes would make agent_run records joinable to
  frontend traces.
